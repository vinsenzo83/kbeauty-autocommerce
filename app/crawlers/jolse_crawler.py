from __future__ import annotations

"""
app/crawlers/jolse_crawler.py
──────────────────────────────
Sprint 7 – per-product inventory check for Jolse (jolse.com).

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
* Out-of-stock detection covers the most common Jolse product-page patterns.
* Price extraction tries structured data first (itemprop="price"), then
  falls back to Jolse-specific price containers.
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
    "#out-of-stock",
]

# Selectors whose *text* may signal out-of-stock
OOS_TEXT_SELECTORS = [
    ".stock-status",
    ".availability",
    ".product-availability",
    "[class*='stock']",
]

# Keywords in page text that indicate out-of-stock
_OOS_KEYWORDS = frozenset(
    ["out of stock", "sold out", "unavailable", "out-of-stock", "soldout"]
)

# Selectors used to extract price
PRICE_SELECTORS = [
    "[itemprop='price']",
    ".price .money",
    ".product-price",
    ".price-box .price",
    ".current-price",
    ".special-price .price",
    "span.price",
]


def _normalise_price(raw: str) -> float | None:
    """
    Clean up a price string and return a float.

    Handles formats: '$12.50', 'USD 25.00', '12,500', '9.99'
    Returns None if the string cannot be parsed.
    """
    if not raw:
        return None
    # Strip currency symbols and whitespace
    cleaned = re.sub(r"[^\d.,]", "", raw.strip())
    # Remove thousands separators (comma) when followed by 3+ digits
    cleaned = re.sub(r",(\d{3})", r"\1", cleaned)
    # Replace remaining comma with dot
    cleaned = cleaned.replace(",", ".")
    try:
        return float(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return None


async def _detect_out_of_stock(page: Any) -> bool:
    """
    Return True if any OOS signal is detected on the page.

    Strategy (in order):
    1. Presence-based selectors (`.sold-out`, etc.)
    2. Text-content keywords in stock-status elements
    """
    # 1. Presence selectors
    for sel in OOS_PRESENCE_SELECTORS:
        el = await page.query_selector(sel)
        if el is not None:
            logger.debug("jolse_crawler.oos_presence", selector=sel)
            return True

    # 2. Text keyword selectors
    for sel in OOS_TEXT_SELECTORS:
        el = await page.query_selector(sel)
        if el is not None:
            text = (await el.inner_text()).lower().strip()
            if any(kw in text for kw in _OOS_KEYWORDS):
                logger.debug("jolse_crawler.oos_text", selector=sel, text=text)
                return True

    return False


async def _extract_price(page: Any) -> float | None:
    """
    Extract the product price from the page.

    Tries selectors in order; returns the first parseable value.
    """
    for sel in PRICE_SELECTORS:
        el = await page.query_selector(sel)
        if el is None:
            continue
        # Try content attribute first (structured data)
        content = await el.get_attribute("content")
        if content:
            price = _normalise_price(content)
            if price is not None:
                return price
        # Fall back to inner text
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
    Fetch inventory data for a single Jolse product URL.

    Parameters
    ----------
    product_url : str
        Canonical product URL on jolse.com.
    page : Playwright Page | None
        Pre-configured page for tests; when None a real browser is launched.

    Returns
    -------
    {"in_stock": bool, "price": float | None}
    """
    log = logger.bind(url=product_url, crawler="jolse")

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
    log.info("jolse_crawler.result", in_stock=in_stock, price=price)
    return {"in_stock": in_stock, "price": price}
