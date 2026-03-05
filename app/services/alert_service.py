from __future__ import annotations

"""
app/services/alert_service.py
───────────────────────────────
Sprint 16 – Alert rule evaluation and event management.

Public API
----------
    fired = await evaluate_alert_rules(session, kpi_snapshot)
    # Returns list[AlertEvent] — newly fired events

    await acknowledge_alert(session, alert_event_id)
    await resolve_alert(session, alert_event_id)
    await list_open_alerts(session, limit=50) -> list[dict]
    await create_alert_rule(session, rule_data) -> AlertRule

Algorithm
---------
1. Load all enabled AlertRule rows.
2. For each rule, look up the matching metric value in KpiSnapshot.
3. Evaluate: observed_value {operator} threshold
4. If condition is True AND no open alert for this rule exists → fire new AlertEvent.
5. If condition is False AND open alert exists → auto-resolve.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertRule, AlertEvent, AlertSeverity, AlertEventStatus
from app.services.metrics_service import KpiSnapshot

logger = structlog.get_logger(__name__)


# ── Operator evaluation ───────────────────────────────────────────────────────

def _evaluate(observed: float, operator: str, threshold: float) -> bool:
    """Return True if the alert condition is met."""
    ops = {
        ">":  observed >  threshold,
        ">=": observed >= threshold,
        "<":  observed <  threshold,
        "<=": observed <= threshold,
        "==": observed == threshold,
    }
    return ops.get(operator, False)


# ── KPI lookup ────────────────────────────────────────────────────────────────

def _get_metric_value(snapshot: KpiSnapshot, metric: str) -> float | None:
    """Extract a named metric value from the KPI snapshot."""
    mapping: dict[str, float] = {
        "total_order_count":         float(snapshot.total_order_count),
        "pending_order_count":       float(snapshot.pending_order_count),
        "order_error_rate":          snapshot.order_error_rate,
        "fulfillment_error_rate":    snapshot.fulfillment_error_rate,
        "fulfillment_error_count":   float(snapshot.fulfillment_error_count),
        "supplier_order_count":      float(snapshot.supplier_order_count),
        "repricing_run_count":       float(snapshot.repricing_run_count),
        "repricing_updated_count":   float(snapshot.repricing_updated_count),
        "repricing_error_count":     float(snapshot.repricing_error_count),
        "publish_job_count":         float(snapshot.publish_job_count),
        "publish_success_count":     float(snapshot.publish_success_count),
        "publish_failure_count":     float(snapshot.publish_failure_count),
        "discovery_candidate_count": float(snapshot.discovery_candidate_count),
        "market_price_count":        float(snapshot.market_price_count),
    }
    return mapping.get(metric)


# ── Main evaluation function ──────────────────────────────────────────────────

async def evaluate_alert_rules(
    session: AsyncSession,
    snapshot: KpiSnapshot,
) -> list[AlertEvent]:
    """
    Evaluate all enabled alert rules against the current KPI snapshot.

    Returns
    -------
    List of newly fired AlertEvent rows (already added to session, not committed).
    """
    # Load all enabled rules
    rules = (await session.execute(
        select(AlertRule).where(AlertRule.enabled == True)  # noqa: E712
    )).scalars().all()

    fired: list[AlertEvent] = []

    for rule in rules:
        observed = _get_metric_value(snapshot, rule.metric)
        if observed is None:
            logger.debug("alert_service.unknown_metric", metric=rule.metric)
            continue

        threshold = float(rule.threshold)
        condition_met = _evaluate(observed, rule.operator, threshold)

        # Check if there's already an open alert for this rule
        existing_open = (await session.execute(
            select(AlertEvent).where(
                AlertEvent.rule_id == rule.id,
                AlertEvent.status == AlertEventStatus.OPEN,
            ).limit(1)
        )).scalar_one_or_none()

        if condition_met and existing_open is None:
            # Fire new alert
            event = AlertEvent(
                id             = uuid.uuid4(),
                rule_id        = rule.id,
                rule_name      = rule.name,
                metric         = rule.metric,
                observed_value = observed,
                threshold      = threshold,
                severity       = rule.severity,
                status         = AlertEventStatus.OPEN,
                notes          = (
                    f"Rule '{rule.name}': "
                    f"observed {rule.metric}={observed:.4f} "
                    f"{rule.operator} threshold={threshold:.4f}"
                ),
            )
            session.add(event)
            fired.append(event)
            logger.warning(
                "alert_service.fired",
                rule=rule.name,
                metric=rule.metric,
                observed=observed,
                threshold=threshold,
                severity=rule.severity,
            )

        elif not condition_met and existing_open is not None:
            # Auto-resolve: condition is no longer met
            existing_open.status      = AlertEventStatus.RESOLVED  # type: ignore[assignment]
            existing_open.resolved_at = datetime.now(tz=timezone.utc)  # type: ignore[assignment]
            existing_open.notes       = (  # type: ignore[assignment]
                (existing_open.notes or "") +
                f" | Auto-resolved: {rule.metric}={observed:.4f} no longer {rule.operator} {threshold:.4f}"
            )
            session.add(existing_open)
            logger.info(
                "alert_service.auto_resolved",
                rule=rule.name,
                metric=rule.metric,
                observed=observed,
            )

    return fired


# ── Alert management helpers ──────────────────────────────────────────────────

async def acknowledge_alert(
    session: AsyncSession,
    alert_event_id: str,
) -> AlertEvent | None:
    """Mark an open alert as acknowledged."""
    event = (await session.execute(
        select(AlertEvent).where(AlertEvent.id == alert_event_id).limit(1)
    )).scalar_one_or_none()

    if event is None:
        return None
    if event.status == AlertEventStatus.OPEN:
        event.status = AlertEventStatus.ACKNOWLEDGED  # type: ignore[assignment]
        session.add(event)
    return event


async def resolve_alert(
    session: AsyncSession,
    alert_event_id: str,
    notes: str | None = None,
) -> AlertEvent | None:
    """Mark an alert as resolved."""
    event = (await session.execute(
        select(AlertEvent).where(AlertEvent.id == alert_event_id).limit(1)
    )).scalar_one_or_none()

    if event is None:
        return None
    event.status      = AlertEventStatus.RESOLVED   # type: ignore[assignment]
    event.resolved_at = datetime.now(tz=timezone.utc)  # type: ignore[assignment]
    if notes:
        event.notes = (event.notes or "") + f" | {notes}"  # type: ignore[assignment]
    session.add(event)
    return event


async def list_open_alerts(
    session: AsyncSession,
    limit: int = 50,
    severity: str | None = None,
) -> list[dict[str, Any]]:
    """Return open (and acknowledged) alert events sorted by fired_at DESC."""
    q = select(AlertEvent).where(
        AlertEvent.status.in_([AlertEventStatus.OPEN, AlertEventStatus.ACKNOWLEDGED])
    ).order_by(AlertEvent.fired_at.desc()).limit(limit)

    if severity:
        q = q.where(AlertEvent.severity == severity)

    rows = (await session.execute(q)).scalars().all()
    return [_event_to_dict(r) for r in rows]


async def list_all_alerts(
    session: AsyncSession,
    limit: int = 100,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Return alert events with optional status filter."""
    q = select(AlertEvent).order_by(AlertEvent.fired_at.desc()).limit(limit)
    if status:
        q = q.where(AlertEvent.status == status)
    rows = (await session.execute(q)).scalars().all()
    return [_event_to_dict(r) for r in rows]


async def create_alert_rule(
    session: AsyncSession,
    name: str,
    metric: str,
    operator: str,
    threshold: float,
    window_minutes: int = 60,
    severity: str = AlertSeverity.WARNING,
    notes: str | None = None,
) -> AlertRule:
    """Create a new alert rule."""
    rule = AlertRule(
        id             = uuid.uuid4(),
        name           = name,
        metric         = metric,
        operator       = operator,
        threshold      = threshold,
        window_minutes = str(window_minutes),
        severity       = severity,
        enabled        = True,
        notes          = notes,
    )
    session.add(rule)
    return rule


def _event_to_dict(e: AlertEvent) -> dict[str, Any]:
    return {
        "id":             str(e.id),
        "rule_id":        str(e.rule_id),
        "rule_name":      e.rule_name,
        "metric":         e.metric,
        "observed_value": float(e.observed_value),
        "threshold":      float(e.threshold),
        "severity":       e.severity,
        "status":         e.status,
        "notes":          e.notes,
        "fired_at":       e.fired_at.isoformat() if e.fired_at else None,
        "resolved_at":    e.resolved_at.isoformat() if e.resolved_at else None,
    }
