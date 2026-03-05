from __future__ import annotations

"""
app/workers/tasks_supplier_products.py
───────────────────────────────────────
Sprint 7 – Celery task: sync supplier inventory across all suppliers.

Task: sync_supplier_products
──────────────────────────────
For every Product in the DB that has supplier_product_url set:
1. Fetch inventory from each supplier (StyleKorean, Jolse, OliveYoung)
   using the same base URL (suppliers are expected to carry the same SKU).
2. Upsert supplier_products rows.
3. Log a summary.

The actual supplier URL for each supplier is currently derived from
a best-effort mapping (same product_url prefix for all suppliers).
In production you would add per-supplier URL columns to the products
table and use those instead.

Schedule: every 60 minutes (configured in celery_app.py).
"""

import asyncio
from typing import Any

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Top-level import so tests can patch `app.workers.tasks_supplier_products.AsyncSessionLocal`
try:
    from app.db.session import AsyncSessionLocal  # noqa: F401
except Exception:  # pragma: no cover
    AsyncSessionLocal = None  # type: ignore[assignment]

# ── Supplier → crawler module mapping ────────────────────────────────────────

_SUPPLIER_CRAWLERS = {
    "STYLEKOREAN": "app.crawlers.stylekorean_inventory",
    "JOLSE":       "app.crawlers.jolse_inventory",
    "OLIVEYOUNG":  "app.crawlers.oliveyoung_inventory",
}


async def _fetch_for_supplier(
    supplier: str,
    product_url: str,
    fetch_fns: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Fetch inventory for one (supplier, url) pair.

    fetch_fns: optional dict[supplier_name -> async callable] for tests.
    """
    if fetch_fns and supplier in fetch_fns:
        return await fetch_fns[supplier](product_url)

    module_path = _SUPPLIER_CRAWLERS.get(supplier)
    if module_path is None:
        return {"in_stock": False, "price": None}

    import importlib
    mod = importlib.import_module(module_path)
    return await mod.fetch_inventory(product_url)


async def _run_sync(
    *,
    fetch_fns: dict[str, Any] | None = None,
    session_factory: Any = None,
) -> dict[str, Any]:
    """
    Core async implementation of the supplier-product sync.

    Parameters
    ----------
    fetch_fns       : {supplier_name: async_fn(url)} for test injection.
    session_factory : async_sessionmaker override for tests.

    Returns
    -------
    {"total_products": int, "rows_upserted": int, "errors": int}
    """
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal as _local
    from app.models.product import Product
    from app.services.supplier_product_service import upsert_supplier_product

    import app.workers.tasks_supplier_products as _self
    _session_factory = session_factory or getattr(_self, "AsyncSessionLocal", _local)

    total_products = 0
    rows_upserted  = 0
    errors         = 0

    async with _session_factory() as session:
        result   = await session.execute(select(Product))
        products = result.scalars().all()
        total_products = len(products)
        logger.info("tasks_supplier.sync_start", total=total_products)

        for product in products:
            if not product.supplier_product_url:
                continue

            for supplier in _SUPPLIER_CRAWLERS:
                try:
                    data = await _fetch_for_supplier(
                        supplier,
                        product.supplier_product_url,
                        fetch_fns,
                    )
                    await upsert_supplier_product(
                        session,
                        product_id          = product.id,
                        supplier            = supplier,
                        supplier_product_id = product.supplier_product_id,
                        price               = data.get("price"),
                        stock_status        = "IN_STOCK" if data.get("in_stock") else "OUT_OF_STOCK",
                    )
                    rows_upserted += 1
                except Exception as exc:
                    errors += 1
                    logger.error(
                        "tasks_supplier.product_error",
                        supplier=supplier,
                        product_id=str(product.id),
                        error=str(exc),
                    )

        await session.commit()

    summary = {
        "total_products": total_products,
        "rows_upserted":  rows_upserted,
        "errors":         errors,
    }
    logger.info("tasks_supplier.sync_done", **summary)
    return summary


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_supplier_products.sync_supplier_products",
    bind=True,
    max_retries=2,
    default_retry_delay=180,
    soft_time_limit=3300,   # 55 min – slightly under the 60-min schedule
    time_limit=3600,
)
def sync_supplier_products(self: Any) -> dict[str, Any]:  # type: ignore[return]
    """
    Celery task: synchronise supplier_products for all products.

    Runs every 60 minutes via Celery beat (configured in celery_app.py).
    """
    logger.info("tasks_supplier.sync_supplier_products.start", task_id=self.request.id)
    try:
        return asyncio.run(_run_sync())
    except Exception as exc:
        logger.error(
            "tasks_supplier.sync_supplier_products.failed",
            error=str(exc),
            task_id=self.request.id,
        )
        raise self.retry(exc=exc)
