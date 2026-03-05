from __future__ import annotations

"""
app/services/inventory_service.py
──────────────────────────────────
Sprint 6 – Inventory synchronisation logic.

Responsibilities
----------------
* check_supplier_inventory(product) — call the supplier crawler and return
  a normalised inventory dict.
* update_product_inventory(product, inventory_data, session, shopify_svc) —
  compare fetched data with current DB state, apply DB updates, and trigger
  Shopify mutations when needed.

Shopify mutations performed
---------------------------
* Product becomes OUT_OF_STOCK → ``shopify_svc.set_inventory_zero(product)``
* Price changed by ≥ threshold  → ``shopify_svc.update_variant_price(product, new_price)``

The service is intentionally side-effect-free regarding Celery; the task
layer (tasks_inventory.py) drives concurrency and error handling.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services.shopify_product_service import ShopifyProductService

from app.models.product import Product

logger = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Minimum relative price change (%) that triggers a Shopify price update.
# Avoids noisy updates for tiny floating-point differences.
PRICE_CHANGE_THRESHOLD_PCT: float = 0.5  # 0.5 %

# Canonical stock status values written to the DB
STATUS_IN_STOCK    = "IN_STOCK"
STATUS_OUT_OF_STOCK = "OUT_OF_STOCK"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_decimal(value: Any) -> Decimal | None:
    """Safely coerce a value to Decimal, returning None on failure."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _price_changed(old: Decimal | None, new: Decimal | None) -> bool:
    """
    Return True if the price changed beyond the threshold.

    None values are treated as "unknown" — if new is not None and old is None,
    we record the price but do NOT push a Shopify update (no reference point).
    """
    if new is None:
        return False
    if old is None:
        return False  # first observation — record only, no Shopify update
    if old == Decimal("0"):
        return new != Decimal("0")
    pct_change = abs((new - old) / old) * 100
    return float(pct_change) >= PRICE_CHANGE_THRESHOLD_PCT


# ── Public API ────────────────────────────────────────────────────────────────

async def check_supplier_inventory(
    product: Product,
    *,
    fetch_fn: Any = None,
) -> dict[str, Any]:
    """
    Call the supplier inventory crawler for a single product.

    Parameters
    ----------
    product  : ORM Product instance (needs ``supplier_product_url``).
    fetch_fn : optional callable replacing the real crawler for tests.
               Signature: ``async fetch_fn(url) -> {"in_stock": bool, "price": float|None}``

    Returns
    -------
    dict with keys ``in_stock`` (bool) and ``price`` (float | None).
    """
    if fetch_fn is None:
        from app.crawlers.stylekorean_inventory import fetch_inventory
        fetch_fn = fetch_inventory

    url = product.supplier_product_url
    log = logger.bind(
        supplier_product_id=product.supplier_product_id,
        url=url,
    )
    log.info("inventory_service.checking")
    result = await fetch_fn(url)
    log.info(
        "inventory_service.check_done",
        in_stock=result.get("in_stock"),
        price=result.get("price"),
    )
    return result


async def update_product_inventory(
    product: Product,
    inventory_data: dict[str, Any],
    session: "AsyncSession",
    shopify_svc: "ShopifyProductService | None" = None,
) -> dict[str, Any]:
    """
    Apply inventory data to a product: update DB fields and trigger Shopify.

    Parameters
    ----------
    product        : ORM Product (attached to ``session``).
    inventory_data : Result from ``check_supplier_inventory`` —
                     ``{"in_stock": bool, "price": float | None}``.
    session        : Active async SQLAlchemy session.
    shopify_svc    : ShopifyProductService instance; if None, Shopify calls
                     are skipped (useful in tests).

    Returns
    -------
    dict summarising what changed:
    {
        "stock_changed":  bool,
        "price_changed":  bool,
        "shopify_zeroed": bool,
        "shopify_repriced": bool,
        "new_stock_status": str,
        "new_price": float | None,
    }
    """
    log = logger.bind(
        supplier_product_id=product.supplier_product_id,
        shopify_product_id=product.shopify_product_id,
    )

    in_stock    = bool(inventory_data.get("in_stock", True))
    raw_price   = inventory_data.get("price")
    new_price   = _to_decimal(raw_price)
    old_price   = _to_decimal(product.last_seen_price)
    now_utc     = datetime.now(timezone.utc)

    new_status  = STATUS_IN_STOCK if in_stock else STATUS_OUT_OF_STOCK
    old_status  = product.stock_status or "unknown"

    # Normalise legacy lowercase values for comparison
    old_status_norm = old_status.upper().replace(" ", "_")

    stock_changed  = new_status != old_status_norm
    price_chg      = _price_changed(old_price, new_price)
    shopify_zeroed = False
    shopify_repriced = False

    # ── DB updates ────────────────────────────────────────────────────────────
    product.stock_status    = new_status
    product.last_checked_at = now_utc
    if new_price is not None:
        product.last_seen_price = new_price

    await session.flush()

    log.info(
        "inventory_service.db_updated",
        new_status=new_status,
        stock_changed=stock_changed,
        new_price=str(new_price) if new_price else None,
        price_changed=price_chg,
    )

    # ── Shopify updates ───────────────────────────────────────────────────────
    if shopify_svc is not None and product.shopify_product_id:

        # 1. Out-of-stock → zero inventory on Shopify
        if not in_stock and stock_changed:
            try:
                await shopify_svc.set_inventory_zero(product)
                shopify_zeroed = True
                log.info("inventory_service.shopify_zeroed")
            except Exception as exc:
                log.error(
                    "inventory_service.shopify_zero_failed",
                    error=str(exc),
                )

        # 2. Price change → update Shopify variant price
        if price_chg and new_price is not None:
            try:
                await shopify_svc.update_variant_price(product, float(new_price))
                shopify_repriced = True
                log.info(
                    "inventory_service.shopify_repriced",
                    new_price=float(new_price),
                )
            except Exception as exc:
                log.error(
                    "inventory_service.shopify_reprice_failed",
                    error=str(exc),
                )

    return {
        "stock_changed":    stock_changed,
        "price_changed":    price_chg,
        "shopify_zeroed":   shopify_zeroed,
        "shopify_repriced": shopify_repriced,
        "new_stock_status": new_status,
        "new_price":        float(new_price) if new_price is not None else None,
    }
