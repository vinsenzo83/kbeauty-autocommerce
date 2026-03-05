"""
app/webhooks/handlers/product_updated.py
──────────────────────────────────────────
Sprint 10 – Handles topic=product.updated from any channel.

Upserts a CanonicalProduct row keyed on canonical_sku derived from
(channel + external_product_id).  Stores raw_payload in
image_urls_json field for audit/debug.
"""
from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_product import CanonicalProduct
from app.webhooks.normalized import NormalizedEvent

logger = structlog.get_logger(__name__)


def _make_sku(channel: str, external_product_id: str) -> str:
    """Derive a stable canonical_sku from channel + external id."""
    return f"{channel}-{external_product_id}"


def _map_product(evt: NormalizedEvent) -> dict[str, Any]:
    """Extract canonical product fields from NormalizedEvent payload."""
    p   = evt.payload
    sku = _make_sku(evt.channel, evt.external_id)

    # ── Shopify ───────────────────────────────────────────────────────────────
    if evt.channel == "shopify":
        variant  = (p.get("variants") or [{}])[0]
        images   = [img.get("src") for img in (p.get("images") or []) if img.get("src")]
        return {
            "canonical_sku":    sku,
            "name":             p.get("title") or f"Product {evt.external_id}",
            "brand":            p.get("vendor"),
            "image_urls_json":  str(images) if images else None,
            "pricing_enabled":  True,
        }

    # ── Shopee ────────────────────────────────────────────────────────────────
    if evt.channel == "shopee":
        images = p.get("image", {}).get("image_url_list") or []
        return {
            "canonical_sku":   sku,
            "name":            p.get("item_name") or f"Product {evt.external_id}",
            "brand":           None,
            "image_urls_json": str(images) if images else None,
            "pricing_enabled": True,
        }

    # ── TikTok ────────────────────────────────────────────────────────────────
    if evt.channel == "tiktok":
        # TikTok wraps product data inside "data" envelope
        d = p.get("data") or p
        images = [img.get("url") for img in (d.get("main_images") or []) if img.get("url")]
        return {
            "canonical_sku":   sku,
            "name":            d.get("title") or f"Product {evt.external_id}",
            "brand":           None,
            "image_urls_json": str(images) if images else None,
            "pricing_enabled": True,
        }

    # ── Generic fallback ──────────────────────────────────────────────────────
    return {
        "canonical_sku":   sku,
        "name":            p.get("title") or p.get("name") or f"Product {evt.external_id}",
        "brand":           p.get("brand") or p.get("vendor"),
        "image_urls_json": None,
        "pricing_enabled": True,
    }


async def handle_product_updated(
    evt: NormalizedEvent,
    db: AsyncSession,
) -> CanonicalProduct:
    """
    Upsert a CanonicalProduct row for the given event.
    """
    data = _map_product(evt)
    sku  = data["canonical_sku"]

    existing = await db.execute(
        select(CanonicalProduct).where(CanonicalProduct.canonical_sku == sku)
    )
    row = existing.scalar_one_or_none()

    if row:
        # Update mutable fields
        row.name             = data["name"]
        row.brand            = data.get("brand")
        row.image_urls_json  = data.get("image_urls_json")
        logger.info(
            "product_updated.updated",
            channel=evt.channel,
            canonical_sku=sku,
        )
        await db.flush()
        return row

    row = CanonicalProduct(**data)
    db.add(row)
    await db.flush()

    logger.info(
        "product_updated.created",
        channel=evt.channel,
        canonical_sku=sku,
        product_id=str(row.id),
    )
    return row
