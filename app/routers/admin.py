from __future__ import annotations

# TODO: Add JWT authentication before exposing this router in production.
#       Suggested approach:
#         from fastapi.security import HTTPBearer
#         from app.auth.jwt import verify_token  (implement with python-jose)
#       Then add `Depends(verify_token)` to each endpoint.

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.order import Order, OrderStatus
from app.services.order_service import get_order_by_id

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post(
    "/orders/{order_id}/retry-place",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry supplier placement for a FAILED order",
    tags=["admin"],
)
async def retry_place_order(
    order_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Re-enqueue the supplier placement Celery task for a FAILED order.

    Rules:
    - Only allowed when order.status == FAILED.
    - Does NOT re-run policy validation (order was already VALIDATED previously).

    TODO: Require admin JWT before production use.
    """
    order: Order | None = await get_order_by_id(db, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order {order_id} not found.",
        )

    if order.status != OrderStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Order is in status '{order.status}'. "
                "Only FAILED orders can be retried."
            ),
        )

    # Import here to avoid circular deps at module load
    from app.workers.celery_app import celery_app

    celery_app.send_task(
        "workers.tasks_order.retry_place_order",
        args=[str(order_id)],
    )

    logger.info(
        "admin.retry_place.enqueued",
        order_id=str(order_id),
        previous_status=order.status,
    )

    return {
        "status": "accepted",
        "order_id": str(order_id),
        "message": "retry-place task enqueued",
    }
