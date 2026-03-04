from __future__ import annotations

"""
tasks_tracking.py
=================
Celery tasks for polling supplier tracking information.

  poll_tracking()  – periodic task (every TRACKING_POLL_INTERVAL seconds)
                     queries PLACED orders without tracking and attempts to
                     fetch tracking from the supplier.

Scheduled via Celery beat (see celery_app.py for the beat_schedule entry).
"""

import asyncio
import os
import uuid as _uuid
from uuid import UUID

import structlog

from app.db.session import AsyncSessionLocal
from app.models.event_log import EventLog
from app.models.order import Order, OrderStatus
from app.services.order_service import get_placed_untracked, mark_shipped
from app.services.shopify_service import get_shopify_client
from app.services.tracking_service import fetch_tracking, record_tracking_failure
from app.suppliers.base import SupplierError
from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.get_event_loop().run_until_complete(coro)


# ── poll_tracking ─────────────────────────────────────────────────────────────


@celery_app.task(
    name="workers.tasks_tracking.poll_tracking",
    bind=True,
    max_retries=0,          # beat-driven; don't auto-retry the whole poll
    acks_late=True,
    ignore_result=True,
)
def poll_tracking(self) -> None:
    """
    Periodic task: poll tracking for all PLACED orders without a tracking number.

    For each qualifying order:
      1. Call supplier_client.get_tracking(supplier_order_id).
      2. If tracking found:
         a. Update order → tracking_number, tracking_url, shipped_at, status=SHIPPED.
         b. Call ShopifyClient.create_fulfillment() to notify Shopify.
         c. Write event_log entry.
      3. If not shipped yet → skip silently.
      4. On SupplierError → log event_log, do NOT change order status.
    """
    _run(_poll_tracking_async())


async def _poll_tracking_async() -> None:
    log = logger.bind(task="poll_tracking")
    log.info("tracking.poll.start")

    async with AsyncSessionLocal() as session:
        async with session.begin():
            orders = await get_placed_untracked(session)

    log.info("tracking.poll.candidates", count=len(orders))

    for order in orders:
        await _process_order_tracking(order)

    log.info("tracking.poll.done")


async def _process_order_tracking(order: Order) -> None:
    log = logger.bind(
        order_id=str(order.id),
        supplier_order_id=order.supplier_order_id,
        supplier=order.supplier,
    )

    # ── Step 1: fetch tracking ────────────────────────────────────────────────
    try:
        tracking_number, tracking_url = await fetch_tracking(order)
    except SupplierError as exc:
        log.error("tracking.fetch.supplier_error", reason=exc.message)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await record_tracking_failure(session, order, reason=exc.message)
        return
    except Exception as exc:
        reason = f"unexpected tracking error: {exc}"
        log.exception("tracking.fetch.unexpected", error=str(exc))
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await record_tracking_failure(session, order, reason=reason)
        return

    # ── Step 2: not yet shipped ───────────────────────────────────────────────
    if not tracking_number:
        log.debug("tracking.not_yet_shipped")
        return

    # ── Step 3: persist SHIPPED transition ───────────────────────────────────
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Re-fetch inside transaction
            from app.services.order_service import get_order_by_id
            fresh = await get_order_by_id(session, order.id)
            if fresh is None or fresh.status != OrderStatus.PLACED:
                log.warning(
                    "tracking.order_state_changed",
                    current_status=fresh.status if fresh else "gone",
                )
                return

            await mark_shipped(
                session,
                fresh,
                tracking_number=tracking_number,
                tracking_url=tracking_url,
            )

            session.add(EventLog(
                event_hash=f"shipped:{order.id}:{_uuid.uuid4().hex[:8]}",
                source="worker",
                event_type="order/shipped",
                payload_ref=order.supplier_order_id,
                note=(
                    f"tracking_number={tracking_number} "
                    f"tracking_url={tracking_url or 'n/a'}"
                ),
            ))
            log.info(
                "tracking.order_shipped",
                tracking_number=tracking_number,
                tracking_url=tracking_url,
            )

    # ── Step 4: notify Shopify ────────────────────────────────────────────────
    try:
        shopify = get_shopify_client()
        await shopify.create_fulfillment(
            order,
            tracking_number=tracking_number,
            tracking_url=tracking_url,
            notify_customer=True,
        )
    except Exception as exc:
        # Fulfillment notification is best-effort; don't revert SHIPPED status
        log.error(
            "tracking.shopify_fulfillment_failed",
            error=str(exc),
            note="Order is SHIPPED in our DB; manual fulfillment may be needed on Shopify.",
        )
