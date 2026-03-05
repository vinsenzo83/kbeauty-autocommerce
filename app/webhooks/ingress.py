"""
app/webhooks/ingress.py
────────────────────────
Sprint 10 – Unified multi-channel webhook ingress.
Sprint 11 – Production-grade Shopify HMAC signature verification.

Routes:
  POST /webhook/shopify   – Shopify webhooks
  POST /webhook/shopee    – Shopee webhooks
  POST /webhook/tiktok    – TikTok Shop webhooks

Common flow for every request:
  1. Parse raw body + headers
  2. Signature verification (Shopify: HMAC-SHA256 when WEBHOOK_VERIFY=1)
     → 401 Unauthorized if verification fails
  3. Build NormalizedEvent
  4. INSERT into webhook_events (idempotency guard via UNIQUE event_id)
  5. Dispatch to topic handler (order.created / product.updated)
  6. Mark webhook_events.status = processed / failed
  7. Return 200 OK immediately (prevents channel retry storms)

Environment variables (via app/config.py):
  WEBHOOK_VERIFY          – 0 (dev/test default) | 1 (production)
  SHOPIFY_WEBHOOK_SECRET  – shared secret from Shopify Partner Dashboard
"""
from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.config import get_settings, Settings
from app.db.session import get_db
from app.models.webhook_event import WebhookEvent
from app.webhooks.normalized import NormalizedEvent, ChannelName, TopicName
from app.webhooks.handlers.order_created import handle_order_created
from app.webhooks.handlers.product_updated import handle_product_updated
from app.webhooks.verify import verify_shopify_webhook

logger = structlog.get_logger(__name__)
router = APIRouter()

# Header name after HTTP normalisation (all-lowercase)
_SHOPIFY_HMAC_HEADER = "x-shopify-hmac-sha256"

# ── Topic detection heuristics ───────────────────────────────────────────────
_SHOPIFY_TOPIC_MAP: dict[str, TopicName] = {
    "orders/create":    "order.created",
    "orders/created":   "order.created",
    "products/update":  "product.updated",
    "products/updated": "product.updated",
}


def _detect_topic(
    channel: ChannelName,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> TopicName:
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
        if "financial_status" in payload or "line_items" in payload:
            return "order.created"
        return "product.updated"

    if channel == "shopee":
        code  = payload.get("code") or payload.get("event_type") or ""
        inner = payload.get("data") or {}
        if str(code) in ("3", "order", "order.created") or "order_sn" in (inner or payload):
            return "order.created"
        return "product.updated"

    if channel == "tiktok":
        etype = str(payload.get("type") or payload.get("event_type") or "")
        inner = payload.get("data") or {}
        if "order" in etype.lower() or "order_id" in (inner or payload):
            return "order.created"
        return "product.updated"

    return "order.created"


# ── Signature verification gate ───────────────────────────────────────────────

def _check_shopify_signature(
    raw_body: bytes,
    headers: dict[str, str],
    settings: Settings,
) -> JSONResponse | None:
    """Return a 401 JSONResponse if Shopify HMAC verification fails, else None.

    Accepts Settings as an explicit argument so that FastAPI dependency
    overrides work correctly in tests.
    """
    header_hmac = headers.get(_SHOPIFY_HMAC_HEADER, "")

    if not header_hmac:
        logger.warning(
            "webhook.shopify.missing_signature",
            reason="SHOPIFY_WEBHOOK_SIGNATURE_INVALID",
            detail="X-Shopify-Hmac-Sha256 header absent",
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "status": "unauthorized",
                "reason": "SHOPIFY_WEBHOOK_SIGNATURE_INVALID",
                "detail": "Missing X-Shopify-Hmac-Sha256 header",
            },
        )

    valid = verify_shopify_webhook(
        secret=settings.SHOPIFY_WEBHOOK_SECRET,
        body=raw_body,
        header_hmac=header_hmac,
    )

    if not valid:
        logger.warning(
            "webhook.shopify.invalid_signature",
            reason="SHOPIFY_WEBHOOK_SIGNATURE_INVALID",
            detail="HMAC digest mismatch",
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "status": "unauthorized",
                "reason": "SHOPIFY_WEBHOOK_SIGNATURE_INVALID",
                "detail": "Invalid X-Shopify-Hmac-Sha256 signature",
            },
        )

    return None  # verification passed ✓


# ── Generic ingress ───────────────────────────────────────────────────────────

async def _process_webhook(
    channel: ChannelName,
    request: Request,
    db: AsyncSession,
    settings: Settings,
) -> JSONResponse:
    """Shared handler for all three channel endpoints."""
    raw_body = await request.body()
    hdrs = {k.lower(): v for k, v in request.headers.items()}

    # ── 1. Signature verification (Shopify only, when WEBHOOK_VERIFY=1) ──────
    if channel == "shopify" and settings.WEBHOOK_VERIFY:
        rejection = _check_shopify_signature(raw_body, hdrs, settings)
        if rejection is not None:
            return rejection

    # ── 2. Parse JSON ────────────────────────────────────────────────────────
    try:
        payload: dict[str, Any] = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        logger.warning("webhook.bad_json", channel=channel, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "error", "detail": "invalid JSON"},
        )

    # ── 3. Detect topic ──────────────────────────────────────────────────────
    topic = _detect_topic(channel, hdrs, payload)

    # ── 4. Build NormalizedEvent ─────────────────────────────────────────────
    evt = NormalizedEvent.build(
        channel=channel,
        topic=topic,
        payload=payload,
        headers=hdrs,
    )

    log = logger.bind(
        channel=channel,
        topic=topic,
        event_id=evt.event_id,
        external_id=evt.external_id,
    )

    # ── 5. Idempotency INSERT ────────────────────────────────────────────────
    we = WebhookEvent(
        event_id     = evt.event_id,
        channel      = evt.channel,
        topic        = evt.topic,
        external_id  = evt.external_id or None,
        occurred_at  = evt.occurred_at,
        status       = "received",
        payload_json = payload,
    )
    try:
        db.add(we)
        await db.flush()
    except IntegrityError:
        await db.rollback()
        log.info("webhook.duplicate_skipped")
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "event_id": evt.event_id},
        )

    # ── 6. Dispatch to topic handler ─────────────────────────────────────────
    result_ref: str | None = None
    try:
        if topic == "order.created":
            order = await handle_order_created(evt, db)
            result_ref = str(order.id)
        elif topic == "product.updated":
            product = await handle_product_updated(evt, db)
            result_ref = str(product.id)

        we.status = "processed"
        await db.commit()
        log.info("webhook.processed", result_ref=result_ref)

    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        try:
            we.status = "failed"
            we.error  = str(exc)[:500]
            await db.merge(we)
            await db.commit()
        except Exception:
            pass
        log.error("webhook.handler_error", exc=str(exc))
        return JSONResponse(
            status_code=200,
            content={
                "status":   "error",
                "event_id": evt.event_id,
                "detail":   str(exc),
            },
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
    db: AsyncSession       = Depends(get_db),
    settings: Settings     = Depends(get_settings),
) -> JSONResponse:
    return await _process_webhook("shopify", request, db, settings)


@router.post("/shopee", summary="Shopee webhook ingress")
async def shopee_webhook(
    request: Request,
    db: AsyncSession       = Depends(get_db),
    settings: Settings     = Depends(get_settings),
) -> JSONResponse:
    return await _process_webhook("shopee", request, db, settings)


@router.post("/tiktok", summary="TikTok Shop webhook ingress")
async def tiktok_webhook(
    request: Request,
    db: AsyncSession       = Depends(get_db),
    settings: Settings     = Depends(get_settings),
) -> JSONResponse:
    return await _process_webhook("tiktok", request, db, settings)
