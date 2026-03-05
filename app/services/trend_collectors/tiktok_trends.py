from __future__ import annotations

"""
app/services/trend_collectors/tiktok_trends.py
───────────────────────────────────────────────
Sprint 15 – TikTok trend signal collector.

Production intent
-----------------
Would call TikTok Creator Marketplace / TikTok Shop API to retrieve
trending products and hashtag analytics.  Because we have no live credentials
in this environment the function returns a **deterministic mock dataset**
that matches the real response schema.

Return format (list of dicts)
-----------------------------
Each dict:
    source       : str  = 'tiktok'
    external_id  : str  – unique TikTok video/product id
    name         : str  – product title
    brand        : str | None
    category     : str | None
    trend_score  : float  – 0.0–10.0 (likes × share_rate normalised)
    raw_data_json: str    – JSON-encoded raw payload

Usage
-----
    from app.services.trend_collectors.tiktok_trends import collect_tiktok_trends
    signals = collect_tiktok_trends()
    # → list[dict]
"""

import json
from typing import Any

# ---------------------------------------------------------------------------
# Mock dataset – deterministic so tests are stable
# ---------------------------------------------------------------------------

_MOCK_TIKTOK_TRENDS: list[dict[str, Any]] = [
    {
        "video_id":       "tiktok-7001234567890",
        "product_name":   "COSRX Advanced Snail 96 Mucin Power Essence 100ml",
        "brand":          "COSRX",
        "category":       "Essence",
        "like_count":     1_850_000,
        "share_count":    420_000,
        "comment_count":  38_000,
        "hashtags":       ["#skincare", "#snailmucin", "#kbeauty"],
    },
    {
        "video_id":       "tiktok-7001234567891",
        "product_name":   "Some By Mi AHA BHA PHA 30 Days Miracle Toner 150ml",
        "brand":          "Some By Mi",
        "category":       "Toner",
        "like_count":     1_420_000,
        "share_count":    310_000,
        "comment_count":  27_000,
        "hashtags":       ["#toner", "#acne", "#kbeauty"],
    },
    {
        "video_id":       "tiktok-7001234567892",
        "product_name":   "Laneige Lip Sleeping Mask Berry 20g",
        "brand":          "Laneige",
        "category":       "Lip Care",
        "like_count":     2_100_000,
        "share_count":    580_000,
        "comment_count":  61_000,
        "hashtags":       ["#lipmask", "#laneige", "#kbeauty"],
    },
    {
        "video_id":       "tiktok-7001234567893",
        "product_name":   "Dr. Jart+ Cicapair Tiger Grass Cream 50ml",
        "brand":          "Dr. Jart+",
        "category":       "Moisturizer",
        "like_count":     980_000,
        "share_count":    190_000,
        "comment_count":  15_500,
        "hashtags":       ["#cicapair", "#rednessrelief", "#kbeauty"],
    },
    {
        "video_id":       "tiktok-7001234567894",
        "product_name":   "Innisfree Green Tea Seed Serum 80ml",
        "brand":          "Innisfree",
        "category":       "Serum",
        "like_count":     760_000,
        "share_count":    145_000,
        "comment_count":  12_300,
        "hashtags":       ["#greentea", "#serum", "#innisfree"],
    },
    {
        "video_id":       "tiktok-7001234567895",
        "product_name":   "Klairs Supple Preparation Facial Toner 180ml",
        "brand":          "Klairs",
        "category":       "Toner",
        "like_count":     530_000,
        "share_count":    98_000,
        "comment_count":  8_700,
        "hashtags":       ["#klairs", "#toner", "#hydration"],
    },
    {
        "video_id":       "tiktok-7001234567896",
        "product_name":   "Missha Time Revolution Night Repair Serum 50ml",
        "brand":          "Missha",
        "category":       "Serum",
        "like_count":     640_000,
        "share_count":    112_000,
        "comment_count":  9_800,
        "hashtags":       ["#missha", "#antiaging", "#serum"],
    },
    {
        "video_id":       "tiktok-7001234567897",
        "product_name":   "Etude House Soon Jung 2x Barrier Intensive Cream 60ml",
        "brand":          "Etude House",
        "category":       "Moisturizer",
        "like_count":     410_000,
        "share_count":    74_000,
        "comment_count":  6_200,
        "hashtags":       ["#soonjung", "#sensitive", "#moisturizer"],
    },
    {
        "video_id":       "tiktok-7001234567898",
        "product_name":   "Beauty of Joseon Glow Deep Serum Rice + Arbutin 30ml",
        "brand":          "Beauty of Joseon",
        "category":       "Serum",
        "like_count":     1_200_000,
        "share_count":    265_000,
        "comment_count":  22_100,
        "hashtags":       ["#beautyofjoseon", "#brightening", "#serum"],
    },
    {
        "video_id":       "tiktok-7001234567899",
        "product_name":   "Torriden DIVE-IN Low Molecule Hyaluronic Acid Serum 50ml",
        "brand":          "Torriden",
        "category":       "Serum",
        "like_count":     890_000,
        "share_count":    178_000,
        "comment_count":  14_600,
        "hashtags":       ["#hyaluronicacid", "#torriden", "#hydrating"],
    },
    {
        "video_id":       "tiktok-7001234567900",
        "product_name":   "Sulwhasoo First Care Activating Serum EX 60ml",
        "brand":          "Sulwhasoo",
        "category":       "Serum",
        "like_count":     560_000,
        "share_count":    89_000,
        "comment_count":  7_500,
        "hashtags":       ["#sulwhasoo", "#luxury", "#kbeauty"],
    },
    {
        "video_id":       "tiktok-7001234567901",
        "product_name":   "IOPE Air Cushion SPF50+ PA+++ 15g×2",
        "brand":          "IOPE",
        "category":       "Cushion Foundation",
        "like_count":     720_000,
        "share_count":    130_000,
        "comment_count":  11_200,
        "hashtags":       ["#cushion", "#SPF50", "#makeup"],
    },
    {
        "video_id":       "tiktok-7001234567902",
        "product_name":   "Peach & Lily Glass Skin Serum 39ml",
        "brand":          "Peach & Lily",
        "category":       "Serum",
        "like_count":     1_050_000,
        "share_count":    220_000,
        "comment_count":  18_400,
        "hashtags":       ["#glassskin", "#peachandlily", "#serum"],
    },
    {
        "video_id":       "tiktok-7001234567903",
        "product_name":   "Anua Heartleaf Pore Control Cleansing Oil 200ml",
        "brand":          "Anua",
        "category":       "Cleansing",
        "like_count":     830_000,
        "share_count":    162_000,
        "comment_count":  13_100,
        "hashtags":       ["#doubleCleans", "#anua", "#pores"],
    },
    {
        "video_id":       "tiktok-7001234567904",
        "product_name":   "Round Lab Birch Juice Moisturizing Toner 300ml",
        "brand":          "Round Lab",
        "category":       "Toner",
        "like_count":     490_000,
        "share_count":    85_000,
        "comment_count":  7_100,
        "hashtags":       ["#roundlab", "#birch", "#moisturizing"],
    },
]

# ---------------------------------------------------------------------------
# Score normalisation helpers
# ---------------------------------------------------------------------------

def _normalise_score(like_count: int, share_count: int) -> float:
    """
    Convert raw engagement numbers to a 0.0–10.0 trend score.

    Formula (simplified TikTok virality proxy):
        raw = log10(like_count + 1) * 0.6 + log10(share_count + 1) * 0.4
    Clipped to [0, 10].
    """
    import math
    raw = math.log10(like_count + 1) * 0.6 + math.log10(share_count + 1) * 0.4
    # The raw range for our dataset is roughly 2–8; normalise to 0–10
    # Max theoretical: log10(10_000_000)*0.6 + log10(2_000_000)*0.4 ≈ 9.3
    return min(round(raw, 4), 10.0)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_tiktok_trends() -> list[dict[str, Any]]:
    """
    Collect trending K-beauty product signals from TikTok.

    Returns a list of normalised trend signal dicts compatible with
    ``TrendProduct`` ORM / ``upsert_trend_product`` service function.

    In production this would call the TikTok Creator Marketplace API.
    Currently returns a deterministic mock dataset.
    """
    signals: list[dict[str, Any]] = []

    for item in _MOCK_TIKTOK_TRENDS:
        score = _normalise_score(item["like_count"], item["share_count"])
        raw   = json.dumps({
            "video_id":     item["video_id"],
            "like_count":   item["like_count"],
            "share_count":  item["share_count"],
            "comment_count":item["comment_count"],
            "hashtags":     item["hashtags"],
        })
        signals.append({
            "source":        "tiktok",
            "external_id":   item["video_id"],
            "name":          item["product_name"],
            "brand":         item["brand"],
            "category":      item["category"],
            "trend_score":   score,
            "raw_data_json": raw,
        })

    # Sort descending by score (deterministic)
    signals.sort(key=lambda x: x["trend_score"], reverse=True)
    return signals
