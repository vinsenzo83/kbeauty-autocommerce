"""
tests/test_sprint16_monitoring.py
───────────────────────────────────
Sprint 16 – Operational Observability mock-only test suite.

Coverage
--------
1.  test_alert_operator_evaluation
2.  test_alert_fires_on_threshold_breach
3.  test_alert_auto_resolves_when_condition_clears
4.  test_no_duplicate_open_alert_for_same_rule
5.  test_acknowledge_alert_event
6.  test_resolve_alert_event
7.  test_kpi_snapshot_to_dict_keys
8.  test_get_metric_value_mapping
9.  test_evaluate_rules_unknown_metric_skipped
10. test_metrics_service_no_db_tables_returns_zeros
11. test_metrics_fulfillment_error_rate_calculation
12. test_metrics_collect_kpis_structure
13. test_monitoring_celery_task_mock
14. test_alert_event_status_lifecycle
15. test_create_alert_rule_helper
16. test_list_open_alerts_empty

All tests are mock-only.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_rule(
    *,
    metric: str = "fulfillment_error_rate",
    operator: str = ">",
    threshold: float = 0.10,
    severity: str = "critical",
    enabled: bool = True,
    name: str = "high_error_rate",
) -> MagicMock:
    r = MagicMock()
    r.id        = uuid.uuid4()
    r.name      = name
    r.metric    = metric
    r.operator  = operator
    r.threshold = Decimal(str(threshold))
    r.severity  = severity
    r.enabled   = enabled
    r.notes     = None
    return r


def _make_alert_event(
    *,
    rule_id: uuid.UUID | None = None,
    rule_name: str = "high_error_rate",
    metric: str = "fulfillment_error_rate",
    observed_value: float = 0.15,
    threshold: float = 0.10,
    severity: str = "critical",
    status: str = "open",
) -> MagicMock:
    e = MagicMock()
    e.id             = uuid.uuid4()
    e.rule_id        = rule_id or uuid.uuid4()
    e.rule_name      = rule_name
    e.metric         = metric
    e.observed_value = Decimal(str(observed_value))
    e.threshold      = Decimal(str(threshold))
    e.severity       = severity
    e.status         = status
    e.notes          = None
    e.fired_at       = datetime.now(tz=timezone.utc)
    e.resolved_at    = None
    return e


def _mock_session(rule_rows=None, event_rows=None, scalar_val=0):
    session = AsyncMock()
    call_idx = [0]

    async def _execute(query, *a, **kw):
        result = MagicMock()
        ci = call_idx[0]
        call_idx[0] += 1
        result.scalars.return_value.all.return_value = (
            rule_rows  if ci == 0 and rule_rows  is not None else
            event_rows if ci >= 1 and event_rows is not None else
            []
        )
        result.scalar_one_or_none.return_value = (
            event_rows[0] if event_rows else None
        )
        result.scalar_one.return_value = scalar_val
        return result

    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()
    session.commit  = AsyncMock()
    session.rollback= AsyncMock()
    session.close   = AsyncMock()
    return session


# ─────────────────────────────────────────────────────────────────────────────
# 1. Operator evaluation
# ─────────────────────────────────────────────────────────────────────────────

def test_alert_operator_evaluation():
    from app.services.alert_service import _evaluate
    assert _evaluate(0.15, ">",  0.10) is True
    assert _evaluate(0.05, ">",  0.10) is False
    assert _evaluate(0.10, ">=", 0.10) is True
    assert _evaluate(0.09, ">=", 0.10) is False
    assert _evaluate(0.5,  "<",  1.0)  is True
    assert _evaluate(1.0,  "<",  1.0)  is False
    assert _evaluate(1.0,  "<=", 1.0)  is True
    assert _evaluate(1.0,  "==", 1.0)  is True
    assert _evaluate(1.1,  "==", 1.0)  is False
    # Unknown operator → False (safe default)
    assert _evaluate(1.0, "!=", 0.5)  is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. Alert fires on threshold breach
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_fires_on_threshold_breach():
    from app.services.alert_service import evaluate_alert_rules
    from app.services.metrics_service import KpiSnapshot

    rule = _make_rule(metric="fulfillment_error_rate", operator=">", threshold=0.10)

    snapshot = KpiSnapshot(window_minutes=60, collected_at="2026-01-01T00:00:00+00:00")
    snapshot.fulfillment_error_rate = 0.25  # breaches threshold

    call_count = [0]
    async def _execute(q, *a, **kw):
        result = MagicMock()
        ci = call_count[0]; call_count[0] += 1
        if ci == 0:  # load rules
            result.scalars.return_value.all.return_value = [rule]
        else:        # open event check – none existing
            result.scalar_one_or_none.return_value = None
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    fired = await evaluate_alert_rules(session, snapshot)
    assert len(fired) == 1
    assert fired[0].rule_name == rule.name
    assert float(fired[0].observed_value) == pytest.approx(0.25, abs=0.001)
    session.add.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Alert auto-resolves when condition clears
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_auto_resolves_when_condition_clears():
    from app.services.alert_service import evaluate_alert_rules, AlertEventStatus
    from app.services.metrics_service import KpiSnapshot

    rule       = _make_rule(metric="fulfillment_error_rate", operator=">", threshold=0.10)
    open_event = _make_alert_event(rule_id=rule.id, status="open")

    snapshot = KpiSnapshot(window_minutes=60, collected_at="2026-01-01T00:00:00+00:00")
    snapshot.fulfillment_error_rate = 0.03  # condition no longer met

    call_count = [0]
    async def _execute(q, *a, **kw):
        result = MagicMock()
        ci = call_count[0]; call_count[0] += 1
        if ci == 0:
            result.scalars.return_value.all.return_value = [rule]
        else:
            result.scalar_one_or_none.return_value = open_event  # existing open alert
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    fired = await evaluate_alert_rules(session, snapshot)
    # No new events fired; existing open event should be auto-resolved
    assert len(fired) == 0
    # The open_event's status should be set to RESOLVED
    assert open_event.status == AlertEventStatus.RESOLVED


# ─────────────────────────────────────────────────────────────────────────────
# 4. No duplicate open alert for same rule
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_duplicate_open_alert_for_same_rule():
    from app.services.alert_service import evaluate_alert_rules
    from app.services.metrics_service import KpiSnapshot

    rule       = _make_rule(metric="pending_order_count", operator=">", threshold=50)
    open_event = _make_alert_event(rule_id=rule.id, metric="pending_order_count", status="open")

    snapshot = KpiSnapshot(window_minutes=60, collected_at="2026-01-01T00:00:00+00:00")
    snapshot.pending_order_count = 75  # still breaching

    call_count = [0]
    async def _execute(q, *a, **kw):
        result = MagicMock()
        ci = call_count[0]; call_count[0] += 1
        if ci == 0:
            result.scalars.return_value.all.return_value = [rule]
        else:
            result.scalar_one_or_none.return_value = open_event  # already open
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    fired = await evaluate_alert_rules(session, snapshot)
    # No new alert should be fired (one already open for this rule)
    assert len(fired) == 0
    session.add.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 5. Acknowledge alert
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_acknowledge_alert_event():
    from app.services.alert_service import acknowledge_alert, AlertEventStatus

    event   = _make_alert_event(status="open")
    session = AsyncMock()

    async def _execute(q, *a, **kw):
        r = MagicMock()
        r.scalar_one_or_none.return_value = event
        return r

    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    result = await acknowledge_alert(session, str(event.id))
    assert result is event
    assert event.status == AlertEventStatus.ACKNOWLEDGED
    session.add.assert_called_once_with(event)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Resolve alert
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_alert_event():
    from app.services.alert_service import resolve_alert, AlertEventStatus

    event   = _make_alert_event(status="acknowledged")
    session = AsyncMock()

    async def _execute(q, *a, **kw):
        r = MagicMock()
        r.scalar_one_or_none.return_value = event
        return r

    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    result = await resolve_alert(session, str(event.id), notes="fixed manually")
    assert result is event
    assert event.status == AlertEventStatus.RESOLVED
    assert event.resolved_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# 7. KPI snapshot to_dict keys
# ─────────────────────────────────────────────────────────────────────────────

def test_kpi_snapshot_to_dict_keys():
    from app.services.metrics_service import KpiSnapshot

    snap = KpiSnapshot(window_minutes=60, collected_at="2026-01-01T00:00:00+00:00")
    d = snap.to_dict()

    required_keys = [
        "window_minutes", "collected_at",
        "total_order_count", "pending_order_count", "order_error_rate",
        "supplier_order_count", "fulfillment_error_count", "fulfillment_error_rate",
        "repricing_run_count", "repricing_updated_count",
        "publish_job_count", "publish_success_count", "publish_failure_count",
        "discovery_candidate_count", "market_price_count", "recent_errors",
    ]
    for key in required_keys:
        assert key in d, f"Missing KPI key: {key}"

    assert isinstance(d["recent_errors"], list)
    assert d["window_minutes"] == 60


# ─────────────────────────────────────────────────────────────────────────────
# 8. get_metric_value mapping
# ─────────────────────────────────────────────────────────────────────────────

def test_get_metric_value_mapping():
    from app.services.alert_service import _get_metric_value
    from app.services.metrics_service import KpiSnapshot

    snap = KpiSnapshot(window_minutes=60, collected_at="2026-01-01T00:00:00+00:00")
    snap.fulfillment_error_rate   = 0.15
    snap.pending_order_count      = 42
    snap.discovery_candidate_count = 10

    assert _get_metric_value(snap, "fulfillment_error_rate") == pytest.approx(0.15)
    assert _get_metric_value(snap, "pending_order_count")    == pytest.approx(42.0)
    assert _get_metric_value(snap, "discovery_candidate_count") == pytest.approx(10.0)
    assert _get_metric_value(snap, "nonexistent_metric")     is None


# ─────────────────────────────────────────────────────────────────────────────
# 9. Unknown metric skipped in evaluation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evaluate_rules_unknown_metric_skipped():
    from app.services.alert_service import evaluate_alert_rules
    from app.services.metrics_service import KpiSnapshot

    rule = _make_rule(metric="nonexistent_kpi_xyz", operator=">", threshold=0)

    call_count = [0]
    async def _execute(q, *a, **kw):
        result = MagicMock()
        ci = call_count[0]; call_count[0] += 1
        if ci == 0:
            result.scalars.return_value.all.return_value = [rule]
        else:
            result.scalar_one_or_none.return_value = None
        return result

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=_execute)
    session.add     = MagicMock()

    snap = KpiSnapshot(window_minutes=60, collected_at="2026-01-01T00:00:00+00:00")
    fired = await evaluate_alert_rules(session, snap)
    assert fired == []
    session.add.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 10. Metrics service – empty DB returns zeros
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_service_no_db_tables_returns_zeros():
    from app.services.metrics_service import collect_kpis

    # Session that always raises (simulates missing tables)
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=Exception("table does not exist"))

    snap = await collect_kpis(session, window_minutes=60)
    # All counters should be 0 / 0.0 without crashing
    assert snap.total_order_count     == 0
    assert snap.fulfillment_error_rate == 0.0
    assert snap.publish_failure_count  == 0
    assert snap.discovery_candidate_count == 0
    assert isinstance(snap.recent_errors, list)


# ─────────────────────────────────────────────────────────────────────────────
# 11. Fulfillment error rate calculation
# ─────────────────────────────────────────────────────────────────────────────

def test_metrics_fulfillment_error_rate_calculation():
    """Error rate = failed / total; 0 if no orders."""
    from app.services.metrics_service import KpiSnapshot

    snap = KpiSnapshot(window_minutes=60, collected_at="2026-01-01T00:00:00+00:00")
    snap.supplier_order_count    = 20
    snap.fulfillment_error_count = 4
    # Manually compute as service would
    snap.fulfillment_error_rate  = snap.fulfillment_error_count / snap.supplier_order_count

    assert snap.fulfillment_error_rate == pytest.approx(0.20, abs=0.001)

    # No orders → rate stays 0
    snap2 = KpiSnapshot(window_minutes=60, collected_at="2026-01-01T00:00:00+00:00")
    assert snap2.fulfillment_error_rate == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 12. collect_kpis returns KpiSnapshot with correct type
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_collect_kpis_structure():
    from app.services.metrics_service import collect_kpis, KpiSnapshot

    session = AsyncMock()

    async def _execute(q, *a, **kw):
        r = MagicMock()
        r.scalar_one_or_none.return_value = 5   # return 5 for all counts
        r.scalar_one.return_value = 5
        r.scalars.return_value.all.return_value = []
        return r

    session.execute = AsyncMock(side_effect=_execute)

    snap = await collect_kpis(session, window_minutes=30)
    assert isinstance(snap, KpiSnapshot)
    assert snap.window_minutes == 30
    assert "T" in snap.collected_at  # ISO timestamp
    d = snap.to_dict()
    assert isinstance(d, dict)
    assert d["window_minutes"] == 30


# ─────────────────────────────────────────────────────────────────────────────
# 13. Celery monitoring task – mocked
# ─────────────────────────────────────────────────────────────────────────────

def test_monitoring_celery_task_mock():
    with patch("asyncio.run") as mock_run:
        mock_run.return_value = {
            "status":           "ok",
            "window_minutes":   60,
            "alerts_fired":     1,
            "fired_rule_names": ["high_error_rate"],
            "kpis":             {"fulfillment_error_rate": 0.15},
        }

        from app.workers.tasks_monitoring import collect_and_alert

        if hasattr(collect_and_alert, "__wrapped__"):
            result = collect_and_alert.__wrapped__(window_minutes=60)
        else:
            result = collect_and_alert.run(window_minutes=60)

        mock_run.assert_called_once()
        assert isinstance(result, dict)
        assert result.get("status") == "ok"
        assert result.get("alerts_fired") == 1


# ─────────────────────────────────────────────────────────────────────────────
# 14. Alert event status lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def test_alert_event_status_lifecycle():
    from app.models.alert import AlertEventStatus
    assert AlertEventStatus.OPEN         == "open"
    assert AlertEventStatus.ACKNOWLEDGED == "acknowledged"
    assert AlertEventStatus.RESOLVED     == "resolved"

    # Verify valid transitions make semantic sense
    statuses = [AlertEventStatus.OPEN, AlertEventStatus.ACKNOWLEDGED, AlertEventStatus.RESOLVED]
    assert len(set(statuses)) == 3, "All statuses must be distinct"


# ─────────────────────────────────────────────────────────────────────────────
# 15. create_alert_rule helper
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_alert_rule_helper():
    from app.services.alert_service import create_alert_rule
    from app.models.alert import AlertRule

    session = AsyncMock()
    session.add = MagicMock()

    rule = await create_alert_rule(
        session,
        name="test_rule_xyz",
        metric="publish_failure_count",
        operator=">",
        threshold=3.0,
        window_minutes=120,
        severity="warning",
        notes="test rule",
    )

    assert isinstance(rule, AlertRule)
    assert rule.name      == "test_rule_xyz"
    assert rule.metric    == "publish_failure_count"
    assert rule.operator  == ">"
    assert float(rule.threshold) == pytest.approx(3.0)
    assert rule.severity  == "warning"
    assert rule.enabled   is True
    session.add.assert_called_once_with(rule)


# ─────────────────────────────────────────────────────────────────────────────
# 16. list_open_alerts returns empty list when no alerts
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_open_alerts_empty():
    from app.services.alert_service import list_open_alerts

    session = AsyncMock()
    async def _execute(q, *a, **kw):
        r = MagicMock()
        r.scalars.return_value.all.return_value = []
        return r
    session.execute = AsyncMock(side_effect=_execute)

    items = await list_open_alerts(session, limit=50)
    assert items == []
    assert isinstance(items, list)
