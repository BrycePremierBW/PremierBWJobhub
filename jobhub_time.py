"""Business-local date helpers shared by JobHub and its scheduler."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_JOBHUB_TIMEZONE = "Australia/Brisbane"


def jobhub_today(now: datetime | None = None) -> date:
    """Return JobHub's business date instead of the Render server's UTC date."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    timezone_name = str(
        os.getenv("JOBHUB_TIMEZONE", DEFAULT_JOBHUB_TIMEZONE)
    ).strip() or DEFAULT_JOBHUB_TIMEZONE
    try:
        business_timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        business_timezone = ZoneInfo(DEFAULT_JOBHUB_TIMEZONE)
    return current.astimezone(business_timezone).date()
