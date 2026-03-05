from __future__ import annotations

"""
app/crawlers/stylekorean_inventory.py
──────────────────────────────────────
Sprint 6 – per-product inventory check for StyleKorean.

Public API
----------
fetch_inventory(product_url, *, page=None) -> dict

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
* Out-of-stock detection uses a multi-selector strategy covering the most
  common patterns found on StyleKorean product pages.
* Price extraction tries structured data first (itemprop="price"), then
  falls back to text scraping.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ── Selector constants (patchable in tests) ───────────────────────────────────

# Selectors whose *text* signals out-of-stock when matched
OUT_OF_STOCK_TEXT_SELECTORS = [
    ".stock-status",
    ".availability",
    "#availability",
    ".product-stock",
    "[class*='stock']",
    "[class*='availability']",
]

# Selectors that, if *present*, directly mean the item is unavailable
OUT_OF_STOCK_PRESENCE_SELECTORS = [
    ".out-of-stock",
    ".sold-out",
    "[class*='out-of-stock']",
    "[class*='sold-out']",
    "#out-of-stock",
]

# Keywords that indicate out-of-stock when found in page text
_OOS_KEYWORDS = frozenset(
    [
        "out of stock",
        "sold out",
        "unavailable",
        "not available",
        "temporarily out",
        "재고없음",      # Korean: no stock
        "품절",          # Korean: out of stock / sold out
    ]
)

# Price selectors in preference order
PRICE_SELECTORS = [
    "[itemprop='price']",
    ".price-box .special-price .price",
    ".price-box .regular-price .price",
    ".product-price .amount",
    ".sale-price .amount",
    ".price .amount",
    "#product-price",
    ".price",
]

_PAGE_TIMEOUT = 30_000  # ms


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_price(raw: str) -> float | None:
    """
    Extract a float price from a messy string like "$12.50", "USD 12.50", "12,500".

    Returns None if no numeric value can be parsed.
    """
    if not raw:
        return None
    # Remove currency symbols and non-numeric junk, keep digits, dot, comma
    cleaned = re.sub(r"[^\d.,]", "", raw.strip())
    if not cleaned:
        return None
    # Handle "12,500" (thousands comma) vs "12.50" (decimal dot)
    # Heuristic: if comma appears and last group after comma has ≠2 digits → thousands sep
    if "," in cleaned and "." not in cleaned:
        # e.g. "12,500" → remove comma
        cleaned = cleaned.replace(",", "")
    else:
        # e.g. "12,50" (European decimal) or "12.50"
        cleaned = cleaned.replace(",", ".")
    try:
        return float(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return None


async def _detect_out_of_stock(page: Any) -> bool:
    """
    Return True if the page indicates the product is out of stock.

    Strategy (in order):
    1. Check for presence-only OOS selectors (element existing → OOS).
    2. Check text-bearing selectors for OOS keywords.
    3. Scan a broader text region for OOS keywords.
    """
    # 1. Presence-only selectors
    for sel in OUT_OF_STOCK_PRESENCE_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el:
                logger.debug("inventory.oos_presence_selector", selector=sel)
                return True
        except Exception:
            pass

    # 2. Text-bearing selectors
    for sel in OUT_OF_STOCK_TEXT_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el:
                text = (await el.inner_text()).lower().strip()
                for kw in _OOS_KEYWORDS:
                    if kw in text:
                        logger.debug(
                            "inventory.oos_text_match",
                            selector=sel,
                            keyword=kw,
                            text=text[:80],
                        )
                        return True
        except Exception:
            pass

    # 3. Broad body text scan (last resort)
    try:
        body_text = (await page.inner_text("body")).lower()
        for kw in _OOS_KEYWORDS:
            if kw in body_text:
                logger.debug("inventory.oos_body_match", keyword=kw)
                return True
    except Exception:
        pass

    return False


async def _extract_price(page: Any) -> float | None:
    """
    Extract the current product price from the page.

    Tries ``content`` attribute first (structured data), then inner text.
    """
    for sel in PRICE_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if not el:
                continue

            # Try itemprop="price" content attribute first
            content_val = await el.get_attribute("content")
            if content_val:
                price = _normalise_price(content_val)
                if price is not None:
                    return price

            # Fall back to visible text
            text = await el.inner_text()
            price = _normalise_price(text)
            if price is not None:
                return price
        except Exception:
            pass

    return None


# ── Public API ────────────────────────────────────────────────────────────────

async def fetch_inventory(
    product_url: str,
    *,
    page: Any = None,
) -> dict[str, Any]:
    """
    Fetch stock status and current price for a single product URL.

    Parameters
    ----------
    product_url : str
        Full URL of the supplier product page.
    page        : playwright Page | None
        Inject a pre-configured Playwright page (for testing).
        When None, a new browser context is launched.

    Returns
    -------
    dict with keys:
        in_stock : bool   — True if product is available
        price    : float | None — current price (None if undetectable)
    """
    log = logger.bind(product_url=product_url)

    _own_browser = page is None  # we need to manage the browser lifecycle

    browser = None
    context = None

    try:
        if _own_browser:
            try:
                from playwright.async_api import async_playwright  # type: ignore[import]
            except ImportError:
                raise RuntimeError(
                    "Playwright is not installed. "
                    "Run: pip install playwright && playwright install chromium"
                )

            _pw_ctx = async_playwright()
            pw = await _pw_ctx.__aenter__()
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

        log.info("inventory.fetch_start")
        await page.goto(product_url, timeout=_PAGE_TIMEOUT, wait_until="domcontentloaded")

        in_stock = not await _detect_out_of_stock(page)
        price    = await _extract_price(page)

        result = {"in_stock": in_stock, "price": price}
        log.info(
            "inventory.fetch_done",
            in_stock=in_stock,
            price=price,
        )
        return result

    except Exception as exc:
        log.error("inventory.fetch_error", error=str(exc))
        # Return conservative "in_stock=True, price=None" on error so we don't
        # accidentally zero-out inventory due to transient network failures.
        return {"in_stock": True, "price": None}

    finally:
        if _own_browser:
            try:
                if context:
                    await context.close()
                if browser:
                    await browser.close()
                # Close the playwright manager if we opened it
                try:
                    await _pw_ctx.__aexit__(None, None, None)  # type: ignore[possibly-undefined]
                except Exception:
                    pass
            except Exception:
                pass
