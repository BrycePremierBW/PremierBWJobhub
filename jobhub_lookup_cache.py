"""Shared lookup-cache invalidation for the JobHub apps.

Option-list helpers (jobs, employees, builders, products, job stages) run once
per write-free rerun and are served from the Streamlit data cache after that.
Every write through a module that calls ``notify_db_write`` clears those caches,
so dropdowns never show stale options after an add/edit. Reads are never
invalidated.
"""

from __future__ import annotations

_tracked = []


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
