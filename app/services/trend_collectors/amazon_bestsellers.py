from __future__ import annotations

"""
app/services/trend_collectors/amazon_bestsellers.py
──────────────────────────────────────────────────────
Sprint 15 – Amazon Bestsellers trend signal collector.

Production intent
-----------------
Would call the Amazon Product Advertising API (PA-API 5.0) or scrape the
Amazon Best Sellers pages for Beauty / K-Beauty categories.
Because we have no live credentials the function returns a **deterministic mock
dataset** that matches the real response schema.

Return format (list of dicts)
-----------------------------
Each dict:
    source       : str  = 'amazon_bestsellers'
    external_id  : str  – ASIN
    name         : str  – product title
    brand        : str | None
    category     : str | None
    trend_score  : float  – 0.0–10.0 (rank-based: rank 1 → ~9.5, rank 100 → ~1.0)
    raw_data_json: str    – JSON-encoded raw payload

Usage
-----
    from app.services.trend_collectors.amazon_bestsellers import collect_amazon_bestsellers
    signals = collect_amazon_bestsellers()
    # → list[dict]
"""

import json
from typing import Any

# ---------------------------------------------------------------------------
# Mock dataset – deterministic so tests are stable
# Each entry reflects a real-world Amazon Beauty bestseller (for realism).
# ---------------------------------------------------------------------------

_MOCK_AMAZON_BESTSELLERS: list[dict[str, Any]] = [
    {
        "asin":       "B00PBD9L2A",
        "rank":       1,
        "product_name": "COSRX Advanced Snail 96 Mucin Power Essence 100ml",
        "brand":      "COSRX",
        "category":   "Essence",
        "rating":     4.5,
        "reviews":    89_432,
        "price_usd":  25.99,
    },
    {
        "asin":       "B07BFSZD6D",
        "rank":       2,
        "product_name": "Laneige Lip Sleeping Mask Berry 20g",
        "brand":      "Laneige",
        "category":   "Lip Care",
        "rating":     4.6,
        "reviews":    71_200,
        "price_usd":  24.00,
    },
    {
        "asin":       "B08BNMB6VF",
        "rank":       3,
        "product_name": "Beauty of Joseon Glow Deep Serum Rice + Arbutin 30ml",
        "brand":      "Beauty of Joseon",
        "category":   "Serum",
        "rating":     4.4,
        "reviews":    54_100,
        "price_usd":  18.00,
    },
    {
        "asin":       "B07D5ZG3J3",
        "rank":       4,
        "product_name": "Some By Mi AHA BHA PHA 30 Days Miracle Toner 150ml",
        "brand":      "Some By Mi",
        "category":   "Toner",
        "rating":     4.3,
        "reviews":    42_800,
        "price_usd":  19.60,
    },
    {
        "asin":       "B01N2VNUPB",
        "rank":       5,
        "product_name": "Klairs Supple Preparation Facial Toner 180ml",
        "brand":      "Klairs",
        "category":   "Toner",
        "rating":     4.4,
        "reviews":    38_500,
        "price_usd":  22.00,
    },
    {
        "asin":       "B078X3Y5JQ",
        "rank":       6,
        "product_name": "Anua Heartleaf Pore Control Cleansing Oil 200ml",
        "brand":      "Anua",
        "category":   "Cleansing",
        "rating":     4.5,
        "reviews":    31_200,
        "price_usd":  21.00,
    },
    {
        "asin":       "B09X4K8B2L",
        "rank":       7,
        "product_name": "Torriden DIVE-IN Low Molecule Hyaluronic Acid Serum 50ml",
        "brand":      "Torriden",
        "category":   "Serum",
        "rating":     4.3,
        "reviews":    27_400,
        "price_usd":  17.50,
    },
    {
        "asin":       "B09C1FWRYS",
        "rank":       8,
        "product_name": "Dr. Jart+ Cicapair Tiger Grass Cream 50ml",
        "brand":      "Dr. Jart+",
        "category":   "Moisturizer",
        "rating":     4.2,
        "reviews":    24_100,
        "price_usd":  52.00,
    },
    {
        "asin":       "B01N5OHEQ2",
        "rank":       9,
        "product_name": "Etude House Soon Jung 2x Barrier Intensive Cream 60ml",
        "brand":      "Etude House",
        "category":   "Moisturizer",
        "rating":     4.4,
        "reviews":    21_800,
        "price_usd":  23.00,
    },
    {
        "asin":       "B07G99ZJ91",
        "rank":       10,
        "product_name": "Innisfree Green Tea Seed Serum 80ml",
        "brand":      "Innisfree",
        "category":   "Serum",
        "rating":     4.3,
        "reviews":    19_600,
        "price_usd":  28.00,
    },
    {
        "asin":       "B08XCG77ZW",
        "rank":       11,
        "product_name": "Round Lab Birch Juice Moisturizing Toner 300ml",
        "brand":      "Round Lab",
        "category":   "Toner",
        "rating":     4.4,
        "reviews":    17_200,
        "price_usd":  16.00,
    },
    {
        "asin":       "B094ZPN8G8",
        "rank":       12,
        "product_name": "Peach & Lily Glass Skin Serum 39ml",
        "brand":      "Peach & Lily",
        "category":   "Serum",
        "rating":     4.2,
        "reviews":    15_800,
        "price_usd":  39.00,
    },
    {
        "asin":       "B00T84NPHU",
        "rank":       13,
        "product_name": "Missha Time Revolution Night Repair Serum 50ml",
        "brand":      "Missha",
        "category":   "Serum",
        "rating":     4.1,
        "reviews":    13_500,
        "price_usd":  42.00,
    },
    {
        "asin":       "B07X5YGHXB",
        "rank":       14,
        "product_name": "Sulwhasoo First Care Activating Serum EX 60ml",
        "brand":      "Sulwhasoo",
        "category":   "Serum",
        "rating":     4.5,
        "reviews":    11_900,
        "price_usd":  82.00,
    },
    {
        "asin":       "B09R6KV1X3",
        "rank":       15,
        "product_name": "IOPE Air Cushion SPF50+ PA+++ 15g×2",
        "brand":      "IOPE",
        "category":   "Cushion Foundation",
        "rating":     4.3,
        "reviews":    10_400,
        "price_usd":  45.00,
    },
]

# ---------------------------------------------------------------------------
# Score normalisation helpers
# ---------------------------------------------------------------------------

def _rank_to_score(rank: int, max_rank: int = 100) -> float:
    """
    Convert a bestseller rank (1 = best) to a 0.0–10.0 trend score.

    Formula:   score = 10 * (1 - (rank - 1) / max_rank)
    Rank 1   → 10.0  (clipped to 9.9 to leave headroom)
    Rank 100 → 0.0
    """
    score = 10.0 * (1.0 - (rank - 1) / max_rank)
    return round(min(max(score, 0.0), 9.9), 4)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_amazon_bestsellers() -> list[dict[str, Any]]:
    """
    Collect K-beauty bestseller trend signals from Amazon.

    Returns a list of normalised trend signal dicts compatible with
    ``TrendProduct`` ORM / ``upsert_trend_product`` service function.

    In production this would call the Amazon PA-API 5.0 or scrape the
    Amazon Beauty Best Sellers page.  Currently returns a deterministic
    mock dataset.
    """
    signals: list[dict[str, Any]] = []

    for item in _MOCK_AMAZON_BESTSELLERS:
        score = _rank_to_score(item["rank"])
        raw   = json.dumps({
            "asin":       item["asin"],
            "rank":       item["rank"],
            "rating":     item["rating"],
            "reviews":    item["reviews"],
            "price_usd":  item["price_usd"],
        })
        signals.append({
            "source":        "amazon_bestsellers",
            "external_id":   item["asin"],
            "name":          item["product_name"],
            "brand":         item["brand"],
            "category":      item["category"],
            "trend_score":   score,
            "raw_data_json": raw,
        })

    # Sort descending by score (deterministic)
    signals.sort(key=lambda x: x["trend_score"], reverse=True)
    return signals
