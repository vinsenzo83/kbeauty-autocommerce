from __future__ import annotations

"""
app/workers/tasks_pricing.py
─────────────────────────────
Sprint 8 – Celery tasks for the pricing engine.

Tasks
-----
sync_prices
    Runs every 6 hours.  For every canonical_product with pricing_enabled=True,
    generates a price quote and applies it to Shopify.

sync_price_for_canonical(canonical_product_id)
    On-demand task for a single canonical product.
    Called from Admin API POST /admin/pricing/canonical/{id}/sync.
"""

import asyncio
from typing import Any
from uuid import UUID

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

# Top-level import so tests can patch AsyncSessionLocal
try:
    from app.db.session import AsyncSessionLocal  # noqa: F401
except Exception:  # pragma: no cover
    AsyncSessionLocal = None  # type: ignore[assignment]


async def _run_price_sync(
    *,
    canonical_product_id: UUID | None = None,
    session_factory: Any = None,
    shopify_service: Any = None,
) -> dict[str, Any]:
    """
    Core async implementation of the pricing sync.

    Parameters
    ----------
    canonical_product_id : If set, sync only this canonical product.
    session_factory      : Override for tests.
    shopify_service      : Override for tests (mock Shopify service).

    Returns
    -------
    {"total": int, "quoted": int, "applied": int, "errors": int}
    """
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal as _local
    from app.models.canonical_product import CanonicalProduct
    from app.services.pricing_service import apply_quote_to_shopify, generate_quote

    import app.workers.tasks_pricing as _self
    _session_factory = session_factory or getattr(_self, "AsyncSessionLocal", _local)

    # Default Shopify service if not injected
    if shopify_service is None:
        from app.services.shopify_product_service import get_shopify_product_service
        shopify_service = get_shopify_product_service()

    total   = 0
    quoted  = 0
    applied = 0
    errors  = 0

    async with _session_factory() as session:
        # Determine which canonical products to sync
        if canonical_product_id is not None:
            stmt = select(CanonicalProduct).where(
                CanonicalProduct.id == canonical_product_id,
                CanonicalProduct.pricing_enabled == True,  # noqa: E712
            )
        else:
            stmt = select(CanonicalProduct).where(
                CanonicalProduct.pricing_enabled == True  # noqa: E712
            )

        result    = await session.execute(stmt)
        products  = list(result.scalars().all())
        total     = len(products)

        logger.info("tasks_pricing.sync_start", total=total)

        for cp in products:
            try:
                quote = await generate_quote(cp.id, session)
                if quote is not None:
                    quoted += 1
                    ok      = await apply_quote_to_shopify(cp.id, session, shopify_service)
                    if ok:
                        applied += 1
            except Exception as exc:
                errors += 1
                logger.error(
                    "tasks_pricing.product_error",
                    canonical_id=str(cp.id),
                    error=str(exc),
                )

        await session.commit()

    summary = {
        "total":   total,
        "quoted":  quoted,
        "applied": applied,
        "errors":  errors,
    }
    logger.info("tasks_pricing.sync_done", **summary)
    return summary


# ── Celery tasks ──────────────────────────────────────────────────────────────

@celery_app.task(
    name="workers.tasks_pricing.sync_prices",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    soft_time_limit=20_000,
    time_limit=21_600,  # 6 h hard limit
)
def sync_prices(self: Any) -> dict[str, Any]:  # type: ignore[return]
    """
    Celery periodic task: sync prices for all canonical products.
    Runs every 6 hours.
    """
    logger.info("tasks_pricing.sync_prices.start", task_id=self.request.id)
    try:
        return asyncio.run(_run_price_sync())
    except Exception as exc:
        logger.error(
            "tasks_pricing.sync_prices.failed",
            error=str(exc),
            task_id=self.request.id,
        )
        raise self.retry(exc=exc)


@celery_app.task(
    name="workers.tasks_pricing.sync_price_for_canonical",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def sync_price_for_canonical(
    self: Any,
    canonical_product_id: str,
) -> dict[str, Any]:  # type: ignore[return]
    """
    On-demand Celery task: sync price for one canonical product.

    Parameters
    ----------
    canonical_product_id : str – UUID of the canonical product.
    """
    from uuid import UUID as _UUID

    cid = _UUID(canonical_product_id)
    logger.info(
        "tasks_pricing.sync_price_for_canonical.start",
        canonical_product_id=canonical_product_id,
        task_id=self.request.id,
    )
    try:
        return asyncio.run(_run_price_sync(canonical_product_id=cid))
    except Exception as exc:
        logger.error(
            "tasks_pricing.sync_price_for_canonical.failed",
            error=str(exc),
            canonical_product_id=canonical_product_id,
        )
        raise self.retry(exc=exc)
