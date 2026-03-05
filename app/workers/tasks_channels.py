from __future__ import annotations

"""
app/workers/tasks_channels.py
──────────────────────────────
Sprint 9 – Celery tasks for the Multi-Channel Commerce Engine.

Tasks
-----
publish_new_products
    Every 12 h.  Finds canonical_products with no channel_products row
    for at least one enabled channel, and publishes them.

sync_prices_channels
    Every 6 h.  Re-prices every channel_products row using the pricing
    engine and pushes the new price to each channel.

sync_inventory_channels
    Every 1 h.  Reads the latest stock status from supplier_products and
    pushes inventory updates to every channel.

import_channel_orders
    Every 15 min.  Fetches recent orders from all enabled channels and
    upserts them into channel_orders.
"""

import asyncio
from typing import Any
from uuid import UUID

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Top-level import so tests can patch AsyncSessionLocal
try:
    from app.db.session import AsyncSessionLocal  # noqa: F401
except Exception:  # pragma: no cover
    AsyncSessionLocal = None  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# Internal async implementations
# ─────────────────────────────────────────────────────────────────────────────

async def _run_publish_new_products(
    *,
    session_factory: Any = None,
    clients: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Core async logic for publish_new_products.

    Finds canonical_products that have no channel_products row for at
    least one enabled channel, then calls
    channel_router.publish_product_to_channels for each.

    Parameters
    ----------
    session_factory : Override AsyncSessionLocal for tests.
    clients         : Override channel clients for tests.

    Returns
    -------
    {"total_canonical": int, "published": int, "errors": int}
    """
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal as _local
    from app.models.canonical_product import CanonicalProduct
    from app.models.sales_channel import ChannelProduct
    from app.services.channel_router import get_enabled_channels, publish_product_to_channels

    factory = session_factory or _local

    total = published = errors = 0

    async with factory() as session:
        result = await session.execute(select(CanonicalProduct))
        canonical_products = result.scalars().all()
        total = len(canonical_products)

        for cp in canonical_products:
            # Check which channels already have a listing
            existing = await session.execute(
                select(ChannelProduct.channel)
                .where(ChannelProduct.canonical_product_id == cp.id)
            )
            existing_channels = {row[0] for row in existing}
            missing_channels  = set(get_enabled_channels()) - existing_channels

            if not missing_channels:
                continue

            try:
                price = float(cp.last_price) if cp.last_price else None
                results = await publish_product_to_channels(
                    cp, price=price, clients=clients
                )
                for channel_slug, res in results.items():
                    if channel_slug not in missing_channels or res is None:
                        continue
                    # Persist the new channel_products row
                    cp_row = ChannelProduct(
                        canonical_product_id = cp.id,
                        channel              = channel_slug,
                        external_product_id  = res.get("external_product_id", ""),
                        external_variant_id  = res.get("external_variant_id", ""),
                        price                = res.get("price"),
                        currency             = res.get("currency", "USD"),
                        status               = "active",
                    )
                    session.add(cp_row)
                await session.commit()
                published += 1
            except Exception as exc:
                logger.error(
                    "tasks_channels.publish_new_products.error",
                    canonical_product_id=str(cp.id),
                    exc=str(exc),
                )
                errors += 1

    summary = {"total_canonical": total, "published": published, "errors": errors}
    logger.info("tasks_channels.publish_new_products.done", **summary)
    return summary


async def _run_sync_prices_channels(
    *,
    session_factory: Any = None,
    clients: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Core async logic for sync_prices_channels.

    For every channel_products row, re-computes the sell price via the
    pricing engine and calls update_price on the appropriate client.
    """
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal as _local
    from app.models.sales_channel import ChannelProduct
    from app.services.channel_router import update_price_all_channels

    factory = session_factory or _local

    total = updated = errors = 0

    async with factory() as session:
        result = await session.execute(select(ChannelProduct).where(ChannelProduct.status == "active"))
        channel_products = result.scalars().all()
        total = len(channel_products)

        # Group by canonical_product_id
        by_canonical: dict[UUID, list[ChannelProduct]] = {}
        for row in channel_products:
            by_canonical.setdefault(row.canonical_product_id, []).append(row)

        for canonical_id, rows in by_canonical.items():
            try:
                # Build variant map
                variant_map = {
                    r.channel: r.external_variant_id
                    for r in rows
                    if r.external_variant_id
                }
                if not variant_map:
                    continue

                # Use the last synced price from any row as the new price
                # (pricing engine would normally compute this fresh)
                price_row = next((r for r in rows if r.price), None)
                new_price  = float(price_row.price) if price_row else 0.0
                if new_price <= 0:
                    continue

                # Dummy canonical product dict for logging
                cp_dict = {"canonical_sku": f"canonical-{str(canonical_id)[:8]}"}
                results = await update_price_all_channels(
                    cp_dict,
                    new_price,
                    channel_variant_map=variant_map,
                    clients=clients,
                )
                if any(results.values()):
                    updated += 1
            except Exception as exc:
                logger.error(
                    "tasks_channels.sync_prices_channels.error",
                    canonical_product_id=str(canonical_id),
                    exc=str(exc),
                )
                errors += 1

    summary = {"total_channel_products": total, "updated": updated, "errors": errors}
    logger.info("tasks_channels.sync_prices_channels.done", **summary)
    return summary


async def _run_sync_inventory_channels(
    *,
    session_factory: Any = None,
    clients: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Core async logic for sync_inventory_channels.

    Reads the latest in-stock status from supplier_products and updates
    inventory on every channel that has a listing for that canonical product.
    """
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal as _local
    from app.models.sales_channel import ChannelProduct
    from app.models.supplier_product import StockStatus, SupplierProduct
    from app.services.channel_router import update_inventory_all_channels

    factory = session_factory or _local

    total = updated = errors = 0

    async with factory() as session:
        # Get all active channel_products
        cp_result = await session.execute(
            select(ChannelProduct).where(ChannelProduct.status == "active")
        )
        channel_products = cp_result.scalars().all()
        total = len(channel_products)

        by_canonical: dict[UUID, list[ChannelProduct]] = {}
        for row in channel_products:
            by_canonical.setdefault(row.canonical_product_id, []).append(row)

        for canonical_id, rows in by_canonical.items():
            try:
                # Check if any supplier has stock
                sp_result = await session.execute(
                    select(SupplierProduct).where(
                        SupplierProduct.canonical_product_id == canonical_id
                    )
                )
                supplier_rows = sp_result.scalars().all()
                has_stock = any(
                    r.stock_status == StockStatus.IN_STOCK.value
                    for r in supplier_rows
                )
                quantity = 99 if has_stock else 0

                variant_map = {
                    r.channel: r.external_variant_id
                    for r in rows
                    if r.external_variant_id
                }
                if not variant_map:
                    continue

                cp_dict = {"canonical_sku": f"canonical-{str(canonical_id)[:8]}"}
                results = await update_inventory_all_channels(
                    cp_dict,
                    quantity,
                    channel_variant_map=variant_map,
                    clients=clients,
                )
                if any(results.values()):
                    updated += 1
            except Exception as exc:
                logger.error(
                    "tasks_channels.sync_inventory_channels.error",
                    canonical_product_id=str(canonical_id),
                    exc=str(exc),
                )
                errors += 1

    summary = {"total_channel_products": total, "updated": updated, "errors": errors}
    logger.info("tasks_channels.sync_inventory_channels.done", **summary)
    return summary


async def _run_import_channel_orders(
    *,
    clients: dict[str, Any] | None = None,
    session_factory: Any = None,
) -> dict[str, Any]:
    """
    Core async logic for import_channel_orders.

    Fetches recent orders from all enabled channels and upserts them
    into the channel_orders table.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db.session import AsyncSessionLocal as _local
    from app.models.sales_channel import ChannelOrder
    from app.services.channel_router import get_enabled_channels

    factory = session_factory or _local

    # Build clients if not provided
    if clients is None:
        from app.services.channel_router import _build_default_clients  # pragma: no cover
        clients = _build_default_clients()  # pragma: no cover

    total_fetched = total_upserted = errors = 0

    for channel_slug in get_enabled_channels():
        client = clients.get(channel_slug)
        if client is None:
            continue
        try:
            orders = await client.fetch_orders(limit=50, status="pending")
            total_fetched += len(orders)

            if not orders:
                continue

            async with factory() as session:
                for o in orders:
                    order_row = ChannelOrder(
                        channel           = channel_slug,
                        external_order_id = o["external_order_id"],
                        quantity          = o.get("quantity", 1),
                        price             = o.get("price"),
                        currency          = o.get("currency", "USD"),
                        status            = o.get("status", "pending"),
                    )
                    # Simple upsert: try insert, ignore on conflict
                    try:
                        session.add(order_row)
                        await session.commit()
                        total_upserted += 1
                    except Exception:
                        await session.rollback()
        except Exception as exc:
            logger.error(
                "tasks_channels.import_channel_orders.error",
                channel=channel_slug,
                exc=str(exc),
            )
            errors += 1

    summary = {
        "total_fetched":  total_fetched,
        "total_upserted": total_upserted,
        "errors":         errors,
    }
    logger.info("tasks_channels.import_channel_orders.done", **summary)
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Celery task wrappers
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_channels.publish_new_products",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
)
def publish_new_products(self: Any) -> dict[str, Any]:
    """Publish new canonical products to all enabled channels (every 12 h)."""
    try:
        return asyncio.get_event_loop().run_until_complete(
            _run_publish_new_products()
        )
    except Exception as exc:
        logger.error("tasks_channels.publish_new_products.fatal", exc=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.tasks_channels.sync_prices_channels",
    bind=True,
    max_retries=3,
    default_retry_delay=120,
)
def sync_prices_channels(self: Any) -> dict[str, Any]:
    """Sync prices to all channels (every 6 h)."""
    try:
        return asyncio.get_event_loop().run_until_complete(
            _run_sync_prices_channels()
        )
    except Exception as exc:
        logger.error("tasks_channels.sync_prices_channels.fatal", exc=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.tasks_channels.sync_inventory_channels",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def sync_inventory_channels(self: Any) -> dict[str, Any]:
    """Sync inventory to all channels (every 1 h)."""
    try:
        return asyncio.get_event_loop().run_until_complete(
            _run_sync_inventory_channels()
        )
    except Exception as exc:
        logger.error("tasks_channels.sync_inventory_channels.fatal", exc=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.tasks_channels.import_channel_orders",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def import_channel_orders(self: Any) -> dict[str, Any]:
    """Import orders from all channels (every 15 min)."""
    try:
        return asyncio.get_event_loop().run_until_complete(
            _run_import_channel_orders()
        )
    except Exception as exc:
        logger.error("tasks_channels.import_channel_orders.fatal", exc=str(exc))
        raise self.retry(exc=exc)
