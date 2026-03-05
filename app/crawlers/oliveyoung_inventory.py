from __future__ import annotations

"""
app/crawlers/oliveyoung_inventory.py
──────────────────────────────────────
Sprint 7 – per-product inventory check for OliveYoung (global.oliveyoung.com).

Public API
----------
fetch_inventory(product_url, *, page=None) -> {"in_stock": bool, "price": float | None}

Implementation notes
--------------------
* Playwright is lazy-imported so unit tests never launch a real browser.
* A ``page`` kwarg allows test injection of a pre-configured mock page.
* OliveYoung uses both Korean and English OOS markers.
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
    ".btn-soldout",
    "#btnSoldOut",
    ".oos-badge",
]

OOS_TEXT_SELECTORS = [
    ".goods-flag",
    ".prd-flag",
    ".status-label",
    ".stock-status",
    ".availability",
    "[class*='stock']",
    "[class*='soldout']",
]

_OOS_KEYWORDS = frozenset(
    [
        "out of stock",
        "sold out",
        "unavailable",
        "품절",
        "일시품절",
        "soldout",
        "out-of-stock",
    ]
)

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
    if not raw:
        return None
    cleaned = re.sub(r"[^\d.,]", "", raw.strip())
    cleaned = re.sub(r",(\d{3})(?!\d)", r"\1", cleaned)
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
    Fetch inventory data for a single OliveYoung product URL.

    Parameters
    ----------
    product_url : str  – canonical product URL on oliveyoung.co.kr or global site
    page        : Playwright Page | None  – injected by tests; None = launch real browser

    Returns
    -------
    {"in_stock": bool, "price": float | None}
    """
    log = logger.bind(url=product_url, supplier="oliveyoung")

    if page is not None:
        if hasattr(page, "goto"):
            await page.goto(product_url)
        in_stock = not await _detect_out_of_stock(page)
        price    = await _extract_price(page)
        log.info("oliveyoung_inventory.result", in_stock=in_stock, price=price)
        return {"in_stock": in_stock, "price": price}

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

    log.info("oliveyoung_inventory.result", in_stock=in_stock, price=price)
    return {"in_stock": in_stock, "price": price}
