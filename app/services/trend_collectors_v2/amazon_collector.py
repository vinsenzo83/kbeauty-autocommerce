from __future__ import annotations

"""
app/services/trend_collectors_v2/amazon_collector.py
──────────────────────────────────────────────────────
Sprint 18 – Amazon bestseller trend collector (mock-first).

Modes
-----
- Default  : Return deterministic mock data from fixtures/trends/amazon_bestsellers_mock.json
- Live     : If TREND_NETWORK_ENABLED=1, attempt real fetch (not implemented; falls back to mock)

Public API
----------
collect_amazon_bestsellers_v2() -> list[dict]
"""

import json
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_NETWORK_ENABLED = os.getenv("TREND_NETWORK_ENABLED", "0") == "1"

# Fixture path (relative to project root)
_FIXTURE_PATH = (
    Path(__file__).parent.parent.parent.parent  # project root
    / "fixtures" / "trends" / "amazon_bestsellers_mock.json"
)


def _load_mock_data() -> list[dict[str, Any]]:
    """Load deterministic mock Amazon bestseller data from fixture file."""
    try:
        with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.debug("amazon_collector_v2.mock_loaded", count=len(data))
        return data
    except FileNotFoundError:
        logger.warning("amazon_collector_v2.fixture_not_found", path=str(_FIXTURE_PATH))
        return _fallback_mock()
    except Exception as exc:
        logger.error("amazon_collector_v2.fixture_error", error=str(exc))
        return _fallback_mock()


def _fallback_mock() -> list[dict[str, Any]]:
    """Inline fallback mock if fixture file is missing."""
    return [
        {
            "external_id":   "B08JXXB1HN",
            "title":         "COSRX Advanced Snail 96 Mucin Power Essence",
            "brand":         "COSRX",
            "rank":          1,
            "price":         24.99,
            "currency":      "USD",
            "rating":        4.7,
            "review_count":  48320,
            "category":      "K-Beauty Essence",
        },
        {
            "external_id":   "B07QG83JGQ",
            "title":         "Beauty of Joseon Relief Sun Rice + Probiotics SPF50",
            "brand":         "Beauty of Joseon",
            "rank":          2,
            "price":         18.00,
            "currency":      "USD",
            "rating":        4.8,
            "review_count":  32150,
            "category":      "K-Beauty Sunscreen",
        },
        {
            "external_id":   "B09M5FVXHD",
            "title":         "SOME BY MI AHA BHA PHA 30 Days Miracle Toner",
            "brand":         "SOME BY MI",
            "rank":          3,
            "price":         14.99,
            "currency":      "USD",
            "rating":        4.5,
            "review_count":  21800,
            "category":      "K-Beauty Toner",
        },
    ]


async def collect_amazon_bestsellers_v2(
    category: str = "K-Beauty",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Collect Amazon bestseller trend signals.

    Parameters
    ----------
    category : Product category to fetch (default 'K-Beauty')
    limit    : Maximum number of items to return

    Returns
    -------
    List of dicts compatible with insert_trend_items()
    """
    if _NETWORK_ENABLED:
        # Real network fetch — not implemented in Sprint 18
        # Falls back to mock silently
        logger.info("amazon_collector_v2.network_mode_fallback_to_mock")

    data = _load_mock_data()
    result = data[:limit]

    logger.info("amazon_collector_v2.collected", count=len(result), mode="mock")
    return result


# ── Public alias (Sprint 18 standardised interface) ───────────────────────────
async def fetch(limit: int = 200, category: str = "K-Beauty") -> list[dict]:
    """Alias for collect_amazon_bestsellers_v2, for uniform fetch() interface."""
    return await collect_amazon_bestsellers_v2(limit=limit, category=category)
