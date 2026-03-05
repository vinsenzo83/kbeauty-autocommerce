from __future__ import annotations

"""
app/services/trend_collectors_v2/tiktok_mentions_collector.py
───────────────────────────────────────────────────────────────
Sprint 18 – TikTok product mention signal collector (mock-first).

Approach: Mention-based (NOT hashtag-only).
Each document contains text fields (caption, comments) that are scanned
for product mention phrases using the mention_dictionary.

Modes
-----
- Default  : Return deterministic mock data from fixtures/trends/tiktok_mentions_mock.json
- Live     : If TREND_NETWORK_ENABLED=1, attempt real fetch (not implemented; falls back to mock)

Public API
----------
collect_tiktok_mentions_v2(limit=50) -> list[dict]
  Returns list of "documents" (caption + comments) for compute_mention_signals().
"""

import json
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_NETWORK_ENABLED = os.getenv("TREND_NETWORK_ENABLED", "0") == "1"

_FIXTURE_PATH = (
    Path(__file__).parent.parent.parent.parent  # project root
    / "fixtures" / "trends" / "tiktok_mentions_mock.json"
)


def _load_mock_data() -> list[dict[str, Any]]:
    """Load deterministic mock TikTok mention documents from fixture file."""
    try:
        with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.debug("tiktok_collector_v2.mock_loaded", count=len(data))
        return data
    except FileNotFoundError:
        logger.warning("tiktok_collector_v2.fixture_not_found", path=str(_FIXTURE_PATH))
        return _fallback_mock()
    except Exception as exc:
        logger.error("tiktok_collector_v2.fixture_error", error=str(exc))
        return _fallback_mock()


def _fallback_mock() -> list[dict[str, Any]]:
    """Inline fallback mock if fixture file is missing."""
    return [
        {
            "external_id": "tt_001",
            "caption": (
                "My skincare routine includes COSRX snail mucin essence and "
                "Beauty of Joseon relief sun. Holy grails! #kbeauty #skincare"
            ),
            "comments": [
                "I love cosrx snail mucin too!",
                "beauty of joseon is amazing for sensitive skin",
            ],
            "views": 892000,
            "likes": 45300,
        },
        {
            "external_id": "tt_002",
            "caption": (
                "Laneige lip sleeping mask review - literally the best lip mask ever. "
                "Also using COSRX low pH cleanser every morning. #laneige #cosrx"
            ),
            "comments": [
                "laneige lip mask is so worth it",
                "cosrx cleanser changed my skin",
            ],
            "views": 1240000,
            "likes": 89700,
        },
        {
            "external_id": "tt_003",
            "caption": (
                "torriden dive in serum + Beauty of Joseon glow serum propolis combo "
                "made my skin glass-like ✨ #skintok #kbeauty"
            ),
            "comments": [
                "torriden serum is so hydrating",
                "beauty of joseon glow serum works so well",
            ],
            "views": 328000,
            "likes": 15400,
        },
    ]


async def collect_tiktok_mentions_v2(
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Collect TikTok mention documents (caption + comments).

    Each document contains text fields that will be passed to
    compute_mention_signals() for phrase extraction and scoring.

    Parameters
    ----------
    limit : Maximum number of documents to return

    Returns
    -------
    List of document dicts, each with:
    - external_id : str  (TikTok video ID)
    - caption     : str  (video caption text)
    - comments    : list[str]  (selected comments)
    - views       : int
    - likes       : int
    """
    if _NETWORK_ENABLED:
        # Real TikTok API fetch — not implemented in Sprint 18
        # Falls back to mock silently
        logger.info("tiktok_collector_v2.network_mode_fallback_to_mock")

    data = _load_mock_data()
    result = data[:limit]

    logger.info("tiktok_collector_v2.collected", count=len(result), mode="mock")
    return result


# ── Public alias (Sprint 18 standardised interface) ───────────────────────────
async def fetch(limit: int = 200) -> list[dict]:
    """Alias for collect_tiktok_mentions_v2, for uniform fetch() interface."""
    return await collect_tiktok_mentions_v2(limit=limit)
