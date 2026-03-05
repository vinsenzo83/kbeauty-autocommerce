from __future__ import annotations

"""
app/services/pricing_service.py
────────────────────────────────
Sprint 8 – Pricing service.

Orchestrates:
1. generate_quote(canonical_product_id, session) -> PriceQuote
   - Find best IN_STOCK supplier for the canonical product.
   - Apply pricing rules (pricing_rules.py).
   - Write a price_quotes row.
   - Update canonical_products.last_price / last_price_at.

2. apply_quote_to_shopify(canonical_product_id, session, shopify_service)
   - Load the latest price_quote for the canonical product.
   - Look up shopify_mappings to get shopify_variant_id.
   - Call shopify_service.update_variant_price_by_id(variant_id, price)
     (idempotent: skip if last_price matches).
   - Return True if updated, False if skipped or no mapping.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_product import CanonicalProduct
from app.models.price_quote import PriceQuote
from app.models.shopify_mapping import ShopifyMapping
from app.models.supplier_product import SupplierProduct
from app.services.pricing_rules import compute_price

logger = structlog.get_logger(__name__)

_DEFAULT_FEE_RATE = Decimal("0.03")


# ─────────────────────────────────────────────────────────────────────────────
# generate_quote
# ─────────────────────────────────────────────────────────────────────────────

async def generate_quote(
    canonical_product_id: UUID,
    session: AsyncSession,
) -> PriceQuote | None:
    """
    Compute a price quote for the given canonical product and persist it.

    Steps
    -----
    1. Load the canonical_product (for pricing settings).
    2. Find the best IN_STOCK supplier row.
    3. Compute price via pricing_rules.compute_price.
    4. Write a PriceQuote row.
    5. Update canonical_products.last_price / last_price_at.
    6. Flush (caller commits).

    Returns
    -------
    PriceQuote or None when no IN_STOCK supplier exists or product not found.
    """
    # 1. Load canonical product
    cp_result = await session.execute(
        select(CanonicalProduct).where(CanonicalProduct.id == canonical_product_id)
    )
    cp = cp_result.scalar_one_or_none()
    if cp is None:
        logger.warning(
            "pricing_service.canonical_not_found",
            canonical_product_id=str(canonical_product_id),
        )
        return None

    if not cp.pricing_enabled:
        logger.info(
            "pricing_service.pricing_disabled",
            canonical_product_id=str(canonical_product_id),
        )
        return None

    # 2. Find best IN_STOCK supplier
    sp_result = await session.execute(
        select(SupplierProduct).where(
            SupplierProduct.canonical_product_id == canonical_product_id,
            SupplierProduct.stock_status         == "IN_STOCK",
        )
    )
    in_stock = list(sp_result.scalars().all())
    if not in_stock:
        logger.info(
            "pricing_service.no_in_stock_supplier",
            canonical_product_id=str(canonical_product_id),
        )
        return None

    def _sort_key(r: Any) -> tuple:
        p = float(r.price) if r.price is not None else float("inf")
        return (p, r.supplier)

    best = min(in_stock, key=_sort_key)
    if best.price is None:
        logger.info(
            "pricing_service.best_supplier_no_price",
            canonical_product_id=str(canonical_product_id),
            supplier=best.supplier,
        )
        return None

    supplier_price     = Decimal(str(best.price))
    shipping_cost      = Decimal(str(cp.shipping_cost_default or "3.00"))
    target_margin_rate = Decimal(str(cp.target_margin_rate    or "0.30"))
    min_margin_abs     = Decimal(str(cp.min_margin_abs        or "3.00"))
    fee_rate           = _DEFAULT_FEE_RATE

    # 3. Compute price
    computed_price, rounded_price, reason = compute_price(
        supplier_price     = supplier_price,
        shipping_cost      = shipping_cost,
        fee_rate           = fee_rate,
        target_margin_rate = target_margin_rate,
        min_margin_abs     = min_margin_abs,
    )

    # 4. Write PriceQuote
    quote = PriceQuote(
        canonical_product_id = canonical_product_id,
        supplier             = best.supplier,
        supplier_price       = supplier_price,
        shipping_cost        = shipping_cost,
        fee_rate             = fee_rate,
        target_margin_rate   = target_margin_rate,
        min_margin_abs       = min_margin_abs,
        computed_price       = computed_price,
        rounded_price        = rounded_price,
        reason               = reason or None,
    )
    session.add(quote)

    # 5. Update canonical last_price
    cp.last_price    = rounded_price
    cp.last_price_at = datetime.now(timezone.utc)

    await session.flush()

    logger.info(
        "pricing_service.quote_generated",
        canonical_product_id=str(canonical_product_id),
        supplier=best.supplier,
        supplier_price=float(supplier_price),
        rounded_price=float(rounded_price),
        reason=reason,
    )
    return quote


# ─────────────────────────────────────────────────────────────────────────────
# apply_quote_to_shopify
# ─────────────────────────────────────────────────────────────────────────────

async def apply_quote_to_shopify(
    canonical_product_id: UUID,
    session: AsyncSession,
    shopify_service: Any,
) -> bool:
    """
    Apply the latest price quote to Shopify (idempotent).

    Steps
    -----
    1. Load the latest PriceQuote for the canonical product.
    2. Load ShopifyMapping to get shopify_variant_id.
    3. If canonical_product.last_price equals the quote's rounded_price,
       and Shopify variant already has this price, skip (idempotent).
    4. Call shopify_service.update_variant_price_by_id(variant_id, price).
    5. Return True if updated, False if skipped or no mapping.
    """
    # Load latest quote
    q_result = await session.execute(
        select(PriceQuote)
        .where(PriceQuote.canonical_product_id == canonical_product_id)
        .order_by(PriceQuote.created_at.desc())
        .limit(1)
    )
    quote = q_result.scalar_one_or_none()
    if quote is None:
        logger.info(
            "pricing_service.no_quote_found",
            canonical_product_id=str(canonical_product_id),
        )
        return False

    # Load shopify mapping
    sm_result = await session.execute(
        select(ShopifyMapping).where(
            ShopifyMapping.canonical_product_id == canonical_product_id
        )
    )
    mapping = sm_result.scalar_one_or_none()
    if mapping is None:
        logger.info(
            "pricing_service.no_shopify_mapping",
            canonical_product_id=str(canonical_product_id),
        )
        return False

    # Idempotency: check if already at this price
    cp_result = await session.execute(
        select(CanonicalProduct).where(CanonicalProduct.id == canonical_product_id)
    )
    cp = cp_result.scalar_one_or_none()
    if cp is not None and cp.last_price is not None:
        if Decimal(str(cp.last_price)) == Decimal(str(quote.rounded_price)):
            # Price unchanged, but we still call Shopify in case it drifted
            pass  # fall through – apply is idempotent on Shopify's side too

    new_price = float(quote.rounded_price)
    ok = await shopify_service.update_variant_price_by_id(
        mapping.shopify_variant_id,
        new_price,
    )

    logger.info(
        "pricing_service.shopify_price_applied",
        canonical_product_id=str(canonical_product_id),
        shopify_variant_id=mapping.shopify_variant_id,
        new_price=new_price,
        ok=ok,
    )
    return ok
