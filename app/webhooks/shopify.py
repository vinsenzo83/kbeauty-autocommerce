from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, Settings
from app.db.session import get_db
from app.models.event_log import EventLog
from app.models.order import Order
from app.services.order_service import create_order, get_order_by_shopify_id
from app.utils.hmac_verify import verify_shopify_hmac
from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)
router = APIRouter()


def _compute_event_hash(raw_body: bytes, event_type: str) -> str:
    h = hashlib.sha256()
    h.update(b"shopify:")
    h.update(event_type.encode())
    h.update(b":")
    h.update(raw_body)
    return h.hexdigest()


@router.post(
    "/order-created",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive Shopify order/created webhook",
)
async def order_created_webhook(
    request: Request,
    x_shopify_hmac_sha256: str = Header(..., alias="X-Shopify-Hmac-Sha256"),
    x_shopify_topic: str = Header(default="orders/create", alias="X-Shopify-Topic"),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    raw_body = await request.body()

    # ── 1. Verify HMAC ────────────────────────────────────────────────────────
    if not verify_shopify_hmac(raw_body, settings.SHOPIFY_WEBHOOK_SECRET, x_shopify_hmac_sha256):
        logger.warning("webhook.hmac_invalid", topic=x_shopify_topic)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid HMAC signature",
        )

    # ── 2. Parse body ─────────────────────────────────────────────────────────
    try:
        payload: dict[str, Any] = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.error("webhook.bad_json", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON body",
        )

    shopify_order_id = str(payload.get("id", ""))
    event_hash = _compute_event_hash(raw_body, x_shopify_topic)

    # ── 3. Idempotency check ─────────────────────────────────────────────────
    existing_event = await db.execute(
        select(EventLog).where(EventLog.event_hash == event_hash)
    )
    if existing_event.scalar_one_or_none():
        logger.info(
            "webhook.duplicate_skipped",
            shopify_order_id=shopify_order_id,
            event_hash=event_hash,
        )
        return {"status": "duplicate", "shopify_order_id": shopify_order_id}

    # ── 4. Log event (idempotency record) ─────────────────────────────────────
    event_log = EventLog(
        event_hash=event_hash,
        source="shopify",
        event_type=x_shopify_topic,
        payload_ref=shopify_order_id,
    )
    db.add(event_log)

    # ── 5. Persist order ──────────────────────────────────────────────────────
    order = await create_order(db, payload)
    await db.flush()

    # ── 6. Enqueue Celery task ────────────────────────────────────────────────
    celery_app.send_task(
        "workers.tasks_order.process_new_order",
        args=[str(order.id)],
    )
    logger.info(
        "webhook.accepted",
        order_id=str(order.id),
        shopify_order_id=shopify_order_id,
    )

    return {
        "status": "accepted",
        "order_id": str(order.id),
        "shopify_order_id": shopify_order_id,
    }
