from __future__ import annotations

"""
app/services/supplier_product_service.py
──────────────────────────────────────────
Sprint 7 – CRUD helpers for the ``supplier_products`` table.

All functions accept an AsyncSession so they can participate in the caller's transaction.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier_product import SupplierProduct

logger = structlog.get_logger(__name__)

# Deterministic tie-breaker: alphabetical order of supplier name
_SUPPLIER_PRIORITY: dict[str, int] = {
    "JOLSE":        1,
    "OLIVEYOUNG":   2,
    "STYLEKOREAN":  3,
}


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public CRUD
# ─────────────────────────────────────────────────────────────────────────────

async def upsert_supplier_product(
    session: AsyncSession,
    *,
    product_id: UUID,
    supplier: str,
    supplier_product_id: str,
    price: float | Decimal | None = None,
    stock_status: str = "IN_STOCK",
    last_checked_at: datetime | None = None,
) -> SupplierProduct:
    """
    Insert or update a SupplierProduct row for (product_id, supplier).

    Parameters
    ----------
    session             : Active async DB session.
    product_id          : FK to products.id.
    supplier            : 'STYLEKOREAN' | 'JOLSE' | 'OLIVEYOUNG'
    supplier_product_id : Supplier-side product ID / SKU.
    price               : Latest price (None if unavailable).
    stock_status        : 'IN_STOCK' or 'OUT_OF_STOCK'.
    last_checked_at     : Override timestamp (defaults to now()).

    Returns
    -------
    The persisted SupplierProduct instance (not yet committed).
    """
    stmt   = select(SupplierProduct).where(
        SupplierProduct.product_id == product_id,
        SupplierProduct.supplier   == supplier,
    )
    result = await session.execute(stmt)
    sp     = result.scalar_one_or_none()

    now = last_checked_at or datetime.now(timezone.utc)

    if sp is None:
        sp = SupplierProduct(
            product_id          = product_id,
            supplier            = supplier,
            supplier_product_id = supplier_product_id,
            price               = _to_decimal(price),
            stock_status        = stock_status,
            last_checked_at     = now,
        )
        session.add(sp)
        logger.info(
            "supplier_product_service.created",
            product_id=str(product_id),
            supplier=supplier,
            price=price,
            stock_status=stock_status,
        )
    else:
        sp.supplier_product_id = supplier_product_id
        sp.price               = _to_decimal(price)
        sp.stock_status        = stock_status
        sp.last_checked_at     = now
        logger.info(
            "supplier_product_service.updated",
            product_id=str(product_id),
            supplier=supplier,
            price=price,
            stock_status=stock_status,
        )

    return sp


# Keep backward-compat alias used by existing code in sprint 6 path
async def save_supplier_product(
    session: AsyncSession,
    *,
    product_id: UUID,
    supplier: str,
    supplier_product_id: str,
    price: float | Decimal | None = None,
    stock_status: str = "IN_STOCK",
) -> SupplierProduct:
    """Alias for upsert_supplier_product (no last_checked_at override)."""
    return await upsert_supplier_product(
        session,
        product_id=product_id,
        supplier=supplier,
        supplier_product_id=supplier_product_id,
        price=price,
        stock_status=stock_status,
    )


async def update_supplier_price(
    session: AsyncSession,
    *,
    product_id: UUID,
    supplier: str,
    new_price: float | Decimal,
) -> bool:
    """Update price for an existing SupplierProduct row. Returns True if found."""
    stmt   = select(SupplierProduct).where(
        SupplierProduct.product_id == product_id,
        SupplierProduct.supplier   == supplier,
    )
    result = await session.execute(stmt)
    sp     = result.scalar_one_or_none()
    if sp is None:
        return False
    sp.price           = _to_decimal(new_price)
    sp.last_checked_at = datetime.now(timezone.utc)
    return True


async def update_supplier_stock(
    session: AsyncSession,
    *,
    product_id: UUID,
    supplier: str,
    in_stock: bool,
) -> bool:
    """Update stock_status for an existing SupplierProduct row. Returns True if found."""
    stmt   = select(SupplierProduct).where(
        SupplierProduct.product_id == product_id,
        SupplierProduct.supplier   == supplier,
    )
    result = await session.execute(stmt)
    sp     = result.scalar_one_or_none()
    if sp is None:
        return False
    sp.stock_status    = "IN_STOCK" if in_stock else "OUT_OF_STOCK"
    sp.last_checked_at = datetime.now(timezone.utc)
    return True


async def get_supplier_products(
    session: AsyncSession,
    product_id: UUID,
) -> list[SupplierProduct]:
    """
    Return all SupplierProduct rows for product_id, ordered cheapest first (NULLs last).
    """
    stmt = (
        select(SupplierProduct)
        .where(SupplierProduct.product_id == product_id)
        .order_by(
            SupplierProduct.price.is_(None),   # NULLs last
            SupplierProduct.price.asc(),
        )
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_best_supplier(
    session: AsyncSession,
    product_id: UUID,
) -> SupplierProduct | None:
    """
    Return the cheapest IN_STOCK SupplierProduct for product_id.

    Algorithm
    ---------
    1. Fetch all rows for product_id.
    2. Filter to IN_STOCK only.
    3. Return the one with the lowest price.
    4. Tie-breaker: alphabetical supplier name (JOLSE < OLIVEYOUNG < STYLEKOREAN).
    5. Returns None when no IN_STOCK row exists.
    """
    rows = await get_supplier_products(session, product_id)
    in_stock = [r for r in rows if r.stock_status == "IN_STOCK"]
    if not in_stock:
        return None

    def _sort_key(r: SupplierProduct) -> tuple:
        # price=None → treated as very high price for sorting
        p = float(r.price) if r.price is not None else float("inf")
        return (p, _SUPPLIER_PRIORITY.get(r.supplier, 99))

    return min(in_stock, key=_sort_key)
