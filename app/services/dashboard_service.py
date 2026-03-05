from __future__ import annotations

"""
app/services/dashboard_service.py
───────────────────────────────────
KPI and alert computations for the Admin Dashboard (Sprint 5).

All day boundaries use Asia/Seoul timezone.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Sequence

import structlog
from sqlalchemy import func, select, text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

try:
    import redis  # noqa: F401 — imported so tests can patch app.services.dashboard_service.redis
except ImportError:
    redis = None  # type: ignore[assignment]

from app.models.event_log import EventLog
from app.models.order import Order, OrderStatus
from app.models.ticket import Ticket, TicketStatus

logger = structlog.get_logger(__name__)

# ── Timezone helper ───────────────────────────────────────────────────────────

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

SEOUL_TZ = ZoneInfo("Asia/Seoul")


def _seoul_day_bounds() -> tuple[datetime, datetime]:
    """
    Return (day_start_utc, day_end_utc) for today in Asia/Seoul timezone.
    """
    now_seoul = datetime.now(SEOUL_TZ)
    day_start = now_seoul.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end   = day_start + timedelta(days=1)
    return day_start.astimezone(timezone.utc), day_end.astimezone(timezone.utc)


# ── KPI ───────────────────────────────────────────────────────────────────────

async def compute_kpi(session: AsyncSession, settings: Any) -> dict[str, Any]:
    """
    Compute dashboard KPIs for today (Asia/Seoul day boundary).

    Returns
    -------
    dict with keys:
        orders_today          int
        revenue_today         float
        avg_margin_pct        float
        failed_today          int
        tracking_stale_count  int   (PLACED > 24h, no tracking_number)
        open_tickets_count    int
    """
    day_start, day_end = _seoul_day_bounds()

    # ── Orders today ─────────────────────────────────────────────────────────
    result = await session.execute(
        select(func.count(Order.id)).where(
            Order.created_at >= day_start,
            Order.created_at <  day_end,
        )
    )
    orders_today: int = result.scalar_one() or 0

    # ── Revenue today (paid orders only) ─────────────────────────────────────
    result = await session.execute(
        select(func.sum(Order.total_price)).where(
            Order.created_at      >= day_start,
            Order.created_at      <  day_end,
            Order.financial_status == "paid",
        )
    )
    revenue_raw = result.scalar_one()
    revenue_today: float = float(revenue_raw) if revenue_raw else 0.0

    # ── Avg margin % (paid orders, use SUPPLIER_COST_RATIO from settings) ────
    cost_ratio: float = float(getattr(settings, "SUPPLIER_COST_RATIO", 0.75))
    margin_pct: float = round((1.0 - cost_ratio) * 100, 2)
    # For paid orders with positive revenue; if we have actual cost data later,
    # replace this with a real weighted avg.
    avg_margin_pct: float = margin_pct if revenue_today > 0 else 0.0

    # ── Failed today ──────────────────────────────────────────────────────────
    result = await session.execute(
        select(func.count(Order.id)).where(
            Order.created_at >= day_start,
            Order.created_at <  day_end,
            Order.status     == OrderStatus.FAILED,
        )
    )
    failed_today: int = result.scalar_one() or 0

    # ── Tracking stale (PLACED > 24h ago, no tracking_number) ────────────────
    stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await session.execute(
        select(func.count(Order.id)).where(
            Order.status          == OrderStatus.PLACED,
            Order.tracking_number.is_(None),           # type: ignore[attr-defined]
            Order.placed_at       <= stale_cutoff,
        )
    )
    tracking_stale_count: int = result.scalar_one() or 0

    # ── Open tickets ──────────────────────────────────────────────────────────
    result = await session.execute(
        select(func.count(Ticket.id)).where(
            Ticket.status == TicketStatus.OPEN
        )
    )
    open_tickets_count: int = result.scalar_one() or 0

    return {
        "orders_today":         orders_today,
        "revenue_today":        revenue_today,
        "avg_margin_pct":       avg_margin_pct,
        "failed_today":         failed_today,
        "tracking_stale_count": tracking_stale_count,
        "open_tickets_count":   open_tickets_count,
    }


# ── Alerts ────────────────────────────────────────────────────────────────────

async def compute_alerts(
    session: AsyncSession,
    settings: Any,
    *,
    redis_url: str | None = None,
) -> dict[str, Any]:
    """
    Compute alert cards for the dashboard.

    Returns
    -------
    dict with keys:
        tracking_stale           list[dict]   (order_id, placed_at, supplier_order_id)
        margin_guard_violations  list[dict]   (order_id, margin_pct)
        bot_failures_last_hour   int
        queue_backlog            int | None   (best-effort)
    """
    margin_guard: float = float(getattr(settings, "MARGIN_GUARD_PCT", 15.0))
    cost_ratio:   float = float(getattr(settings, "SUPPLIER_COST_RATIO", 0.75))
    order_margin: float = round((1.0 - cost_ratio) * 100, 2)

    stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    # ── Tracking stale ────────────────────────────────────────────────────────
    result = await session.execute(
        select(Order).where(
            Order.status          == OrderStatus.PLACED,
            Order.tracking_number.is_(None),           # type: ignore[attr-defined]
            Order.placed_at       <= stale_cutoff,
        ).limit(50)
    )
    stale_orders = result.scalars().all()
    tracking_stale = [
        {
            "order_id":         str(o.id),
            "placed_at":        o.placed_at.isoformat() if o.placed_at else None,
            "supplier_order_id": o.supplier_order_id,
        }
        for o in stale_orders
    ]

    # ── Margin guard violations ───────────────────────────────────────────────
    # With static cost_ratio: if order_margin < margin_guard, all paid orders violate.
    margin_violations: list[dict[str, Any]] = []
    if order_margin < margin_guard:
        day_start, day_end = _seoul_day_bounds()
        result2 = await session.execute(
            select(Order).where(
                Order.financial_status == "paid",
                Order.created_at       >= day_start,
                Order.created_at       <  day_end,
            ).limit(50)
        )
        violating = result2.scalars().all()
        margin_violations = [
            {"order_id": str(o.id), "margin_pct": order_margin}
            for o in violating
        ]

    # ── Bot failures last hour ────────────────────────────────────────────────
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    result3 = await session.execute(
        select(func.count(EventLog.id)).where(
            EventLog.event_type.in_([           # type: ignore[attr-defined]
                "order/place_failed",
                "order/tracking_failed",
            ]),
            EventLog.created_at >= one_hour_ago,
        )
    )
    bot_failures_last_hour: int = result3.scalar_one() or 0

    # ── Queue backlog (Redis / Celery) ────────────────────────────────────────
    queue_backlog: int | None = None
    try:
        if redis is not None:
            _url = redis_url or "redis://localhost:6379/0"
            r = redis.from_url(_url, socket_connect_timeout=1)
            queue_backlog = r.llen("celery")
    except Exception:
        pass  # best-effort; None means unavailable

    return {
        "tracking_stale":          tracking_stale,
        "margin_guard_violations": margin_violations,
        "bot_failures_last_hour":  bot_failures_last_hour,
        "queue_backlog":           queue_backlog,
    }


# ── 7-day chart data ──────────────────────────────────────────────────────────

async def get_orders_chart(session: AsyncSession, days: int = 7) -> list[dict[str, Any]]:
    """Return daily order count + revenue for the past ``days`` days (Seoul TZ)."""
    now_seoul = datetime.now(SEOUL_TZ)
    result_rows = []

    for delta in range(days - 1, -1, -1):
        day     = now_seoul - timedelta(days=delta)
        d_start = day.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        d_end   = (d_start + timedelta(days=1))

        cnt_res = await session.execute(
            select(func.count(Order.id)).where(
                Order.created_at >= d_start,
                Order.created_at <  d_end,
            )
        )
        rev_res = await session.execute(
            select(func.sum(Order.total_price)).where(
                Order.created_at      >= d_start,
                Order.created_at      <  d_end,
                Order.financial_status == "paid",
            )
        )
        count = cnt_res.scalar_one() or 0
        rev   = float(rev_res.scalar_one() or 0)

        result_rows.append({
            "date":    day.strftime("%Y-%m-%d"),
            "orders":  count,
            "revenue": rev,
        })

    return result_rows


# ── Health ────────────────────────────────────────────────────────────────────

async def compute_health(
    session: AsyncSession,
    *,
    redis_url: str | None = None,
) -> dict[str, Any]:
    """Return system health: DB, Redis, Celery queue depth, recent failures."""

    # DB check
    db_ok = False
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    # Redis check
    redis_ok      = False
    queue_depth: int | None = None
    try:
        if redis is not None:
            _url = redis_url or "redis://localhost:6379/0"
            r    = redis.from_url(_url, socket_connect_timeout=1)
            r.ping()
            redis_ok    = True
            queue_depth = r.llen("celery")
    except Exception:
        pass

    # Recent failures (last 24 h)
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    res = await session.execute(
        select(EventLog.event_type, func.count(EventLog.id))
        .where(EventLog.created_at >= since)
        .group_by(EventLog.event_type)
    )
    failures = [
        {"event_type": row[0], "count": row[1]}
        for row in res.fetchall()
    ]

    return {
        "db_ok":      db_ok,
        "redis_ok":   redis_ok,
        "queue_depth": queue_depth,
        "recent_failures_24h": failures,
    }


# ── Metrics ───────────────────────────────────────────────────────────────────

async def compute_metrics(session: AsyncSession) -> dict[str, Any]:
    """
    Compute order count metrics for the Metrics dashboard page.

    Returns
    -------
    dict with keys:
        orders_today   int  — all orders created today (Seoul day boundary)
        pending        int  — RECEIVED + VALIDATED + PLACING
        processing     int  — PLACED (supplier confirmed, awaiting tracking)
        failed         int  — FAILED (all time)
        shipped        int  — SHIPPED (all time)
        canceled       int  — CANCELED (all time)
        total          int  — total orders in DB
    """
    day_start, day_end = _seoul_day_bounds()

    # orders today
    r = await session.execute(
        select(func.count(Order.id)).where(
            Order.created_at >= day_start,
            Order.created_at <  day_end,
        )
    )
    orders_today: int = r.scalar_one() or 0

    # per-status counts (all time)
    status_rows = await session.execute(
        select(Order.status, func.count(Order.id)).group_by(Order.status)
    )
    counts: dict[str, int] = {row[0].value: row[1] for row in status_rows.fetchall()}

    pending    = (counts.get("RECEIVED",  0)
                + counts.get("VALIDATED", 0)
                + counts.get("PLACING",   0))
    processing = counts.get("PLACED",   0)
    failed     = counts.get("FAILED",   0)
    shipped    = counts.get("SHIPPED",  0)
    canceled   = counts.get("CANCELED", 0)
    total      = sum(counts.values())

    return {
        "orders_today": orders_today,
        "pending":      pending,
        "processing":   processing,
        "failed":       failed,
        "shipped":      shipped,
        "canceled":     canceled,
        "total":        total,
    }
