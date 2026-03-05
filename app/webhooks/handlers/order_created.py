"""
app/webhooks/handlers/order_created.py
───────────────────────────────────────
Sprint 10 – Handles topic=order.created from any channel.

Upserts a row into channel_orders_v2 keyed on (channel, external_order_id).
"""
from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel_order import ChannelOrderV2
from app.webhooks.normalized import NormalizedEvent

logger = structlog.get_logger(__name__)


def _map_order(evt: NormalizedEvent) -> dict[str, Any]:
    """Extract canonical order fields from NormalizedEvent payload."""
    p = evt.payload

    # ── Shopify ───────────────────────────────────────────────────────────────
    if evt.channel == "shopify":
        buyer = p.get("customer") or {}
        return {
            "external_order_id": evt.external_id,
            "channel":           evt.channel,
            "currency":          p.get("currency"),
            "total_price":       _safe_decimal(p.get("total_price")),
            "buyer_name":        (
                f"{buyer.get('first_name', '')} {buyer.get('last_name', '')}".strip()
                or None
            ),
            "buyer_email":       p.get("email") or buyer.get("email"),
            "status":            "received",
            "raw_payload":       p,
            "webhook_event_id":  evt.event_id,
        }

    # ── Shopee ────────────────────────────────────────────────────────────────
    if evt.channel == "shopee":
        # Shopee wraps data inside a "data" envelope: {"code": 3, "data": {...}}
        d = p.get("data") or p
        return {
            "external_order_id": evt.external_id,
            "channel":           evt.channel,
            "currency":          d.get("currency"),
            "total_price":       _safe_decimal(d.get("total_amount") or d.get("total_price")),
            "buyer_name":        d.get("buyer_username"),
            "buyer_email":       None,
            "status":            "received",
            "raw_payload":       p,
            "webhook_event_id":  evt.event_id,
        }

    # ── TikTok ────────────────────────────────────────────────────────────────
    if evt.channel == "tiktok":
        # TikTok wraps data inside "data" envelope: {"type": "...", "data": {...}}
        d = p.get("data") or p
        return {
            "external_order_id": evt.external_id,
            "channel":           evt.channel,
            "currency":          d.get("currency"),
            "total_price":       _safe_decimal(d.get("payment_info", {}).get("total_amount")
                                               or d.get("total_price")),
            "buyer_name":        d.get("recipient_address", {}).get("name"),
            "buyer_email":       None,
            "status":            "received",
            "raw_payload":       p,
            "webhook_event_id":  evt.event_id,
        }

    # ── Generic fallback ──────────────────────────────────────────────────────
    return {
        "external_order_id": evt.external_id,
        "channel":           evt.channel,
        "currency":          p.get("currency"),
        "total_price":       _safe_decimal(p.get("total_price")),
        "buyer_name":        p.get("buyer_name"),
        "buyer_email":       p.get("buyer_email"),
        "status":            "received",
        "raw_payload":       p,
        "webhook_event_id":  evt.event_id,
    }


async def handle_order_created(
    evt: NormalizedEvent,
    db: AsyncSession,
) -> ChannelOrderV2:
    """
    Upsert a ChannelOrderV2 row for the given event.

    If a row for (channel, external_order_id) already exists we still
    return it without raising (the upstream idempotency guard already
    prevented double-processing via webhook_events).
    """
    data = _map_order(evt)

    # Check for existing row
    existing = await db.execute(
        select(ChannelOrderV2).where(
            ChannelOrderV2.channel == data["channel"],
            ChannelOrderV2.external_order_id == data["external_order_id"],
        )
    )
    row = existing.scalar_one_or_none()

    if row:
        logger.info(
            "order_created.already_exists",
            channel=evt.channel,
            external_order_id=evt.external_id,
        )
        return row

    row = ChannelOrderV2(**data)
    db.add(row)
    await db.flush()

    logger.info(
        "order_created.saved",
        channel=evt.channel,
        external_order_id=evt.external_id,
        order_id=str(row.id),
    )

    # ── Sprint 14: enqueue auto-fulfillment Celery task ───────────────────────
    _enqueue_fulfillment(str(row.id), channel=evt.channel)

    return row


def _enqueue_fulfillment(channel_order_id: str, *, channel: str = "shopify") -> None:
    """
    Enqueue the process_order_fulfillment Celery task.

    Only fires for Shopify orders (other channels may not support auto-fulfillment yet).
    Silently no-ops if Celery is unavailable (e.g. during unit tests without broker).
    """
    if channel != "shopify":
        return
    try:
        from app.workers.tasks_fulfillment import process_order_fulfillment
        # Use apply_async with ignore_result=True to avoid blocking on broker connection.
        # In test environments the task is always_eager or broker is absent.
        result = process_order_fulfillment.apply_async(
            kwargs={"channel_order_id": channel_order_id, "dry_run": False},
            countdown=2,  # small delay to ensure DB commit completes first
        )
        logger.info(
            "order_created.fulfillment_enqueued",
            channel_order_id=channel_order_id,
            task_id=getattr(result, "id", "unknown"),
        )
    except Exception as exc:  # noqa: BLE001
        # Never let task enqueue failure break the webhook response.
        # This is expected in test environments without a real Redis broker.
        logger.warning(
            "order_created.fulfillment_enqueue_failed",
            channel_order_id=channel_order_id,
            error=str(exc),
        )


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_decimal(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
