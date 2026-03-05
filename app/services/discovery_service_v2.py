from __future__ import annotations

"""
app/services/discovery_service_v2.py
──────────────────────────────────────
Sprint 17 – AI Discovery Engine v2.

Public API
----------
    score = score_product(parts)           → float
    candidates = await generate_candidates(session, limit=200)   → list[ProductCandidateV2]
    top = await get_top_candidates(session, limit=20)             → list[ProductCandidateV2]

Score formula
─────────────
score = amazon_rank_score  * 0.35
      + supplier_rank_score * 0.25
      + margin_score        * 0.20
      + review_score        * 0.10
      + competition_score   * 0.10

All component scores are normalised to [0.0, 1.0].

Algorithm: generate_candidates
──────────────────────────────
1. Load all canonical_products (limit).
2. For each canonical product:
   a. Best supplier cost  – query supplier_products for lowest in-stock price.
   b. supplier_rank_score – fraction of suppliers with IN_STOCK status (0→1).
   c. margin_score        – (last_price - best_cost) / last_price, clamped 0-1.
                            If no last_price, uses target_margin_rate as proxy.
   d. competition_score   – market price band width signal if market_prices row
                            exists; else neutral default 0.5.
   e. amazon_rank_score   – neutral default 0.5 (no Amazon source in DB).
   f. review_score        – neutral default 0.5 (no review data in DB).
3. Compute final score.
4. Upsert: if a row with (canonical_product_id, status=candidate) already exists,
   update scores; otherwise insert new row.
5. Return sorted list.

Idempotency
───────────
Re-running generate_candidates does NOT create duplicates: the unique partial
index on (canonical_product_id) WHERE status='candidate' enforced by the service
logic (select-then-update-or-insert pattern).
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product_candidate_v2 import ProductCandidateV2, CandidateStatusV2

logger = structlog.get_logger(__name__)

# ── Score weights (must sum to 1.0) ──────────────────────────────────────────
WEIGHT_AMAZON_RANK    = 0.35
WEIGHT_SUPPLIER_RANK  = 0.25
WEIGHT_MARGIN         = 0.20
WEIGHT_REVIEW         = 0.10
WEIGHT_COMPETITION    = 0.10

# Neutral default for missing signals
NEUTRAL_SCORE = 0.5

# ── Score formula ─────────────────────────────────────────────────────────────

def score_product(parts: dict[str, float]) -> float:
    """
    Compute weighted composite score from component parts.

    Parameters
    ----------
    parts : dict with keys:
        amazon_rank_score, supplier_rank_score, margin_score,
        review_score, competition_score
        All values must be float in [0.0, 1.0].

    Returns
    -------
    float in [0.0, 1.0]

    Formula
    -------
    score = amazon_rank_score  * 0.35
          + supplier_rank_score * 0.25
          + margin_score        * 0.20
          + review_score        * 0.10
          + competition_score   * 0.10
    """
    amazon   = float(parts.get("amazon_rank_score",   NEUTRAL_SCORE))
    supplier = float(parts.get("supplier_rank_score", NEUTRAL_SCORE))
    margin   = float(parts.get("margin_score",        NEUTRAL_SCORE))
    review   = float(parts.get("review_score",        NEUTRAL_SCORE))
    comp     = float(parts.get("competition_score",   NEUTRAL_SCORE))

    raw = (
        amazon   * WEIGHT_AMAZON_RANK
        + supplier * WEIGHT_SUPPLIER_RANK
        + margin   * WEIGHT_MARGIN
        + review   * WEIGHT_REVIEW
        + comp     * WEIGHT_COMPETITION
    )
    # Clamp to [0.0, 1.0]
    return max(0.0, min(1.0, raw))


# ── Score component helpers ───────────────────────────────────────────────────

def _margin_score(last_price: float | None, best_cost: float | None,
                  target_margin_rate: float = 0.30) -> float:
    """
    Compute margin score.

    If last_price and best_cost are available:
        margin = (last_price - best_cost) / last_price  clamped [0, 1]
    Else fall back to target_margin_rate proxy.
    """
    if last_price and last_price > 0 and best_cost is not None and best_cost >= 0:
        margin = (last_price - best_cost) / last_price
        return max(0.0, min(1.0, float(margin)))
    # Proxy: use target_margin_rate as a neutral estimate
    return max(0.0, min(1.0, float(target_margin_rate)))


def _supplier_rank_score(in_stock: int, total: int) -> float:
    """Fraction of suppliers that are IN_STOCK."""
    if total == 0:
        return NEUTRAL_SCORE
    return max(0.0, min(1.0, in_stock / total))


def _competition_score(price_band_width: float | None) -> float:
    """
    Inverse competition signal.
    A wide price band → more competition → lower score.
    Narrow band or no data → neutral 0.5.
    If band_width / mid_price > 50% → very competitive → ~0.2
    """
    if price_band_width is None:
        return NEUTRAL_SCORE
    # Normalise: assume band_width <= 20 USD is low competition (score ~0.8)
    #             band_width >= 80 USD is high competition (score ~0.2)
    clamped = max(0.0, min(80.0, float(price_band_width)))
    return round(1.0 - (clamped / 100.0), 4)


# ── Main service functions ────────────────────────────────────────────────────

async def generate_candidates(
    session: AsyncSession,
    limit: int = 200,
) -> list[ProductCandidateV2]:
    """
    Compute scored ProductCandidateV2 rows for all canonical products.

    Steps
    -----
    1. Load canonical products (up to `limit`).
    2. For each, compute score components from supplier & market price data.
    3. Upsert into product_candidates_v2 (update if candidate row exists).
    4. Return sorted list by score DESC.

    Parameters
    ----------
    session : Async SQLAlchemy session
    limit   : Max canonical products to process (default 200)

    Returns
    -------
    list[ProductCandidateV2] sorted by score DESC, status=candidate
    """
    from app.models.canonical_product import CanonicalProduct
    from app.models.supplier_product import SupplierProduct

    # Load canonical products
    cp_rows = (await session.execute(
        select(CanonicalProduct).limit(limit)
    )).scalars().all()

    if not cp_rows:
        logger.info("discovery_v2.no_canonical_products")
        return []

    results: list[ProductCandidateV2] = []

    for cp in cp_rows:
        try:
            # ── Supplier signals ──────────────────────────────────────────────
            sp_rows = (await session.execute(
                select(SupplierProduct).where(
                    SupplierProduct.canonical_product_id == cp.id
                )
            )).scalars().all()

            total_suppliers = len(sp_rows)
            in_stock_count  = sum(
                1 for sp in sp_rows
                if (sp.stock_status or "").lower() in ("in_stock", "in stock", "instock")
            )
            prices = [
                float(sp.price)
                for sp in sp_rows
                if sp.price is not None and float(sp.price) > 0
                and (sp.stock_status or "").lower() in ("in_stock", "in stock", "instock")
            ]
            best_cost = min(prices) if prices else None

            supplier_rs = _supplier_rank_score(in_stock_count, total_suppliers)

            # ── Margin signal ─────────────────────────────────────────────────
            last_price = float(cp.last_price) if cp.last_price else None
            target_mr  = float(cp.target_margin_rate) if cp.target_margin_rate else 0.30
            margin_s   = _margin_score(last_price, best_cost, target_mr)

            # ── Competition signal (from market prices) ───────────────────────
            comp_s = NEUTRAL_SCORE
            try:
                from app.models.market_price import MarketPrice
                mp_rows = (await session.execute(
                    select(MarketPrice).where(
                        MarketPrice.canonical_product_id == cp.id
                    ).limit(10)
                )).scalars().all()
                if mp_rows:
                    mp_prices = [float(r.price) for r in mp_rows if r.price]
                    if len(mp_prices) >= 2:
                        band_width = max(mp_prices) - min(mp_prices)
                        comp_s = _competition_score(band_width)
            except Exception:
                pass  # Market price table may not exist in test env

            # ── Amazon + review signals (neutral – no Amazon source in DB) ────
            amazon_rs = NEUTRAL_SCORE
            review_s  = NEUTRAL_SCORE

            # ── Compute final score ───────────────────────────────────────────
            final = score_product({
                "amazon_rank_score":   amazon_rs,
                "supplier_rank_score": supplier_rs,
                "margin_score":        margin_s,
                "review_score":        review_s,
                "competition_score":   comp_s,
            })

            # ── Upsert: find existing candidate row ───────────────────────────
            existing = (await session.execute(
                select(ProductCandidateV2).where(
                    ProductCandidateV2.canonical_product_id == cp.id,
                    ProductCandidateV2.status == CandidateStatusV2.CANDIDATE,
                ).limit(1)
            )).scalar_one_or_none()

            now = datetime.now(tz=timezone.utc)

            if existing is not None:
                # Update scores on existing candidate
                existing.score              = final
                existing.amazon_rank_score  = amazon_rs
                existing.supplier_rank_score = supplier_rs
                existing.margin_score       = margin_s
                existing.review_score       = review_s
                existing.competition_score  = comp_s
                existing.updated_at         = now
                session.add(existing)
                results.append(existing)
            else:
                # Insert new candidate
                candidate = ProductCandidateV2(
                    id                   = uuid.uuid4(),
                    canonical_product_id = cp.id,
                    score                = final,
                    amazon_rank_score    = amazon_rs,
                    supplier_rank_score  = supplier_rs,
                    margin_score         = margin_s,
                    review_score         = review_s,
                    competition_score    = comp_s,
                    status               = CandidateStatusV2.CANDIDATE,
                )
                session.add(candidate)
                results.append(candidate)

        except Exception as exc:
            logger.warning(
                "discovery_v2.score_error",
                canonical_id=str(cp.id),
                error=str(exc),
            )

    # Sort in-memory by score DESC
    results.sort(key=lambda c: float(c.score), reverse=True)

    logger.info(
        "discovery_v2.generated",
        total=len(results),
    )
    return results


async def get_top_candidates(
    session: AsyncSession,
    limit: int = 20,
    status: str = CandidateStatusV2.CANDIDATE,
) -> list[ProductCandidateV2]:
    """
    Return top-N scored candidates from the database, sorted by score DESC.

    Parameters
    ----------
    session : Async SQLAlchemy session
    limit   : Number of candidates to return (default 20)
    status  : Filter by status (default 'candidate')

    Returns
    -------
    list[ProductCandidateV2] sorted by score DESC
    """
    rows = (await session.execute(
        select(ProductCandidateV2)
        .where(ProductCandidateV2.status == status)
        .order_by(desc(ProductCandidateV2.score))
        .limit(limit)
    )).scalars().all()

    return list(rows)


async def reject_candidate(
    session: AsyncSession,
    candidate_id: str,
    reason: str | None = None,
) -> ProductCandidateV2 | None:
    """
    Mark a candidate as rejected.

    Parameters
    ----------
    session      : Async SQLAlchemy session
    candidate_id : UUID string of the candidate to reject
    reason       : Optional rejection reason stored in notes

    Returns
    -------
    Updated ProductCandidateV2 or None if not found
    """
    candidate = (await session.execute(
        select(ProductCandidateV2).where(
            ProductCandidateV2.id == candidate_id
        ).limit(1)
    )).scalar_one_or_none()

    if candidate is None:
        return None

    candidate.status     = CandidateStatusV2.REJECTED
    candidate.notes      = reason or "Manually rejected"
    candidate.updated_at = datetime.now(tz=timezone.utc)
    session.add(candidate)

    logger.info(
        "discovery_v2.candidate_rejected",
        candidate_id=candidate_id,
        reason=reason,
    )
    return candidate


async def mark_candidate_published(
    session: AsyncSession,
    canonical_product_id: str | uuid.UUID,
    notes: str | None = None,
) -> ProductCandidateV2 | None:
    """
    Mark the active candidate for a canonical product as published.

    Used by the publish integration after a successful Shopify publish.
    Idempotent: if already published, returns the row unchanged.
    """
    candidate = (await session.execute(
        select(ProductCandidateV2).where(
            ProductCandidateV2.canonical_product_id == str(canonical_product_id)
            if isinstance(canonical_product_id, uuid.UUID)
            else ProductCandidateV2.canonical_product_id == canonical_product_id,
            ProductCandidateV2.status.in_([
                CandidateStatusV2.CANDIDATE,
                CandidateStatusV2.PUBLISHED,
            ]),
        ).order_by(desc(ProductCandidateV2.score)).limit(1)
    )).scalar_one_or_none()

    if candidate is None:
        return None

    if candidate.status != CandidateStatusV2.PUBLISHED:
        candidate.status     = CandidateStatusV2.PUBLISHED
        candidate.notes      = notes or "Published via discovery pipeline"
        candidate.updated_at = datetime.now(tz=timezone.utc)
        session.add(candidate)

    return candidate


# ── Discovery run summary ─────────────────────────────────────────────────────

async def run_discovery_v2(
    session: AsyncSession,
    limit: int = 200,
    top_n: int = 20,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Full discovery run: generate candidates, return top-N summary.

    Parameters
    ----------
    session  : Async SQLAlchemy session
    limit    : Max canonical products to score (default 200)
    top_n    : Top candidates to surface (default 20)
    dry_run  : If True, generate but do not commit

    Returns
    -------
    dict with run summary
    """
    candidates = await generate_candidates(session, limit=limit)
    top        = candidates[:top_n]

    summary = {
        "dry_run":             dry_run,
        "candidates_generated": len(candidates),
        "top_n":               len(top),
        "top_candidates": [
            {
                "canonical_product_id": str(c.canonical_product_id),
                "score":               round(float(c.score), 4),
                "amazon_rank_score":   round(float(c.amazon_rank_score), 4),
                "supplier_rank_score": round(float(c.supplier_rank_score), 4),
                "margin_score":        round(float(c.margin_score), 4),
                "review_score":        round(float(c.review_score), 4),
                "competition_score":   round(float(c.competition_score), 4),
                "status":              c.status,
            }
            for c in top
        ],
    }

    logger.info(
        "discovery_v2.run_complete",
        dry_run=dry_run,
        candidates=len(candidates),
        top_n=len(top),
    )
    return summary
