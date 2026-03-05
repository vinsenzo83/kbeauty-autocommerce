from __future__ import annotations

"""
app/crawlers/oliveyoung_crawler.py
────────────────────────────────────
Sprint 7 – per-product inventory check for OliveYoung (oliveyoung.co.kr / global).

Public API
----------
fetch_product_inventory(product_url, *, page=None) -> dict

Returns
-------
{
    "in_stock": bool,
    "price": float | None,
}

Implementation notes
--------------------
* Playwright is lazy-imported so unit tests never launch a real browser.
* A ``page`` kwarg allows test injection of a pre-configured mock page.
* OliveYoung uses Korean and English OOS markers; both are handled.
* Price extraction covers OliveYoung's price container selectors.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ── Selector constants ────────────────────────────────────────────────────────

# Selectors whose *presence* signals out-of-stock
OOS_PRESENCE_SELECTORS = [
    ".sold-out",
    ".out-of-stock",
    "[class*='sold-out']",
    "[class*='out-of-stock']",
    ".btn-soldout",
    "#btnSoldOut",
    ".oos-badge",
]

# Selectors whose *text* may signal out-of-stock (Korean + English)
OOS_TEXT_SELECTORS = [
    ".goods-flag",
    ".prd-flag",
    ".status-label",
    ".stock-status",
    ".availability",
    "[class*='stock']",
    "[class*='soldout']",
]

# Keywords (lowercase) that indicate out-of-stock
_OOS_KEYWORDS = frozenset(
    [
        "out of stock",
        "sold out",
        "unavailable",
        "품절",         # Korean: "out of stock"
        "일시품절",     # Korean: "temporarily out of stock"
        "soldout",
        "out-of-stock",
    ]
)

# Price selectors (OliveYoung-specific + generic fallbacks)
PRICE_SELECTORS = [
    "[itemprop='price']",
    ".price-box .price",
    ".prd-price .price",
    ".goods-price .price",
    ".final-price",
    ".sale-price",
    "span.price",
    ".product-price",
]


def _normalise_price(raw: str) -> float | None:
    """
    Clean up a price string and return a float.

    Handles: '$12.50', '₩15,000', '15.00 USD', '12,500'
    Returns None if unparseable.
    """
    if not raw:
        return None
    # Strip all non-numeric except dot and comma
    cleaned = re.sub(r"[^\d.,]", "", raw.strip())
    # Remove thousands separators when followed by exactly 3 digits
    cleaned = re.sub(r",(\d{3})(?!\d)", r"\1", cleaned)
    # Replace remaining comma with dot
    cleaned = cleaned.replace(",", ".")
    try:
        return float(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return None


async def _detect_out_of_stock(page: Any) -> bool:
    """
    Return True if any OOS signal is detected on the page.
    """
    # 1. Presence selectors
    for sel in OOS_PRESENCE_SELECTORS:
        el = await page.query_selector(sel)
        if el is not None:
            logger.debug("oliveyoung_crawler.oos_presence", selector=sel)
            return True

    # 2. Text keyword selectors
    for sel in OOS_TEXT_SELECTORS:
        el = await page.query_selector(sel)
        if el is not None:
            text = (await el.inner_text()).lower().strip()
            if any(kw in text for kw in _OOS_KEYWORDS):
                logger.debug("oliveyoung_crawler.oos_text", selector=sel, text=text)
                return True

    return False


async def _extract_price(page: Any) -> float | None:
    """
    Extract the product price from the page.
    """
    for sel in PRICE_SELECTORS:
        el = await page.query_selector(sel)
        if el is None:
            continue
        content = await el.get_attribute("content")
        if content:
            price = _normalise_price(content)
            if price is not None:
                return price
        text = await el.inner_text()
        price = _normalise_price(text)
        if price is not None:
            return price

    return None


async def fetch_product_inventory(
    product_url: str,
    *,
    page: Any = None,
) -> dict[str, Any]:
    """
    Fetch inventory data for a single OliveYoung product URL.

    Parameters
    ----------
    product_url : str
        Canonical product URL on oliveyoung.co.kr or global.oliveyoung.com.
    page : Playwright Page | None
        Pre-configured page for tests; when None a real browser is launched.

    Returns
    -------
    {"in_stock": bool, "price": float | None}
    """
    log = logger.bind(url=product_url, crawler="oliveyoung")

    _own_browser = page is None

    if _own_browser:
        try:
            from playwright.async_api import async_playwright  # type: ignore[import]
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "playwright is not installed. "
                "Run: pip install playwright && playwright install chromium"
            ) from exc

    if _own_browser:
        async with async_playwright() as pw:  # type: ignore[name-defined]
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context()
            _page = await ctx.new_page()
            try:
                await _page.goto(product_url, wait_until="networkidle")
                return await _scrape(_page, log)
            finally:
                await browser.close()
    else:
        if hasattr(page, "goto"):
            await page.goto(product_url)
        return await _scrape(page, log)


async def _scrape(page: Any, log: Any) -> dict[str, Any]:
    in_stock = not await _detect_out_of_stock(page)
    price    = await _extract_price(page)
    log.info("oliveyoung_crawler.result", in_stock=in_stock, price=price)
    return {"in_stock": in_stock, "price": price}
