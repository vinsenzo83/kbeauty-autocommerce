from __future__ import annotations

"""
app/workers/tasks_products.py
──────────────────────────────
Celery tasks for Sprint 4:

    crawl_best_sellers()      – Crawl StyleKorean top-500 best sellers,
                                parse, and upsert products into DB.

    sync_products_to_shopify() – Pull all un-synced products from DB,
                                 create/update on Shopify, and store
                                 the returned Shopify product ID.

Beat schedule (configured in celery_app.py):
    crawl_best_sellers     → every 12 hours
    sync_products_to_shopify → every 30 minutes
"""

import asyncio
from typing import Any

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


# ── Helper: run async code from a sync Celery task ────────────────────────────

def _run(coro: Any) -> Any:
    """Run an async coroutine in the current thread's event loop."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Task: crawl_best_sellers ──────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_products.crawl_best_sellers",
    bind=True,
    max_retries=2,
    default_retry_delay=300,  # 5 minutes
    acks_late=True,
)
def crawl_best_sellers(self: Any) -> dict[str, Any]:  # type: ignore[return]
    """
    Crawl StyleKorean Best Sellers (up to PRODUCT_CRAWL_LIMIT items),
    parse each product page, and upsert records into the database.

    Returns
    -------
    {"crawled": int, "upserted": int}
    """
    logger.info("tasks_products.crawl_best_sellers.start")

    async def _crawl() -> dict[str, Any]:
        from app.crawlers.stylekorean_crawler import crawl_best_sellers as do_crawl
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            products = await do_crawl(session)
            await session.commit()
            return {"crawled": len(products), "upserted": len(products)}

    try:
        result = _run(_crawl())
        logger.info("tasks_products.crawl_best_sellers.done", **result)
        return result
    except Exception as exc:
        logger.error("tasks_products.crawl_best_sellers.error", error=str(exc))
        raise self.retry(exc=exc)


# ── Task: sync_products_to_shopify ────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_products.sync_products_to_shopify",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def sync_products_to_shopify(self: Any) -> dict[str, Any]:  # type: ignore[return]
    """
    Fetch all products that have no Shopify product ID yet and
    create/update them on Shopify. Stores the returned Shopify ID.

    Returns
    -------
    {"synced": int, "skipped": int, "failed": int}
    """
    logger.info("tasks_products.sync_products_to_shopify.start")

    async def _sync() -> dict[str, Any]:
        from app.db.session import AsyncSessionLocal
        from app.services.product_service import (
            get_unsynced_products,
            mark_synced,
        )
        from app.services.shopify_product_service import get_shopify_product_service

        svc     = get_shopify_product_service()
        synced  = 0
        skipped = 0
        failed  = 0

        async with AsyncSessionLocal() as session:
            products = await get_unsynced_products(session)
            logger.info(
                "tasks_products.sync.found_unsynced",
                count=len(products),
            )

            for product in products:
                try:
                    shopify_id = await svc.create_or_update_product(product)

                    if shopify_id:
                        await mark_synced(session, product, shopify_id)
                        synced += 1
                        logger.info(
                            "tasks_products.sync.product_synced",
                            supplier_product_id=product.supplier_product_id,
                            shopify_product_id=shopify_id,
                        )
                    else:
                        # Stub mode (no credentials) – count as skipped
                        skipped += 1
                        logger.debug(
                            "tasks_products.sync.skipped_stub",
                            supplier_product_id=product.supplier_product_id,
                        )
                except Exception as exc:
                    failed += 1
                    logger.error(
                        "tasks_products.sync.product_failed",
                        supplier_product_id=product.supplier_product_id,
                        error=str(exc),
                    )

            await session.commit()

        return {"synced": synced, "skipped": skipped, "failed": failed}

    try:
        result = _run(_sync())
        logger.info("tasks_products.sync_products_to_shopify.done", **result)
        return result
    except Exception as exc:
        logger.error("tasks_products.sync_products_to_shopify.error", error=str(exc))
        raise self.retry(exc=exc)
