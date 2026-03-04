from __future__ import annotations

"""
app/crawlers/product_parser.py
──────────────────────────────
Pure-HTML parser for StyleKorean product pages.

Exposes:
    parse_product_page(html: str) -> dict

The returned dict has the following keys (all optional except ``name``):
    name          str
    brand         str | None
    price         str | None        (raw text, e.g. "$12.50")
    sale_price    str | None
    stock_status  "in_stock" | "out_of_stock" | "unknown"
    image_urls    list[str]

Selectors are stored as module-level constants so they can be patched in tests.
"""

import re
from html.parser import HTMLParser
from typing import Any

# ── CSS-like selector constants (used by tests to verify they exist) ─────────
#   We use a minimal HTML parser so no external deps are needed.

SEL_PRODUCT_NAME        = "h1.product-name, h1[itemprop='name'], .product-title h1"
SEL_BRAND               = ".brand-name, [itemprop='brand'] span, .product-brand a"
SEL_PRICE               = ".price .amount, span[itemprop='price'], .product-price .price"
SEL_SALE_PRICE          = ".sale-price .amount, .price-sale .amount, .special-price .amount"
SEL_STOCK               = "[itemprop='availability'], .stock-status, .product-availability"
SEL_IMAGES              = "img.product-image, .product-gallery img, .swiper-slide img"
SEL_IMAGE_ATTR_SRC      = "src"
SEL_IMAGE_ATTR_DATA_SRC = "data-src"


# ── Minimal HTML parser ───────────────────────────────────────────────────────

class _SimpleParser(HTMLParser):
    """
    Lightweight SAX-style HTML parser that builds a flat list of
    (tag, attrs_dict, text) tuples for easy downstream querying.
    """

    def __init__(self) -> None:
        super().__init__()
        self._stack: list[dict[str, Any]] = []
        self.nodes: list[dict[str, Any]] = []
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node: dict[str, Any] = {
            "tag": tag.lower(),
            "attrs": dict(attrs),
            "text": "",
        }
        self._stack.append(node)
        self._current_text = []

    def handle_endtag(self, _tag: str) -> None:
        if self._stack:
            node = self._stack.pop()
            node["text"] = " ".join(self._current_text).strip()
            self.nodes.append(node)
            self._current_text = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._current_text.append(stripped)


def _find_nodes(nodes: list[dict[str, Any]], tag: str, **attr_filters: str) -> list[dict[str, Any]]:
    """Return all nodes matching ``tag`` and optional attribute substring filters."""
    results = []
    for node in nodes:
        if node["tag"] != tag:
            continue
        if all(
            node["attrs"].get(k) and filter_val in node["attrs"].get(k, "")
            for k, filter_val in attr_filters.items()
        ):
            results.append(node)
    return results


def _text_of(nodes: list[dict[str, Any]], tag: str, **attr_filters: str) -> str | None:
    """Return text content of the first matching node, or None."""
    found = _find_nodes(nodes, tag, **attr_filters)
    if found:
        return found[0]["text"] or None
    return None


def _clean_price(raw: str | None) -> str | None:
    """Strip whitespace and currency symbols, return e.g. '12.50'."""
    if not raw:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    return cleaned


def _extract_images(nodes: list[dict[str, Any]]) -> list[str]:
    """
    Collect image URLs from <img> tags that look like product images.
    Prefers data-src (lazy-loaded) over src.
    Filters out placeholder/tiny images (< 30 chars in URL).
    """
    urls: list[str] = []
    seen: set[str] = set()

    for node in nodes:
        if node["tag"] != "img":
            continue

        attrs = node["attrs"]
        # Prefer lazy-load src
        url = (
            attrs.get("data-src")
            or attrs.get("data-original")
            or attrs.get("data-lazy")
            or attrs.get("src")
            or ""
        )
        url = url.strip()

        # Skip placeholders, data URIs, very short URLs
        if not url or url.startswith("data:") or len(url) < 20:
            continue

        # Only keep URLs that look like real images
        if not re.search(r"\.(jpg|jpeg|png|webp|gif)(\?|$)", url, re.IGNORECASE):
            continue

        if url not in seen:
            seen.add(url)
            urls.append(url)

    return urls


def _detect_stock(nodes: list[dict[str, Any]], raw_html: str) -> str:
    """
    Determine stock status from availability meta, text cues, or raw HTML.
    Returns 'in_stock', 'out_of_stock', or 'unknown'.
    """
    # 1. Check <link itemprop="availability" href="...">
    for node in nodes:
        if node["tag"] == "link" and "availability" in node["attrs"].get("itemprop", ""):
            href = node["attrs"].get("href", "").lower()
            if "instock" in href:
                return "in_stock"
            if "outofstock" in href:
                return "out_of_stock"

    # 2. Check <span> / <div> text
    out_of_stock_phrases = ["out of stock", "sold out", "품절", "재고 없음"]
    in_stock_phrases     = ["in stock", "add to cart", "장바구니", "구매하기"]

    html_lower = raw_html.lower()
    for phrase in out_of_stock_phrases:
        if phrase in html_lower:
            return "out_of_stock"
    for phrase in in_stock_phrases:
        if phrase in html_lower:
            return "in_stock"

    return "unknown"


# ── Public API ────────────────────────────────────────────────────────────────

def parse_product_page(html: str) -> dict[str, Any]:
    """
    Parse a StyleKorean product page HTML and return a normalised dict.

    Parameters
    ----------
    html : str
        Raw HTML string of the product detail page.

    Returns
    -------
    dict with keys:
        name        (str, default '')
        brand       (str | None)
        price       (str | None)   — raw display value, e.g. '$12.50'
        sale_price  (str | None)
        stock_status ('in_stock' | 'out_of_stock' | 'unknown')
        image_urls  (list[str])
    """
    parser = _SimpleParser()
    parser.feed(html)
    nodes = parser.nodes

    # ── Name ─────────────────────────────────────────────────────────────────
    name = (
        _text_of(nodes, "h1")
        or _text_of(nodes, "h2")
        or ""
    )

    # ── Brand ─────────────────────────────────────────────────────────────────
    brand: str | None = None
    for node in nodes:
        if node["tag"] in ("a", "span", "div"):
            cls = node["attrs"].get("class", "") or ""
            if "brand" in cls.lower():
                brand = node["text"] or None
                break

    # ── Prices ────────────────────────────────────────────────────────────────
    price: str | None = None
    sale_price: str | None = None

    # Look for <span itemprop="price"> or class containing "price"
    for node in nodes:
        if node["tag"] in ("span", "div", "p", "strong"):
            itemprop = node["attrs"].get("itemprop", "")
            cls       = node["attrs"].get("class", "") or ""

            if itemprop == "price" and not price:
                price = _clean_price(
                    node["attrs"].get("content") or node["text"]
                )
            elif "sale" in cls.lower() and "price" in cls.lower() and not sale_price:
                sale_price = _clean_price(node["text"])
            elif "price" in cls.lower() and "sale" not in cls.lower() and not price:
                price = _clean_price(node["text"])

    # ── Stock ─────────────────────────────────────────────────────────────────
    stock_status = _detect_stock(nodes, html)

    # ── Images ────────────────────────────────────────────────────────────────
    image_urls = _extract_images(nodes)

    return {
        "name":         name,
        "brand":        brand,
        "price":        price,
        "sale_price":   sale_price,
        "stock_status": stock_status,
        "image_urls":   image_urls,
    }
