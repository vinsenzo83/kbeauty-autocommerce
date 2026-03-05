from __future__ import annotations

"""
app/models/alert.py
────────────────────
Sprint 16 – ORM models for operational alerting.

Tables
------
alert_rules   : Configurable threshold rules for KPI metrics.
alert_events  : Fired alert events when a rule threshold is breached.

Severity levels
---------------
info     – informational, no action required
warning  – should investigate
critical – requires immediate attention

Alert event status lifecycle
-----------------------------
open → acknowledged → resolved
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ── Status / severity constants ───────────────────────────────────────────────

class AlertSeverity:
    INFO     = "info"
    WARNING  = "warning"
    CRITICAL = "critical"


class AlertEventStatus:
    OPEN         = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED     = "resolved"


# ── AlertRule ─────────────────────────────────────────────────────────────────

class AlertRule(Base):
    """
    Defines a threshold-based alerting rule for an operational KPI.

    Columns
    -------
    id              : UUID primary key
    name            : Unique human-readable name, e.g. 'high_error_rate'
    metric          : KPI key the rule monitors, e.g. 'fulfillment_error_rate'
    operator        : Comparison operator: '>', '>=', '<', '<=', '=='
    threshold       : Numeric threshold value
    window_minutes  : Evaluation time window in minutes
    severity        : info | warning | critical
    enabled         : Whether the rule is active
    notes           : Optional description / runbook link
    created_at / updated_at : Audit timestamps
    """

    __tablename__ = "alert_rules"
    __allow_unmapped__ = True

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name           = Column(String(128), nullable=False, unique=True)
    metric         = Column(String(64),  nullable=False, index=True)
    operator       = Column(String(8),   nullable=False)   # >, >=, <, <=, ==
    threshold      = Column(Numeric(18, 6), nullable=False)
    window_minutes = Column(String(16),  nullable=False, default="60")  # stored as str for flexibility
    severity       = Column(String(16),  nullable=False, default=AlertSeverity.WARNING)
    enabled        = Column(Boolean,     nullable=False, default=True)
    notes          = Column(Text,        nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<AlertRule name={self.name!r} metric={self.metric!r} "
            f"{self.operator}{self.threshold} severity={self.severity!r}>"
        )


# ── AlertEvent ────────────────────────────────────────────────────────────────

class AlertEvent(Base):
    """
    Fired event when an AlertRule threshold is breached.

    Columns
    -------
    id              : UUID primary key
    rule_id         : Soft FK → alert_rules.id
    rule_name       : Denormalised rule name for quick reads
    metric          : KPI key (denormalised from rule)
    observed_value  : Actual metric value that triggered the alert
    threshold       : Rule threshold at time of firing
    severity        : Copied from rule at time of firing
    status          : open | acknowledged | resolved
    notes           : Optional context / operator notes
    fired_at        : When the alert was triggered
    resolved_at     : When the alert was resolved (nullable)
    created_at      : Audit timestamp
    """

    __tablename__ = "alert_events"
    __allow_unmapped__ = True

    id             = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id        = Column(UUID(as_uuid=True), nullable=False, index=True)
    rule_name      = Column(String(128), nullable=False)
    metric         = Column(String(64),  nullable=False)
    observed_value = Column(Numeric(18, 6), nullable=False)
    threshold      = Column(Numeric(18, 6), nullable=False)
    severity       = Column(String(16),  nullable=False)
    status         = Column(String(16),  nullable=False, default=AlertEventStatus.OPEN)
    notes          = Column(Text,        nullable=True)

    fired_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<AlertEvent rule={self.rule_name!r} metric={self.metric!r} "
            f"observed={self.observed_value} status={self.status!r}>"
        )
