from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "kbeauty",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.tasks_order",
        "app.workers.tasks_tracking",
        "app.workers.tasks_products",          # Sprint 4
        "app.workers.tasks_inventory",         # Sprint 6
        "app.workers.tasks_supplier_products", # Sprint 7
    ],
)

# ── Interval constants (seconds) – overridable via env ───────────────────────
_POLL_INTERVAL               = int(os.getenv("TRACKING_POLL_INTERVAL",       "600"))   # 10 min
_CRAWL_INTERVAL              = int(os.getenv("PRODUCT_CRAWL_INTERVAL",        "43200")) # 12 h
_SHOPIFY_SYNC_INTERVAL       = int(os.getenv("PRODUCT_SYNC_INTERVAL",         "1800"))  # 30 min
_INVENTORY_SYNC_INTERVAL     = int(os.getenv("INVENTORY_SYNC_INTERVAL",       "1800"))  # 30 min
_SUPPLIER_SYNC_INTERVAL      = int(os.getenv("SUPPLIER_SYNC_INTERVAL",        "3600"))  # 60 min

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Time
    timezone="Asia/Seoul",
    enable_utc=True,

    # Reliability
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    result_expires=3600,

    # ── Periodic schedule (Celery beat) ───────────────────────────────────────
    beat_schedule={
        # Sprint 3: tracking poll
        "poll-tracking-every-interval": {
            "task":     "workers.tasks_tracking.poll_tracking",
            "schedule": _POLL_INTERVAL,
            "options":  {"expires": _POLL_INTERVAL},
        },

        # Sprint 4: best-seller crawl every 12 h
        "crawl-best-sellers-every-12h": {
            "task":     "workers.tasks_products.crawl_best_sellers",
            "schedule": _CRAWL_INTERVAL,
            "options":  {"expires": _CRAWL_INTERVAL},
        },

        # Sprint 4: Shopify product sync every 30 min
        "sync-products-to-shopify-every-30m": {
            "task":     "workers.tasks_products.sync_products_to_shopify",
            "schedule": _SHOPIFY_SYNC_INTERVAL,
            "options":  {"expires": _SHOPIFY_SYNC_INTERVAL},
        },

        # Sprint 6: Inventory sync every 30 min
        "sync-inventory-every-30m": {
            "task":     "workers.tasks_inventory.sync_inventory",
            "schedule": _INVENTORY_SYNC_INTERVAL,
            "options":  {"expires": _INVENTORY_SYNC_INTERVAL},
        },

        # Sprint 7: Multi-supplier product sync every 60 min
        "sync-supplier-products-every-60m": {
            "task":     "workers.tasks_supplier_products.sync_supplier_products",
            "schedule": _SUPPLIER_SYNC_INTERVAL,
            "options":  {"expires": _SUPPLIER_SYNC_INTERVAL},
        },
    },
)
