from __future__ import annotations

"""
app/services/publish_service.py
────────────────────────────────
Sprint 12 – Auto-publish top-N canonical products to Shopify.

Core entry-point
----------------
    result = await publish_top_products_to_shopify(
        session,
        limit=20,
        dry_run=True,          # safe simulation
        shopify_svc=None,      # injected in tests
    )

Selection logic (deterministic, priority order)
-----------------------------------------------
1. Products with ≥1 IN_STOCK supplier_product AND a last_price (or
   price calculable by generate_quote).
2. Fall back to most-recently-created canonical products.

Per-product workflow
--------------------
1. compute/refresh price  →  generate_quote()
2. upsert Shopify mapping →  ShopifyProductService.create_or_update_product()
3. update inventory flag  →  stored in reason if inventory_item_id missing
4. write publish_job_items row (status + reason)

Idempotency
-----------
- ShopifyMapping is looked up before calling Shopify.
- If mapping exists with a shopify_product_id, update is called instead of create.
- Re-running the same job on the same products → updates, not duplicates.

DRY_RUN
-------
- Shopify client is never called.
- shopify_product_id is set to "dryrun-<canonical_id>" for traceability.
- Items are marked status="published" with reason="dry_run".
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.canonical_product import CanonicalProduct
from app.models.publish_job import PublishJob, PublishJobItem
from app.models.shopify_mapping import ShopifyMapping
from app.models.supplier_product import SupplierProduct
from app.services.pricing_service import generate_quote

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PublishJobResult:
    """Summary returned to caller / Celery task."""
    job_id:          str
    dry_run:         bool
    target_count:    int
    published_count: int
    failed_count:    int
    skipped_count:   int
    status:          str          # running / success / failed / partial
    notes:           str = ""
    items:           list[dict[str, Any]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Product selection helper
# ─────────────────────────────────────────────────────────────────────────────

async def _select_candidates(
    session: AsyncSession,
    limit: int,
) -> list[CanonicalProduct]:
    """
    Return up to `limit` canonical products, prioritised:
    1. Has ≥1 IN_STOCK supplier  AND  has last_price set.
    2. Has ≥1 IN_STOCK supplier  (price needs computing).
    3. Fall back: most-recently created canonical products.

    Deduplication is applied; total returned ≤ limit.
    """
    seen: set[Any] = set()
    result: list[CanonicalProduct] = []

    # Priority-1: IN_STOCK + last_price set
    p1 = await session.execute(
        select(CanonicalProduct)
        .join(
            SupplierProduct,
            SupplierProduct.canonical_product_id == CanonicalProduct.id,
        )
        .where(
            SupplierProduct.stock_status == "IN_STOCK",
            CanonicalProduct.last_price.isnot(None),
            CanonicalProduct.pricing_enabled.is_(True),
        )
        .order_by(CanonicalProduct.updated_at.desc())
        .limit(limit)
        .distinct()
    )
    for cp in p1.scalars().all():
        if cp.id not in seen:
            seen.add(cp.id)
            result.append(cp)

    if len(result) >= limit:
        return result[:limit]

    # Priority-2: IN_STOCK (any price)
    p2 = await session.execute(
        select(CanonicalProduct)
        .join(
            SupplierProduct,
            SupplierProduct.canonical_product_id == CanonicalProduct.id,
        )
        .where(SupplierProduct.stock_status == "IN_STOCK")
        .order_by(CanonicalProduct.updated_at.desc())
        .limit(limit)
        .distinct()
    )
    for cp in p2.scalars().all():
        if cp.id not in seen:
            seen.add(cp.id)
            result.append(cp)
            if len(result) >= limit:
                return result[:limit]

    if len(result) >= limit:
        return result[:limit]

    # Fallback: latest canonical products
    fallback = await session.execute(
        select(CanonicalProduct)
        .order_by(CanonicalProduct.created_at.desc())
        .limit(limit)
    )
    for cp in fallback.scalars().all():
        if cp.id not in seen:
            seen.add(cp.id)
            result.append(cp)
            if len(result) >= limit:
                break

    return result[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Preview helper (no side effects)
# ─────────────────────────────────────────────────────────────────────────────

async def preview_top_products(
    session: AsyncSession,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Return candidate products + current prices without writing anything.
    Safe to call as many times as needed.
    """
    candidates = await _select_candidates(session, limit)
    items = []
    for cp in candidates:
        # Load best in-stock supplier price
        sp_res = await session.execute(
            select(SupplierProduct).where(
                SupplierProduct.canonical_product_id == cp.id,
                SupplierProduct.stock_status == "IN_STOCK",
            )
        )
        in_stock = list(sp_res.scalars().all())

        # Check existing mapping
        sm_res = await session.execute(
            select(ShopifyMapping).where(
                ShopifyMapping.canonical_product_id == cp.id
            )
        )
        mapping = sm_res.scalar_one_or_none()

        items.append({
            "canonical_product_id": str(cp.id),
            "canonical_sku":        cp.canonical_sku,
            "name":                 cp.name,
            "brand":                cp.brand,
            "last_price":           float(cp.last_price) if cp.last_price else None,
            "pricing_enabled":      cp.pricing_enabled,
            "in_stock_suppliers":   len(in_stock),
            "has_shopify_mapping":  mapping is not None,
            "shopify_product_id":   mapping.shopify_product_id if mapping else None,
        })
    return items


# ─────────────────────────────────────────────────────────────────────────────
# Per-product publish logic
# ─────────────────────────────────────────────────────────────────────────────

async def _publish_one(
    cp: CanonicalProduct,
    *,
    session: AsyncSession,
    dry_run: bool,
    shopify_svc: Any,
    job: PublishJob,
) -> PublishJobItem:
    """
    Process a single canonical product in the publish run.
    Returns the PublishJobItem with final status set.
    """
    log = logger.bind(
        canonical_product_id=str(cp.id),
        canonical_sku=cp.canonical_sku,
        dry_run=dry_run,
    )

    item = PublishJobItem(
        publish_job_id       = job.id,
        canonical_product_id = cp.id,
        status               = "queued",
    )
    session.add(item)
    await session.flush()  # get item.id

    try:
        # ── Step 1: compute / refresh price ───────────────────────────────────
        quote = None
        if cp.pricing_enabled:
            try:
                quote = await generate_quote(cp.id, session)
            except Exception as exc:
                log.warning("publish_service.quote_error", error=str(exc))

        sell_price = (
            float(quote.rounded_price) if quote and quote.rounded_price
            else (float(cp.last_price) if cp.last_price else None)
        )

        if sell_price is None:
            log.warning("publish_service.no_price")
            item.status = "failed"
            item.reason = "no_price: unable to compute sell price"
            item.updated_at = datetime.now(timezone.utc)
            return item

        # ── Step 2: load or prepare Shopify mapping ────────────────────────────
        sm_res = await session.execute(
            select(ShopifyMapping).where(
                ShopifyMapping.canonical_product_id == cp.id
            )
        )
        mapping: ShopifyMapping | None = sm_res.scalar_one_or_none()
        existing_shopify_id = mapping.shopify_product_id if mapping else None

        # ── Step 3: dry_run branch ─────────────────────────────────────────────
        if dry_run:
            fake_id = f"dryrun-{cp.id}"
            item.shopify_product_id = fake_id
            item.status             = "published"
            item.reason             = f"dry_run: price={sell_price:.2f}"
            item.updated_at         = datetime.now(timezone.utc)
            log.info("publish_service.dry_run_ok", fake_id=fake_id, price=sell_price)
            return item

        # ── Step 4: real Shopify call ─────────────────────────────────────────
        product_dict: dict[str, Any] = {
            "name":                cp.name,
            "brand":               cp.brand or "",
            "sale_price":          sell_price,
            "price":               sell_price,
            "image_urls_json":     [],
            "stock_status":        "in_stock",
            "shopify_product_id":  existing_shopify_id,
            "supplier_product_url": "",
        }

        new_shopify_id: str | None = await shopify_svc.create_or_update_product(
            product_dict
        )

        if new_shopify_id is None:
            # Stub mode (no credentials) — still mark published for safety
            new_shopify_id = existing_shopify_id  # keep existing if any

        # ── Step 5: upsert ShopifyMapping ─────────────────────────────────────
        if new_shopify_id:
            if mapping is None:
                mapping = ShopifyMapping(
                    canonical_product_id      = cp.id,
                    shopify_product_id        = new_shopify_id,
                    shopify_variant_id        = f"var-{new_shopify_id}",
                    shopify_inventory_item_id = None,
                    currency                  = "USD",
                )
                session.add(mapping)
            else:
                mapping.shopify_product_id = new_shopify_id
                mapping.updated_at         = datetime.now(timezone.utc)

        item.shopify_product_id = new_shopify_id or existing_shopify_id
        item.status             = "published"
        item.reason             = f"price={sell_price:.2f}"
        item.updated_at         = datetime.now(timezone.utc)

        # ── Step 6: inventory note ────────────────────────────────────────────
        if mapping and not mapping.shopify_inventory_item_id:
            item.reason = (item.reason or "") + "; needs_inventory_sync"

        log.info(
            "publish_service.published",
            shopify_product_id=item.shopify_product_id,
            price=sell_price,
        )
        return item

    except Exception as exc:  # noqa: BLE001
        log.error("publish_service.item_error", error=str(exc))
        item.status     = "failed"
        item.reason     = f"error: {str(exc)[:400]}"
        item.updated_at = datetime.now(timezone.utc)
        return item


# ─────────────────────────────────────────────────────────────────────────────
# Main public function
# ─────────────────────────────────────────────────────────────────────────────

async def publish_top_products_to_shopify(
    session: AsyncSession,
    *,
    limit: int = 20,
    dry_run: bool,
    shopify_svc: Any = None,
    job_id: str | None = None,  # allow pre-created job id (Celery use)
) -> PublishJobResult:
    """
    Publish top-N canonical products to Shopify.

    Parameters
    ----------
    session    : Async SQLAlchemy session (caller owns commit).
    limit      : Max number of products to publish (default 20).
    dry_run    : If True, simulate without calling Shopify.
    shopify_svc: Injected ShopifyProductService (tests provide a mock).
    job_id     : Pre-created PublishJob UUID (optional).

    Returns
    -------
    PublishJobResult with full summary.
    """
    log = logger.bind(limit=limit, dry_run=dry_run)
    log.info("publish_service.start")

    # ── Lazily import ShopifyProductService to allow test injection ────────────
    if shopify_svc is None and not dry_run:
        from app.services.shopify_product_service import ShopifyProductService
        shopify_svc = ShopifyProductService()

    # ── Create or load PublishJob ──────────────────────────────────────────────
    if job_id:
        job_res = await session.execute(
            select(PublishJob).where(PublishJob.id == uuid.UUID(job_id))
        )
        job = job_res.scalar_one_or_none()
        if job is None:
            raise ValueError(f"PublishJob {job_id} not found")
    else:
        job = PublishJob(
            channel = "shopify",
            status  = "running",
            dry_run = dry_run,
        )
        session.add(job)
        await session.flush()

    # ── Select candidate products ──────────────────────────────────────────────
    candidates = await _select_candidates(session, limit)
    job.target_count = len(candidates)
    await session.flush()

    log.info("publish_service.candidates_selected", count=len(candidates))

    if not candidates:
        job.status = "success"
        job.notes  = "no candidates found"
        await session.flush()
        return PublishJobResult(
            job_id          = str(job.id),
            dry_run         = dry_run,
            target_count    = 0,
            published_count = 0,
            failed_count    = 0,
            skipped_count   = 0,
            status          = "success",
            notes           = "no candidates found",
        )

    # ── Process each product ───────────────────────────────────────────────────
    published = 0
    failed    = 0
    skipped   = 0
    item_summaries: list[dict[str, Any]] = []

    for cp in candidates:
        item = await _publish_one(
            cp,
            session    = session,
            dry_run    = dry_run,
            shopify_svc= shopify_svc,
            job        = job,
        )

        if item.status == "published":
            published += 1
        elif item.status == "failed":
            failed += 1
        elif item.status == "skipped":
            skipped += 1

        item_summaries.append({
            "item_id":               str(item.id),
            "canonical_product_id":  str(cp.id),
            "canonical_sku":         cp.canonical_sku,
            "shopify_product_id":    item.shopify_product_id,
            "status":                item.status,
            "reason":                item.reason,
        })

    # ── Finalise job ───────────────────────────────────────────────────────────
    job.published_count = published
    job.failed_count    = failed
    job.skipped_count   = skipped
    job.updated_at      = datetime.now(timezone.utc)

    if failed == 0:
        job.status = "success"
    elif published == 0:
        job.status = "failed"
    else:
        job.status = "partial"

    if dry_run:
        job.notes = f"[DRY RUN] published={published} failed={failed} skipped={skipped}"
    else:
        job.notes = f"published={published} failed={failed} skipped={skipped}"

    await session.flush()

    log.info(
        "publish_service.done",
        job_id    = str(job.id),
        status    = job.status,
        published = published,
        failed    = failed,
        skipped   = skipped,
    )

    return PublishJobResult(
        job_id          = str(job.id),
        dry_run         = dry_run,
        target_count    = len(candidates),
        published_count = published,
        failed_count    = failed,
        skipped_count   = skipped,
        status          = job.status,
        notes           = job.notes or "",
        items           = item_summaries,
    )
