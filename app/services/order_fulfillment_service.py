from __future__ import annotations

"""
app/services/order_fulfillment_service.py
──────────────────────────────────────────
Sprint 14 – Automated supplier order placement and fulfillment.

Main entry point
----------------
process_channel_order(channel_order_id, session, *, dry_run=False)
    → SupplierOrder

Pipeline
--------
1.  Load ChannelOrderV2 by id.
2.  Extract items → determine canonical_product_ids from raw_payload.
3.  For each canonical product:
        a. select_best_supplier_for_canonical()   (Task 4)
        b. build order_payload
        c. client.place_order(order_payload)       (Task 5)
        d. persist SupplierOrder row
4.  Update channel_order status to 'processing'.
5.  Return the created SupplierOrder.

Failure handling
----------------
- NO_SUPPLIER_AVAILABLE : no in-stock supplier found.
- SUPPLIER_API_ERROR    : supplier.place_order() raised SupplierError.
- Max 3 retries with exponential back-off (managed by Celery task, not here).
- On failure, SupplierOrder.status = 'failed', failure_reason = <code>.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.channel_order import ChannelOrderV2
from app.models.supplier_order import FailureReason, SupplierOrder, SupplierOrderStatus
from app.services.supplier_router import choose_best_supplier_for_canonical
from app.suppliers.base import SupplierError

logger = structlog.get_logger(__name__)

# Maximum retries before marking FAILED permanently
MAX_RETRIES = 3


# ─────────────────────────────────────────────────────────────────────────────
# Helper: resolve canonical_product_id from channel order payload
# ─────────────────────────────────────────────────────────────────────────────

def _extract_canonical_ids_from_payload(
    raw_payload: dict[str, Any],
) -> list[str]:
    """
    Best-effort extraction of canonical_product_ids from a raw webhook payload.

    Shopify line_items may carry a `sku` field that maps to canonical_sku.
    For now we return SKUs; the caller resolves them to UUIDs via DB.
    Falls back to empty list → caller marks NO_SUPPLIER_AVAILABLE.
    """
    line_items = raw_payload.get("line_items") or []
    skus: list[str] = []
    for item in line_items:
        sku = item.get("sku") or item.get("variant_sku")
        if sku:
            skus.append(str(sku))
    return skus


async def _resolve_canonical_id_by_sku(
    sku: str,
    session: AsyncSession,
) -> uuid.UUID | None:
    """Resolve canonical_sku → canonical_product_id."""
    from app.models.canonical_product import CanonicalProduct

    res = await session.execute(
        select(CanonicalProduct).where(CanonicalProduct.canonical_sku == sku)
    )
    cp = res.scalar_one_or_none()
    return cp.id if cp else None


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build order_payload for supplier
# ─────────────────────────────────────────────────────────────────────────────

def _build_order_payload(
    order: ChannelOrderV2,
    supplier_info: dict[str, Any],
    canonical_sku: str,
) -> dict[str, Any]:
    """Build the dict passed to supplier.place_order()."""
    raw = order.raw_payload or {}

    # Extract shipping address from Shopify payload
    shipping_addr = raw.get("shipping_address") or {}

    return {
        "channel_order_id":    str(order.id),
        "canonical_sku":       canonical_sku,
        "supplier_product_id": supplier_info.get("supplier_product_id", ""),
        "quantity":            1,
        "cost":                supplier_info.get("price"),
        "currency":            order.currency or "USD",
        "shipping_address": {
            "name":     shipping_addr.get("name") or order.buyer_name or "",
            "address1": shipping_addr.get("address1", ""),
            "city":     shipping_addr.get("city", ""),
            "country":  shipping_addr.get("country_code", "US"),
            "zip":      shipping_addr.get("zip", ""),
        },
        "buyer_name":  order.buyer_name,
        "buyer_email": order.buyer_email,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helper: get or create SupplierOrder row
# ─────────────────────────────────────────────────────────────────────────────

async def _get_or_create_supplier_order(
    session: AsyncSession,
    channel_order_id: uuid.UUID,
    supplier_name: str,
    cost: float | None,
    currency: str,
) -> SupplierOrder:
    """Get existing or create new SupplierOrder for (channel_order, supplier)."""
    res = await session.execute(
        select(SupplierOrder).where(
            SupplierOrder.channel_order_id == channel_order_id,
            SupplierOrder.supplier == supplier_name,
        )
    )
    so = res.scalar_one_or_none()
    if so is None:
        so = SupplierOrder(
            channel_order_id=channel_order_id,
            supplier=supplier_name,
            cost=cost,
            currency=currency,
            status=SupplierOrderStatus.PENDING,
        )
        session.add(so)
        await session.flush()
    return so


# ─────────────────────────────────────────────────────────────────────────────
# Main: process_channel_order
# ─────────────────────────────────────────────────────────────────────────────

async def process_channel_order(
    channel_order_id: str | uuid.UUID,
    session: AsyncSession,
    *,
    dry_run: bool = False,
    supplier_client_factory: Any = None,
) -> list[SupplierOrder]:
    """
    Auto-fulfillment pipeline for a single ChannelOrderV2.

    Parameters
    ----------
    channel_order_id       : UUID of the ChannelOrderV2 row.
    session                : Async SQLAlchemy session (caller commits).
    dry_run                : If True, do not call supplier.place_order().
    supplier_client_factory: Callable(supplier_name) → SupplierClient.
                             Injected in tests for mocking.

    Returns
    -------
    list[SupplierOrder] – one per processed line item / canonical product.
    """
    if isinstance(channel_order_id, str):
        channel_order_id = uuid.UUID(channel_order_id)

    log = logger.bind(channel_order_id=str(channel_order_id), dry_run=dry_run)
    log.info("fulfillment.start")

    # ── 1. Load order ─────────────────────────────────────────────────────────
    res = await session.execute(
        select(ChannelOrderV2).where(ChannelOrderV2.id == channel_order_id)
    )
    order = res.scalar_one_or_none()
    if order is None:
        log.error("fulfillment.order_not_found")
        return []

    # ── 2. Extract SKUs from payload ──────────────────────────────────────────
    skus = _extract_canonical_ids_from_payload(order.raw_payload or {})
    if not skus:
        # No line items — create a single failed SupplierOrder
        so = SupplierOrder(
            channel_order_id=channel_order_id,
            supplier="UNKNOWN",
            status=SupplierOrderStatus.FAILED,
            failure_reason=FailureReason.NO_SUPPLIER_AVAILABLE,
        )
        session.add(so)
        await session.flush()
        log.warning("fulfillment.no_line_items")
        return [so]

    results: list[SupplierOrder] = []

    for sku in skus:
        sku_log = log.bind(sku=sku)

        # ── 3a. Resolve canonical_product_id ──────────────────────────────────
        canonical_id = await _resolve_canonical_id_by_sku(sku, session)
        if canonical_id is None:
            sku_log.warning("fulfillment.sku_not_found_in_canonical")
            continue

        # ── 3b. Select best supplier ──────────────────────────────────────────
        supplier_info = await choose_best_supplier_for_canonical(canonical_id, session)
        if supplier_info is None:
            so = await _get_or_create_supplier_order(
                session, channel_order_id, "UNKNOWN", None, order.currency or "USD"
            )
            so.status = SupplierOrderStatus.FAILED
            so.failure_reason = FailureReason.NO_SUPPLIER_AVAILABLE
            await session.flush()
            sku_log.warning("fulfillment.no_in_stock_supplier")
            results.append(so)
            continue

        supplier_name = supplier_info["supplier"].upper()
        cost = supplier_info.get("price")
        currency = order.currency or "USD"

        # ── 3c. Get or create SupplierOrder row ───────────────────────────────
        so = await _get_or_create_supplier_order(
            session, channel_order_id, supplier_name, cost, currency
        )

        if so.status not in (SupplierOrderStatus.PENDING, SupplierOrderStatus.FAILED):
            sku_log.info("fulfillment.already_processed", status=so.status)
            results.append(so)
            continue

        # ── 3d. Build order_payload ───────────────────────────────────────────
        order_payload = _build_order_payload(order, supplier_info, sku)

        # ── 3e. Place order (or simulate in dry_run) ──────────────────────────
        if dry_run:
            so.supplier_order_id = f"DRYRUN-{str(channel_order_id)[:8]}"
            so.supplier_status   = "placed"
            so.status            = SupplierOrderStatus.PLACED
            await session.flush()
            sku_log.info("fulfillment.dry_run_placed", supplier=supplier_name)
            results.append(so)
            continue

        # Get supplier client
        if supplier_client_factory is not None:
            client = supplier_client_factory(supplier_name)
        else:
            from app.services.supplier_router import _make_client
            client = _make_client(supplier_name)

        try:
            placed = await client.place_order(order_payload)
            so.supplier_order_id = placed.supplier_order_id
            so.supplier_status   = placed.status
            so.status            = SupplierOrderStatus.PLACED
            so.cost              = placed.cost or cost
            so.updated_at        = datetime.now(timezone.utc)
            await session.flush()
            sku_log.info(
                "fulfillment.placed",
                supplier=supplier_name,
                supplier_order_id=placed.supplier_order_id,
            )

        except SupplierError as exc:
            so.status         = SupplierOrderStatus.FAILED
            so.failure_reason = FailureReason.SUPPLIER_API_ERROR
            so.retry_count    = (so.retry_count or 0) + 1
            so.updated_at     = datetime.now(timezone.utc)
            await session.flush()
            sku_log.error(
                "fulfillment.supplier_error",
                supplier=supplier_name,
                error=str(exc),
                retryable=exc.retryable,
            )

        except Exception as exc:  # noqa: BLE001
            so.status         = SupplierOrderStatus.FAILED
            so.failure_reason = FailureReason.SUPPLIER_API_ERROR
            so.retry_count    = (so.retry_count or 0) + 1
            so.updated_at     = datetime.now(timezone.utc)
            await session.flush()
            sku_log.error("fulfillment.unexpected_error", error=str(exc))

        results.append(so)

    # ── 4. Update channel order status ────────────────────────────────────────
    placed_count = sum(
        1 for so in results if so.status == SupplierOrderStatus.PLACED
    )
    if placed_count > 0:
        order.status = "processing"
    elif all(so.status == SupplierOrderStatus.FAILED for so in results):
        order.status = "fulfillment_failed"
    order.updated_at = datetime.now(timezone.utc)
    await session.flush()

    log.info(
        "fulfillment.done",
        total=len(results),
        placed=placed_count,
        failed=len(results) - placed_count,
    )
    return results
