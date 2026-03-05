from __future__ import annotations

"""
app/services/repricing_service.py
───────────────────────────────────
Sprint 13 – Shopify repricing service.

Public API
----------
preview_reprice(session, limit=50) -> list[dict]
    Returns preview data: canonical info, competitor band, recommended price,
    current Shopify price, delta. No DB writes.

apply_reprice_to_shopify(session, limit=50, dry_run=True, shopify_svc=None)
    -> str (repricing_run_id)
    Creates a RepricingRun, computes recommended prices, and applies them
    to Shopify (or simulates in dry_run mode).

Skip reasons
------------
NO_CHANGE                : recommended == current Shopify price (within $0.01)
MISSING_SHOPIFY_MAPPING  : no ShopifyMapping row
NO_IN_STOCK_SUPPLIER     : no SupplierProduct with stock_status=IN_STOCK
NO_PRICE                 : cannot compute price (no cost data)
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_product import CanonicalProduct
from app.models.market_price import RepricingRun, RepricingRunItem
from app.models.shopify_mapping import ShopifyMapping
from app.models.supplier_product import SupplierProduct
from app.services.market_price_service import get_competitor_band
from app.services.repricing_rules import compute_recommended_price

logger = structlog.get_logger(__name__)

_PRICE_TOLERANCE = Decimal("0.01")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_best_supplier_cost(
    session: AsyncSession,
    canonical_product_id: uuid.UUID,
) -> Decimal | None:
    """Return lowest-price IN_STOCK supplier cost, or None."""
    result = await session.execute(
        select(SupplierProduct).where(
            SupplierProduct.canonical_product_id == canonical_product_id,
            SupplierProduct.stock_status == "IN_STOCK",
        )
    )
    in_stock = list(result.scalars().all())
    if not in_stock:
        return None
    best = min(
        (sp for sp in in_stock if sp.price is not None),
        key=lambda sp: float(sp.price),
        default=None,
    )
    return Decimal(str(best.price)) if best and best.price is not None else None


async def _get_current_shopify_price(
    session: AsyncSession,
    canonical_product_id: uuid.UUID,
) -> tuple[ShopifyMapping | None, Decimal | None]:
    """Return (mapping, current_price) or (None, None)."""
    result = await session.execute(
        select(ShopifyMapping).where(
            ShopifyMapping.canonical_product_id == canonical_product_id
        )
    )
    mapping = result.scalar_one_or_none()
    if mapping is None:
        return None, None
    # canonical_product.last_price is the authoritative Shopify sell price
    cp_res = await session.execute(
        select(CanonicalProduct).where(CanonicalProduct.id == canonical_product_id)
    )
    cp = cp_res.scalar_one_or_none()
    current_price = Decimal(str(cp.last_price)) if cp and cp.last_price else None
    return mapping, current_price


async def _load_canonical_products(
    session: AsyncSession,
    limit: int,
) -> list[CanonicalProduct]:
    """Load pricing-enabled canonical products, most-recently updated first."""
    result = await session.execute(
        select(CanonicalProduct)
        .where(CanonicalProduct.pricing_enabled.is_(True))
        .order_by(CanonicalProduct.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────────────────
# Preview
# ─────────────────────────────────────────────────────────────────────────────

async def preview_reprice(
    session: AsyncSession,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Return a repricing preview for up to `limit` products.

    No writes. Safe to call as many times as needed.
    """
    products = await _load_canonical_products(session, limit)
    result = []

    for cp in products:
        row: dict[str, Any] = {
            "canonical_product_id": str(cp.id),
            "canonical_sku":        cp.canonical_sku,
            "name":                 cp.name,
            "brand":                cp.brand,
        }

        # Supplier cost
        cost = await _get_best_supplier_cost(session, cp.id)
        if cost is None:
            row.update({
                "skip_reason":       "NO_IN_STOCK_SUPPLIER",
                "recommended_price": None,
                "current_price":     None,
                "delta":             None,
                "competitor_min":    None,
                "competitor_median": None,
                "competitor_max":    None,
            })
            result.append(row)
            continue

        # Competitor band
        band = await get_competitor_band(session, cp.id)

        # Compute recommended
        rec = compute_recommended_price(
            supplier_cost      = cost,
            shipping_cost      = Decimal(str(cp.shipping_cost_default or "3.00")),
            fee_rate           = Decimal("0.03"),
            target_margin_rate = Decimal(str(cp.target_margin_rate or "0.30")),
            min_margin_abs     = Decimal(str(cp.min_margin_abs or "3.00")),
            competitor_band    = band,
        )

        # Current Shopify price
        _, current_price = await _get_current_shopify_price(session, cp.id)

        delta = None
        if current_price is not None:
            delta = float(rec.recommended_price - current_price)

        row.update({
            "skip_reason":         None,
            "supplier_cost":       float(cost),
            "recommended_price":   float(rec.recommended_price),
            "base_price":          float(rec.base_rounded),
            "current_price":       float(current_price) if current_price else None,
            "delta":               round(delta, 2) if delta is not None else None,
            "expected_margin_pct": rec.expected_margin_pct,
            "repricing_reason":    rec.reason,
            "competitor_min":      float(band.min_price)    if band else None,
            "competitor_median":   float(band.median_price) if band else None,
            "competitor_max":      float(band.max_price)    if band else None,
            "competitor_samples":  band.sample_count        if band else 0,
        })
        result.append(row)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Apply
# ─────────────────────────────────────────────────────────────────────────────

async def apply_reprice_to_shopify(
    session: AsyncSession,
    *,
    limit: int = 50,
    dry_run: bool,
    shopify_svc: Any = None,
) -> str:
    """
    Compute and (optionally) apply repriced prices to Shopify.

    Parameters
    ----------
    session    : Async SQLAlchemy session (caller commits).
    limit      : Max products to process.
    dry_run    : If True, compute but do not call Shopify.
    shopify_svc: Injected ShopifyProductService (tests mock this).

    Returns
    -------
    repricing_run_id (str UUID)
    """
    if shopify_svc is None and not dry_run:
        from app.services.shopify_product_service import ShopifyProductService
        shopify_svc = ShopifyProductService()

    # ── Create run ─────────────────────────────────────────────────────────────
    run = RepricingRun(channel="shopify", status="running", dry_run=dry_run)
    session.add(run)
    await session.flush()
    log = logger.bind(run_id=str(run.id), dry_run=dry_run)
    log.info("repricing_service.start")

    products = await _load_canonical_products(session, limit)
    run.target_count = len(products)
    await session.flush()

    updated = skipped = failed = 0

    for cp in products:
        item = RepricingRunItem(
            repricing_run_id     = run.id,
            canonical_product_id = cp.id,
            status               = "skipped",
        )
        session.add(item)
        await session.flush()

        try:
            # ── Supplier cost ────────────────────────────────────────────────
            cost = await _get_best_supplier_cost(session, cp.id)
            if cost is None:
                item.reason = "NO_IN_STOCK_SUPPLIER"
                skipped += 1
                continue

            # ── Competitor band ──────────────────────────────────────────────
            band = await get_competitor_band(session, cp.id)

            # ── Compute recommended price ────────────────────────────────────
            rec = compute_recommended_price(
                supplier_cost      = cost,
                shipping_cost      = Decimal(str(cp.shipping_cost_default or "3.00")),
                fee_rate           = Decimal("0.03"),
                target_margin_rate = Decimal(str(cp.target_margin_rate or "0.30")),
                min_margin_abs     = Decimal(str(cp.min_margin_abs or "3.00")),
                competitor_band    = band,
            )
            rec_price = rec.recommended_price
            item.recommended_price = rec_price

            # ── Current Shopify mapping + price ──────────────────────────────
            mapping, current_price = await _get_current_shopify_price(session, cp.id)
            if mapping is None:
                item.reason = "MISSING_SHOPIFY_MAPPING"
                skipped += 1
                continue

            item.old_price = current_price

            # ── Idempotency: skip if no change ───────────────────────────────
            if current_price is not None and abs(rec_price - current_price) <= _PRICE_TOLERANCE:
                item.reason = "NO_CHANGE"
                skipped += 1
                continue

            # ── Apply ─────────────────────────────────────────────────────────
            if dry_run:
                item.applied_price = rec_price
                item.status        = "updated"
                item.reason        = f"dry_run: {rec.reason or 'ok'}"
                updated += 1
                log.info(
                    "repricing_service.dry_run_update",
                    canonical_sku=cp.canonical_sku,
                    old_price=float(current_price or 0),
                    new_price=float(rec_price),
                )
            else:
                ok = await shopify_svc.update_variant_price_by_id(
                    mapping.shopify_variant_id,
                    float(rec_price),
                )
                if ok or ok is None:   # None = stub mode
                    # Update canonical_product.last_price
                    cp.last_price    = rec_price
                    cp.last_price_at = datetime.now(timezone.utc)
                    item.applied_price = rec_price
                    item.status        = "updated"
                    item.reason        = rec.reason or "repriced"
                    updated += 1
                else:
                    item.status = "failed"
                    item.reason = "SHOPIFY_API_ERROR"
                    failed += 1

            item.updated_at = datetime.now(timezone.utc)

        except Exception as exc:  # noqa: BLE001
            logger.error("repricing_service.item_error", error=str(exc))
            item.status = "failed"
            item.reason = f"error:{str(exc)[:200]}"
            failed += 1

    # ── Finalise run ───────────────────────────────────────────────────────────
    run.updated_count = updated
    run.skipped_count = skipped
    run.failed_count  = failed
    run.updated_at    = datetime.now(timezone.utc)

    if failed == 0 and updated > 0:
        run.status = "success"
    elif updated == 0 and failed == 0:
        run.status = "success"   # all skipped is still success
    elif failed > 0 and updated > 0:
        run.status = "partial"
    elif failed > 0:
        run.status = "failed"
    else:
        run.status = "success"

    prefix = "[DRY RUN] " if dry_run else ""
    run.notes = f"{prefix}updated={updated} skipped={skipped} failed={failed}"
    await session.flush()

    log.info(
        "repricing_service.done",
        status=run.status,
        updated=updated,
        skipped=skipped,
        failed=failed,
    )
    return str(run.id)
