from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import structlog
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.event_log import EventLog
from app.models.order import Order, OrderStatus
from app.services.order_service import get_order_by_id, mark_failed, mark_validated
from app.services.policy_service import PolicyViolation, validate_order_policy
from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


def _run(coro):  # type: ignore[no-untyped-def]
    """Run a coroutine in a new event loop (Celery worker context)."""
    return asyncio.get_event_loop().run_until_complete(coro)


@celery_app.task(
    name="workers.tasks_order.process_new_order",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
)
def process_new_order(self, order_id: str) -> dict:  # type: ignore[type-arg]
    """
    Celery task: validate a newly received order.

    Steps:
    1. Load the order from the database.
    2. Run policy validation.
    3. Transition status to VALIDATED or FAILED.
    4. Append a note to event_log on failure.
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

            # Build a minimal dict for policy validation from persisted data
            order_data = {
                "id": order.shopify_order_id,
                "financial_status": order.financial_status,
                "shipping_address": order.shipping_address_json,
                "line_items": order.line_items_json,
            }

            try:
                validate_order_policy(order_data)
                await mark_validated(session, order)
                log.info("task.order_validated")
                return {"status": "validated", "order_id": order_id}

            except PolicyViolation as exc:
                await mark_failed(session, order, reason=exc.reason)

                # Append failure note to event_log
                event_note = EventLog(
                    event_hash=f"fail:{order_id}",
                    source="worker",
                    event_type="order/validation_failed",
                    payload_ref=order.shopify_order_id,
                    note=exc.reason,
                )
                session.add(event_note)
                log.warning("task.order_failed", reason=exc.reason)
                return {"status": "failed", "order_id": order_id, "reason": exc.reason}

            except Exception as exc:
                log.exception("task.unexpected_error", error=str(exc))
                try:
                    raise task.retry(exc=exc)
                except task.MaxRetriesExceededError:
                    await mark_failed(session, order, reason=f"max retries exceeded: {exc}")
                    return {
                        "status": "failed",
                        "order_id": order_id,
                        "reason": str(exc),
                    }
