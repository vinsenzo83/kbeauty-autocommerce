from __future__ import annotations

"""
Tracking service
================
High-level helpers used by the Celery poll_tracking task.

  fetch_tracking(order)               – call supplier.get_tracking() for one order
  mark_shipped(order, ...)            – thin alias kept here for service-layer cohesion
  record_tracking_failure(...)        – write an EventLog entry on scraping failure
"""

from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event_log import EventLog
from app.models.order import Order
from app.services.order_service import mark_shipped as _mark_shipped
from app.services.supplier_router import choose_supplier
from app.suppliers.base import SupplierError

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


async def fetch_tracking(
    order: Order,
) -> tuple[str | None, str | None]:
    """
    Ask the appropriate supplier client for tracking info.

    Returns
    -------
    (tracking_number, tracking_url)
        Both None when the order has not yet shipped.

    Raises
    ------
    SupplierError
        Propagated from the supplier client on scraping failure.
    """
    if not order.supplier_order_id:
        logger.warning(
            "tracking.fetch.no_supplier_order_id",
            order_id=str(order.id),
        )
        return (None, None)

    client = choose_supplier(order)
    log    = logger.bind(
        order_id=str(order.id),
        supplier_order_id=order.supplier_order_id,
        supplier=client.name,
    )
    log.debug("tracking.fetch.start")
    tracking_number, tracking_url = await client.get_tracking(order.supplier_order_id)
    log.debug(
        "tracking.fetch.result",
        tracking_number=tracking_number,
        tracking_url=tracking_url,
    )
    return (tracking_number, tracking_url)


async def mark_shipped(
    session: AsyncSession,
    order: Order,
    *,
    tracking_number: str,
    tracking_url: str | None,
) -> Order:
    """Transition order to SHIPPED and persist tracking fields."""
    return await _mark_shipped(
        session,
        order,
        tracking_number=tracking_number,
        tracking_url=tracking_url,
    )


async def record_tracking_failure(
    session: AsyncSession,
    order: Order,
    reason: str,
) -> None:
    """Write an event_log entry when a tracking poll fails."""
    import uuid as _uuid

    session.add(EventLog(
        event_hash=f"tracking_fail:{order.id}:{_uuid.uuid4().hex[:8]}",
        source="worker",
        event_type="order/tracking_failed",
        payload_ref=order.supplier_order_id or str(order.id),
        note=reason,
    ))
    logger.warning(
        "tracking.failure_logged",
        order_id=str(order.id),
        reason=reason,
    )
