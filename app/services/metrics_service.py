from __future__ import annotations

"""
app/services/metrics_service.py
─────────────────────────────────
Sprint 16 – Operational KPI metrics aggregation service.

Public API
----------
    snapshot = await collect_kpis(session, window_minutes=60)
    # Returns KpiSnapshot dataclass

KPI catalogue
-------------
orders
    total_order_count       – all channel_orders_v2 rows
    pending_order_count     – orders with status not in (shipped, delivered, cancelled)
    order_error_rate        – fraction of orders with status=failed / total (last window)

fulfillment
    supplier_order_count    – supplier_orders rows in window
    fulfillment_error_count – supplier_orders with status=failed in window
    fulfillment_error_rate  – fulfillment_error_count / supplier_order_count (0 if none)
    avg_fulfillment_hours   – avg(shipped_at - created_at) in hours (mocked: uses updated_at)

repricing
    repricing_run_count     – repricing_runs rows in window
    repricing_updated_count – sum(updated_count) across runs in window
    repricing_error_count   – repricing_runs with status=failed in window

publishing
    publish_job_count       – publish_jobs rows in window
    publish_success_count   – publish_jobs with status=success in window
    publish_failure_count   – publish_jobs with status=failed  in window

discovery
    discovery_candidate_count – product_candidates with status=candidate

market_prices
    market_price_count      – distinct canonical products with market price data

system
    collected_at            – timestamp of collection
    window_minutes          – evaluation window used
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


# ── KPI result dataclass ──────────────────────────────────────────────────────

@dataclass
class KpiSnapshot:
    """Full KPI snapshot returned to caller."""
    window_minutes: int
    collected_at:   str                 # ISO-8601 string

    # Orders
    total_order_count:    int   = 0
    pending_order_count:  int   = 0
    order_error_rate:     float = 0.0

    # Fulfillment
    supplier_order_count:    int   = 0
    fulfillment_error_count: int   = 0
    fulfillment_error_rate:  float = 0.0
    avg_fulfillment_hours:   float = 0.0

    # Repricing
    repricing_run_count:     int = 0
    repricing_updated_count: int = 0
    repricing_error_count:   int = 0

    # Publishing
    publish_job_count:     int = 0
    publish_success_count: int = 0
    publish_failure_count: int = 0

    # Discovery
    discovery_candidate_count: int = 0

    # Market prices
    market_price_count: int = 0

    # Raw errors list for dashboard feed
    recent_errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_minutes":            self.window_minutes,
            "collected_at":              self.collected_at,
            "total_order_count":         self.total_order_count,
            "pending_order_count":       self.pending_order_count,
            "order_error_rate":          round(self.order_error_rate, 4),
            "supplier_order_count":      self.supplier_order_count,
            "fulfillment_error_count":   self.fulfillment_error_count,
            "fulfillment_error_rate":    round(self.fulfillment_error_rate, 4),
            "avg_fulfillment_hours":     round(self.avg_fulfillment_hours, 2),
            "repricing_run_count":       self.repricing_run_count,
            "repricing_updated_count":   self.repricing_updated_count,
            "repricing_error_count":     self.repricing_error_count,
            "publish_job_count":         self.publish_job_count,
            "publish_success_count":     self.publish_success_count,
            "publish_failure_count":     self.publish_failure_count,
            "discovery_candidate_count": self.discovery_candidate_count,
            "market_price_count":        self.market_price_count,
            "recent_errors":             self.recent_errors,
        }


# ── Safe count helper ─────────────────────────────────────────────────────────

async def _count(session: AsyncSession, query) -> int:
    """Execute a count query safely; return 0 on any error."""
    try:
        result = await session.execute(query)
        val = result.scalar_one_or_none()
        return int(val) if val is not None else 0
    except Exception as exc:
        logger.debug("metrics_service.count_error", error=str(exc))
        return 0


async def _scalar(session: AsyncSession, query, default=0.0):
    """Execute a scalar query safely; return default on any error."""
    try:
        result = await session.execute(query)
        val = result.scalar_one_or_none()
        return val if val is not None else default
    except Exception as exc:
        logger.debug("metrics_service.scalar_error", error=str(exc))
        return default


# ── Main aggregation ──────────────────────────────────────────────────────────

async def collect_kpis(
    session: AsyncSession,
    window_minutes: int = 60,
) -> KpiSnapshot:
    """
    Aggregate operational KPI metrics over the last `window_minutes`.

    Parameters
    ----------
    session        : Async SQLAlchemy session
    window_minutes : Look-back window (default 60 min)

    Returns
    -------
    KpiSnapshot dataclass with all metrics.
    """
    now        = datetime.now(tz=timezone.utc)
    since      = now - timedelta(minutes=window_minutes)
    since_str  = since.isoformat()
    snapshot   = KpiSnapshot(
        window_minutes=window_minutes,
        collected_at=now.isoformat(),
    )

    # ── Orders ────────────────────────────────────────────────────────────────
    try:
        from app.models.channel_order import ChannelOrderV2
        snapshot.total_order_count = await _count(session, select(func.count()).select_from(ChannelOrderV2))
        snapshot.pending_order_count = await _count(session,
            select(func.count()).select_from(ChannelOrderV2).where(
                ChannelOrderV2.status.notin_(["shipped", "delivered", "cancelled"])
            )
        )
        # Error rate within window
        window_total = await _count(session,
            select(func.count()).select_from(ChannelOrderV2).where(
                ChannelOrderV2.created_at >= since
            )
        )
        window_failed = await _count(session,
            select(func.count()).select_from(ChannelOrderV2).where(
                ChannelOrderV2.created_at >= since,
                ChannelOrderV2.status == "failed",
            )
        )
        snapshot.order_error_rate = (window_failed / window_total) if window_total > 0 else 0.0
    except Exception as exc:
        logger.debug("metrics_service.orders_error", error=str(exc))

    # ── Fulfillment ───────────────────────────────────────────────────────────
    try:
        from app.models.supplier_order import SupplierOrder
        snapshot.supplier_order_count = await _count(session,
            select(func.count()).select_from(SupplierOrder).where(
                SupplierOrder.created_at >= since
            )
        )
        snapshot.fulfillment_error_count = await _count(session,
            select(func.count()).select_from(SupplierOrder).where(
                SupplierOrder.created_at >= since,
                SupplierOrder.status == "failed",
            )
        )
        if snapshot.supplier_order_count > 0:
            snapshot.fulfillment_error_rate = (
                snapshot.fulfillment_error_count / snapshot.supplier_order_count
            )
    except Exception as exc:
        logger.debug("metrics_service.fulfillment_error", error=str(exc))

    # ── Repricing ─────────────────────────────────────────────────────────────
    try:
        from app.models.market_price import RepricingRun
        snapshot.repricing_run_count = await _count(session,
            select(func.count()).select_from(RepricingRun).where(
                RepricingRun.created_at >= since
            )
        )
        updated_raw = await _scalar(session,
            select(func.sum(RepricingRun.updated_count)).where(
                RepricingRun.created_at >= since
            ), default=0
        )
        snapshot.repricing_updated_count = int(updated_raw) if updated_raw else 0
        snapshot.repricing_error_count = await _count(session,
            select(func.count()).select_from(RepricingRun).where(
                RepricingRun.created_at >= since,
                RepricingRun.status == "failed",
            )
        )
    except Exception as exc:
        logger.debug("metrics_service.repricing_error", error=str(exc))

    # ── Publishing ────────────────────────────────────────────────────────────
    try:
        from app.models.publish_job import PublishJob
        snapshot.publish_job_count = await _count(session,
            select(func.count()).select_from(PublishJob).where(
                PublishJob.created_at >= since
            )
        )
        snapshot.publish_success_count = await _count(session,
            select(func.count()).select_from(PublishJob).where(
                PublishJob.created_at >= since,
                PublishJob.status == "success",
            )
        )
        snapshot.publish_failure_count = await _count(session,
            select(func.count()).select_from(PublishJob).where(
                PublishJob.created_at >= since,
                PublishJob.status == "failed",
            )
        )
    except Exception as exc:
        logger.debug("metrics_service.publishing_error", error=str(exc))

    # ── Discovery ─────────────────────────────────────────────────────────────
    try:
        from app.models.product_candidate import ProductCandidate, CandidateStatus
        snapshot.discovery_candidate_count = await _count(session,
            select(func.count()).select_from(ProductCandidate).where(
                ProductCandidate.status == CandidateStatus.CANDIDATE
            )
        )
    except Exception as exc:
        logger.debug("metrics_service.discovery_error", error=str(exc))

    # ── Market prices ─────────────────────────────────────────────────────────
    try:
        from app.models.market_price import MarketPrice
        snapshot.market_price_count = await _count(session,
            select(func.count(func.distinct(MarketPrice.canonical_product_id)))
            .select_from(MarketPrice)
        )
    except Exception as exc:
        logger.debug("metrics_service.market_prices_error", error=str(exc))

    # ── Recent errors (last 20 supplier + repricing failures) ─────────────────
    errors: list[dict[str, Any]] = []
    try:
        from app.models.supplier_order import SupplierOrder
        so_rows = (await session.execute(
            select(SupplierOrder)
            .where(SupplierOrder.status == "failed")
            .order_by(SupplierOrder.updated_at.desc())
            .limit(10)
        )).scalars().all()
        for r in so_rows:
            errors.append({
                "type":    "fulfillment",
                "id":      str(r.id),
                "reason":  r.failure_reason or "unknown",
                "supplier": r.supplier,
                "ts":      r.updated_at.isoformat() if r.updated_at else None,
            })
    except Exception:
        pass

    try:
        from app.models.market_price import RepricingRun
        rr_rows = (await session.execute(
            select(RepricingRun)
            .where(RepricingRun.status == "failed")
            .order_by(RepricingRun.updated_at.desc())
            .limit(10)
        )).scalars().all()
        for r in rr_rows:
            errors.append({
                "type":   "repricing",
                "id":     str(r.id),
                "reason": r.notes or "unknown",
                "ts":     r.updated_at.isoformat() if r.updated_at else None,
            })
    except Exception:
        pass

    # Sort by ts descending, keep last 20
    errors.sort(key=lambda e: e.get("ts") or "", reverse=True)
    snapshot.recent_errors = errors[:20]

    logger.info(
        "metrics_service.collected",
        window_minutes=window_minutes,
        pending_orders=snapshot.pending_order_count,
        fulfillment_error_rate=round(snapshot.fulfillment_error_rate, 4),
        discovery_candidates=snapshot.discovery_candidate_count,
    )
    return snapshot
