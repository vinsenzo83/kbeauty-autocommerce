from __future__ import annotations

"""
app/crawlers/stylekorean_crawler.py
────────────────────────────────────
Crawl the StyleKorean Best Sellers page, collect up to
``PRODUCT_CRAWL_LIMIT`` product URLs, fetch each page via Playwright,
parse with ``product_parser``, and upsert into the database.

Usage (Celery task):
    from app.crawlers.stylekorean_crawler import crawl_best_sellers
    await crawl_best_sellers(db_session)

Playwright is always lazy-imported so unit tests never touch a real browser.
"""

import asyncio
import os
from typing import Any

import structlog

from app.config import get_settings
from app.crawlers.product_parser import parse_product_page

logger = structlog.get_logger(__name__)

# ── Selector / URL constants (patchable in tests) ────────────────────────────
BEST_SELLERS_PATH         = "/best-sellers"
PRODUCT_GRID_SELECTOR     = "ul.product-list li.item, .products-grid .item"
PRODUCT_LINK_SELECTOR     = "a.product-link, .product-name a, h2.product-name a"
NEXT_PAGE_SELECTOR        = "a.next, .pager .next a, li.pages-item-next a"
PAGE_LOAD_WAIT_SELECTOR   = ".products-grid, ul.product-list"
PRODUCT_CONTENT_SELECTOR  = ".product-view, #product-content, main"

# ── Crawl configuration (env-overridable) ────────────────────────────────────
_settings = get_settings()
_BASE_URL  = os.getenv("STYLEKOREAN_BASE_URL", "https://www.stylekorean.com")
_LIMIT     = int(os.getenv("PRODUCT_CRAWL_LIMIT", "500"))

# Per-page request timeout (ms)
_PAGE_TIMEOUT = 30_000


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _collect_product_urls(page: Any, limit: int) -> list[str]:
    """
    Navigate Best Sellers page(s) and collect up to ``limit`` product URLs.
    """
    urls: list[str] = []
    current_url = f"{_BASE_URL}{BEST_SELLERS_PATH}"

    while len(urls) < limit:
        logger.info(
            "crawler.navigate",
            url=current_url,
            collected=len(urls),
            limit=limit,
        )

        await page.goto(current_url, timeout=_PAGE_TIMEOUT, wait_until="domcontentloaded")

        try:
            await page.wait_for_selector(PAGE_LOAD_WAIT_SELECTOR, timeout=10_000)
        except Exception:
            logger.warning("crawler.grid_not_found", url=current_url)
            break

        # Collect links on current page
        link_elements = await page.query_selector_all(PRODUCT_LINK_SELECTOR)
        for el in link_elements:
            href = await el.get_attribute("href")
            if href:
                full = href if href.startswith("http") else f"{_BASE_URL}{href}"
                if full not in urls:
                    urls.append(full)
                if len(urls) >= limit:
                    break

        # Next page?
        next_el = await page.query_selector(NEXT_PAGE_SELECTOR)
        if not next_el or len(urls) >= limit:
            break

        next_href = await next_el.get_attribute("href")
        if not next_href:
            break
        current_url = next_href if next_href.startswith("http") else f"{_BASE_URL}{next_href}"

    return urls[:limit]


async def _fetch_product_data(page: Any, url: str) -> dict[str, Any] | None:
    """
    Navigate to a product page and return parsed data dict, or None on failure.
    """
    try:
        await page.goto(url, timeout=_PAGE_TIMEOUT, wait_until="domcontentloaded")
        html = await page.content()
        data = parse_product_page(html)
        data["supplier_product_url"] = url
        data["supplier_product_id"]  = _url_to_product_id(url)
        return data
    except Exception as exc:
        logger.warning("crawler.product_fetch_failed", url=url, error=str(exc))
        return None


def _url_to_product_id(url: str) -> str:
    """
    Derive a stable supplier_product_id from the product URL.
    Uses the last non-empty path segment (before query string).

    Examples
    --------
    https://www.stylekorean.com/products/some-cream-123  →  "some-cream-123"
    """
    from urllib.parse import urlparse
    parsed   = urlparse(url)
    segments = [s for s in parsed.path.split("/") if s]
    return segments[-1] if segments else url


# ── Public API ────────────────────────────────────────────────────────────────

async def crawl_best_sellers(
    db_session: Any,
    *,
    limit: int | None = None,
    upsert_fn: Any = None,
) -> list[dict[str, Any]]:
    """
    Crawl StyleKorean Best Sellers and upsert products into the database.

    Parameters
    ----------
    db_session : AsyncSession
        Active SQLAlchemy async session.
    limit      : int | None
        Override ``PRODUCT_CRAWL_LIMIT`` env variable.
    upsert_fn  : callable | None
        Inject a custom upsert coroutine for testing.
        Signature: ``async upsert_fn(session, product_data) -> Product``
        Defaults to ``app.services.product_service.upsert_product``.

    Returns
    -------
    List of raw parsed product dicts (without DB model objects).
    """
    if upsert_fn is None:
        from app.services.product_service import upsert_product
        upsert_fn = upsert_product

    effective_limit = limit if limit is not None else _LIMIT

    # Lazy-import Playwright so tests without browser still work
    try:
        from playwright.async_api import async_playwright  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "Playwright is not installed. "
            "Run: pip install playwright && playwright install chromium"
        )

    results: list[dict[str, Any]] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        try:
            # Phase 1: collect product URLs
            product_urls = await _collect_product_urls(page, limit=effective_limit)
            logger.info(
                "crawler.urls_collected",
                count=len(product_urls),
                limit=effective_limit,
            )

            # Phase 2: fetch + parse + upsert each product
            for idx, url in enumerate(product_urls, start=1):
                data = await _fetch_product_data(page, url)
                if data is None:
                    continue

                # Ensure required fields have fallbacks
                if not data.get("name"):
                    data["name"] = data.get("supplier_product_id", "Unknown")

                data["supplier"] = "stylekorean"
                if not data.get("currency"):
                    data["currency"] = "USD"

                try:
                    await upsert_fn(db_session, data)
                    results.append(data)
                    logger.info(
                        "crawler.product_upserted",
                        idx=idx,
                        total=len(product_urls),
                        name=data["name"],
                    )
                except Exception as exc:
                    logger.error(
                        "crawler.upsert_failed",
                        url=url,
                        error=str(exc),
                    )

        finally:
            await context.close()
            await browser.close()

    logger.info("crawler.done", upserted=len(results))
    return results
