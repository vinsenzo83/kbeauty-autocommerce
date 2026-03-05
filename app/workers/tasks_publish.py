from __future__ import annotations

"""
app/workers/tasks_publish.py
─────────────────────────────
Sprint 12 – Celery task for auto-publishing top products to Shopify.

Task
----
publish_shopify_top_products(limit=20, dry_run=False)

Safety
------
* Acquires a Redis lock "publish:shopify" (TTL 15 min) to prevent
  overlapping concurrent runs.
* Uses AsyncSessionLocal pattern identical to tasks_pricing.py.

Beat schedule
-------------
NOT added to the default beat_schedule.
Trigger only via Admin API:  POST /admin/publish/shopify
"""

import asyncio
from typing import Any

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

_LOCK_KEY = "publish:shopify"
_LOCK_TTL = 900  # 15 minutes in seconds

# Top-level import so tests can patch AsyncSessionLocal
try:
    from app.db.session import AsyncSessionLocal  # noqa: F401
except Exception:  # pragma: no cover
    AsyncSessionLocal = None  # type: ignore[assignment]


async def _run_publish(
    *,
    limit: int = 20,
    dry_run: bool = False,
    session_factory: Any = None,
    shopify_svc: Any = None,
) -> dict[str, Any]:
    """
    Async core: acquires Redis lock, runs publish, releases lock.

    Parameters
    ----------
    limit          : Max products to publish.
    dry_run        : If True, no real Shopify calls.
    session_factory: Override for tests.
    shopify_svc    : Override for tests (mock service).

    Returns
    -------
    dict summarising the run.
    """
    import redis.asyncio as aioredis

    from app.config import get_settings
    from app.services.publish_service import publish_top_products_to_shopify

    settings = get_settings()
    _sf = session_factory or AsyncSessionLocal

    # ── Acquire Redis lock ─────────────────────────────────────────────────────
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    lock_acquired = await redis_client.set(
        _LOCK_KEY, "1", nx=True, ex=_LOCK_TTL
    )
    if not lock_acquired:
        logger.warning(
            "tasks_publish.lock_busy",
            lock_key=_LOCK_KEY,
        )
        return {
            "status":  "skipped",
            "reason":  "another publish run is already in progress",
            "dry_run": dry_run,
        }

    logger.info("tasks_publish.lock_acquired", lock_key=_LOCK_KEY, ttl=_LOCK_TTL)

    try:
        async with _sf() as session:
            result = await publish_top_products_to_shopify(
                session,
                limit      = limit,
                dry_run    = dry_run,
                shopify_svc= shopify_svc,
            )
            await session.commit()

        logger.info(
            "tasks_publish.done",
            job_id          = result.job_id,
            status          = result.status,
            dry_run         = result.dry_run,
            published_count = result.published_count,
            failed_count    = result.failed_count,
            skipped_count   = result.skipped_count,
        )

        return {
            "status":          result.status,
            "job_id":          result.job_id,
            "dry_run":         result.dry_run,
            "target_count":    result.target_count,
            "published_count": result.published_count,
            "failed_count":    result.failed_count,
            "skipped_count":   result.skipped_count,
            "notes":           result.notes,
        }

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "tasks_publish.error",
            error=str(exc),
            dry_run=dry_run,
        )
        return {
            "status":  "failed",
            "error":   str(exc),
            "dry_run": dry_run,
        }

    finally:
        # Always release the lock
        try:
            await redis_client.delete(_LOCK_KEY)
            logger.info("tasks_publish.lock_released", lock_key=_LOCK_KEY)
        except Exception:  # noqa: BLE001
            pass
        await redis_client.aclose()


@celery_app.task(
    name="workers.tasks_publish.publish_shopify_top_products",
    bind=True,
    max_retries=0,         # do not auto-retry; admin can re-trigger manually
    acks_late=True,
)
def publish_shopify_top_products(
    self,  # noqa: ANN001
    limit: int = 20,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Celery task: publish top-N canonical products to Shopify.

    Parameters
    ----------
    limit   : Number of products to select and publish.
    dry_run : If True, simulate without calling Shopify API.

    Returns
    -------
    Summary dict with job_id, status, counts.
    """
    logger.info(
        "tasks_publish.started",
        limit=limit,
        dry_run=dry_run,
    )
    return asyncio.get_event_loop().run_until_complete(
        _run_publish(limit=limit, dry_run=dry_run)
    )
