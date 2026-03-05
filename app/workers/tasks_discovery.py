from __future__ import annotations

"""
app/workers/tasks_discovery.py
────────────────────────────────
Sprint 15 – Celery task for the AI Product Discovery pipeline.

Public tasks
------------
run_discovery_pipeline(dry_run=False, top_n=50)
    1. Acquire Redis lock 'discovery:pipeline' (TTL 30 min) to prevent overlap.
    2. Call discovery_service.run_product_discovery(session, dry_run, top_n).
    3. Commit the session.
    4. Return summary dict.

Beat schedule
-------------
Registered conditionally:
    DISCOVERY_ENABLED=1         – enable (default off)
    DISCOVERY_CRON=0 2 * * *   – cron string (default: 02:00 daily)

Override via environment variables to match production schedule.
"""

import asyncio
import os
from typing import Any

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
_DISCOVERY_ENABLED = os.getenv("DISCOVERY_ENABLED", "0") == "1"
_DISCOVERY_CRON    = os.getenv("DISCOVERY_CRON", "0 2 * * *")   # daily at 02:00 KST
_LOCK_TTL          = int(os.getenv("DISCOVERY_LOCK_TTL", str(30 * 60)))  # 30 min
_LOCK_KEY          = "discovery:pipeline"
_DEFAULT_TOP_N     = 50


# ── Redis lock helper ─────────────────────────────────────────────────────────

async def _acquire_lock(redis_url: str, ttl: int) -> bool:
    """
    Try to SET NX EX lock in Redis.
    Returns True if lock was acquired, False otherwise.
    """
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(redis_url, socket_connect_timeout=3)
        acquired = await client.set(_LOCK_KEY, "1", nx=True, ex=ttl)
        await client.aclose()
        return bool(acquired)
    except Exception as exc:
        logger.warning("discovery_task.lock_error", error=str(exc))
        return True  # Proceed without lock if Redis unavailable


async def _release_lock(redis_url: str) -> None:
    """Release the discovery pipeline lock."""
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(redis_url, socket_connect_timeout=3)
        await client.delete(_LOCK_KEY)
        await client.aclose()
    except Exception as exc:
        logger.warning("discovery_task.release_lock_error", error=str(exc))


# ── Async core ────────────────────────────────────────────────────────────────

async def _run_discovery_async(
    dry_run: bool,
    top_n: int,
) -> dict[str, Any]:
    from app.config import get_settings
    from app.db.session import AsyncSessionLocal
    from app.services.discovery_service import run_product_discovery

    settings = get_settings()
    redis_url = settings.REDIS_URL

    # Acquire lock
    acquired = await _acquire_lock(redis_url, _LOCK_TTL)
    if not acquired:
        logger.warning("discovery_task.skipped_concurrent_lock")
        return {"status": "skipped", "reason": "concurrent_lock"}

    session = AsyncSessionLocal()
    try:
        result = await run_product_discovery(session, dry_run=dry_run, top_n=top_n)

        if not dry_run:
            await session.commit()
            logger.info("discovery_task.committed")

        return {
            "status":               "ok",
            "dry_run":              dry_run,
            "signals_collected":    result.signals_collected,
            "signals_matched":      result.signals_matched,
            "candidates_created":   result.candidates_created,
            "candidates_updated":   result.candidates_updated,
            "candidates_rejected":  result.candidates_rejected,
            "errors":               result.errors,
            "top_count":            len(result.top_candidates),
        }
    except Exception as exc:
        logger.error("discovery_task.error", error=str(exc), exc_info=True)
        await session.rollback()
        return {"status": "error", "error": str(exc)}
    finally:
        await session.close()
        if not dry_run:
            await _release_lock(redis_url)


# ── Celery task ────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_discovery.run_discovery_pipeline",
    bind=True,
    max_retries=2,
    default_retry_delay=300,   # 5 min retry delay
)
def run_discovery_pipeline(
    self,
    dry_run: bool = False,
    top_n: int = _DEFAULT_TOP_N,
) -> dict[str, Any]:
    """
    Celery task: run the AI product discovery pipeline.

    Parameters
    ----------
    dry_run : bool
        If True, collect and score but do not write to the database.
    top_n   : int
        Maximum number of candidates to keep (default 50).

    Returns
    -------
    Summary dict with pipeline statistics.
    """
    logger.info("discovery_task.start", dry_run=dry_run, top_n=top_n)
    try:
        return asyncio.run(_run_discovery_async(dry_run=dry_run, top_n=top_n))
    except Exception as exc:
        logger.error("discovery_task.unhandled", error=str(exc), exc_info=True)
        raise self.retry(exc=exc)


# ── Beat schedule registration ────────────────────────────────────────────────

if _DISCOVERY_ENABLED:
    from celery.schedules import crontab

    # Parse "minute hour day_of_month month day_of_week" from env var
    _cron_parts = _DISCOVERY_CRON.split()
    if len(_cron_parts) == 5:
        _minute, _hour, _dom, _month, _dow = _cron_parts
    else:
        _minute, _hour, _dom, _month, _dow = "0", "2", "*", "*", "*"

    celery_app.conf.beat_schedule["run-discovery-pipeline-daily"] = {
        "task":    "workers.tasks_discovery.run_discovery_pipeline",
        "schedule": crontab(
            minute=_minute,
            hour=_hour,
            day_of_month=_dom,
            month_of_year=_month,
            day_of_week=_dow,
        ),
        "kwargs": {"dry_run": False, "top_n": _DEFAULT_TOP_N},
        "options": {"expires": _LOCK_TTL},
    }
    logger.info(
        "discovery_task.beat_registered",
        cron=_DISCOVERY_CRON,
        enabled=True,
    )
