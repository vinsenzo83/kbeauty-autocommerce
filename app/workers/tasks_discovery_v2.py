from __future__ import annotations

"""
app/workers/tasks_discovery_v2.py
───────────────────────────────────
Sprint 17 – Celery task for the AI Discovery Engine v2.

Public tasks
────────────
run_discovery_and_publish(limit=20, dry_run=True)
    1. Acquire Redis lock 'discovery:run' TTL 30 min to prevent overlap.
    2. Call discovery_service_v2.generate_candidates(session, limit=200).
    3. Call publish_service.discover_and_publish_top20(session, limit=limit, dry_run=dry_run).
    4. Commit session (if not dry_run).
    5. Return summary dict.

Beat schedule
─────────────
Registered conditionally when DISCOVERY_ENABLED=1 (default off).
Default schedule: daily at 03:00.
dry_run=True by default for safety.

Environment variables
─────────────────────
DISCOVERY_ENABLED          – '1' to enable beat schedule (default '0')
DISCOVERY_LOCK_TTL         – Redis lock TTL in seconds (default 1800 = 30 min)
DISCOVERY_V2_DAILY_HOUR    – Hour for daily cron (default '3')
DISCOVERY_V2_DAILY_MINUTE  – Minute for daily cron (default '0')
"""

import asyncio
import os
from typing import Any

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
_DISCOVERY_ENABLED  = os.getenv("DISCOVERY_ENABLED", "0") == "1"
_LOCK_KEY           = "discovery:run"
_LOCK_TTL           = int(os.getenv("DISCOVERY_LOCK_TTL", str(30 * 60)))  # 30 min
_DAILY_HOUR         = os.getenv("DISCOVERY_V2_DAILY_HOUR", "3")
_DAILY_MINUTE       = os.getenv("DISCOVERY_V2_DAILY_MINUTE", "0")
_DEFAULT_LIMIT      = 20
_GENERATE_LIMIT     = 200


# ── Redis lock helpers ────────────────────────────────────────────────────────

async def _acquire_lock(redis_url: str, ttl: int) -> bool:
    """
    Try to SET NX EX in Redis.
    Returns True if lock acquired, False if already locked.
    Falls back to True (allow run) if Redis is unavailable.
    """
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(redis_url, socket_connect_timeout=3)
        acquired = await client.set(_LOCK_KEY, "1", nx=True, ex=ttl)
        await client.aclose()
        return bool(acquired)
    except Exception as exc:
        logger.warning("discovery_v2_task.lock_error", error=str(exc))
        return True   # Proceed without lock if Redis is unavailable


async def _release_lock(redis_url: str) -> None:
    """Release the discovery lock."""
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(redis_url, socket_connect_timeout=3)
        await client.delete(_LOCK_KEY)
        await client.aclose()
    except Exception as exc:
        logger.warning("discovery_v2_task.release_lock_error", error=str(exc))


# ── Async core ────────────────────────────────────────────────────────────────

async def _run_discovery_and_publish_async(
    limit: int,
    dry_run: bool,
) -> dict[str, Any]:
    """Async implementation of the discovery + publish pipeline."""
    from app.config import get_settings
    from app.db.session import AsyncSessionLocal
    from app.services.discovery_service_v2 import generate_candidates
    from app.services.publish_service import discover_and_publish_top20

    settings  = get_settings()
    redis_url = settings.REDIS_URL

    # Acquire Redis lock to prevent concurrent runs
    acquired = await _acquire_lock(redis_url, _LOCK_TTL)
    if not acquired:
        logger.warning("discovery_v2_task.skipped_locked")
        return {"status": "skipped", "reason": "concurrent_lock"}

    session = AsyncSessionLocal()
    result  = {}

    try:
        # Step 1: Generate / refresh candidate scores
        candidates = await generate_candidates(session, limit=_GENERATE_LIMIT)

        # Step 2: Publish top-N candidates
        publish_result = await discover_and_publish_top20(
            session,
            limit=limit,
            dry_run=dry_run,
        )

        if not dry_run:
            await session.commit()
            logger.info("discovery_v2_task.committed")

        result = {
            "status":               "ok",
            "dry_run":              dry_run,
            "candidates_generated": len(candidates),
            "top_limit":            limit,
            "published":            publish_result.published,
            "skipped":              publish_result.skipped,
            "failed":               publish_result.failed,
            "job_id":               str(publish_result.job_id) if publish_result.job_id else None,
        }

    except Exception as exc:
        logger.error("discovery_v2_task.error", error=str(exc), exc_info=True)
        await session.rollback()
        result = {"status": "error", "error": str(exc)}

    finally:
        await session.close()
        if not dry_run:
            await _release_lock(redis_url)

    return result


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_discovery_v2.run_discovery_and_publish",
    bind=True,
    max_retries=2,
    default_retry_delay=300,  # 5 min
)
def run_discovery_and_publish(
    self,
    limit: int = _DEFAULT_LIMIT,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Celery task: run AI Discovery Engine v2 and publish top candidates.

    Parameters
    ----------
    limit   : Number of top candidates to publish (default 20)
    dry_run : If True, generate but do not publish to Shopify (default True)

    Returns
    -------
    Summary dict with pipeline statistics.
    """
    logger.info("discovery_v2_task.start", limit=limit, dry_run=dry_run)
    try:
        return asyncio.run(
            _run_discovery_and_publish_async(limit=limit, dry_run=dry_run)
        )
    except Exception as exc:
        logger.error("discovery_v2_task.unhandled", error=str(exc), exc_info=True)
        raise self.retry(exc=exc)


# ── Beat schedule registration ────────────────────────────────────────────────

if _DISCOVERY_ENABLED:
    from celery.schedules import crontab

    celery_app.conf.beat_schedule["run-discovery-v2-daily"] = {
        "task": "workers.tasks_discovery_v2.run_discovery_and_publish",
        "schedule": crontab(
            minute=_DAILY_MINUTE,
            hour=_DAILY_HOUR,
        ),
        "kwargs": {"limit": _DEFAULT_LIMIT, "dry_run": True},  # Safe default
        "options": {"expires": _LOCK_TTL},
    }
    logger.info(
        "discovery_v2_task.beat_registered",
        hour=_DAILY_HOUR,
        minute=_DAILY_MINUTE,
        dry_run_default=True,
    )
