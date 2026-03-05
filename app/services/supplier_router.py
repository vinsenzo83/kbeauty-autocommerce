from __future__ import annotations

"""
app/services/supplier_router.py
─────────────────────────────────
Sprint 7 + Sprint 8 – Multi-supplier routing.

Entry points
------------
choose_best_supplier_for_canonical(canonical_product_id, session)
    → dict  [Sprint 8 primary]
    Select cheapest IN_STOCK supplier via canonical_product_id.

choose_best_supplier(product_id, session)
    → dict  [Sprint 7, backward compat]
    Resolves product_id → canonical_product_id internally.

choose_supplier(order)
    → SupplierClient  [legacy Sprint 2–6 order pipeline]
    Returns the supplier client for an order.

_make_client(supplier_name) -> SupplierClient
    Factory for supplier clients.
"""

from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.suppliers.base import SupplierClient
from app.suppliers.stylekorean import StyleKoreanClient

if TYPE_CHECKING:
    from app.models.order import Order

logger = structlog.get_logger(__name__)

# ── Supplier name → client factory ────────────────────────────────────────────

def _make_client(supplier_name: str) -> SupplierClient:
    """Instantiate the correct SupplierClient for the given supplier name."""
    name = supplier_name.upper()
    if name == "STYLEKOREAN":
        return StyleKoreanClient(mode="playwright", headless=True)
    if name == "JOLSE":
        from app.suppliers.jolse import JolseClient
        return JolseClient(headless=True)
    if name == "OLIVEYOUNG":
        from app.suppliers.oliveyoung import OliveYoungClient
        return OliveYoungClient(headless=True)
    # Unknown supplier → fall back to StyleKorean and log warning
    logger.warning("supplier_router.unknown_supplier", supplier=supplier_name)
    return StyleKoreanClient(mode="playwright", headless=True)


# ── Legacy: order-based routing (Sprint 2–6 pipeline) ─────────────────────────

def choose_supplier(order: "Order") -> SupplierClient:
    """
    Return the appropriate SupplierClient for a given order.

    If the order already has a ``supplier`` field set, honour it.
    Otherwise fall back to StyleKorean.
    """
    supplier_name = getattr(order, "supplier", None) or "stylekorean"
    client = _make_client(supplier_name)
    logger.debug(
        "supplier_router.chose",
        supplier=client.name,
        order_id=str(order.id),
    )
    return client


# ── Sprint 8: canonical-based best-supplier selection ────────────────────────

async def choose_best_supplier_for_canonical(
    canonical_product_id: UUID,
    session: AsyncSession,
) -> dict[str, Any] | None:
    """
    Return the best (cheapest IN_STOCK) supplier for a canonical product.

    Algorithm
    ---------
    1. Load all supplier_products WHERE canonical_product_id = ? AND stock_status = 'IN_STOCK'.
    2. Choose lowest price.
    3. Tie-breaker: alphabetical supplier name (JOLSE < OLIVEYOUNG < STYLEKOREAN).
    4. Return None when no IN_STOCK row exists.

    Returns
    -------
    {
        "supplier":             str,
        "supplier_product_id":  str,
        "supplier_product_url": str | None,
        "price":                float | None,
        "canonical_product_id": str,
    }
    or None.
    """
    from app.models.supplier_product import SupplierProduct

    stmt = select(SupplierProduct).where(
        SupplierProduct.canonical_product_id == canonical_product_id,
        SupplierProduct.stock_status         == "IN_STOCK",
    )
    result   = await session.execute(stmt)
    in_stock = list(result.scalars().all())

    if not in_stock:
        logger.info(
            "supplier_router.no_in_stock_for_canonical",
            canonical_product_id=str(canonical_product_id),
        )
        return None

    def _sort_key(row: Any) -> tuple:
        price = float(row.price) if row.price is not None else float("inf")
        return (price, row.supplier)  # alphabetical tie-breaker

    best = min(in_stock, key=_sort_key)

    result_dict: dict[str, Any] = {
        "supplier":             best.supplier,
        "supplier_product_id":  best.supplier_product_id,
        "supplier_product_url": getattr(best, "supplier_product_url", None),
        "price":                float(best.price) if best.price is not None else None,
        "canonical_product_id": str(canonical_product_id),
    }
    logger.info(
        "supplier_router.best_supplier_for_canonical",
        canonical_product_id=str(canonical_product_id),
        supplier=best.supplier,
        price=result_dict["price"],
    )
    return result_dict


# ── Sprint 7: product_id-based best-supplier (backward compat) ────────────────

async def choose_best_supplier(
    product_id: UUID,
    session: AsyncSession,
) -> dict[str, Any] | None:
    """
    Return the best supplier for a product_id.

    Internally resolves product_id → canonical_product_id, then calls
    choose_best_supplier_for_canonical.  Kept for backward compatibility
    with Sprint 7 code.

    Falls back to direct product_id lookup if no canonical mapping exists.
    """
    from app.models.product import Product

    # Resolve canonical_product_id
    canonical_id: UUID | None = None
    try:
        stmt   = select(Product).where(Product.id == product_id)
        result = await session.execute(stmt)
        prod   = result.scalar_one_or_none()

        if prod is not None:
            canonical_id = getattr(prod, "canonical_product_id", None)
    except Exception:
        # products table may not exist in test environments
        pass

    if canonical_id is not None:
        return await choose_best_supplier_for_canonical(canonical_id, session)

    # Fallback: direct product_id lookup (pre-Sprint 8 rows)
    from app.services.supplier_product_service import get_best_supplier

    best = await get_best_supplier(session, product_id)
    if best is None:
        logger.info(
            "supplier_router.no_in_stock_supplier",
            product_id=str(product_id),
        )
        return None

    result_dict = {
        "supplier":            best.supplier,
        "supplier_product_id": best.supplier_product_id,
        "price":               float(best.price) if best.price is not None else None,
    }
    logger.info(
        "supplier_router.best_supplier_chosen_legacy",
        product_id=str(product_id),
        **result_dict,
    )
    return result_dict
