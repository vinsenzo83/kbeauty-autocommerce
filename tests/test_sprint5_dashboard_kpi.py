from __future__ import annotations

"""
tests/test_sprint5_dashboard_kpi.py
─────────────────────────────────────
Sprint 5 tests — KPI computations and Asia/Seoul day boundary.

Uses MagicMock/AsyncMock for DB sessions; no network or real DB.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

SEOUL_TZ = ZoneInfo("Asia/Seoul")


# ── Helpers ───────────────────────────────────────────────────────────────────


class FakeSettings:
    MARGIN_GUARD_PCT    = 15.0
    SUPPLIER_COST_RATIO = 0.75
    REDIS_URL           = "redis://localhost:6379/0"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _today_seoul_start() -> datetime:
    now_seoul = datetime.now(SEOUL_TZ)
    start = now_seoul.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def _make_scalar_result(value):
    """Return a mock that behaves like an SQLAlchemy scalar result."""
    m = MagicMock()
    m.scalar_one = MagicMock(return_value=value)
    m.scalar_one_or_none = MagicMock(return_value=value)
    m.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    m.fetchall = MagicMock(return_value=[])
    return m


def _make_async_session(scalar_values: list):
    """
    Build an AsyncMock session whose execute() returns scalar_values in order.
    Each call to session.execute() returns the next mock result.
    """
    session = AsyncMock()
    results = [_make_scalar_result(v) for v in scalar_values]
    session.execute = AsyncMock(side_effect=results)
    return session


# ══════════════════════════════════════════════════════════════════════════════
# A) Seoul day boundary (pure-logic tests — no DB)
# ══════════════════════════════════════════════════════════════════════════════


class TestSeoulDayBoundary:
    def test_day_bounds_are_utc(self) -> None:
        from app.services.dashboard_service import _seoul_day_bounds
        start, end = _seoul_day_bounds()
        assert start.tzinfo is not None
        assert end.tzinfo   is not None

    def test_day_bounds_span_24h(self) -> None:
        from app.services.dashboard_service import _seoul_day_bounds
        start, end = _seoul_day_bounds()
        delta = end - start
        assert abs(delta.total_seconds() - 86400) < 1

    def test_day_start_is_midnight_seoul(self) -> None:
        from app.services.dashboard_service import _seoul_day_bounds
        start, _ = _seoul_day_bounds()
        start_seoul = start.astimezone(SEOUL_TZ)
        assert start_seoul.hour   == 0
        assert start_seoul.minute == 0
        assert start_seoul.second == 0

    def test_today_start_offset_from_utc(self) -> None:
        """Seoul is UTC+9, so midnight Seoul = 15:00 previous UTC day."""
        from app.services.dashboard_service import _seoul_day_bounds
        start, _ = _seoul_day_bounds()
        assert start.hour == 15


# ══════════════════════════════════════════════════════════════════════════════
# B) KPI computations (mocked DB)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_kpi_empty_db() -> None:
    """All KPIs should be 0 / 0.0 for an empty database."""
    from app.services.dashboard_service import compute_kpi

    session = AsyncMock()
    # 5 scalar queries: orders_today, revenue_today, failed_today,
    # tracking_stale_count, open_tickets_count
    session.execute = AsyncMock(side_effect=[
        _make_scalar_result(0),    # orders_today
        _make_scalar_result(None), # revenue_today
        _make_scalar_result(0),    # failed_today
        _make_scalar_result(0),    # tracking_stale_count
        _make_scalar_result(0),    # open_tickets_count
    ])

    kpi = await compute_kpi(session, FakeSettings())
    assert kpi["orders_today"]         == 0
    assert kpi["revenue_today"]        == 0.0
    assert kpi["avg_margin_pct"]       == 0.0
    assert kpi["failed_today"]         == 0
    assert kpi["tracking_stale_count"] == 0
    assert kpi["open_tickets_count"]   == 0


@pytest.mark.asyncio
async def test_kpi_orders_today_counts() -> None:
    from app.services.dashboard_service import compute_kpi

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        _make_scalar_result(3),       # orders_today = 3
        _make_scalar_result(Decimal("150000.00")),  # revenue
        _make_scalar_result(0),       # failed
        _make_scalar_result(0),       # stale
        _make_scalar_result(0),       # open tickets
    ])

    kpi = await compute_kpi(session, FakeSettings())
    assert kpi["orders_today"] == 3


@pytest.mark.asyncio
async def test_kpi_revenue_paid_only() -> None:
    from app.services.dashboard_service import compute_kpi

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        _make_scalar_result(1),
        _make_scalar_result(Decimal("100.00")),  # only paid revenue
        _make_scalar_result(0),
        _make_scalar_result(0),
        _make_scalar_result(0),
    ])

    kpi = await compute_kpi(session, FakeSettings())
    assert kpi["revenue_today"] == pytest.approx(100.0)
    assert kpi["avg_margin_pct"] > 0.0  # cost_ratio=0.75 → margin=25%


@pytest.mark.asyncio
async def test_kpi_failed_today() -> None:
    from app.services.dashboard_service import compute_kpi

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        _make_scalar_result(5),      # orders_today
        _make_scalar_result(Decimal("250.00")),
        _make_scalar_result(3),      # failed_today = 3
        _make_scalar_result(0),
        _make_scalar_result(0),
    ])

    kpi = await compute_kpi(session, FakeSettings())
    assert kpi["failed_today"] == 3


@pytest.mark.asyncio
async def test_kpi_tracking_stale() -> None:
    from app.services.dashboard_service import compute_kpi

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        _make_scalar_result(0),
        _make_scalar_result(None),
        _make_scalar_result(0),
        _make_scalar_result(2),      # tracking_stale_count = 2
        _make_scalar_result(0),
    ])

    kpi = await compute_kpi(session, FakeSettings())
    assert kpi["tracking_stale_count"] == 2


@pytest.mark.asyncio
async def test_kpi_open_tickets() -> None:
    from app.services.dashboard_service import compute_kpi

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        _make_scalar_result(0),
        _make_scalar_result(None),
        _make_scalar_result(0),
        _make_scalar_result(0),
        _make_scalar_result(4),      # open_tickets_count = 4
    ])

    kpi = await compute_kpi(session, FakeSettings())
    assert kpi["open_tickets_count"] == 4


# ══════════════════════════════════════════════════════════════════════════════
# C) Alerts (mocked DB + mocked Redis)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_alerts_tracking_stale_list() -> None:
    from app.services.dashboard_service import compute_alerts

    # Build a fake stale order object
    stale_at = _now_utc() - timedelta(hours=30)
    fake_order = MagicMock()
    fake_order.id              = "uuid-stale-001"
    fake_order.placed_at       = stale_at
    fake_order.supplier_order_id = "SK-001"

    # stale list result
    stale_result = MagicMock()
    stale_result.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[fake_order]))
    )
    # failures result
    fail_result = _make_scalar_result(0)

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[stale_result, fail_result])

    with patch("app.services.dashboard_service.redis") as mock_redis_mod:
        mock_redis_mod.from_url.side_effect = Exception("no redis in test")
        alerts = await compute_alerts(session, FakeSettings())

    assert len(alerts["tracking_stale"]) == 1
    assert alerts["tracking_stale"][0]["supplier_order_id"] == "SK-001"


@pytest.mark.asyncio
async def test_alerts_queue_backlog_unavailable_returns_none() -> None:
    """When Redis is unreachable, queue_backlog must be None (not raise)."""
    from app.services.dashboard_service import compute_alerts

    stale_result = MagicMock()
    stale_result.scalars = MagicMock(
        return_value=MagicMock(all=MagicMock(return_value=[]))
    )
    fail_result = _make_scalar_result(0)

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[stale_result, fail_result])

    with patch("app.services.dashboard_service.redis") as mock_redis_mod:
        mock_redis_mod.from_url.side_effect = Exception("Redis unavailable")
        alerts = await compute_alerts(session, FakeSettings())

    assert alerts["queue_backlog"] is None


# ══════════════════════════════════════════════════════════════════════════════
# D) Chart data (mocked DB)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_orders_chart_returns_n_days() -> None:
    from app.services.dashboard_service import get_orders_chart

    # 7 days × 2 queries each = 14 execute calls
    side_effects = []
    for _ in range(7):
        side_effects.append(_make_scalar_result(0))   # count
        side_effects.append(_make_scalar_result(None)) # revenue

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=side_effects)

    data = await get_orders_chart(session, days=7)
    assert len(data) == 7
    for row in data:
        assert "date"    in row
        assert "orders"  in row
        assert "revenue" in row


# ══════════════════════════════════════════════════════════════════════════════
# E) Margin computation (pure-logic)
# ══════════════════════════════════════════════════════════════════════════════


class TestMarginComputation:
    def test_margin_pct_from_cost_ratio(self) -> None:
        cost_ratio = 0.75
        margin     = round((1.0 - cost_ratio) * 100, 2)
        assert margin == pytest.approx(25.0)

    def test_margin_guard_triggered(self) -> None:
        margin_guard = 30.0
        cost_ratio   = 0.75
        order_margin = round((1.0 - cost_ratio) * 100, 2)
        assert order_margin < margin_guard

    def test_margin_guard_not_triggered(self) -> None:
        margin_guard = 20.0
        cost_ratio   = 0.75
        order_margin = round((1.0 - cost_ratio) * 100, 2)
        assert order_margin >= margin_guard
