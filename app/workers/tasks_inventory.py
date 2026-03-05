from __future__ import annotations

"""
app/workers/tasks_inventory.py
────────────────────────────────
Sprint 6 – Celery task: periodic inventory synchronisation.

Task: sync_inventory
---------------------
1. Fetch all products from DB.
2. For each product, call the supplier inventory crawler.
3. Persist DB changes (stock_status, last_seen_price, last_checked_at).
4. Apply Shopify mutations when needed:
   - OUT_OF_STOCK transition → set_inventory_zero
   - Price change            → update_variant_price

The task is intentionally serialised (no fan-out) so we can respect
StyleKorean's rate limits without a dedicated throttle mechanism.
For very large catalogues a batched/parallel approach can be introduced
in a future sprint.
"""

import asyncio
from typing import Any

import structlog

from app.workers.celery_app import celery_app

# Top-level import so tests can patch `app.workers.tasks_inventory.AsyncSessionLocal`
try:
    from app.db.session import AsyncSessionLocal  # noqa: F401 — re-exported for patching
except Exception:  # pragma: no cover
    AsyncSessionLocal = None  # type: ignore[assignment]

logger = structlog.get_logger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

async def _run_sync(
    *,
    fetch_fn: Any = None,
    shopify_svc: Any = None,
    session_factory: Any = None,
) -> dict[str, Any]:
    """
    Core async implementation of the inventory sync.

    Parameters
    ----------
    fetch_fn        : optional crawler override (tests)
    shopify_svc     : optional ShopifyProductService override (tests)
    session_factory : optional SQLAlchemy async_sessionmaker override (tests).
                      When None, uses ``AsyncSessionLocal`` from app.db.session.

    Returns
    -------
    dict with ``total``, ``updated``, ``errors`` counts.
    """
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal as _AsyncSessionLocal
    from app.models.product import Product
    from app.services.inventory_service import (
        check_supplier_inventory,
        update_product_inventory,
    )
    from app.services.shopify_product_service import get_shopify_product_service

    if session_factory is None:
        # Use the module-level name so tests that patched it take effect
        import app.workers.tasks_inventory as _self_module
        session_factory = getattr(_self_module, "AsyncSessionLocal", _AsyncSessionLocal)

    if shopify_svc is None:
        shopify_svc = get_shopify_product_service()

    total   = 0
    updated = 0
    errors  = 0

    async with session_factory() as session:
        result = await session.execute(select(Product))
        products = result.scalars().all()
        total = len(products)
        logger.info("tasks_inventory.sync_start", total=total)

        for product in products:
            try:
                inventory_data = await check_supplier_inventory(
                    product, fetch_fn=fetch_fn
                )
                change = await update_product_inventory(
                    product,
                    inventory_data,
                    session,
                    shopify_svc=shopify_svc,
                )
                await session.commit()
                if change["stock_changed"] or change["price_changed"]:
                    updated += 1
                    logger.info(
                        "tasks_inventory.product_updated",
                        supplier_product_id=product.supplier_product_id,
                        stock_changed=change["stock_changed"],
                        price_changed=change["price_changed"],
                        shopify_zeroed=change["shopify_zeroed"],
                        shopify_repriced=change["shopify_repriced"],
                    )
            except Exception as exc:
                errors += 1
                logger.error(
                    "tasks_inventory.product_error",
                    supplier_product_id=getattr(product, "supplier_product_id", "?"),
                    error=str(exc),
                )
                await session.rollback()

    result_summary = {"total": total, "updated": updated, "errors": errors}
    logger.info("tasks_inventory.sync_done", **result_summary)
    return result_summary


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_inventory.sync_inventory",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    soft_time_limit=1800,  # 30 min — same as schedule interval
    time_limit=2100,       # hard kill after 35 min
)
def sync_inventory(self: Any) -> dict[str, Any]:  # type: ignore[return]
    """
    Celery task: synchronise inventory for all products.

    Runs every 30 minutes via Celery beat (configured in celery_app.py).

    Returns
    -------
    dict with ``total``, ``updated``, ``errors`` counts.
    """
    logger.info("tasks_inventory.sync_inventory.start", task_id=self.request.id)
    try:
        return asyncio.run(_run_sync())
    except Exception as exc:
        logger.error(
            "tasks_inventory.sync_inventory.failed",
            error=str(exc),
            task_id=self.request.id,
        )
        raise self.retry(exc=exc)
