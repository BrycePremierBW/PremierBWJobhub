"""Hard UI permission boundary for purchase-order information.

Premier Brushworks requires purchase orders to be visible only to JobHub admin
accounts.  This guard is deliberately broader than hiding the Upload PO menu: it
also suppresses direct PO controls, purchasing routes, uploads, downloads,
headings and PO-backed tables when a manager or employee is signed in.

Database ownership and existing admin workflows are unchanged.
"""
from __future__ import annotations

import re
import sys
from types import SimpleNamespace
from typing import Any


PATCH_MARKER = "_pb_po_admin_only_guard"
PO_ROUTE_LABELS = {
    "Upload PO",
    "Purchase Orders",
    "Purchase Order",
    "Job Purchase Orders",
    "Purchasing",
    "Procurement",
}


def _st() -> Any:
    return sys.modules.get("streamlit")


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def current_role(st: Any | None = None) -> str:
    app_role = _app_attr("current_role")
    if callable(app_role):
        try:
            return str(app_role() or "").strip().lower()
        except Exception:
            pass
    st = st or _st()
    if st is None:
        return ""
    try:
        return str((st.session_state.get("user") or {}).get("role") or "").strip().lower()
    except Exception:
        return ""


def is_admin(st: Any | None = None) -> bool:
    return current_role(st) == "admin"


def is_po_sensitive_text(value: Any) -> bool:
    text = re.sub(r"[_-]+", " ", str(value or "")).strip()
    if not text:
        return False
    folded = text.casefold()
    if folded in {"purchasing", "procurement"}:
        return True
    return bool(
        re.search(r"\bpurchase\s+orders?\b", text, flags=re.IGNORECASE)
        or re.search(r"\bPO\b", text, flags=re.IGNORECASE)
        or re.search(r"\bpo\s+number\b", text, flags=re.IGNORECASE)
    )


def filter_po_options(options: Any, admin: bool) -> list[Any]:
    values = list(options or [])
    if admin:
        return values
    return [value for value in values if not is_po_sensitive_text(value)]


def _data_is_po_sensitive(data: Any) -> bool:
    columns = getattr(data, "columns", None)
    if columns is None:
        return False
    return any(is_po_sensitive_text(column) for column in list(columns))


class _RestrictedTableResult(dict):
    """Selection-compatible result for a hidden st.dataframe call."""

    def __init__(self) -> None:
        selection = SimpleNamespace(rows=[], columns=[])
        super().__init__({"selection": {"rows": [], "columns": []}})
        self.selection = selection


def _notice_once(st: Any) -> None:
    key = "_pb_po_restricted_notice_shown"
    try:
        if st.session_state.get(key):
            return
        st.session_state[key] = True
    except Exception:
        pass
    try:
        # This text is intentionally generic because the caption renderer itself
        # is protected by this guard for non-admin users.
        original_caption = getattr(getattr(st, "caption", None), "_pb_original", None)
        if callable(original_caption):
            original_caption("This financial information is restricted to administrators.")
    except Exception:
        pass


def _patch_choice(owner: Any, method_name: str, st: Any) -> bool:
    original = getattr(owner, method_name, None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        if is_admin(st):
            return original(*args, **kwargs)
        arg_list = list(args)
        options_index = None
        if len(arg_list) >= 2 and isinstance(arg_list[0], str):
            options_index = 1
        elif len(arg_list) >= 3:
            options_index = 2
        elif "options" in kwargs:
            options_index = None
        options = arg_list[options_index] if options_index is not None else kwargs.get("options")
        filtered = filter_po_options(options, False)
        if filtered != list(options or []):
            if not filtered:
                _notice_once(st)
                return None
            if options_index is not None:
                arg_list[options_index] = filtered
            else:
                kwargs["options"] = filtered
            if isinstance(kwargs.get("index"), int) and kwargs["index"] >= len(filtered):
                kwargs["index"] = 0
            # Clear a remembered restricted value before Streamlit validates the
            # widget state against the now-shorter option list.
            key = str(kwargs.get("key") or "")
            try:
                if key and st.session_state.get(key) not in filtered:
                    st.session_state[key] = filtered[0]
            except Exception:
                pass
        return original(*tuple(arg_list), **kwargs)

    wrapper._pb_original = original
    setattr(wrapper, PATCH_MARKER, True)
    setattr(owner, method_name, wrapper)
    return True


def _patch_text_control(owner: Any, method_name: str, st: Any, hidden_return: Any) -> bool:
    original = getattr(owner, method_name, None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        if is_admin(st):
            return original(*args, **kwargs)
        label = kwargs.get("label")
        if label is None and args:
            label = args[1] if len(args) > 1 and not isinstance(args[0], str) else args[0]
        key = kwargs.get("key")
        if is_po_sensitive_text(label) or is_po_sensitive_text(key):
            _notice_once(st)
            return hidden_return
        return original(*args, **kwargs)

    wrapper._pb_original = original
    setattr(wrapper, PATCH_MARKER, True)
    setattr(owner, method_name, wrapper)
    return True


def _patch_text_output(owner: Any, method_name: str, st: Any) -> bool:
    original = getattr(owner, method_name, None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        if not is_admin(st):
            value = kwargs.get("body")
            if value is None and args:
                value = args[1] if len(args) > 1 and not isinstance(args[0], (str, bytes)) else args[0]
            if is_po_sensitive_text(value):
                return None
        return original(*args, **kwargs)

    wrapper._pb_original = original
    setattr(wrapper, PATCH_MARKER, True)
    setattr(owner, method_name, wrapper)
    return True


def _patch_dataframe(owner: Any, st: Any) -> bool:
    original = getattr(owner, "dataframe", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        if is_admin(st):
            return original(*args, **kwargs)
        data = kwargs.get("data")
        if data is None and args:
            if hasattr(args[0], "columns"):
                data = args[0]
            elif len(args) > 1 and hasattr(args[1], "columns"):
                data = args[1]
        key = kwargs.get("key")
        if is_po_sensitive_text(key) or _data_is_po_sensitive(data):
            _notice_once(st)
            return _RestrictedTableResult()
        return original(*args, **kwargs)

    wrapper._pb_original_dataframe = original
    setattr(wrapper, PATCH_MARKER, True)
    setattr(owner, "dataframe", wrapper)
    return True


def _patch_data_editor(owner: Any, st: Any) -> bool:
    original = getattr(owner, "data_editor", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        if is_admin(st):
            return original(*args, **kwargs)
        data = kwargs.get("data")
        if data is None and args:
            data = args[0] if hasattr(args[0], "columns") else (args[1] if len(args) > 1 else None)
        if is_po_sensitive_text(kwargs.get("key")) or _data_is_po_sensitive(data):
            _notice_once(st)
            try:
                return data.copy()
            except Exception:
                return data
        return original(*args, **kwargs)

    wrapper._pb_original_data_editor = original
    setattr(wrapper, PATCH_MARKER, True)
    setattr(owner, "data_editor", wrapper)
    return True


def _patch_owner(owner: Any, st: Any) -> bool:
    installed = False
    for name in ("radio", "selectbox"):
        installed = _patch_choice(owner, name, st) or installed
    for name, hidden in (
        ("button", False),
        ("download_button", False),
        ("file_uploader", None),
        ("metric", None),
    ):
        installed = _patch_text_control(owner, name, st, hidden) or installed
    for name in ("header", "subheader", "markdown", "caption", "write", "info", "warning", "success"):
        installed = _patch_text_output(owner, name, st) or installed
    installed = _patch_dataframe(owner, st) or installed
    installed = _patch_data_editor(owner, st) or installed
    return installed


def install_po_admin_only_guard() -> bool:
    st = _st()
    if st is None:
        return False
    installed = _patch_owner(st, st)
    sidebar = getattr(st, "sidebar", None)
    if sidebar is not None:
        installed = _patch_owner(sidebar, st) or installed
    try:
        delta_module = sys.modules.get("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_owner(delta_cls, st) or installed
    return installed
