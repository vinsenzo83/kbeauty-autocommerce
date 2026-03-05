from __future__ import annotations

"""
app/crawlers/jolse_inventory.py
────────────────────────────────
Sprint 7 – per-product inventory check for Jolse (jolse.com).

Public API
----------
fetch_inventory(product_url, *, page=None) -> {"in_stock": bool, "price": float | None}

Implementation notes
--------------------
* Playwright is lazy-imported so unit tests never launch a real browser.
* A ``page`` kwarg allows test injection of a pre-configured mock page.
* Design mirrors stylekorean_inventory.py for consistency.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ── Selector constants ────────────────────────────────────────────────────────

OOS_PRESENCE_SELECTORS = [
    ".sold-out",
    ".out-of-stock",
    "[class*='sold-out']",
    "[class*='out-of-stock']",
    "#out-of-stock",
]

OOS_TEXT_SELECTORS = [
    ".stock-status",
    ".availability",
    ".product-availability",
    "[class*='stock']",
]

_OOS_KEYWORDS = frozenset(
    ["out of stock", "sold out", "unavailable", "out-of-stock", "soldout"]
)

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
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.,]", "", raw.strip())
    cleaned = re.sub(r",(\d{3})", r"\1", cleaned)
    cleaned = cleaned.replace(",", ".")
    try:
        return float(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return None


async def _detect_out_of_stock(page: Any) -> bool:
    for sel in OOS_PRESENCE_SELECTORS:
        el = await page.query_selector(sel)
        if el is not None:
            return True
    for sel in OOS_TEXT_SELECTORS:
        el = await page.query_selector(sel)
        if el is not None:
            text = (await el.inner_text()).lower().strip()
            if any(kw in text for kw in _OOS_KEYWORDS):
                return True
    return False


async def _extract_price(page: Any) -> float | None:
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


async def fetch_inventory(
    product_url: str,
    *,
    page: Any = None,
) -> dict[str, Any]:
    """
    Fetch inventory data for a single Jolse product URL.

    Parameters
    ----------
    product_url : str  – canonical product URL on jolse.com
    page        : Playwright Page | None  – injected by tests; None = launch real browser

    Returns
    -------
    {"in_stock": bool, "price": float | None}
    """
    log = logger.bind(url=product_url, supplier="jolse")

    if page is not None:
        # Test-injected page – skip browser launch
        if hasattr(page, "goto"):
            await page.goto(product_url)
        in_stock = not await _detect_out_of_stock(page)
        price    = await _extract_price(page)
        log.info("jolse_inventory.result", in_stock=in_stock, price=price)
        return {"in_stock": in_stock, "price": price}

    # ── Real browser (production) ─────────────────────────────────────────────
    try:
        from playwright.async_api import async_playwright  # type: ignore[import]
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "playwright is not installed. "
            "Run: pip install playwright && playwright install chromium"
        ) from exc

    async with async_playwright() as pw:  # type: ignore[name-defined]
        browser = await pw.chromium.launch(headless=True)
        ctx     = await browser.new_context()
        _page   = await ctx.new_page()
        try:
            await _page.goto(product_url, wait_until="networkidle")
            in_stock = not await _detect_out_of_stock(_page)
            price    = await _extract_price(_page)
        finally:
            await browser.close()

    log.info("jolse_inventory.result", in_stock=in_stock, price=price)
    return {"in_stock": in_stock, "price": price}
