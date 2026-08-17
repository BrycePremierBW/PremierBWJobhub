"""Shared lookup-cache invalidation for the JobHub apps.

Option-list helpers (jobs, employees, builders, products, job stages) run once
per write-free rerun and are served from the Streamlit data cache after that.
Every write through a module that calls ``notify_db_write`` clears those caches,
so dropdowns never show stale options after an add/edit. Reads are never
invalidated.
"""

from __future__ import annotations

import re


_tracked = []
_MUTATING_SQL = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|REPLACE|CREATE|ALTER|DROP|TRUNCATE)\b",
    flags=re.IGNORECASE,
)


def _cache_identity(fn):
    """Return a stable identity for a cached helper across Streamlit reruns."""
    return (
        str(getattr(fn, "__module__", "") or ""),
        str(getattr(fn, "__qualname__", getattr(fn, "__name__", "")) or ""),
    )


def track_cached(fn):
    """Register one live wrapper per cached helper.

    Streamlit re-executes the main script on every rerun, creating fresh wrapper
    objects for the same helper functions. Replacing the previous wrapper keeps
    this registry bounded instead of retaining every historical wrapper for the
    life of the process.
    """
    identity = _cache_identity(fn)
    for index, existing in enumerate(_tracked):
        if _cache_identity(existing) == identity:
            _tracked[index] = fn
            break
    else:
        _tracked.append(fn)
    return fn


def _is_read(sql):
    normalised = " ".join(str(sql or "").split())
    upper = normalised.upper()
    if upper.startswith(("SELECT", "PRAGMA")):
        return True
    if upper.startswith("EXPLAIN"):
        # EXPLAIN without ANALYZE is planning-only. Treat EXPLAIN ANALYZE as a
        # potential write because PostgreSQL executes the explained statement.
        return "ANALYZE" not in upper and "ANALYSE" not in upper
    if upper.startswith("WITH"):
        # CTEs are not necessarily reads: PostgreSQL permits data-modifying CTEs
        # such as WITH changed AS (UPDATE ... RETURNING ...) SELECT ... .
        return _MUTATING_SQL.search(normalised) is None
    return False


def notify_db_write(sql=""):
    """Clear cached option lists after a non-read SQL statement runs."""
    if _is_read(sql):
        return
    for fn in list(_tracked):
        try:
            fn.clear()
        except Exception:
            pass
