from __future__ import annotations

"""
app/services/product_scoring.py
─────────────────────────────────
Sprint 15 – Weighted product scoring engine for the AI discovery pipeline.

Public API
----------
    result = await compute_product_score(session, canonical_product_id, trend_score)
    # Returns ScoreBreakdown dataclass

Score formula
-------------
final_score = trend_score     * 0.35
            + margin_score    * 0.25
            + competition_score * 0.20
            + supplier_score  * 0.10
            + content_score   * 0.10

Each component is normalised to [0.0, 1.0].

Component definitions
---------------------
trend_score (input)
    Provided by caller (from TrendProduct.trend_score / 10).
    0.0 = no trend, 1.0 = maximum trend.

margin_score
    Derived from CanonicalProduct.last_price and min IN_STOCK supplier cost.
    margin = (sell_price - supplier_cost) / sell_price
    score  = clamp(margin / TARGET_MARGIN, 0.0, 1.0)
    Falls back to 0.5 if last_price is None (neutral).

competition_score
    Derived from MarketPrice data.
    score = 1.0 if no competitor data (unopposed market).
    score = clamp(1 - (num_competitors / MAX_COMPETITORS), 0.0, 1.0)
    Bonus: if our last_price < competitor_min → +0.2 clamped to 1.0.

supplier_score
    Fraction of IN_STOCK suppliers out of all known suppliers for this product.
    score = in_stock_count / total_count  (0.0 if no suppliers)

content_score
    Data-completeness score:
    +0.3 if name is set
    +0.2 if brand is set
    +0.2 if image_urls_json is non-empty
    +0.15 if ean is set
    +0.15 if last_price is set
    Total maximum = 1.0
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_product import CanonicalProduct
from app.models.supplier_product import SupplierProduct, StockStatus

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
TARGET_MARGIN       = 0.30   # 30 % target margin for full margin_score
MAX_COMPETITORS     = 5      # 5+ competitors → competition_score = 0


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ScoreBreakdown:
    """Full score breakdown returned by compute_product_score."""
    canonical_product_id: uuid.UUID
    trend_score:        float   # 0.0–1.0 (input, normalised)
    margin_score:       float   # 0.0–1.0
    competition_score:  float   # 0.0–1.0
    supplier_score:     float   # 0.0–1.0
    content_score:      float   # 0.0–1.0
    final_score:        float   # 0.0–1.0 weighted composite
    notes:              list[str] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


def _round4(val: float) -> float:
    return round(val, 4)


async def _compute_margin_score(
    session: AsyncSession,
    product: CanonicalProduct,
    notes: list[str],
) -> float:
    """Score based on sell-price vs cheapest in-stock supplier cost."""
    sell_price = float(product.last_price) if product.last_price else None
    if sell_price is None or sell_price <= 0:
        notes.append("margin_score: no last_price, using neutral 0.5")
        return 0.5

    # Cheapest IN_STOCK supplier
    result = await session.execute(
        select(func.min(SupplierProduct.price)).where(
            SupplierProduct.canonical_product_id == product.id,
            SupplierProduct.stock_status == StockStatus.IN_STOCK,
        )
    )
    min_cost_raw = result.scalar_one_or_none()
    if min_cost_raw is None:
        notes.append("margin_score: no in-stock supplier, using neutral 0.5")
        return 0.5

    cost   = float(min_cost_raw)
    margin = (sell_price - cost) / sell_price if sell_price > 0 else 0.0
    score  = _clamp(margin / TARGET_MARGIN)
    notes.append(f"margin_score: sell={sell_price:.2f} cost={cost:.2f} margin={margin:.2%} → {score:.4f}")
    return _round4(score)


async def _compute_competition_score(
    session: AsyncSession,
    product: CanonicalProduct,
    notes: list[str],
) -> float:
    """Score inversely proportional to number of competitors."""
    # Lazy import to avoid circular import at module load time
    try:
        from app.models.market_price import MarketPrice
        result = await session.execute(
            select(func.count()).select_from(MarketPrice).where(
                MarketPrice.canonical_product_id == product.id,
            )
        )
        num_competitors = result.scalar_one() or 0
    except Exception:  # MarketPrice table may not exist in all test environments
        num_competitors = 0

    if num_competitors == 0:
        notes.append("competition_score: no competitors → 1.0 (unopposed)")
        return 1.0

    score = _clamp(1.0 - num_competitors / MAX_COMPETITORS)

    # Price-advantage bonus: if our price < competitor_min add 0.2
    try:
        from app.models.market_price import MarketPrice
        min_result = await session.execute(
            select(func.min(MarketPrice.price)).where(
                MarketPrice.canonical_product_id == product.id
            )
        )
        competitor_min_raw = min_result.scalar_one_or_none()
        if (
            competitor_min_raw is not None
            and product.last_price is not None
            and float(product.last_price) < float(competitor_min_raw)
        ):
            score = _clamp(score + 0.2)
            notes.append(
                f"competition_score: price advantage bonus "
                f"(ours={product.last_price} < min={competitor_min_raw})"
            )
    except Exception:
        pass

    notes.append(f"competition_score: {num_competitors} competitors → {score:.4f}")
    return _round4(score)


async def _compute_supplier_score(
    session: AsyncSession,
    product: CanonicalProduct,
    notes: list[str],
) -> float:
    """Fraction of IN_STOCK suppliers."""
    total_result = await session.execute(
        select(func.count()).select_from(SupplierProduct).where(
            SupplierProduct.canonical_product_id == product.id
        )
    )
    total = total_result.scalar_one() or 0
    if total == 0:
        notes.append("supplier_score: no suppliers → 0.0")
        return 0.0

    in_stock_result = await session.execute(
        select(func.count()).select_from(SupplierProduct).where(
            SupplierProduct.canonical_product_id == product.id,
            SupplierProduct.stock_status == StockStatus.IN_STOCK,
        )
    )
    in_stock = in_stock_result.scalar_one() or 0
    score    = _round4(in_stock / total)
    notes.append(f"supplier_score: {in_stock}/{total} in-stock → {score:.4f}")
    return score


def _compute_content_score(product: CanonicalProduct, notes: list[str]) -> float:
    """Data completeness of the canonical product."""
    score = 0.0
    if product.name:
        score += 0.30
    if product.brand:
        score += 0.20
    if product.image_urls_json:
        score += 0.20
    if product.ean:
        score += 0.15
    if product.last_price:
        score += 0.15
    score = _round4(score)
    notes.append(f"content_score: {score:.4f}")
    return score


# ── Public entry-point ────────────────────────────────────────────────────────

async def compute_product_score(
    session: AsyncSession,
    canonical_product_id: uuid.UUID,
    trend_score_raw: float,         # raw from TrendProduct.trend_score (0–10)
) -> ScoreBreakdown | None:
    """
    Compute the full scoring breakdown for a canonical product.

    Parameters
    ----------
    session               : Async SQLAlchemy session
    canonical_product_id  : UUID of the canonical product
    trend_score_raw       : Raw trend score from the collector (0.0 – 10.0)

    Returns
    -------
    ScoreBreakdown, or None if the product cannot be found.
    """
    # Fetch canonical product
    product_row = (await session.execute(
        select(CanonicalProduct).where(CanonicalProduct.id == canonical_product_id).limit(1)
    )).scalar_one_or_none()

    if product_row is None:
        logger.warning("product_scoring.not_found", canonical_product_id=str(canonical_product_id))
        return None

    notes: list[str] = []

    # Normalise trend score: 0–10 → 0.0–1.0
    trend_score = _clamp(_round4(trend_score_raw / 10.0))

    # Compute individual components
    margin_score      = await _compute_margin_score(session, product_row, notes)
    competition_score = await _compute_competition_score(session, product_row, notes)
    supplier_score    = await _compute_supplier_score(session, product_row, notes)
    content_score     = _compute_content_score(product_row, notes)

    # Weighted composite
    final_score = _round4(
        trend_score       * 0.35
        + margin_score    * 0.25
        + competition_score * 0.20
        + supplier_score  * 0.10
        + content_score   * 0.10
    )

    logger.debug(
        "product_scoring.result",
        canonical_product_id=str(canonical_product_id),
        trend_score=trend_score,
        margin_score=margin_score,
        competition_score=competition_score,
        supplier_score=supplier_score,
        content_score=content_score,
        final_score=final_score,
    )

    return ScoreBreakdown(
        canonical_product_id=canonical_product_id,
        trend_score=trend_score,
        margin_score=margin_score,
        competition_score=competition_score,
        supplier_score=supplier_score,
        content_score=content_score,
        final_score=final_score,
        notes=notes,
    )
