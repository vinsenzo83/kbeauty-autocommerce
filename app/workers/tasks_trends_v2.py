from __future__ import annotations

"""
app/workers/tasks_trends_v2.py
──────────────────────────────
Sprint 18 – Celery task for Trend Signal v2 collection.

Pipeline
--------
1. Upsert Amazon and TikTok trend sources
2. Load Amazon bestsellers mock (or live) → insert TrendItems
3. Build / refresh mention_dictionary from canonical products
4. Load TikTok mentions mock (or live) → compute MentionSignals
5. Return summary dict

Schedule: daily 02:30 KST, gated by env TRENDS_ENABLED=1.
Redis lock: "trends:run" TTL=30 min to prevent concurrent runs.
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import structlog

from app.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
_ENABLED          = os.getenv("TRENDS_ENABLED", "0") == "1"
_LOCK_TTL         = int(os.getenv("TREND_LOCK_TTL", str(30 * 60)))   # 30 min
_LOCK_KEY         = "trends:run"
_DAILY_HOUR       = int(os.getenv("TRENDS_V2_DAILY_HOUR", "2"))
_DAILY_MINUTE     = int(os.getenv("TRENDS_V2_DAILY_MINUTE", "30"))
_DRY_RUN_DEFAULT  = os.getenv("TRENDS_DRY_RUN_DEFAULT", "1") == "1"


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(
    name="tasks_trends_v2.run_trend_collection_v2",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def run_trend_collection_v2(self, dry_run: bool = True, limit: int = 200) -> dict[str, Any]:
    """
    Collect Amazon bestseller data and TikTok mention signals.
    Runs synchronously by delegating to an async helper.
    """
    return asyncio.get_event_loop().run_until_complete(
        _run_async(dry_run=dry_run, limit=limit)
    )


# ── Async implementation ──────────────────────────────────────────────────────

async def _acquire_lock(redis_client, key: str, ttl: int) -> bool:
    """Acquire Redis lock.  Returns True if lock was acquired."""
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: redis_client.set(key, "1", nx=True, ex=ttl),
        )
        return bool(result)
    except Exception as exc:
        logger.warning("trend_v2.lock_acquire_failed", exc=str(exc))
        return True   # fail-open so the task can still run


async def _release_lock(redis_client, key: str) -> None:
    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: redis_client.delete(key),
        )
    except Exception as exc:
        logger.warning("trend_v2.lock_release_failed", exc=str(exc))


async def _run_async(dry_run: bool, limit: int) -> dict[str, Any]:
    from app.db import get_async_session_context
    from app.services import trend_signal_service_v2 as tsvc
    from app.services.trend_collectors_v2 import amazon_collector, tiktok_mentions_collector

    # ── Optional Redis lock ───────────────────────────────────────────────────
    redis_client = None
    lock_acquired = True
    try:
        import redis as _redis
        _url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = _redis.from_url(_url, decode_responses=True)
        lock_acquired = await _acquire_lock(redis_client, _LOCK_KEY, _LOCK_TTL)
    except Exception:
        pass   # Redis not available – skip locking

    if not lock_acquired:
        logger.warning("trend_v2.already_running")
        return {"status": "skipped", "reason": "lock_held"}

    summary: dict[str, Any] = {
        "dry_run":          dry_run,
        "started_at":       datetime.now(tz=timezone.utc).isoformat(),
        "amazon_items":     0,
        "tiktok_signals":   0,
        "dictionary_rows":  0,
        "errors":           [],
    }

    try:
        async with get_async_session_context() as session:
            # ── Step 1: upsert sources ────────────────────────────────────────
            amazon_src  = await tsvc.upsert_trend_source(session, "amazon",  "Amazon Bestsellers")
            tiktok_src  = await tsvc.upsert_trend_source(session, "tiktok",  "TikTok Mentions")

            # ── Step 2: load Amazon items ─────────────────────────────────────
            try:
                amazon_items = await amazon_collector.fetch(limit=limit)
                n_amazon = await tsvc.insert_trend_items(session, amazon_src.id, amazon_items)
                summary["amazon_items"] = n_amazon
                logger.info("trend_v2.amazon_inserted", n=n_amazon, dry_run=dry_run)
            except Exception as exc:
                logger.error("trend_v2.amazon_error", exc=str(exc))
                summary["errors"].append(f"amazon: {exc}")

            # ── Step 3: build mention dictionary ──────────────────────────────
            try:
                n_dict = await tsvc.build_mention_dictionary(session)
                summary["dictionary_rows"] = n_dict
                logger.info("trend_v2.dictionary_built", n=n_dict)
            except Exception as exc:
                logger.error("trend_v2.dictionary_error", exc=str(exc))
                summary["errors"].append(f"dictionary: {exc}")

            # ── Step 4: load TikTok mentions ──────────────────────────────────
            try:
                # Build phrase dict for mention extraction
                from sqlalchemy import select as sa_select
                from app.models.trend_signal_v2 import MentionDictionary
                rows = (await session.execute(sa_select(MentionDictionary))).scalars().all()
                phrase_dict = {r.phrase: str(r.canonical_product_id) for r in rows}

                tiktok_docs = await tiktok_mentions_collector.fetch(limit=limit)
                n_tiktok = await tsvc.compute_mention_signals(
                    session, tiktok_src.id, tiktok_docs,
                    phrase_dict=phrase_dict,
                )
                summary["tiktok_signals"] = n_tiktok
                logger.info("trend_v2.tiktok_computed", n=n_tiktok, dry_run=dry_run)
            except Exception as exc:
                logger.error("trend_v2.tiktok_error", exc=str(exc))
                summary["errors"].append(f"tiktok: {exc}")

            # ── Commit only if not dry-run ────────────────────────────────────
            if not dry_run:
                await session.commit()
                logger.info("trend_v2.committed")
            else:
                await session.rollback()
                logger.info("trend_v2.dry_run_rollback")

    except Exception as exc:
        logger.error("trend_v2.fatal", exc=str(exc))
        summary["errors"].append(f"fatal: {exc}")
    finally:
        if redis_client and lock_acquired:
            await _release_lock(redis_client, _LOCK_KEY)

    summary["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
    return summary


# ── Beat schedule (registered in celery_app.py) ───────────────────────────────
# Conditionally added when TRENDS_ENABLED=1 (see celery_app.py)
