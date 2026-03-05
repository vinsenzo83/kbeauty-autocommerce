from __future__ import annotations

"""
app/workers/tasks_monitoring.py
─────────────────────────────────
Sprint 16 – Celery task for operational KPI monitoring and alerting.

Public tasks
------------
collect_and_alert(window_minutes=60)
    1. Collect KPI snapshot via metrics_service.collect_kpis().
    2. Evaluate alert rules via alert_service.evaluate_alert_rules().
    3. Commit any new alert events.
    4. Return summary dict.

Beat schedule
-------------
Registered when MONITORING_ENABLED=1 (default off).
MONITORING_INTERVAL_SECONDS controls frequency (default 300 = 5 min).
"""

import asyncio
import os
from typing import Any

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
_MONITORING_ENABLED  = os.getenv("MONITORING_ENABLED", "0") == "1"
_MONITORING_INTERVAL = int(os.getenv("MONITORING_INTERVAL_SECONDS", "300"))   # 5 min
_KPI_WINDOW_MINUTES  = int(os.getenv("KPI_WINDOW_MINUTES", "60"))


# ── Async core ────────────────────────────────────────────────────────────────

async def _collect_and_alert_async(window_minutes: int) -> dict[str, Any]:
    from app.db.session import AsyncSessionLocal
    from app.services.metrics_service import collect_kpis
    from app.services.alert_service import evaluate_alert_rules

    session = AsyncSessionLocal()
    try:
        # Collect KPIs
        snapshot = await collect_kpis(session, window_minutes=window_minutes)

        # Evaluate rules
        fired_events = await evaluate_alert_rules(session, snapshot)

        # Commit new alert events
        if fired_events:
            await session.commit()
            logger.warning(
                "monitoring_task.alerts_fired",
                count=len(fired_events),
                rules=[e.rule_name for e in fired_events],
            )

        return {
            "status":              "ok",
            "window_minutes":      window_minutes,
            "kpis":                snapshot.to_dict(),
            "alerts_fired":        len(fired_events),
            "fired_rule_names":    [e.rule_name for e in fired_events],
        }

    except Exception as exc:
        logger.error("monitoring_task.error", error=str(exc), exc_info=True)
        await session.rollback()
        return {"status": "error", "error": str(exc)}
    finally:
        await session.close()


# ── Celery task ────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_monitoring.collect_and_alert",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def collect_and_alert(
    self,
    window_minutes: int = _KPI_WINDOW_MINUTES,
) -> dict[str, Any]:
    """
    Celery task: collect KPI snapshot and evaluate alert rules.

    Parameters
    ----------
    window_minutes : Lookback window for KPI aggregation (default from env KPI_WINDOW_MINUTES)

    Returns
    -------
    Dict with KPI snapshot and fired alert summary.
    """
    logger.info("monitoring_task.start", window_minutes=window_minutes)
    try:
        return asyncio.run(_collect_and_alert_async(window_minutes=window_minutes))
    except Exception as exc:
        logger.error("monitoring_task.unhandled", error=str(exc), exc_info=True)
        raise self.retry(exc=exc)


# ── Beat schedule registration ────────────────────────────────────────────────

if _MONITORING_ENABLED:
    celery_app.conf.beat_schedule["collect-kpis-every-5m"] = {
        "task":     "workers.tasks_monitoring.collect_and_alert",
        "schedule": _MONITORING_INTERVAL,
        "kwargs":   {"window_minutes": _KPI_WINDOW_MINUTES},
        "options":  {"expires": _MONITORING_INTERVAL},
    }
    logger.info(
        "monitoring_task.beat_registered",
        interval_seconds=_MONITORING_INTERVAL,
        window_minutes=_KPI_WINDOW_MINUTES,
    )
