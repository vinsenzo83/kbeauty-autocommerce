from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderStatus

logger = structlog.get_logger(__name__)


async def create_order(session: AsyncSession, payload: dict[str, Any]) -> Order:
    """
    Persist a new order from a Shopify webhook payload.
    Status is set to RECEIVED on creation.
    """
    order = Order(
        shopify_order_id=str(payload["id"]),
        email=payload.get("email"),
        total_price=payload.get("total_price"),
        currency=payload.get("currency"),
        shipping_address_json=payload.get("shipping_address"),
        line_items_json=payload.get("line_items", []),
        financial_status=payload.get("financial_status"),
        status=OrderStatus.RECEIVED,
    )
    session.add(order)
    await session.flush()
    logger.info(
        "order.created",
        order_id=str(order.id),
        shopify_order_id=order.shopify_order_id,
    )
    return order


async def get_order_by_id(session: AsyncSession, order_id: UUID) -> Order | None:
    result = await session.execute(select(Order).where(Order.id == order_id))
    return result.scalar_one_or_none()


async def get_order_by_shopify_id(
    session: AsyncSession, shopify_order_id: str
) -> Order | None:
    result = await session.execute(
        select(Order).where(Order.shopify_order_id == shopify_order_id)
    )
    return result.scalar_one_or_none()


async def mark_validated(session: AsyncSession, order: Order) -> Order:
    order.status = OrderStatus.VALIDATED
    session.add(order)
    await session.flush()
    logger.info("order.validated", order_id=str(order.id))
    return order


async def mark_placing(session: AsyncSession, order: Order) -> Order:
    """Transition order to PLACING (supplier call in progress)."""
    order.status = OrderStatus.PLACING
    session.add(order)
    await session.flush()
    logger.info("order.placing", order_id=str(order.id))
    return order


async def mark_placed(
    session: AsyncSession,
    order: Order,
    *,
    supplier: str,
    supplier_order_id: str,
) -> Order:
    """Transition order to PLACED after successful supplier confirmation."""
    order.status            = OrderStatus.PLACED
    order.supplier          = supplier
    order.supplier_order_id = supplier_order_id
    order.placed_at         = datetime.now(timezone.utc)
    session.add(order)
    await session.flush()
    logger.info(
        "order.placed",
        order_id=str(order.id),
        supplier=supplier,
        supplier_order_id=supplier_order_id,
    )
    return order


async def mark_failed(session: AsyncSession, order: Order, reason: str) -> Order:
    order.status     = OrderStatus.FAILED
    order.fail_reason = reason
    session.add(order)
    await session.flush()
    logger.warning("order.failed", order_id=str(order.id), reason=reason)
    return order
