from __future__ import annotations

"""
app/workers/tasks_repricing.py
───────────────────────────────
Sprint 13 – Celery task for scheduled / manual repricing.

Task
----
run_repricing(limit=50, dry_run=False)

Safety
------
* Redis lock "repricing:shopify" (TTL 15 min) prevents concurrent runs.
* Uses AsyncSessionLocal pattern identical to tasks_pricing.py / tasks_publish.py.

Beat schedule
-------------
Controlled by env var:
    REPRICING_ENABLED=1          (default 0 = off)
    REPRICING_INTERVAL=21600     (default 6 h in seconds)

Set REPRICING_ENABLED=1 in .env to activate automatic repricing.
Manual trigger: POST /admin/repricing/apply
"""

import asyncio
import os
from typing import Any

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

_LOCK_KEY = "repricing:shopify"
_LOCK_TTL = 900  # 15 minutes

# Top-level import so tests can patch AsyncSessionLocal
try:
    from app.db.session import AsyncSessionLocal  # noqa: F401
except Exception:  # pragma: no cover
    AsyncSessionLocal = None  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# Async core
# ─────────────────────────────────────────────────────────────────────────────

async def _run_repricing_async(
    *,
    limit: int = 50,
    dry_run: bool = False,
    session_factory: Any = None,
    shopify_svc: Any = None,
) -> dict[str, Any]:
    """
    Core async implementation.

    1. Acquire Redis lock.
    2. Call apply_reprice_to_shopify.
    3. Release lock.
    """
    import redis.asyncio as aioredis

    from app.config import get_settings
    from app.services.repricing_service import apply_reprice_to_shopify

    settings = get_settings()
    _sf = session_factory or AsyncSessionLocal

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    lock_acquired = await redis_client.set(
        _LOCK_KEY, "1", nx=True, ex=_LOCK_TTL
    )
    if not lock_acquired:
        logger.warning("tasks_repricing.lock_busy", lock_key=_LOCK_KEY)
        await redis_client.aclose()
        return {
            "status":  "skipped",
            "reason":  "another repricing run is already in progress",
            "dry_run": dry_run,
        }

    logger.info("tasks_repricing.lock_acquired", lock_key=_LOCK_KEY, ttl=_LOCK_TTL)

    try:
        async with _sf() as session:
            run_id = await apply_reprice_to_shopify(
                session,
                limit      = limit,
                dry_run    = dry_run,
                shopify_svc= shopify_svc,
            )
            await session.commit()

        logger.info("tasks_repricing.done", run_id=run_id, dry_run=dry_run)
        return {"status": "ok", "run_id": run_id, "dry_run": dry_run}

    except Exception as exc:  # noqa: BLE001
        logger.error("tasks_repricing.error", error=str(exc), dry_run=dry_run)
        return {"status": "failed", "error": str(exc), "dry_run": dry_run}

    finally:
        try:
            await redis_client.delete(_LOCK_KEY)
            logger.info("tasks_repricing.lock_released", lock_key=_LOCK_KEY)
        except Exception:  # noqa: BLE001
            pass
        await redis_client.aclose()


# ─────────────────────────────────────────────────────────────────────────────
# Celery task
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_repricing.run_repricing",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def run_repricing(
    self,  # noqa: ANN001
    limit: int = 50,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Celery task: compute and apply repriced Shopify prices.

    Parameters
    ----------
    limit   : Max products per run.
    dry_run : If True, compute prices but do not call Shopify API.
    """
    logger.info("tasks_repricing.started", limit=limit, dry_run=dry_run)
    return asyncio.get_event_loop().run_until_complete(
        _run_repricing_async(limit=limit, dry_run=dry_run)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Register beat schedule if REPRICING_ENABLED=1
# ─────────────────────────────────────────────────────────────────────────────

_REPRICING_ENABLED  = os.getenv("REPRICING_ENABLED", "0") == "1"
_REPRICING_INTERVAL = int(os.getenv("REPRICING_INTERVAL", "21600"))  # 6 h default

if _REPRICING_ENABLED:
    celery_app.conf.beat_schedule["auto-reprice-every-interval"] = {
        "task":     "workers.tasks_repricing.run_repricing",
        "schedule": _REPRICING_INTERVAL,
        "kwargs":   {"limit": 50, "dry_run": False},
        "options":  {"expires": _REPRICING_INTERVAL},
    }
    logger.info(
        "tasks_repricing.beat_schedule_registered",
        interval_seconds=_REPRICING_INTERVAL,
    )
