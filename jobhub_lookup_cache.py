"""Shared lookup-cache invalidation for the JobHub apps.

Option-list helpers (jobs, employees, builders, products, job stages) run once
per write-free rerun and are served from the Streamlit data cache after that.
Every write through a module that calls ``notify_db_write`` clears those caches,
so dropdowns never show stale options after an add/edit. Reads are never
invalidated.
"""

from __future__ import annotations

_tracked = []


def track_cached(fn):
    """Register a Streamlit-cached function so writes can clear it."""
    _tracked.append(fn)
    return fn


def _is_read(sql):
    head = " ".join(str(sql or "").split())[:24].upper()
    return head.startswith(("SELECT", "WITH", "PRAGMA", "EXPLAIN"))


def notify_db_write(sql=""):
    """Clear cached option lists after a non-read SQL statement runs."""
    if _is_read(sql):
        return
    for fn in list(_tracked):
        try:
            fn.clear()
        except Exception:
            pass
