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
    ],
)

# ── Poll interval (seconds) – overridable via env ─────────────────────────────
_POLL_INTERVAL = int(os.getenv("TRACKING_POLL_INTERVAL", "600"))  # default 10 min

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
        "poll-tracking-every-interval": {
            "task":     "workers.tasks_tracking.poll_tracking",
            "schedule": _POLL_INTERVAL,          # seconds (timedelta-compatible int)
            "options":  {"expires": _POLL_INTERVAL},
        },
    },
)
