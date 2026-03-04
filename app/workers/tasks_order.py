from __future__ import annotations

import asyncio
import uuid
from uuid import UUID

import structlog

from app.db.session import AsyncSessionLocal
from app.models.event_log import EventLog
from app.models.order import Order, OrderStatus
from app.services.order_service import (
    get_order_by_id,
    mark_failed,
    mark_placing,
    mark_placed,
    mark_validated,
)
from app.services.policy_service import PolicyViolation, validate_order_policy
from app.services.supplier_router import choose_supplier
from app.suppliers.base import SupplierError
from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


def _run(coro):  # type: ignore[no-untyped-def]
    """Run a coroutine in a new event loop (Celery worker context)."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ─────────────────────────────────────────────────────────────────────────────
# Task: process_new_order
# Pipeline: RECEIVED → (policy) → VALIDATED → (supplier) → PLACING → PLACED
#                                           ↘ FAILED (policy or supplier error)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_order.process_new_order",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
)
def process_new_order(self, order_id: str) -> dict:  # type: ignore[type-arg]
    """
    Celery task: validate and place a newly received order.

    Steps:
    1. Load the order from the database.
    2. Run policy validation → VALIDATED or FAILED.
    3. Call supplier_router to choose a SupplierClient.
    4. Set status=PLACING.
    5. Call supplier.create_order(order) → supplier_order_id.
    6. On success: set PLACED + store supplier metadata.
    7. On SupplierError / unexpected exception: set FAILED + write event_log.
    """
    return _run(_process_new_order_async(self, order_id))


async def _process_new_order_async(task, order_id: str) -> dict:  # type: ignore[type-arg]
    log = logger.bind(order_id=order_id, task_id=task.request.id)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            order = await get_order_by_id(session, UUID(order_id))
            if order is None:
                log.error("task.order_not_found")
                return {"status": "error", "detail": "order not found"}

            # ── Step 1: Policy validation ─────────────────────────────────────
            order_data = {
                "id":               order.shopify_order_id,
                "financial_status": order.financial_status,
                "shipping_address": order.shipping_address_json,
                "line_items":       order.line_items_json,
            }

            try:
                validate_order_policy(order_data)
            except PolicyViolation as exc:
                await mark_failed(session, order, reason=exc.reason)
                session.add(EventLog(
                    event_hash=f"policy_fail:{order_id}",
                    source="worker",
                    event_type="order/validation_failed",
                    payload_ref=order.shopify_order_id,
                    note=exc.reason,
                ))
                log.warning("task.policy_failed", reason=exc.reason)
                return {"status": "failed", "order_id": order_id, "reason": exc.reason}

            await mark_validated(session, order)
            log.info("task.order_validated")

            # ── Step 2: Supplier placement ────────────────────────────────────
            await mark_placing(session, order)
            log.info("task.order_placing")

    # Re-open session for supplier call (outside transaction to avoid long lock)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            order = await get_order_by_id(session, UUID(order_id))
            if order is None:
                return {"status": "error", "detail": "order disappeared"}

            supplier_client = choose_supplier(order)
            log = log.bind(supplier=supplier_client.name)

            try:
                supplier_order_id = await supplier_client.create_order(order)
                await mark_placed(
                    session,
                    order,
                    supplier=supplier_client.name,
                    supplier_order_id=supplier_order_id,
                )
                session.add(EventLog(
                    event_hash=f"placed:{order_id}",
                    source="worker",
                    event_type="order/placed",
                    payload_ref=order.shopify_order_id,
                    note=f"supplier={supplier_client.name} ref={supplier_order_id}",
                ))
                log.info("task.order_placed", supplier_order_id=supplier_order_id)
                return {
                    "status": "placed",
                    "order_id": order_id,
                    "supplier_order_id": supplier_order_id,
                }

            except SupplierError as exc:
                await mark_failed(session, order, reason=exc.message)
                session.add(EventLog(
                    event_hash=f"supplier_fail:{order_id}:{uuid.uuid4().hex[:8]}",
                    source="worker",
                    event_type="order/supplier_failed",
                    payload_ref=order.shopify_order_id,
                    note=exc.message,
                ))
                log.error("task.supplier_failed", reason=exc.message, retryable=exc.retryable)

                if exc.retryable:
                    try:
                        raise task.retry(exc=exc)
                    except task.MaxRetriesExceededError:
                        pass

                return {"status": "failed", "order_id": order_id, "reason": exc.message}

            except Exception as exc:
                reason = f"unexpected: {exc}"
                await mark_failed(session, order, reason=reason)
                session.add(EventLog(
                    event_hash=f"unexpected:{order_id}:{uuid.uuid4().hex[:8]}",
                    source="worker",
                    event_type="order/unexpected_error",
                    payload_ref=order.shopify_order_id,
                    note=reason,
                ))
                log.exception("task.unexpected_error", error=str(exc))
                try:
                    raise task.retry(exc=exc)
                except task.MaxRetriesExceededError:
                    return {"status": "failed", "order_id": order_id, "reason": reason}


# ─────────────────────────────────────────────────────────────────────────────
# Task: retry_place_order  (triggered by admin endpoint)
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_order.retry_place_order",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def retry_place_order(self, order_id: str) -> dict:  # type: ignore[type-arg]
    """
    Re-attempt supplier placement for a FAILED order.
    Called by POST /admin/orders/{id}/retry-place.
    Skips policy re-validation (order was already VALIDATED before).
    """
    return _run(_retry_place_order_async(self, order_id))


async def _retry_place_order_async(task, order_id: str) -> dict:  # type: ignore[type-arg]
    log = logger.bind(order_id=order_id, task_id=task.request.id)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            order = await get_order_by_id(session, UUID(order_id))
            if order is None:
                log.error("retry.order_not_found")
                return {"status": "error", "detail": "order not found"}

            await mark_placing(session, order)
            log.info("retry.placing")

    async with AsyncSessionLocal() as session:
        async with session.begin():
            order = await get_order_by_id(session, UUID(order_id))
            if order is None:
                return {"status": "error", "detail": "order disappeared"}

            supplier_client = choose_supplier(order)
            log = log.bind(supplier=supplier_client.name)

            try:
                supplier_order_id = await supplier_client.create_order(order)
                await mark_placed(
                    session,
                    order,
                    supplier=supplier_client.name,
                    supplier_order_id=supplier_order_id,
                )
                log.info("retry.placed", supplier_order_id=supplier_order_id)
                return {
                    "status": "placed",
                    "order_id": order_id,
                    "supplier_order_id": supplier_order_id,
                }

            except (SupplierError, Exception) as exc:
                reason = exc.message if isinstance(exc, SupplierError) else str(exc)
                await mark_failed(session, order, reason=reason)
                session.add(EventLog(
                    event_hash=f"retry_fail:{order_id}:{uuid.uuid4().hex[:8]}",
                    source="worker",
                    event_type="order/retry_failed",
                    payload_ref=order.shopify_order_id,
                    note=reason,
                ))
                log.error("retry.failed", reason=reason)
                return {"status": "failed", "order_id": order_id, "reason": reason}
