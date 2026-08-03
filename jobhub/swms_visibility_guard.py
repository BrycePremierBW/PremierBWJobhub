"""Make the SWMS panel visibly render inside the employee Generate Forms tab."""

from __future__ import annotations

from typing import Any

from . import swms_guard


def install_swms_visibility_guard() -> bool:
    proxy_cls = getattr(swms_guard, "_TabProxy", None)
    if proxy_cls is None or getattr(proxy_cls, "_pb_swms_visibility_guard", False):
        return False

    original_enter = getattr(proxy_cls, "__enter__", None)
    original_exit = getattr(proxy_cls, "__exit__", None)
    if original_enter is None or original_exit is None:
        return False

    def visible_enter(self: Any) -> Any:
        entered = original_enter(self)
        try:
            swms_guard.render_swms_panel(employee_mode=True, key_prefix="employee_swms")
        except Exception as exc:
            st = swms_guard._st()
            if st is not None:
                st.warning(f"SWMS panel could not render: {exc}")
        return entered

    def visible_exit(self: Any, exc_type: Any, exc: Any, tb: Any) -> Any:
        # The previous guard rendered SWMS during __exit__, which could leave the
        # controls hidden or duplicated. Rendering now happens on __enter__, while
        # the tab context is active and before the existing Generate Forms content.
        return self._tab.__exit__(exc_type, exc, tb)

    proxy_cls.__enter__ = visible_enter
    proxy_cls.__exit__ = visible_exit
    proxy_cls._pb_swms_visibility_guard = True
    return True
