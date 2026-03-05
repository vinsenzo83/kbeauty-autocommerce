from __future__ import annotations

"""
app/services/trend_signal_service_v2.py
─────────────────────────────────────────
Sprint 18 – Trend Signal v2 service layer.

Public API
----------
upsert_trend_source(session, source, name) -> TrendSource
insert_trend_items(session, source_id, items) -> int
build_mention_dictionary(session, limit_per_brand=50) -> int
extract_mentions(text, dictionary) -> dict[str, int]
compute_mention_signals(session, source_id, docs, observed_at=None) -> int
get_latest_amazon_scores(session) -> dict[str, dict]
get_latest_tiktok_scores(session) -> dict[str, float]
"""

import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import structlog
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trend_signal_v2 import (
    TrendSource, TrendItem, MentionDictionary, MentionSignal,
)

logger = structlog.get_logger(__name__)

# ── Normalisation helper ──────────────────────────────────────────────────────

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = text.lower()
    t = _PUNCT_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ── A) upsert_trend_source ────────────────────────────────────────────────────

async def upsert_trend_source(
    session: AsyncSession,
    source: str,
    name: str,
    is_enabled: bool = True,
) -> TrendSource:
    """
    Return existing TrendSource or create a new one.
    Idempotent: (source, name) is unique.
    """
    existing = (await session.execute(
        select(TrendSource).where(
            TrendSource.source == source,
            TrendSource.name   == name,
        ).limit(1)
    )).scalar_one_or_none()

    if existing is not None:
        return existing

    row = TrendSource(
        id         = uuid.uuid4(),
        source     = source,
        name       = name,
        is_enabled = is_enabled,
    )
    session.add(row)
    logger.info("trend_signal_v2.source_created", source=source, name=name)
    return row


# ── B) insert_trend_items ─────────────────────────────────────────────────────

async def insert_trend_items(
    session: AsyncSession,
    source_id: uuid.UUID | str,
    items: list[dict[str, Any]],
) -> int:
    """
    Batch-insert trend items for a given source.

    Parameters
    ----------
    source_id : UUID of the TrendSource row
    items     : List of dicts with keys:
                external_id, title, brand, category, rank,
                price, currency, rating, review_count, raw_json

    Returns
    -------
    Number of rows inserted
    """
    count = 0
    now   = datetime.now(tz=timezone.utc)

    for item in items:
        row = TrendItem(
            id           = uuid.uuid4(),
            source_id    = source_id,
            observed_at  = now,
            external_id  = item.get("external_id"),
            title        = item.get("title"),
            brand        = item.get("brand"),
            category     = item.get("category"),
            rank         = item.get("rank"),
            price        = item.get("price"),
            currency     = item.get("currency", "USD"),
            rating       = item.get("rating"),
            review_count = item.get("review_count"),
            raw_json     = item.get("raw_json") or item,
        )
        session.add(row)
        count += 1

    logger.info("trend_signal_v2.items_inserted", count=count, source_id=str(source_id))
    return count


# ── C) build_mention_dictionary ───────────────────────────────────────────────

async def build_mention_dictionary(
    session: AsyncSession,
    limit_per_brand: int = 50,
) -> int:
    """
    Build/refresh the mention dictionary from canonical products.

    For each canonical product, generate normalized phrases from:
    - brand + name combined
    - name alone (if distinct from brand+name)
    - individual meaningful tokens (length >= 4)

    Idempotent: upserts by (canonical_product_id, phrase).

    Returns
    -------
    Number of phrases upserted/created
    """
    from app.models.canonical_product import CanonicalProduct

    products = (await session.execute(
        select(CanonicalProduct).limit(limit_per_brand * 10)
    )).scalars().all()

    created = 0

    for cp in products:
        phrases: dict[str, float] = {}

        name  = _normalize(cp.name  or "")
        brand = _normalize(cp.brand or "")

        if brand and name:
            phrases[f"{brand} {name}"] = 2.0  # highest weight: brand + name

        if name and len(name) >= 4:
            phrases[name] = 1.5

        if brand and len(brand) >= 4:
            phrases[brand] = 1.0

        # Add meaningful tokens (4+ chars) from name
        for tok in name.split():
            if len(tok) >= 4 and tok not in ("with", "and", "for", "the"):
                phrases.setdefault(tok, 0.8)

        for phrase, weight in phrases.items():
            if len(phrase) < 3:
                continue

            # Check if exists
            existing = (await session.execute(
                select(MentionDictionary).where(
                    MentionDictionary.canonical_product_id == cp.id,
                    MentionDictionary.phrase               == phrase,
                ).limit(1)
            )).scalar_one_or_none()

            if existing is None:
                row = MentionDictionary(
                    id                   = uuid.uuid4(),
                    canonical_product_id = cp.id,
                    phrase               = phrase,
                    weight               = weight,
                )
                session.add(row)
                created += 1
            else:
                existing.weight = weight
                session.add(existing)

    logger.info("trend_signal_v2.dictionary_built", phrases_created=created)
    return created


# ── D) extract_mentions ───────────────────────────────────────────────────────

def extract_mentions(
    text: str,
    dictionary: "list[MentionDictionary] | dict[str, str]",
) -> dict[str, int]:
    """
    Count product mentions in text using the mention dictionary.

    Parameters
    ----------
    text       : Raw text to scan (caption, title, comment, etc.)
    dictionary : Either:
                 - List of MentionDictionary ORM rows (normal usage)
                 - dict[phrase, canonical_product_id_str] (testing/task usage)

    Returns
    -------
    dict[str(canonical_product_id), int]  — only products with count > 0
    """
    norm_text = _normalize(text)
    counts: dict[str, int] = {}

    # Support both ORM list and plain dict input
    if isinstance(dictionary, dict):
        items = [(phrase, cp_id) for phrase, cp_id in dictionary.items()]
    else:
        items = [(entry.phrase, str(entry.canonical_product_id)) for entry in dictionary]

    for phrase, cp_id in items:
        if not phrase or len(phrase) < 3:
            continue
        norm_phrase = _normalize(phrase)

        # Count non-overlapping occurrences
        n = 0
        start = 0
        while True:
            idx = norm_text.find(norm_phrase, start)
            if idx == -1:
                break
            # Simple word-boundary check
            before_ok = (idx == 0 or norm_text[idx - 1] == " ")
            after_idx = idx + len(norm_phrase)
            after_ok  = (after_idx >= len(norm_text) or norm_text[after_idx] == " ")
            if before_ok and after_ok:
                n += 1
            start = idx + 1

        if n > 0:
            counts[cp_id] = counts.get(cp_id, 0) + n

    return counts


# ── E) compute_mention_signals ────────────────────────────────────────────────

async def compute_mention_signals(
    session: AsyncSession,
    source_id: uuid.UUID | str,
    docs: list[dict[str, Any]],
    observed_at: datetime | None = None,
    phrase_dict: "dict[str, str] | None" = None,
) -> int:
    """
    Compute and upsert daily mention signals from a document corpus.

    Parameters
    ----------
    session     : Async SQLAlchemy session
    source_id   : TrendSource.id for the signal source
    docs        : List of documents; each dict may have any of:
                  title, caption, desc, comments (list[str])
    observed_at : Observation timestamp (default: now)
    phrase_dict : Optional pre-built dict[phrase, canonical_product_id_str].
                  If provided, skips DB lookup of MentionDictionary rows.

    Returns
    -------
    Number of MentionSignal rows created/updated
    """
    if observed_at is None:
        observed_at = datetime.now(tz=timezone.utc)

    # Load mention dictionary (or use pre-built dict)
    if phrase_dict is not None:
        dict_rows: Any = phrase_dict   # dict[phrase -> cp_id_str]
    else:
        dict_rows = (await session.execute(
            select(MentionDictionary)
        )).scalars().all()

    if not dict_rows:
        logger.debug("trend_signal_v2.empty_dictionary")
        return 0

    # Aggregate mentions across all docs
    agg: dict[str, int] = {}
    for doc in docs:
        text_parts = []
        for field in ("title", "caption", "desc", "description"):
            if doc.get(field):
                text_parts.append(str(doc[field]))
        # comments may be a list
        for comment in doc.get("comments", []):
            if comment:
                text_parts.append(str(comment))

        full_text = " ".join(text_parts)
        if not full_text.strip():
            continue

        for cp_id, cnt in extract_mentions(full_text, dict_rows).items():
            agg[cp_id] = agg.get(cp_id, 0) + cnt

    if not agg:
        logger.debug("trend_signal_v2.no_mentions_found", docs=len(docs))
        return 0

    # Compute velocity heuristic using yesterday's signal
    yesterday = observed_at - timedelta(days=1)
    created   = 0

    for cp_id_str, mention_count in agg.items():
        try:
            cp_uuid = uuid.UUID(cp_id_str)
        except ValueError:
            continue

        # Load yesterday's signal for velocity
        prev = (await session.execute(
            select(MentionSignal).where(
                MentionSignal.canonical_product_id == cp_uuid,
                MentionSignal.source_id            == source_id,
                MentionSignal.observed_at          >= yesterday - timedelta(hours=12),
                MentionSignal.observed_at          <  yesterday + timedelta(hours=12),
            ).limit(1)
        )).scalar_one_or_none()

        velocity = 0.0
        if prev and prev.mentions > 0:
            velocity = (mention_count - prev.mentions) / prev.mentions
            velocity = max(-1.0, min(5.0, velocity))  # clamp

        score = mention_count * (1.0 + velocity)

        # Check for existing row for today (idempotent upsert)
        day_start = observed_at.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end   = day_start + timedelta(days=1)

        existing = (await session.execute(
            select(MentionSignal).where(
                MentionSignal.canonical_product_id == cp_uuid,
                MentionSignal.source_id            == source_id,
                MentionSignal.observed_at          >= day_start,
                MentionSignal.observed_at          <  day_end,
            ).limit(1)
        )).scalar_one_or_none()

        if existing is not None:
            existing.mentions    = mention_count
            existing.velocity    = velocity
            existing.score       = score
            existing.updated_at  = datetime.now(tz=timezone.utc)
            session.add(existing)
        else:
            row = MentionSignal(
                id                   = uuid.uuid4(),
                canonical_product_id = cp_uuid,
                source_id            = source_id,
                observed_at          = observed_at,
                observed_date        = observed_at.strftime("%Y-%m-%d"),
                mentions             = mention_count,
                velocity             = velocity,
                score                = score,
                raw_json             = {"cp_id": cp_id_str, "mention_count": mention_count},
            )
            session.add(row)
            created += 1

    logger.info(
        "trend_signal_v2.mention_signals_computed",
        products=len(agg),
        created=created,
    )
    return len(agg)


# ── F) get_latest_amazon_scores ───────────────────────────────────────────────

async def get_latest_amazon_scores(
    session: AsyncSession,
    max_rank: int = 200,
) -> dict[str, dict[str, float]]:
    """
    Build amazon_rank_score + review_score for canonical products by matching
    TrendItem titles to canonical product names (simple normalized contains check).

    Returns
    -------
    dict[str(canonical_product_id), {"amazon_rank_score": float, "review_score": float}]
    0.5 neutral if no match.
    """
    from app.models.canonical_product import CanonicalProduct

    # Load recent Amazon items (last 7 days)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=7)
    amazon_source = (await session.execute(
        select(TrendSource).where(
            TrendSource.source == "amazon",
            TrendSource.is_enabled == True,  # noqa: E712
        ).limit(1)
    )).scalar_one_or_none()

    if amazon_source is None:
        return {}

    items = (await session.execute(
        select(TrendItem).where(
            TrendItem.source_id   == amazon_source.id,
            TrendItem.observed_at >= cutoff,
        ).order_by(TrendItem.rank.asc().nullslast()).limit(500)
    )).scalars().all()

    if not items:
        return {}

    # Load canonical products
    products = (await session.execute(
        select(CanonicalProduct)
    )).scalars().all()

    scores: dict[str, dict[str, float]] = {}

    for cp in products:
        cp_norm = _normalize(f"{cp.brand or ''} {cp.name or ''}")
        best_item = None
        best_match_len = 0

        for item in items:
            item_norm = _normalize(f"{item.brand or ''} {item.title or ''}")
            # Find common token overlap
            cp_tokens   = set(t for t in cp_norm.split() if len(t) >= 4)
            item_tokens = set(t for t in item_norm.split() if len(t) >= 4)
            overlap = len(cp_tokens & item_tokens)

            if overlap > best_match_len and overlap >= 2:
                best_match_len = overlap
                best_item = item

        if best_item is not None:
            # amazon_rank_score: higher rank number → lower score
            # rank 1 → 1.0; rank max_rank → 0.0
            rank = best_item.rank or max_rank
            amazon_rs = max(0.0, 1.0 - (rank - 1) / max_rank)

            # review_score: normalise review_count (50k reviews → 1.0)
            rc = best_item.review_count or 0
            review_s = min(1.0, rc / 50_000)

            # Boost if high rating
            if best_item.rating and float(best_item.rating) >= 4.5:
                review_s = min(1.0, review_s + 0.1)

            scores[str(cp.id)] = {
                "amazon_rank_score": round(amazon_rs, 4),
                "review_score":      round(review_s, 4),
            }

    logger.info("trend_signal_v2.amazon_scores", matched=len(scores))
    return scores


# ── G) get_latest_tiktok_scores ───────────────────────────────────────────────

async def get_latest_tiktok_scores(
    session: AsyncSession,
    days: int = 7,
    max_score: float = 200.0,
) -> dict[str, float]:
    """
    Compute tiktok_trend_score [0, 1] for canonical products from
    MentionSignal rows (aggregated TikTok mentions over last `days` days).

    Score normalisation: max_score → 1.0; 0 → 0.0

    Returns
    -------
    dict[str(canonical_product_id), tiktok_trend_score]
    """
    tiktok_source = (await session.execute(
        select(TrendSource).where(
            TrendSource.source == "tiktok",
            TrendSource.is_enabled == True,  # noqa: E712
        ).limit(1)
    )).scalar_one_or_none()

    if tiktok_source is None:
        return {}

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    rows   = (await session.execute(
        select(MentionSignal).where(
            MentionSignal.source_id   == tiktok_source.id,
            MentionSignal.observed_at >= cutoff,
        ).order_by(desc(MentionSignal.score))
    )).scalars().all()

    if not rows:
        return {}

    # Aggregate score per canonical product (sum across days)
    agg: dict[str, float] = {}
    for r in rows:
        cp_id = str(r.canonical_product_id)
        agg[cp_id] = agg.get(cp_id, 0.0) + r.score

    # Normalise to [0, 1]
    actual_max = max(agg.values()) if agg else 1.0
    normalizer = max(actual_max, max_score)

    result = {
        cp_id: round(min(1.0, raw / normalizer), 4)
        for cp_id, raw in agg.items()
    }

    logger.info("trend_signal_v2.tiktok_scores", products=len(result))
    return result
