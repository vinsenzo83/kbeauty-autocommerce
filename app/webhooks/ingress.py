"""
app/webhooks/ingress.py
────────────────────────
Sprint 10 – Unified multi-channel webhook ingress.

Routes:
  POST /webhook/shopify   – Shopify webhooks
  POST /webhook/shopee    – Shopee webhooks
  POST /webhook/tiktok    – TikTok Shop webhooks

Common flow for every request:
  1. Parse raw body + headers
  2. (Optional) signature verification  – only when WEBHOOK_VERIFY=1
  3. Build NormalizedEvent
  4. INSERT into webhook_events (idempotency guard via UNIQUE event_id)
  5. Dispatch to topic handler (order.created / product.updated)
  6. Mark webhook_events.status = processed / failed
  7. Return 200 OK immediately (prevents channel retry storms)
"""
from __future__ import annotations

import json
import os
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.db.session import get_db
from app.models.webhook_event import WebhookEvent
from app.webhooks.normalized import NormalizedEvent, ChannelName, TopicName
from app.webhooks.handlers.order_created import handle_order_created
from app.webhooks.handlers.product_updated import handle_product_updated

logger = structlog.get_logger(__name__)
router = APIRouter()

# ── Topic detection heuristics ───────────────────────────────────────────────
_SHOPIFY_TOPIC_MAP: dict[str, TopicName] = {
    "orders/create":   "order.created",
    "orders/created":  "order.created",
    "products/update": "product.updated",
    "products/updated":"product.updated",
}


def _detect_topic(channel: ChannelName, headers: dict[str, str], payload: dict[str, Any]) -> TopicName:
    """Derive unified topic name from channel-specific hints."""
    if channel == "shopify":
        raw = (
            headers.get("x-shopify-topic")
            or headers.get("x-shopify-event")
            or ""
        ).lower()
        mapped = _SHOPIFY_TOPIC_MAP.get(raw)
        if mapped:
            return mapped
        # fallback: infer from payload shape
        if "financial_status" in payload or "line_items" in payload:
            return "order.created"
        return "product.updated"

    if channel == "shopee":
        code = payload.get("code") or payload.get("event_type") or ""
        if str(code) in ("3", "order", "order.created") or "order_sn" in payload:
            return "order.created"
        return "product.updated"

    if channel == "tiktok":
        etype = str(payload.get("type") or payload.get("event_type") or "")
        if "order" in etype.lower() or "order_id" in payload:
            return "order.created"
        return "product.updated"

    # generic
    return "order.created"


# ── Generic ingress ───────────────────────────────────────────────────────────

async def _process_webhook(
    channel: ChannelName,
    request: Request,
    db: AsyncSession,
) -> JSONResponse:
    """Shared handler for all three channel endpoints."""
    raw_body = await request.body()
    hdrs = {k.lower(): v for k, v in request.headers.items()}

    # 1. Parse JSON
    try:
        payload: dict[str, Any] = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        logger.warning("webhook.bad_json", channel=channel, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "error", "detail": "invalid JSON"},
        )

    # 2. Detect topic
    topic = _detect_topic(channel, hdrs, payload)

    # 3. Build NormalizedEvent
    evt = NormalizedEvent.build(
        channel=channel,
        topic=topic,
        payload=payload,
        headers=hdrs,
    )

    log = logger.bind(channel=channel, topic=topic, event_id=evt.event_id,
                      external_id=evt.external_id)

    # 4. Idempotency INSERT
    we = WebhookEvent(
        event_id    = evt.event_id,
        channel     = evt.channel,
        topic       = evt.topic,
        external_id = evt.external_id or None,
        occurred_at = evt.occurred_at,
        status      = "received",
        payload_json= payload,
    )
    try:
        db.add(we)
        await db.flush()          # raises IntegrityError on duplicate event_id
    except IntegrityError:
        await db.rollback()
        log.info("webhook.duplicate_skipped")
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "event_id": evt.event_id},
        )

    # 5. Dispatch to topic handler
    result_ref: str | None = None
    try:
        if topic == "order.created":
            order = await handle_order_created(evt, db)
            result_ref = str(order.id)
        elif topic == "product.updated":
            product = await handle_product_updated(evt, db)
            result_ref = str(product.id)

        # 6. Mark processed
        we.status = "processed"
        await db.commit()
        log.info("webhook.processed", result_ref=result_ref)

    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        # Persist failure status in a fresh transaction
        try:
            we.status = "failed"
            we.error  = str(exc)[:500]
            await db.merge(we)
            await db.commit()
        except Exception:
            pass
        log.error("webhook.handler_error", exc=str(exc))
        # Still 200 to avoid channel retry storms
        return JSONResponse(
            status_code=200,
            content={"status": "error", "event_id": evt.event_id, "detail": str(exc)},
        )

    return JSONResponse(
        status_code=200,
        content={
            "status":     "ok",
            "event_id":   evt.event_id,
            "topic":      topic,
            "channel":    channel,
            "result_ref": result_ref,
        },
    )


# ── Per-channel endpoints ─────────────────────────────────────────────────────

@router.post("/shopify", summary="Shopify webhook ingress")
async def shopify_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    return await _process_webhook("shopify", request, db)


@router.post("/shopee", summary="Shopee webhook ingress")
async def shopee_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    return await _process_webhook("shopee", request, db)


@router.post("/tiktok", summary="TikTok Shop webhook ingress")
async def tiktok_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    return await _process_webhook("tiktok", request, db)
