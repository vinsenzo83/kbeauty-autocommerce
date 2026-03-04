from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
UTC = timezone.utc


def now_kst() -> datetime:
    """Return the current datetime in Asia/Seoul (KST, UTC+9)."""
    return datetime.now(tz=KST)


def now_utc() -> datetime:
    """Return the current datetime in UTC."""
    return datetime.now(tz=UTC)


def to_kst(dt: datetime) -> datetime:
    """Convert any timezone-aware datetime to KST."""
    return dt.astimezone(KST)


def today_kst_iso() -> str:
    """Return today's date in KST as an ISO-8601 string (YYYY-MM-DD)."""
    return now_kst().date().isoformat()
