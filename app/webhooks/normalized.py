"""
app/webhooks/normalized.py
──────────────────────────
Sprint 10 – Unified NormalizedEvent dataclass.

All channel-specific webhook payloads are converted to this
before any DB write or handler dispatch.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

ChannelName = Literal["shopify", "shopee", "tiktok"]
TopicName   = Literal["order.created", "product.updated"]


@dataclass
class NormalizedEvent:
    channel:     ChannelName
    topic:       TopicName
    event_id:    str          # idempotency key
    external_id: str          # order-id / product-id from the channel
    payload:     dict[str, Any]
    occurred_at: datetime     = field(default_factory=lambda: datetime.now(timezone.utc))
    shop_id:     str | None   = None
    headers:     dict[str, str] = field(default_factory=dict)

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        *,
        channel:     ChannelName,
        topic:       TopicName,
        payload:     dict[str, Any],
        headers:     dict[str, str] | None = None,
        external_id: str | None = None,
        occurred_at: datetime | None = None,
        shop_id:     str | None = None,
    ) -> "NormalizedEvent":
        """
        Build a NormalizedEvent, deriving event_id if not supplied by channel.

        event_id priority:
          1) X-Event-Id / X-Request-Id header (if present)
          2) sha256(channel + topic + external_id + raw_payload)
        """
        hdrs = {k.lower(): v for k, v in (headers or {}).items()}

        # Try to pick a stable event_id from headers
        event_id = (
            hdrs.get("x-event-id")
            or hdrs.get("x-request-id")
            or hdrs.get("x-shopify-webhook-id")
        )

        ext_id = external_id or _extract_external_id(channel, topic, payload)

        if not event_id:
            raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            event_id = _sha256(channel, topic, ext_id or "", raw)

        ts = occurred_at or _extract_occurred_at(channel, payload)

        return cls(
            channel=channel,
            topic=topic,
            event_id=event_id,
            external_id=ext_id or "",
            payload=payload,
            occurred_at=ts,
            shop_id=shop_id,
            headers=hdrs,
        )


# ── helpers ───────────────────────────────────────────────────────────────────

def _sha256(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode())
        h.update(b":")
    return h.hexdigest()


def _extract_external_id(
    channel: str, topic: str, payload: dict[str, Any]
) -> str | None:
    """Best-effort external ID extraction per channel × topic.

    Handles both flat payloads (Shopify) and nested ``data`` envelopes
    (Shopee, TikTok).
    """
    # Many channels wrap the real event data inside a "data" key
    inner: dict[str, Any] = payload.get("data") or payload

    pid = (
        inner.get("id")
        or inner.get("order_id")
        or inner.get("product_id")
        or payload.get("id")
        or payload.get("order_id")
        or payload.get("product_id")
    )
    if pid:
        return str(pid)

    # Shopee order: inner.order_sn
    if channel == "shopee":
        sn = inner.get("order_sn") or payload.get("order_sn")
        if sn:
            return str(sn)

    # TikTok – already handled via inner.order_id / inner.product_id above
    return None


def _extract_occurred_at(channel: str, payload: dict[str, Any]) -> datetime:
    """Best-effort timestamp extraction."""
    now = datetime.now(timezone.utc)
    for key in ("created_at", "updated_at", "create_time", "update_time"):
        val = payload.get(key)
        if not val:
            continue
        try:
            if isinstance(val, (int, float)):
                return datetime.fromtimestamp(val, tz=timezone.utc)
            return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except (ValueError, OSError):
            pass
    return now
