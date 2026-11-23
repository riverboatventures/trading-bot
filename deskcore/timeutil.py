"""UTC-only time helpers. All internal timestamps are integer epoch milliseconds."""
from __future__ import annotations

from datetime import datetime, timezone

MIN_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000

INTERVAL_MS = {"1m": MIN_MS, "1h": HOUR_MS}


def utcnow_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def iso(ms: int | None) -> str:
    if ms is None:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def day_start_ms(ms: int) -> int:
    return ms - (ms % DAY_MS)


def parse_iso(value: str) -> int:
    """Parse '2026-11-23', '2026-11-23T10:00:00Z' or '...+00:00' as UTC.

    A timestamp with a time component but no explicit UTC marker is rejected,
    so a local time can never silently be read as UTC.
    """
    v = (value or "").strip()
    if not v:
        raise ValueError("empty timestamp")
    if len(v) == 10:
        dt = datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            raise ValueError(f"timestamp '{value}' has no timezone; append 'Z' for UTC")
        dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp() * 1000)
