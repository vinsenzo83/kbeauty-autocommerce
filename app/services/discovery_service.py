from __future__ import annotations

"""
app/services/discovery_service.py
───────────────────────────────────
Sprint 15 – AI Product Discovery pipeline orchestrator.

Public API
----------
    result = await run_product_discovery(session, dry_run=False, top_n=50)
    # Returns DiscoveryResult dataclass

Pipeline steps
--------------
1. Collect trend signals
   - collect_tiktok_trends()
   - collect_amazon_bestsellers()
   Deduplicate by name+source; persist to trend_products table.

2. Match each trend signal to a canonical product
   - match_trend_to_canonical(session, trend)
   Only proceed with signals that resolve to a canonical product.

3. Score each matched canonical product
   - compute_product_score(session, canonical_product_id, trend_score)
   Uses the weighted formula:
       final = trend*0.35 + margin*0.25 + competition*0.20
               + supplier*0.10 + content*0.10

4. Insert / update product_candidates
   - Upsert (canonical_product_id) per run: update scores if higher.
   - Deduplicate by canonical_product_id (keep highest-scored entry).

5. Trim to top-N
   - Keep top 50 candidates ranked by final_score.
   - Mark the rest as 'rejected' with reason='below_top_50'.

6. Return summary stats.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_candidate import ProductCandidate, CandidateStatus
from app.models.trend_product import TrendProduct
from app.services.trend_collectors.tiktok_trends import collect_tiktok_trends
from app.services.trend_collectors.amazon_bestsellers import collect_amazon_bestsellers
from app.services.product_matcher import match_trend_to_canonical
from app.services.product_scoring import compute_product_score

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
TOP_N_CANDIDATES = 50


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class DiscoveryResult:
    """Summary returned to caller / Celery task."""
    dry_run:              bool
    signals_collected:    int
    signals_matched:      int
    candidates_created:   int
    candidates_updated:   int
    candidates_rejected:  int
    top_candidates:       list[dict[str, Any]] = field(default_factory=list)
    errors:               list[str]            = field(default_factory=list)


# ── Trend persistence helpers ─────────────────────────────────────────────────

async def _upsert_trend_product(
    session: AsyncSession,
    signal: dict[str, Any],
    dry_run: bool,
) -> TrendProduct:
    """
    Insert or update a TrendProduct row for the given signal.
    Uses (source, external_id) as the unique key.
    """
    existing_row = (await session.execute(
        select(TrendProduct).where(
            TrendProduct.source      == signal["source"],
            TrendProduct.external_id == signal["external_id"],
        ).limit(1)
    )).scalar_one_or_none()

    if existing_row is not None:
        # Update score and metadata if trend has changed
        existing_row.trend_score    = signal["trend_score"]    # type: ignore[assignment]
        existing_row.name           = signal["name"]           # type: ignore[assignment]
        existing_row.brand          = signal.get("brand")      # type: ignore[assignment]
        existing_row.category       = signal.get("category")   # type: ignore[assignment]
        existing_row.raw_data_json  = signal.get("raw_data_json")  # type: ignore[assignment]
        if not dry_run:
            session.add(existing_row)
        return existing_row

    row = TrendProduct(
        id           = uuid.uuid4(),
        source       = signal["source"],
        external_id  = signal["external_id"],
        name         = signal["name"],
        brand        = signal.get("brand"),
        category     = signal.get("category"),
        trend_score  = signal["trend_score"],
        raw_data_json= signal.get("raw_data_json"),
    )
    if not dry_run:
        session.add(row)
    return row


# ── Candidate helpers ─────────────────────────────────────────────────────────

async def _upsert_candidate(
    session: AsyncSession,
    canonical_product_id: uuid.UUID,
    trend_product_id: uuid.UUID | None,
    score,   # ScoreBreakdown
    dry_run: bool,
) -> tuple[ProductCandidate, bool]:
    """
    Insert or update a ProductCandidate row.
    Returns (candidate, was_created).
    """
    existing = (await session.execute(
        select(ProductCandidate).where(
            ProductCandidate.canonical_product_id == canonical_product_id,
            ProductCandidate.status == CandidateStatus.CANDIDATE,
        ).limit(1)
    )).scalar_one_or_none()

    if existing is not None:
        # Keep the record with the higher final_score
        if score.final_score > float(existing.final_score):
            existing.trend_score       = score.trend_score        # type: ignore[assignment]
            existing.margin_score      = score.margin_score       # type: ignore[assignment]
            existing.competition_score = score.competition_score  # type: ignore[assignment]
            existing.supplier_score    = score.supplier_score     # type: ignore[assignment]
            existing.content_score     = score.content_score      # type: ignore[assignment]
            existing.final_score       = score.final_score        # type: ignore[assignment]
            existing.trend_product_id  = trend_product_id         # type: ignore[assignment]
            if not dry_run:
                session.add(existing)
        return existing, False  # updated

    candidate = ProductCandidate(
        id                   = uuid.uuid4(),
        canonical_product_id = canonical_product_id,
        trend_product_id     = trend_product_id,
        trend_score          = score.trend_score,
        margin_score         = score.margin_score,
        competition_score    = score.competition_score,
        supplier_score       = score.supplier_score,
        content_score        = score.content_score,
        final_score          = score.final_score,
        status               = CandidateStatus.CANDIDATE,
    )
    if not dry_run:
        session.add(candidate)
    return candidate, True  # created


# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run_product_discovery(
    session: AsyncSession,
    dry_run: bool = False,
    top_n: int = TOP_N_CANDIDATES,
) -> DiscoveryResult:
    """
    Execute the full AI product discovery pipeline.

    Parameters
    ----------
    session : Async SQLAlchemy session (caller is responsible for commit)
    dry_run : If True, no DB writes are performed
    top_n   : Maximum number of candidates to keep (default 50)

    Returns
    -------
    DiscoveryResult with summary statistics and top candidate list.
    """
    logger.info("discovery.start", dry_run=dry_run, top_n=top_n)

    errors:   list[str] = []
    created:  int = 0
    updated:  int = 0
    rejected: int = 0

    # ── Step 1: Collect trend signals ────────────────────────────────────────
    all_signals: list[dict[str, Any]] = []
    for collector, name in [
        (collect_tiktok_trends,       "tiktok"),
        (collect_amazon_bestsellers,  "amazon_bestsellers"),
    ]:
        try:
            signals = collector()
            all_signals.extend(signals)
            logger.info("discovery.collected", source=name, count=len(signals))
        except Exception as exc:
            msg = f"collector {name} failed: {exc}"
            errors.append(msg)
            logger.error("discovery.collector_error", source=name, error=str(exc))

    signals_collected = len(all_signals)

    # ── Step 2: Deduplicate by (source, external_id) ─────────────────────────
    seen_ids: set[str] = set()
    unique_signals: list[dict[str, Any]] = []
    for s in all_signals:
        key = f"{s['source']}:{s['external_id']}"
        if key not in seen_ids:
            seen_ids.add(key)
            unique_signals.append(s)

    # ── Step 3: Persist trend products + match to canonical ──────────────────
    scored_candidates: list[tuple[uuid.UUID, uuid.UUID | None, Any]] = []
    # (canonical_product_id, trend_product_id, ScoreBreakdown)

    matched = 0
    for signal in unique_signals:
        try:
            # Persist trend signal
            trend_row = await _upsert_trend_product(session, signal, dry_run)
            if not dry_run:
                await session.flush()

            # Match to canonical
            canonical_id = await match_trend_to_canonical(session, signal)
            if canonical_id is None:
                logger.debug("discovery.no_match", name=signal["name"])
                continue
            matched += 1

            # Score
            score = await compute_product_score(session, canonical_id, signal["trend_score"])
            if score is None:
                continue

            scored_candidates.append((canonical_id, trend_row.id if not dry_run else None, score))

        except Exception as exc:
            msg = f"signal '{signal.get('name','?')}' error: {exc}"
            errors.append(msg)
            logger.error("discovery.signal_error", name=signal.get("name"), error=str(exc))

    signals_matched = matched

    # ── Step 4: Deduplicate scored candidates by canonical_product_id ────────
    # Keep highest final_score per canonical product
    best_by_canonical: dict[uuid.UUID, tuple[uuid.UUID | None, Any]] = {}
    for (cp_id, tp_id, sc) in scored_candidates:
        if cp_id not in best_by_canonical or sc.final_score > best_by_canonical[cp_id][1].final_score:
            best_by_canonical[cp_id] = (tp_id, sc)

    # ── Step 5: Upsert candidates ─────────────────────────────────────────────
    all_candidates: list[ProductCandidate] = []
    for cp_id, (tp_id, sc) in best_by_canonical.items():
        try:
            cand, was_created = await _upsert_candidate(session, cp_id, tp_id, sc, dry_run)
            all_candidates.append(cand)
            if was_created:
                created += 1
            else:
                updated += 1
        except Exception as exc:
            errors.append(f"upsert candidate {cp_id}: {exc}")
            logger.error("discovery.upsert_error", cp_id=str(cp_id), error=str(exc))

    # ── Step 6: Trim to top-N ─────────────────────────────────────────────────
    # Sort by final_score descending
    all_candidates.sort(key=lambda c: float(c.final_score), reverse=True)
    top_candidates     = all_candidates[:top_n]
    overflow_candidates = all_candidates[top_n:]

    if not dry_run:
        for c in overflow_candidates:
            c.status = CandidateStatus.REJECTED        # type: ignore[assignment]
            c.notes  = "below_top_50"                  # type: ignore[assignment]
            session.add(c)
            rejected += 1

    # ── Build response summary ────────────────────────────────────────────────
    top_summary = [
        {
            "canonical_product_id": str(c.canonical_product_id),
            "trend_score":          float(c.trend_score),
            "margin_score":         float(c.margin_score),
            "competition_score":    float(c.competition_score),
            "supplier_score":       float(c.supplier_score),
            "content_score":        float(c.content_score),
            "final_score":          float(c.final_score),
            "status":               c.status,
        }
        for c in top_candidates
    ]

    result = DiscoveryResult(
        dry_run=dry_run,
        signals_collected=signals_collected,
        signals_matched=signals_matched,
        candidates_created=created,
        candidates_updated=updated,
        candidates_rejected=rejected,
        top_candidates=top_summary,
        errors=errors,
    )
    logger.info(
        "discovery.complete",
        dry_run=dry_run,
        signals=signals_collected,
        matched=signals_matched,
        created=created,
        updated=updated,
        rejected=rejected,
        errors=len(errors),
    )
    return result


# ── Utility: list top candidates (used by admin API and publish_service) ──────

async def get_top_candidates(
    session: AsyncSession,
    limit: int = TOP_N_CANDIDATES,
    status: str = CandidateStatus.CANDIDATE,
) -> list[dict[str, Any]]:
    """
    Return top product candidates sorted by final_score descending.

    Parameters
    ----------
    session : Async SQLAlchemy session
    limit   : Maximum rows to return (default 50)
    status  : Filter by status ('candidate', 'published', 'rejected')

    Returns
    -------
    List of dicts with candidate metadata + score breakdown.
    """
    rows = (await session.execute(
        select(ProductCandidate)
        .where(ProductCandidate.status == status)
        .order_by(desc(ProductCandidate.final_score))
        .limit(limit)
    )).scalars().all()

    return [
        {
            "id":                     str(r.id),
            "canonical_product_id":   str(r.canonical_product_id),
            "trend_product_id":       str(r.trend_product_id) if r.trend_product_id else None,
            "trend_score":            float(r.trend_score),
            "margin_score":           float(r.margin_score),
            "competition_score":      float(r.competition_score),
            "supplier_score":         float(r.supplier_score),
            "content_score":          float(r.content_score),
            "final_score":            float(r.final_score),
            "status":                 r.status,
            "notes":                  r.notes,
            "created_at":             r.created_at.isoformat() if r.created_at else None,
            "updated_at":             r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]
