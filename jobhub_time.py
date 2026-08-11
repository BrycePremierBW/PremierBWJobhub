"""Business-local date helpers shared by JobHub and its scheduler."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_JOBHUB_TIMEZONE = "Australia/Brisbane"


def _business_local(now: datetime | None = None) -> datetime:
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
    return current.astimezone(business_timezone)


def jobhub_today(now: datetime | None = None) -> date:
    """Return JobHub's business date instead of the Render server's UTC date."""
    return _business_local(now).date()


def jobhub_now(now: datetime | None = None) -> datetime:
    """Return JobHub's business-local wall time (naive, Brisbane by default).

    JobHub stores naive timestamps, so the offset is stripped after the
    conversion to keep ``strftime`` output, ``isoformat`` strings and
    comparisons with stored naive timestamps unchanged in format.
    """
    return _business_local(now).replace(tzinfo=None)
