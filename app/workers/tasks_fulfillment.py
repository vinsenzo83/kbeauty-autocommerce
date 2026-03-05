from __future__ import annotations

"""
app/workers/tasks_fulfillment.py
──────────────────────────────────
Sprint 14 – Celery tasks for automated supplier order fulfillment.

Tasks
-----
process_order_fulfillment(channel_order_id, dry_run=False)
    Triggered by webhook after ChannelOrderV2 is saved.
    Calls order_fulfillment_service.process_channel_order().
    Redis lock per channel_order_id prevents duplicate runs.

poll_supplier_orders(limit=50)
    Periodic task: polls placed/confirmed supplier orders for status updates.
    On 'shipped' → extracts tracking → calls Shopify fulfillment API.
    Redis lock "fulfillment:poll" (TTL 10 min) prevents concurrent runs.

Beat schedule
-------------
    FULFILLMENT_POLL_ENABLED=1    (default 0 = off; set 1 in production)
    FULFILLMENT_POLL_INTERVAL=300  (default 5 min)
"""

import asyncio
import os
from typing import Any

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

_POLL_LOCK_KEY   = "fulfillment:poll"
_POLL_LOCK_TTL   = 600   # 10 min
_ORDER_LOCK_TTL  = 300   # 5 min

# Top-level import so tests can patch
try:
    from app.db.session import AsyncSessionLocal  # noqa: F401
except Exception:  # pragma: no cover
    AsyncSessionLocal = None  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# Task 1: process_order_fulfillment
# ─────────────────────────────────────────────────────────────────────────────

async def _process_order_async(
    channel_order_id: str,
    *,
    dry_run: bool = False,
    session_factory: Any = None,
    supplier_client_factory: Any = None,
) -> dict[str, Any]:
    """Core async implementation of order fulfillment."""
    import redis.asyncio as aioredis

    from app.config import get_settings
    from app.services.order_fulfillment_service import process_channel_order

    settings = get_settings()
    _sf = session_factory or AsyncSessionLocal

    lock_key = f"fulfillment:order:{channel_order_id}"
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    lock_acquired = await redis_client.set(lock_key, "1", nx=True, ex=_ORDER_LOCK_TTL)
    if not lock_acquired:
        logger.warning("tasks_fulfillment.order_lock_busy", channel_order_id=channel_order_id)
        await redis_client.aclose()
        return {"status": "skipped", "reason": "already processing", "channel_order_id": channel_order_id}

    logger.info("tasks_fulfillment.order_started", channel_order_id=channel_order_id, dry_run=dry_run)

    try:
        async with _sf() as session:
            supplier_orders = await process_channel_order(
                channel_order_id,
                session,
                dry_run=dry_run,
                supplier_client_factory=supplier_client_factory,
            )
            await session.commit()

        results = [
            {
                "id":                str(so.id),
                "supplier":          so.supplier,
                "status":            so.status,
                "supplier_order_id": so.supplier_order_id,
                "failure_reason":    so.failure_reason,
            }
            for so in supplier_orders
        ]
        logger.info(
            "tasks_fulfillment.order_done",
            channel_order_id=channel_order_id,
            results=results,
        )
        return {"status": "ok", "channel_order_id": channel_order_id, "results": results}

    except Exception as exc:  # noqa: BLE001
        logger.error("tasks_fulfillment.order_error", error=str(exc), channel_order_id=channel_order_id)
        return {"status": "failed", "error": str(exc), "channel_order_id": channel_order_id}

    finally:
        try:
            await redis_client.delete(lock_key)
        except Exception:  # pragma: no cover
            pass
        await redis_client.aclose()


MAX_RETRIES = 3


@celery_app.task(
    name="workers.tasks_fulfillment.process_order_fulfillment",
    bind=True,
    max_retries=MAX_RETRIES,
    acks_late=True,
    default_retry_delay=60,
)
def process_order_fulfillment(
    self,
    channel_order_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Celery task: place supplier order for a channel order.

    Retries up to MAX_RETRIES=3 times with exponential backoff on failure.
    """
    logger.info(
        "tasks_fulfillment.process_order",
        channel_order_id=channel_order_id,
        dry_run=dry_run,
        attempt=self.request.retries + 1,
    )
    result = asyncio.get_event_loop().run_until_complete(
        _process_order_async(channel_order_id, dry_run=dry_run)
    )

    # Auto-retry on failure (SupplierError may be retryable)
    if result.get("status") == "failed" and self.request.retries < self.max_retries:
        countdown = 2 ** self.request.retries * 30  # 30s, 60s, 120s
        logger.warning(
            "tasks_fulfillment.retrying",
            channel_order_id=channel_order_id,
            attempt=self.request.retries + 1,
            countdown=countdown,
        )
        raise self.retry(countdown=countdown, exc=RuntimeError(result.get("error", "unknown")))

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Task 2: poll_supplier_orders
# ─────────────────────────────────────────────────────────────────────────────

async def _poll_supplier_orders_async(
    limit: int = 50,
    session_factory: Any = None,
    shopify_svc: Any = None,
    supplier_client_factory: Any = None,
) -> dict[str, Any]:
    """Core async polling implementation."""
    import redis.asyncio as aioredis

    from app.config import get_settings
    from app.models.supplier_order import SupplierOrder, SupplierOrderStatus
    from app.services.shopify_fulfillment_service import get_shopify_fulfillment_service
    from app.services.supplier_router import _make_client
    from sqlalchemy import select

    settings = get_settings()
    _sf = session_factory or AsyncSessionLocal

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    lock_acquired = await redis_client.set(_POLL_LOCK_KEY, "1", nx=True, ex=_POLL_LOCK_TTL)
    if not lock_acquired:
        logger.warning("tasks_fulfillment.poll_lock_busy")
        await redis_client.aclose()
        return {"status": "skipped", "reason": "another poll is already running"}

    logger.info("tasks_fulfillment.poll_started", limit=limit)

    updated = skipped = failed = 0

    try:
        async with _sf() as session:
            # Find placed/confirmed orders that need polling
            res = await session.execute(
                select(SupplierOrder).where(
                    SupplierOrder.status.in_([
                        SupplierOrderStatus.PLACED,
                        SupplierOrderStatus.CONFIRMED,
                    ])
                ).limit(limit)
            )
            orders = list(res.scalars().all())
            logger.info("tasks_fulfillment.poll_found", count=len(orders))

            for so in orders:
                try:
                    # Get supplier client
                    if supplier_client_factory is not None:
                        client = supplier_client_factory(so.supplier)
                    else:
                        client = _make_client(so.supplier)

                    # Poll status
                    status_obj = await client.get_order_status(so.supplier_order_id)
                    so.supplier_status = status_obj.status

                    if status_obj.status == "shipped":
                        so.status            = SupplierOrderStatus.SHIPPED
                        so.tracking_number   = status_obj.tracking_number
                        so.tracking_carrier  = status_obj.tracking_carrier

                        # Create Shopify fulfillment
                        if shopify_svc is None:
                            _svc = get_shopify_fulfillment_service()
                        else:
                            _svc = shopify_svc

                        # Resolve Shopify order ID from channel order
                        from app.models.channel_order import ChannelOrderV2
                        co_res = await session.execute(
                            select(ChannelOrderV2).where(
                                ChannelOrderV2.id == so.channel_order_id
                            )
                        )
                        co = co_res.scalar_one_or_none()
                        if co is not None and co.channel == "shopify":
                            shopify_order_id = co.external_order_id
                            await _svc.create_shopify_fulfillment(
                                shopify_order_id=shopify_order_id,
                                tracking_number=so.tracking_number,
                                carrier=so.tracking_carrier,
                            )

                        updated += 1
                        logger.info(
                            "tasks_fulfillment.order_shipped",
                            supplier_order_id=so.supplier_order_id,
                            tracking=so.tracking_number,
                        )

                    elif status_obj.status == "delivered":
                        so.status = SupplierOrderStatus.DELIVERED
                        updated += 1

                    elif status_obj.status == "confirmed":
                        so.status = SupplierOrderStatus.CONFIRMED
                        skipped += 1

                    else:
                        skipped += 1

                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "tasks_fulfillment.poll_item_error",
                        supplier_order_id=str(so.id),
                        error=str(exc),
                    )
                    failed += 1

            await session.commit()

    except Exception as exc:  # noqa: BLE001
        logger.error("tasks_fulfillment.poll_error", error=str(exc))
        return {"status": "failed", "error": str(exc)}

    finally:
        try:
            await redis_client.delete(_POLL_LOCK_KEY)
        except Exception:  # pragma: no cover
            pass
        await redis_client.aclose()

    logger.info("tasks_fulfillment.poll_done", updated=updated, skipped=skipped, failed=failed)
    return {"status": "ok", "updated": updated, "skipped": skipped, "failed": failed}


@celery_app.task(
    name="workers.tasks_fulfillment.poll_supplier_orders",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def poll_supplier_orders(self, limit: int = 50) -> dict[str, Any]:
    """Celery task: poll placed/confirmed supplier orders and update tracking."""
    logger.info("tasks_fulfillment.poll_task_started", limit=limit)
    return asyncio.get_event_loop().run_until_complete(
        _poll_supplier_orders_async(limit=limit)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Beat schedule registration
# ─────────────────────────────────────────────────────────────────────────────

_POLL_ENABLED  = os.getenv("FULFILLMENT_POLL_ENABLED", "0") == "1"
_POLL_INTERVAL = int(os.getenv("FULFILLMENT_POLL_INTERVAL", "300"))  # 5 min default

if _POLL_ENABLED:
    celery_app.conf.beat_schedule["poll-supplier-orders"] = {
        "task":     "workers.tasks_fulfillment.poll_supplier_orders",
        "schedule": _POLL_INTERVAL,
        "kwargs":   {"limit": 50},
        "options":  {"expires": _POLL_INTERVAL},
    }
    logger.info(
        "tasks_fulfillment.beat_registered",
        interval_seconds=_POLL_INTERVAL,
    )
