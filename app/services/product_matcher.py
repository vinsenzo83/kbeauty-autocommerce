from __future__ import annotations

"""
app/services/product_matcher.py
────────────────────────────────
Sprint 15 – Fuzzy matching between trend signals and canonical products.

Public API
----------
    canonical_id = await match_trend_to_canonical(session, trend_product_dict)
    # Returns UUID | None

Algorithm (multi-pass, highest-confidence first)
-------------------------------------------------
Pass 1 – Exact canonical_sku match after slug normalisation
Pass 2 – Exact brand + normalised name match (SQL)
Pass 3 – Fuzzy token overlap:
            score = token_overlap_ratio(trend_name_tokens, canonical_name_tokens)
            threshold ≥ 0.55 → accept
Pass 4 – Brand-only fallback with first-name-token match
Pass 5 – No match → return None

Name normalisation
------------------
- lower-case
- strip punctuation and filler words (ml, oz, g, pack, set, ...)
- tokenise on whitespace

Dependencies
------------
Pure Python only – no external NLP libraries required.
"""

import re
import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_product import CanonicalProduct

logger = structlog.get_logger(__name__)

# ── Stop-words / noise tokens to ignore in fuzzy matching ─────────────────────
_STOP_TOKENS: frozenset[str] = frozenset({
    "ml", "oz", "g", "kg", "l", "fl", "pack", "set", "kit", "duo",
    "the", "a", "an", "for", "with", "and", "of", "in", "to",
    "new", "original", "official", "limited", "edition", "value",
    "skincare", "beauty", "kbeauty", "korean", "cream",
})

_PUNCT_RE = re.compile(r"[^a-z0-9\s]")
_SPACE_RE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Lower-case, remove punctuation, collapse whitespace."""
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text


def _tokenise(text: str) -> list[str]:
    """Normalise then split; filter stop-tokens and very short tokens."""
    tokens = _normalise(text).split()
    return [t for t in tokens if t not in _STOP_TOKENS and len(t) > 1]


def _to_slug(text: str) -> str:
    """Convert text to a canonical-sku-like slug."""
    text = _normalise(text)
    return _SPACE_RE.sub("-", text).strip("-")


def _token_overlap_ratio(a_tokens: list[str], b_tokens: list[str]) -> float:
    """
    Jaccard-like overlap ratio:
        |intersection| / |union|
    """
    if not a_tokens or not b_tokens:
        return 0.0
    a_set = set(a_tokens)
    b_set = set(b_tokens)
    intersection = len(a_set & b_set)
    union = len(a_set | b_set)
    return intersection / union if union else 0.0


# ── Match result dataclass (for logging) ─────────────────────────────────────

class MatchResult:
    __slots__ = ("canonical_id", "method", "score")

    def __init__(
        self,
        canonical_id: uuid.UUID | None,
        method: str,
        score: float,
    ) -> None:
        self.canonical_id = canonical_id
        self.method       = method
        self.score        = score

    def __bool__(self) -> bool:
        return self.canonical_id is not None


# ── Core matcher ──────────────────────────────────────────────────────────────

async def match_trend_to_canonical(
    session: AsyncSession,
    trend: dict[str, Any],
    fuzzy_threshold: float = 0.55,
) -> uuid.UUID | None:
    """
    Try to map a trend signal dict to an existing CanonicalProduct.

    Parameters
    ----------
    session         : Async SQLAlchemy session
    trend           : Dict with at least 'name'; optionally 'brand', 'external_id'
    fuzzy_threshold : Minimum token-overlap ratio to accept a fuzzy match (0–1)

    Returns
    -------
    UUID of the matching CanonicalProduct, or None if no match found.
    """

    trend_name  = trend.get("name", "") or ""
    trend_brand = (trend.get("brand") or "").lower().strip()

    if not trend_name:
        return None

    trend_tokens = _tokenise(trend_name)

    # ── Pass 1: exact slug match against canonical_sku ─────────────────────
    slug = _to_slug(trend_name)
    row = (await session.execute(
        select(CanonicalProduct).where(CanonicalProduct.canonical_sku == slug).limit(1)
    )).scalar_one_or_none()
    if row is not None:
        logger.debug("product_matcher.pass1_slug", canonical_id=str(row.id))
        return row.id  # type: ignore[return-value]

    # ── Pass 2: exact brand + normalised name match ────────────────────────
    if trend_brand:
        norm_name = _normalise(trend_name)
        rows = (await session.execute(
            select(CanonicalProduct).where(
                CanonicalProduct.brand.ilike(trend_brand)
            ).limit(100)
        )).scalars().all()
        for r in rows:
            if _normalise(r.name) == norm_name:
                logger.debug("product_matcher.pass2_exact", canonical_id=str(r.id))
                return r.id  # type: ignore[return-value]

    # ── Pass 3: fuzzy token overlap (all products, or brand-filtered) ──────
    if trend_brand:
        candidates = (await session.execute(
            select(CanonicalProduct).where(
                CanonicalProduct.brand.ilike(trend_brand)
            ).limit(200)
        )).scalars().all()
    else:
        candidates = (await session.execute(
            select(CanonicalProduct).limit(500)
        )).scalars().all()

    best_id:    uuid.UUID | None = None
    best_score: float            = 0.0

    for cp in candidates:
        cp_tokens = _tokenise(cp.name)
        score = _token_overlap_ratio(trend_tokens, cp_tokens)
        if score > best_score:
            best_score = score
            best_id    = cp.id  # type: ignore[assignment]

    if best_id is not None and best_score >= fuzzy_threshold:
        logger.debug(
            "product_matcher.pass3_fuzzy",
            canonical_id=str(best_id),
            score=round(best_score, 3),
        )
        return best_id

    # ── Pass 4: brand + first name token ─────────────────────────────────
    if trend_brand and trend_tokens:
        first_token = trend_tokens[0]
        rows = (await session.execute(
            select(CanonicalProduct).where(
                CanonicalProduct.brand.ilike(trend_brand),
                CanonicalProduct.name.ilike(f"%{first_token}%"),
            ).limit(1)
        )).scalars().all()
        if rows:
            logger.debug("product_matcher.pass4_brand_token", canonical_id=str(rows[0].id))
            return rows[0].id  # type: ignore[return-value]

    # ── Pass 5: no match ───────────────────────────────────────────────────
    logger.debug(
        "product_matcher.no_match",
        trend_name=trend_name,
        trend_brand=trend_brand,
        best_fuzzy_score=round(best_score, 3),
    )
    return None
