"""Phone-safe navigation for the Streamlit JobHub app.

Streamlit's native sidebar is unreliable as the only navigation surface on
phones.  This guard keeps desktop behaviour intact, but on small screens it
neutralises the sidebar overlay and adds a main-page Quick Menu beside the real
router widgets.  Staff can navigate without needing the sidebar to be usable.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any


_MOBILE_NAV_CSS = """
<style id="pb-mobile-phone-navigation-fix">
/* PB_JOBHUB_MOBILE_PHONE_NAVIGATION_FIX_V4
   Phone navigation no longer depends on the Streamlit sidebar.  A main-page
   Quick Menu is rendered by the radio guard below; on phones the native sidebar
   is hidden/neutralised so it cannot trap the page or make content unusable. */
@media (max-width: 768px) {
    html,
    body,
    .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    .main {
        width: 100vw !important;
        max-width: 100vw !important;
        min-width: 0 !important;
        margin-left: 0 !important;
        left: 0 !important;
        overflow-x: hidden !important;
        overscroll-behavior-x: none !important;
    }

    .block-container {
        width: 100vw !important;
        max-width: 100vw !important;
        min-width: 0 !important;
        margin-left: 0 !important;
        padding-top: calc(0.85rem + env(safe-area-inset-top)) !important;
        padding-left: max(0.7rem, env(safe-area-inset-left)) !important;
        padding-right: max(0.7rem, env(safe-area-inset-right)) !important;
        padding-bottom: calc(2.5rem + env(safe-area-inset-bottom)) !important;
        box-sizing: border-box !important;
    }

    section[data-testid="stSidebar"],
    [data-testid="stSidebar"],
    [data-testid="stSidebarNav"],
    [data-testid="collapsedControl"] {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        width: 0 !important;
        min-width: 0 !important;
        max-width: 0 !important;
        transform: translateX(-120vw) !important;
        pointer-events: none !important;
    }

    div[data-baseweb="select"],
    div[data-baseweb="popover"],
    div[data-baseweb="menu"] {
        max-width: calc(100vw - 18px) !important;
        z-index: 2147483646 !important;
    }

    div[data-baseweb="select"] * {
        min-width: 0 !important;
        white-space: normal !important;
        overflow-wrap: anywhere !important;
    }

    /* Make forms/tables less likely to blow out the phone width. */
    [data-testid="stDataFrame"],
    [data-testid="stTable"],
    iframe,
    canvas,
    img,
    video {
        max-width: 100% !important;
    }
}
</style>
"""

_MAIN_MENU_MARKERS = {
    "Dashboard", "Control Centre", "Operations Hub", "Jobs", "Job Folders",
    "Estimating", "Site Operations", "Reports", "Management", "AI Assistant",
    "Field Mode", "Employee Portal", "SWMS / Safety Sign-off",
}
_SUB_MENU_MARKERS = {
    "User Accounts", "Builders & Clients", "Employees", "Staff Requests",
    "Products", "Equipment", "Import / Create Job Pack", "Estimate Working Sheet",
    "Job Progress Tracker", "Estimating Rate Library", "Job Costs / Forecasting",
    "Staff Scheduler", "Painting Intelligence", "Material Costs", "Wages",
    "Timesheets", "Job Photos", "JobHub AI Assistant", "App Builder AI",
    "Reports / Export", "SWMS / Safety Sign-off",
}
_EMPLOYEE_MARKERS = {
    "My Job Info", "Requests", "Submit Timesheet", "View Equipment",
    "Generate Forms", "Upload Photos", "Change Password", "SWMS / Safety Sign-off",
}


def _install_page_config_guard(streamlit_module: Any) -> bool:
    original = getattr(streamlit_module, "set_page_config", None)
    if original is None or getattr(original, "_pb_mobile_sidebar_page_config_guard", False):
        return False

    def pb_mobile_page_config(*args: Any, **kwargs: Any):
        requested = str(kwargs.get("initial_sidebar_state", "")).casefold()
        if requested in {"", "auto", "expanded"}:
            kwargs["initial_sidebar_state"] = "collapsed"
        return original(*args, **kwargs)

    pb_mobile_page_config._pb_mobile_sidebar_page_config_guard = True
    pb_mobile_page_config._pb_original_set_page_config = original
    streamlit_module.set_page_config = pb_mobile_page_config
    return True


def _inject_mobile_css(streamlit_module: Any) -> None:
    if bool(getattr(streamlit_module, "_pb_mobile_nav_css_done", False)):
        return
    try:
        setattr(streamlit_module, "_pb_mobile_nav_css_done", True)
        markdown = getattr(streamlit_module, "markdown", None)
        original = getattr(markdown, "_pb_original_markdown", markdown)
        if callable(original):
            original(_MOBILE_NAV_CSS, unsafe_allow_html=True)
    except Exception:
        pass


def _install_mobile_markdown_guard(streamlit_module: Any) -> bool:
    original = getattr(streamlit_module, "markdown", None)
    if original is None or getattr(original, "_pb_mobile_sidebar_guard", False):
        return False

    def pb_mobile_sidebar_markdown(body: Any, *args: Any, **kwargs: Any):
        result = original(body, *args, **kwargs)
        # Inject once after the first normal markdown call. This is safer than
        # relying on a particular theme marker that may change between builds.
        _inject_mobile_css(streamlit_module)
        return result

    pb_mobile_sidebar_markdown._pb_mobile_sidebar_guard = True
    pb_mobile_sidebar_markdown._pb_original_markdown = original
    streamlit_module.markdown = pb_mobile_sidebar_markdown
    return True


def _option_labels(options: Any) -> list[str]:
    try:
        return [str(option) for option in list(options)]
    except Exception:
        return []


def _radio_signature(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, int | None, str, str]:
    # st.radio(label, options, ...)
    # DeltaGenerator.radio(self, label, options, ...)
    label = kwargs.get("label", "")
    options_index: int | None = None
    if len(args) >= 2 and isinstance(args[0], str):
        label = args[0]
        options_index = 1
    elif len(args) >= 3:
        label = args[1]
        options_index = 2
    elif "options" in kwargs and args:
        label = args[0]
    key = str(kwargs.get("key") or "")
    return label, options_index, str(label or ""), key


def _navigation_kind(label: str, key: str, options: Any) -> str:
    labels = set(_option_labels(options))
    if not labels:
        return ""
    if key == "main_menu" or label == "Menu" or len(labels.intersection(_MAIN_MENU_MARKERS)) >= 2:
        return "main"
    if key in {"management_menu", "estimating_menu", "site_operations_menu", "ai_menu"} or len(labels.intersection(_SUB_MENU_MARKERS)) >= 2:
        return "sub"
    if key in {"employee_menu", "employee_portal_menu"} or len(labels.intersection(_EMPLOYEE_MARKERS)) >= 2:
        return "employee"
    return ""


def _quick_menu_key(widget_key: str, kind: str, label: str) -> str:
    clean_label = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(label or kind))[:40]
    return f"pb_mobile_quick_{widget_key or clean_label or kind}"


def _sync_state_for_choice(st: Any, widget_key: str, choice: str) -> None:
    if not widget_key:
        return
    try:
        st.session_state[widget_key] = choice
    except Exception:
        pass


def _render_quick_menu(st: Any, label: str, key: str, options: Any, kind: str) -> str | None:
    values = list(options)
    labels = [str(value) for value in values]
    if not labels:
        return None

    _inject_mobile_css(st)

    current = None
    try:
        current = st.session_state.get(key) if key else None
    except Exception:
        current = None
    if str(current) not in labels:
        current = values[0]

    title = "📱 Mobile Quick Menu" if kind == "main" else f"📱 Mobile {label or 'section'}"
    try:
        with st.container(border=True):
            st.caption(title)
            selected = st.selectbox(
                "Choose page" if kind == "main" else "Choose section",
                values,
                index=labels.index(str(current)),
                key=_quick_menu_key(key, kind, label),
                label_visibility="collapsed",
            )
    except TypeError:
        st.caption(title)
        selected = st.selectbox(
            "Choose page" if kind == "main" else "Choose section",
            values,
            index=labels.index(str(current)),
            key=_quick_menu_key(key, kind, label),
            label_visibility="collapsed",
        )

    selected_text = str(selected)
    _sync_state_for_choice(st, key, selected_text)
    return selected_text


def _patch_radio(owner: Any, streamlit_module: Any) -> bool:
    original = getattr(owner, "radio", None)
    if original is None or getattr(original, "_pb_mobile_quick_menu_guard", False):
        return False

    def pb_mobile_quick_radio(*args: Any, **kwargs: Any):
        arg_list = list(args)
        label, options_index, label_text, key_text = _radio_signature(tuple(arg_list), kwargs)
        options = arg_list[options_index] if options_index is not None else kwargs.get("options")
        kind = _navigation_kind(label_text, key_text, options)
        quick_choice = None
        if kind:
            try:
                quick_choice = _render_quick_menu(streamlit_module, label_text, key_text, options, kind)
            except Exception:
                quick_choice = None

        result = original(*tuple(arg_list), **kwargs)
        # The Quick Menu is the phone-safe control. Returning its value also lets
        # the current run route immediately without waiting on the hidden sidebar.
        return quick_choice if quick_choice is not None else result

    pb_mobile_quick_radio._pb_mobile_quick_menu_guard = True
    pb_mobile_quick_radio._pb_original_radio = original
    setattr(owner, "radio", pb_mobile_quick_radio)
    return True


def _install_mobile_quick_menu_guard(streamlit_module: Any) -> bool:
    installed = _patch_radio(streamlit_module, streamlit_module)
    try:
        delta_module = sys.modules.get("streamlit.delta_generator") or importlib.import_module("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None)
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_radio(delta_cls, streamlit_module) or installed
    return installed


def _install_mobile_sidebar_html_guard(streamlit_module: Any) -> bool:
    original = getattr(streamlit_module, "html", None)
    if original is None or getattr(original, "_pb_mobile_sidebar_html_guard", False):
        return False

    script = """
<script id="pb-mobile-sidebar-neutralise-v4">
(() => {
  const rootWindow = (() => {
    try { if (window.parent && window.parent.document) return window.parent; } catch (error) {}
    return window;
  })();
  const rootDocument = rootWindow.document;
  if (!rootDocument || rootDocument.__pbMobileSidebarNeutraliseV4) return;
  rootDocument.__pbMobileSidebarNeutraliseV4 = true;

  function isPhone() {
    try { return rootWindow.matchMedia('(max-width: 768px)').matches; }
    catch (error) { return false; }
  }

  function hideSidebar() {
    if (!isPhone()) return;
    const sidebar = rootDocument.querySelector('section[data-testid="stSidebar"]');
    if (sidebar) {
      sidebar.style.display = 'none';
      sidebar.style.visibility = 'hidden';
      sidebar.style.width = '0px';
      sidebar.style.minWidth = '0px';
      sidebar.style.maxWidth = '0px';
      sidebar.style.pointerEvents = 'none';
    }
    const app = rootDocument.querySelector('[data-testid="stAppViewContainer"]');
    const main = rootDocument.querySelector('[data-testid="stMain"]');
    [app, main].forEach((node) => {
      if (!node) return;
      node.style.marginLeft = '0px';
      node.style.left = '0px';
      node.style.width = '100vw';
      node.style.maxWidth = '100vw';
    });
  }

  hideSidebar();
  rootWindow.setTimeout(hideSidebar, 250);
  rootWindow.setTimeout(hideSidebar, 900);
  rootWindow.setTimeout(hideSidebar, 1800);
  try {
    const observer = new MutationObserver(hideSidebar);
    observer.observe(rootDocument.body || rootDocument.documentElement, { childList: true, subtree: true, attributes: true });
  } catch (error) {}
})();
</script>
"""

    injecting = False

    def pb_mobile_sidebar_html(body: Any, *args: Any, **kwargs: Any):
        nonlocal injecting
        result = original(body, *args, **kwargs)
        if not injecting and isinstance(body, str) and "apple-mobile-web-app-title" in body:
            try:
                injecting = True
                original(script, unsafe_allow_javascript=True)
            finally:
                injecting = False
        return result

    pb_mobile_sidebar_html._pb_mobile_sidebar_html_guard = True
    pb_mobile_sidebar_html._pb_original_html = original
    streamlit_module.html = pb_mobile_sidebar_html
    return True


def install_mobile_sidebar_guard() -> bool:
    streamlit_module = sys.modules.get("streamlit")
    if streamlit_module is None:
        return False
    page_config_installed = _install_page_config_guard(streamlit_module)
    markdown_installed = _install_mobile_markdown_guard(streamlit_module)
    html_installed = _install_mobile_sidebar_html_guard(streamlit_module)
    quick_menu_installed = _install_mobile_quick_menu_guard(streamlit_module)
    return bool(page_config_installed or markdown_installed or html_installed or quick_menu_installed)
