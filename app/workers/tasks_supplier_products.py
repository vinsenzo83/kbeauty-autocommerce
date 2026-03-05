from __future__ import annotations

"""
app/workers/tasks_supplier_products.py
───────────────────────────────────────
Sprint 7 + Sprint 8 – Celery task: sync supplier inventory.

Sprint 8 changes
----------------
- Iterates canonical_products (not products directly).
- For each canonical_product, fetches supplier_products rows to get
  (supplier, supplier_product_url) pairs.
- Calls supplier-specific fetch_inventory(url) per supplier row.
- Updates price / stock_status / last_checked_at on the supplier_products row.

Task schedule: every 60 minutes (configured in celery_app.py).
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

    Sprint 8 behaviour
    ------------------
    1. Load all canonical_products.
    2. For each canonical_product, load its supplier_products rows.
    3. For each supplier row that has a supplier_product_url, call fetch_inventory.
    4. Update price / stock_status / last_checked_at.

    Falls back to legacy product-based sync when no canonical_products exist
    (pre-migration or test environments that haven't run 0008).

    Parameters
    ----------
    fetch_fns       : {supplier_name: async_fn(url)} for test injection.
    session_factory : async_sessionmaker override for tests.

    Returns
    -------
    {"total_canonicals": int, "rows_updated": int, "errors": int}
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal as _local
    from app.models.canonical_product import CanonicalProduct
    from app.models.supplier_product import SupplierProduct

    import app.workers.tasks_supplier_products as _self
    _session_factory = session_factory or getattr(_self, "AsyncSessionLocal", _local)

    total_canonicals = 0
    rows_updated     = 0
    errors           = 0

    async with _session_factory() as session:
        # ── Load all canonical_products ───────────────────────────────────
        cp_result    = await session.execute(select(CanonicalProduct))
        canonicals   = list(cp_result.scalars().all())
        total_canonicals = len(canonicals)
        logger.info("tasks_supplier.sync_start", total_canonicals=total_canonicals)

        if total_canonicals == 0:
            # Legacy fallback: iterate products directly (pre-migration)
            from app.models.product import Product
            from app.services.supplier_product_service import upsert_supplier_product

            prod_result = await session.execute(select(Product))
            products    = list(prod_result.scalars().all())
            logger.info(
                "tasks_supplier.sync_legacy_fallback",
                total_products=len(products),
            )
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
                        rows_updated += 1
                    except Exception as exc:
                        errors += 1
                        logger.error(
                            "tasks_supplier.legacy_product_error",
                            supplier=supplier,
                            product_id=str(product.id),
                            error=str(exc),
                        )
            await session.commit()
            summary = {
                "total_canonicals": 0,
                "rows_updated": rows_updated,
                "errors": errors,
            }
            logger.info("tasks_supplier.sync_done", **summary)
            return summary

        # ── Canonical-based sync ──────────────────────────────────────────
        for cp in canonicals:
            # Load supplier rows for this canonical
            sp_result  = await session.execute(
                select(SupplierProduct).where(
                    SupplierProduct.canonical_product_id == cp.id,
                )
            )
            sp_rows = list(sp_result.scalars().all())

            for sp in sp_rows:
                url = getattr(sp, "supplier_product_url", None)
                if not url:
                    continue  # no URL, skip

                try:
                    data = await _fetch_for_supplier(sp.supplier, url, fetch_fns)

                    sp.price           = data.get("price")
                    sp.stock_status    = "IN_STOCK" if data.get("in_stock") else "OUT_OF_STOCK"
                    sp.last_checked_at = datetime.now(timezone.utc)
                    rows_updated += 1

                    logger.debug(
                        "tasks_supplier.row_updated",
                        canonical_id=str(cp.id),
                        supplier=sp.supplier,
                        in_stock=data.get("in_stock"),
                        price=data.get("price"),
                    )
                except Exception as exc:
                    errors += 1
                    logger.error(
                        "tasks_supplier.row_error",
                        canonical_id=str(cp.id),
                        supplier=sp.supplier,
                        error=str(exc),
                    )

        await session.commit()

    summary = {
        "total_canonicals": total_canonicals,
        "rows_updated":     rows_updated,
        "errors":           errors,
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
    Celery task: synchronise supplier_products for all canonical_products.

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
