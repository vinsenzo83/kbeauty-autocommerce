from __future__ import annotations

"""
app/services/product_service.py
────────────────────────────────
CRUD and query helpers for the ``products`` table.

All functions accept an ``AsyncSession`` and return ORM model instances.
"""

from typing import Any, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product

logger = structlog.get_logger(__name__)


# ── Write helpers ─────────────────────────────────────────────────────────────

async def upsert_product(session: AsyncSession, data: dict[str, Any]) -> Product:
    """
    Insert or update a product keyed by ``supplier_product_id``.

    Parameters
    ----------
    session : AsyncSession
    data    : dict with at minimum ``supplier_product_id`` and ``name``.

    Returns
    -------
    The upserted ORM Product instance (refreshed from DB).

    Notes
    -----
    Uses PostgreSQL ``INSERT … ON CONFLICT DO UPDATE`` so this is safe
    for concurrent workers.

    For SQLite (used in tests) we fall back to a SELECT-then-INSERT/UPDATE
    pattern because SQLite's ``INSERT OR REPLACE`` resets the primary key.
    """
    from sqlalchemy.engine import make_url

    url_obj = session.bind.engine.url if hasattr(session, "bind") else None  # type: ignore[attr-defined]

    # Detect dialect via bind info in session
    engine_dialect = ""
    try:
        engine_dialect = session.get_bind().dialect.name  # type: ignore[attr-defined]
    except Exception:
        try:
            # async session → access via engine attribute on sessionmaker bind
            engine_dialect = session.sync_session.bind.dialect.name  # type: ignore[attr-defined]
        except Exception:
            pass

    supplier_product_id = data["supplier_product_id"]

    if engine_dialect == "sqlite":
        # ── SQLite fallback (tests) ───────────────────────────────────────────
        result = await session.execute(
            select(Product).where(Product.supplier_product_id == supplier_product_id)
        )
        existing = result.scalar_one_or_none()

        if existing is None:
            product = Product(
                supplier             = data.get("supplier", "stylekorean"),
                supplier_product_id  = supplier_product_id,
                supplier_product_url = data.get("supplier_product_url", ""),
                name                 = data.get("name", ""),
                brand                = data.get("brand"),
                price                = data.get("price"),
                sale_price           = data.get("sale_price"),
                currency             = data.get("currency", "USD"),
                stock_status         = data.get("stock_status", "unknown"),
                image_urls_json      = data.get("image_urls") or data.get("image_urls_json"),
                shopify_product_id   = data.get("shopify_product_id"),
            )
            session.add(product)
            await session.flush()
            logger.info(
                "product_service.inserted",
                supplier_product_id=supplier_product_id,
            )
        else:
            _apply_updates(existing, data)
            await session.flush()
            product = existing
            logger.info(
                "product_service.updated",
                supplier_product_id=supplier_product_id,
            )
        await session.refresh(product)
        return product

    else:
        # ── PostgreSQL (production) ───────────────────────────────────────────
        stmt = (
            pg_insert(Product)
            .values(
                supplier             = data.get("supplier", "stylekorean"),
                supplier_product_id  = supplier_product_id,
                supplier_product_url = data.get("supplier_product_url", ""),
                name                 = data.get("name", ""),
                brand                = data.get("brand"),
                price                = data.get("price"),
                sale_price           = data.get("sale_price"),
                currency             = data.get("currency", "USD"),
                stock_status         = data.get("stock_status", "unknown"),
                image_urls_json      = data.get("image_urls") or data.get("image_urls_json"),
                shopify_product_id   = data.get("shopify_product_id"),
            )
            .on_conflict_do_update(
                index_elements=["supplier_product_id"],
                set_={
                    "name":                 data.get("name", ""),
                    "brand":                data.get("brand"),
                    "price":                data.get("price"),
                    "sale_price":           data.get("sale_price"),
                    "stock_status":         data.get("stock_status", "unknown"),
                    "image_urls_json":      data.get("image_urls") or data.get("image_urls_json"),
                    "supplier_product_url": data.get("supplier_product_url", ""),
                },
            )
            .returning(Product)
        )
        result = await session.execute(stmt)
        product = result.scalar_one()
        await session.flush()
        logger.info(
            "product_service.upserted",
            supplier_product_id=supplier_product_id,
        )
        return product


def _apply_updates(product: Product, data: dict[str, Any]) -> None:
    """Apply mutable fields from ``data`` onto an existing ``Product`` instance."""
    for field in (
        "name", "brand", "price", "sale_price",
        "currency", "stock_status", "supplier_product_url",
    ):
        if field in data:
            setattr(product, field, data[field])
    if "image_urls" in data:
        product.image_urls_json = data["image_urls"]
    elif "image_urls_json" in data:
        product.image_urls_json = data["image_urls_json"]
    if "shopify_product_id" in data:
        product.shopify_product_id = data["shopify_product_id"]


# ── Read helpers ──────────────────────────────────────────────────────────────

async def get_product_by_id(session: AsyncSession, product_id: str) -> Product | None:
    result = await session.execute(
        select(Product).where(Product.id == product_id)
    )
    return result.scalar_one_or_none()


async def get_product_by_supplier_id(
    session: AsyncSession, supplier_product_id: str
) -> Product | None:
    result = await session.execute(
        select(Product).where(Product.supplier_product_id == supplier_product_id)
    )
    return result.scalar_one_or_none()


async def get_unsynced_products(session: AsyncSession) -> Sequence[Product]:
    """
    Return all products that have no ``shopify_product_id`` yet
    (i.e. not yet synced to Shopify).
    """
    result = await session.execute(
        select(Product).where(Product.shopify_product_id.is_(None))  # type: ignore[attr-defined]
    )
    return result.scalars().all()


async def mark_synced(
    session: AsyncSession,
    product: Product,
    shopify_product_id: str,
) -> Product:
    """Set shopify_product_id on a product and flush."""
    product.shopify_product_id = shopify_product_id
    await session.flush()
    logger.info(
        "product_service.mark_synced",
        supplier_product_id=product.supplier_product_id,
        shopify_product_id=shopify_product_id,
    )
    return product
