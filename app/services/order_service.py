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


async def mark_shipped(
    session: AsyncSession,
    order: Order,
    *,
    tracking_number: str,
    tracking_url: str | None = None,
) -> Order:
    """Transition order to SHIPPED after tracking confirmed."""
    order.status         = OrderStatus.SHIPPED
    order.tracking_number = tracking_number
    order.tracking_url   = tracking_url
    order.shipped_at     = datetime.now(timezone.utc)
    session.add(order)
    await session.flush()
    logger.info(
        "order.shipped",
        order_id=str(order.id),
        tracking_number=tracking_number,
        tracking_url=tracking_url,
    )
    return order


async def get_placed_untracked(session: AsyncSession) -> list[Order]:
    """Return all PLACED orders that have no tracking_number yet."""
    from sqlalchemy import and_

    result = await session.execute(
        select(Order).where(
            and_(
                Order.status == OrderStatus.PLACED,
                Order.tracking_number.is_(None),
            )
        )
    )
    return list(result.scalars().all())


async def mark_failed(session: AsyncSession, order: Order, reason: str) -> Order:
    order.status      = OrderStatus.FAILED
    order.fail_reason = reason
    session.add(order)
    await session.flush()
    logger.warning("order.failed", order_id=str(order.id), reason=reason)
    return order


async def mark_canceled(session: AsyncSession, order: Order, reason: str = "") -> Order:
    """Transition order to CANCELED (MVP admin action)."""
    order.status      = "CANCELED"
    order.fail_reason = reason or "Admin canceled"
    session.add(order)
    await session.flush()
    logger.info("order.canceled", order_id=str(order.id), reason=reason)
    return order


async def list_orders(
    session: AsyncSession,
    *,
    status_filter: str | None     = None,
    supplier_filter: str | None   = None,
    country_filter: str | None    = None,
    q: str | None                 = None,
    margin_min: float | None      = None,
    margin_max: float | None      = None,
    date_from: str | None         = None,
    date_to: str | None           = None,
    page: int                     = 1,
    page_size: int                = 20,
) -> tuple[list[Order], int]:
    """
    Paginated order list with optional filters.

    Returns (orders, total_count).
    """
    from datetime import datetime, timezone
    from sqlalchemy import func, or_

    query = select(Order)

    if status_filter:
        query = query.where(Order.status == status_filter)
    if supplier_filter:
        query = query.where(Order.supplier == supplier_filter)
    if country_filter:
        # Use a cast-to-text LIKE search that works on both PostgreSQL and SQLite
        from sqlalchemy import cast, Text
        query = query.where(
            cast(Order.shipping_address_json, Text).ilike(f"%{country_filter}%")  # type: ignore[arg-type]
        )
    if q:
        query = query.where(
            or_(
                Order.email.ilike(f"%{q}%"),                    # type: ignore[attr-defined]
                Order.shopify_order_id.ilike(f"%{q}%"),         # type: ignore[attr-defined]
                Order.supplier_order_id.ilike(f"%{q}%"),        # type: ignore[attr-defined]
            )
        )
    if date_from:
        try:
            dt = datetime.fromisoformat(date_from).replace(tzinfo=timezone.utc)
            query = query.where(Order.created_at >= dt)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to).replace(tzinfo=timezone.utc)
            query = query.where(Order.created_at <= dt)
        except ValueError:
            pass

    # Count
    cnt_q  = select(func.count()).select_from(query.subquery())
    cnt_r  = await session.execute(cnt_q)
    total  = cnt_r.scalar_one() or 0

    # Paginate
    offset = (page - 1) * page_size
    query  = query.order_by(Order.created_at.desc()).offset(offset).limit(page_size)  # type: ignore[attr-defined]
    result = await session.execute(query)
    return list(result.scalars().all()), total
