"""Keep the PO Upload page from running database DDL on every Streamlit rerun.

The PO upload route is injected after JobHub's normal database startup.  Its
legacy renderer still called ``_ensure_schema`` every time the page reran, and
the split-PO guard also attempted to drop the PO-number uniqueness constraint at
install time and on every page render.  Selecting a file, changing the split
toggle or reopening the page could therefore wait on PostgreSQL table locks.

This guard makes those operations demand-driven:

* cache table-column discovery for the running process;
* treat JobHub's already-created core PO/document tables as ready without DDL;
* run the legacy schema migration only if the core tables are genuinely missing;
* defer split-PO constraint relaxation until a split line is actually saved;
* reject unexpectedly large PO files before copying them to persistent storage.
"""

from __future__ import annotations

import functools
import importlib
import sys
import threading
from typing import Any, Callable


PATCH_MARKER = "_pb_po_upload_performance_guard"
MAX_PO_UPLOAD_BYTES = 25 * 1024 * 1024

_SCHEMA_LOCK = threading.RLock()
_SCHEMA_READY = False
_SPLIT_LOCK = threading.RLock()
_SPLIT_CONSTRAINT_READY = False


def _po_module() -> Any:
    return sys.modules.get("jobhub.po_upload_guard") or importlib.import_module(
        "jobhub.po_upload_guard"
    )


def _split_module() -> Any:
    return sys.modules.get("jobhub.po_upload_split_guard") or importlib.import_module(
        "jobhub.po_upload_split_guard"
    )


def _core_schema_ready(po: Any) -> bool:
    try:
        documents = set(po._table_columns("job_documents") or ())
        purchase_orders = set(po._table_columns("job_purchase_orders") or ())
    except Exception:
        return False

    document_ready = (
        "job_id" in documents
        and bool({"file_name", "filename", "name"}.intersection(documents))
        and bool({"file_path", "path"}.intersection(documents))
    )
    po_ready = (
        "job_id" in purchase_orders
        and "po_number" in purchase_orders
        and bool(
            {"po_value_ex_gst", "value_ex_gst", "amount_ex_gst"}.intersection(
                purchase_orders
            )
        )
    )
    return document_ready and po_ready


def _patch_table_columns(po: Any) -> bool:
    original = getattr(po, "_table_columns", None)
    if not callable(original) or getattr(original, PATCH_MARKER, False):
        return False

    cache: dict[tuple[bool, str], frozenset[str]] = {}
    cache_lock = threading.RLock()

    @functools.wraps(original)
    def cached_table_columns(table: str) -> set[str]:
        try:
            postgres = bool(po._use_postgres())
        except Exception:
            postgres = False
        key = (postgres, str(table))
        with cache_lock:
            cached = cache.get(key)
        if cached is not None:
            return set(cached)

        result = set(original(table) or ())
        # Do not cache an empty result: a temporary database failure or a table
        # being created by startup migrations should be retried on the next call.
        if result:
            with cache_lock:
                cache[key] = frozenset(result)
        return result

    def cache_clear() -> None:
        with cache_lock:
            cache.clear()

    setattr(cached_table_columns, PATCH_MARKER, True)
    cached_table_columns._pb_original_table_columns = original
    cached_table_columns.cache_clear = cache_clear
    po._table_columns = cached_table_columns
    return True


def _clear_column_cache(po: Any) -> None:
    clear = getattr(getattr(po, "_table_columns", None), "cache_clear", None)
    if callable(clear):
        clear()


def _patch_schema_check(po: Any) -> bool:
    original = getattr(po, "_ensure_schema", None)
    if not callable(original) or getattr(original, PATCH_MARKER, False):
        return False

    @functools.wraps(original)
    def fast_ensure_schema() -> None:
        global _SCHEMA_READY
        if _SCHEMA_READY:
            return
        if _core_schema_ready(po):
            _SCHEMA_READY = True
            return

        with _SCHEMA_LOCK:
            if _SCHEMA_READY:
                return
            if _core_schema_ready(po):
                _SCHEMA_READY = True
                return

            # This is a fallback for a genuinely incomplete/older database.  It
            # is no longer executed merely because the PO page rerendered.
            original()
            _clear_column_cache(po)
            if not _core_schema_ready(po):
                raise RuntimeError(
                    "Purchase-order storage is not ready after the schema migration."
                )
            _SCHEMA_READY = True

    setattr(fast_ensure_schema, PATCH_MARKER, True)
    fast_ensure_schema._pb_original_ensure_schema = original
    po._ensure_schema = fast_ensure_schema
    return True


def _uploaded_size(uploaded_file: Any) -> int:
    size = getattr(uploaded_file, "size", None)
    try:
        if size is not None:
            return max(0, int(size))
    except Exception:
        pass

    getbuffer = getattr(uploaded_file, "getbuffer", None)
    if callable(getbuffer):
        return len(getbuffer())
    getvalue = getattr(uploaded_file, "getvalue", None)
    if callable(getvalue):
        return len(getvalue())
    return 0


def _patch_file_save(po: Any) -> bool:
    original = getattr(po, "_save_uploaded_file", None)
    if not callable(original) or getattr(original, PATCH_MARKER, False):
        return False

    @functools.wraps(original)
    def checked_save(job_id: int, po_number: str, uploaded_file: Any):
        size = _uploaded_size(uploaded_file)
        if size > MAX_PO_UPLOAD_BYTES:
            raise ValueError(
                f"PO file is {size / (1024 * 1024):.1f} MB. "
                "The PO upload limit is 25 MB."
            )
        return original(job_id, po_number, uploaded_file)

    setattr(checked_save, PATCH_MARKER, True)
    checked_save._pb_original_save_uploaded_file = original
    po._save_uploaded_file = checked_save
    return True


def _run_split_relax_once(original: Callable[..., Any], po: Any) -> None:
    global _SPLIT_CONSTRAINT_READY
    try:
        postgres = bool(po._use_postgres())
    except Exception:
        postgres = False
    if not postgres or _SPLIT_CONSTRAINT_READY:
        return

    with _SPLIT_LOCK:
        if _SPLIT_CONSTRAINT_READY:
            return
        # Database lock/query timeouts installed by database_timeout_guard bound
        # this explicit save-time migration.  It no longer runs while opening or
        # interacting with the PO Upload page.
        original(po)
        _SPLIT_CONSTRAINT_READY = True
        _clear_column_cache(po)


def _patch_split_constraint(split: Any, po: Any) -> bool:
    original_relax = getattr(split, "_relax_po_number_uniqueness", None)
    original_record = getattr(split, "_record_po_line", None)
    if (
        not callable(original_relax)
        or not callable(original_record)
        or getattr(original_record, PATCH_MARKER, False)
    ):
        return False

    @functools.wraps(original_relax)
    def no_page_render_ddl(*args: Any, **kwargs: Any) -> None:
        # install_po_upload_split_guard and _render_split_upload_page both call
        # this function.  Those calls must remain no-ops; the record wrapper
        # below performs it once, immediately before the first split insert.
        return None

    setattr(no_page_render_ddl, PATCH_MARKER, True)
    no_page_render_ddl._pb_original_relax_po_number_uniqueness = original_relax
    split._relax_po_number_uniqueness = no_page_render_ddl

    @functools.wraps(original_record)
    def record_po_line(*args: Any, **kwargs: Any):
        _run_split_relax_once(original_relax, po)
        return original_record(*args, **kwargs)

    setattr(record_po_line, PATCH_MARKER, True)
    record_po_line._pb_original_record_po_line = original_record
    split._record_po_line = record_po_line
    return True


def install_po_upload_performance_guard() -> bool:
    po = _po_module()
    split = _split_module()
    installed = _patch_table_columns(po)
    installed = _patch_schema_check(po) or installed
    installed = _patch_file_save(po) or installed
    installed = _patch_split_constraint(split, po) or installed
    return installed
