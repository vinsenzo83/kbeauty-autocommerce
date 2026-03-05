from __future__ import annotations

"""
app/services/supplier_router.py
─────────────────────────────────
Sprint 7 – Multi-supplier routing.

Two entry points
----------------
choose_supplier(order)                    → SupplierClient   (legacy, order-based)
choose_best_supplier(product_id, session) → dict             (Sprint 7, DB-based)

The DB-based function picks the cheapest IN_STOCK supplier from the
``supplier_products`` table.  The order-based function is kept for
backward compatibility with the Celery order pipeline.
"""

from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
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

    Sprint 7 behaviour
    ------------------
    If the order already has a ``supplier`` field set (from a previous best-supplier
    selection), honour it.  Otherwise fall back to StyleKorean.
    """
    supplier_name = getattr(order, "supplier", None) or "stylekorean"
    client = _make_client(supplier_name)
    logger.debug(
        "supplier_router.chose",
        supplier=client.name,
        order_id=str(order.id),
    )
    return client


# ── Sprint 7: DB-based best-supplier selection ────────────────────────────────

async def choose_best_supplier(
    product_id: UUID,
    session: AsyncSession,
) -> dict[str, Any] | None:
    """
    Return the best (cheapest IN_STOCK) supplier for a product.

    Reads from the ``supplier_products`` table.

    Parameters
    ----------
    product_id : UUID   – products.id
    session    : AsyncSession

    Returns
    -------
    {
        "supplier":             str,   # e.g. "STYLEKOREAN"
        "supplier_product_id":  str,
        "price":                float | None,
    }
    or None when no IN_STOCK row exists.
    """
    from app.services.supplier_product_service import get_best_supplier

    best = await get_best_supplier(session, product_id)
    if best is None:
        logger.info(
            "supplier_router.no_in_stock_supplier",
            product_id=str(product_id),
        )
        return None

    result = {
        "supplier":            best.supplier,
        "supplier_product_id": best.supplier_product_id,
        "price":               float(best.price) if best.price is not None else None,
    }
    logger.info(
        "supplier_router.best_supplier_chosen",
        product_id=str(product_id),
        **result,
    )
    return result
