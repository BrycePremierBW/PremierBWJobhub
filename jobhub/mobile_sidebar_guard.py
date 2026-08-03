"""Mobile sidebar safeguards for the Streamlit JobHub app.

This guard must never replace the value returned by JobHub's real navigation
radio widgets.  A previous phone quick-menu experiment returned a fallback value
on desktop too, which made the app route back to Dashboard.  This version keeps
normal desktop navigation untouched and applies only CSS/JavaScript that is
scoped to phone-sized viewports.
"""

from __future__ import annotations

import sys
from typing import Any


_MOBILE_NAV_CSS = """
<style id="pb-mobile-phone-navigation-fix">
/* PB_JOBHUB_MOBILE_PHONE_NAVIGATION_FIX_V5
   CSS-only phone fix.  Do not patch or override Streamlit radio return values.
   Desktop navigation must stay completely native. */
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
        padding-top: calc(2.8rem + env(safe-area-inset-top)) !important;
        padding-left: max(0.7rem, env(safe-area-inset-left)) !important;
        padding-right: max(0.7rem, env(safe-area-inset-right)) !important;
        padding-bottom: calc(2.5rem + env(safe-area-inset-bottom)) !important;
        box-sizing: border-box !important;
    }

    [data-testid="collapsedControl"] {
        display: flex !important;
        visibility: visible !important;
        opacity: 1 !important;
        position: fixed !important;
        top: calc(0.45rem + env(safe-area-inset-top)) !important;
        left: calc(0.45rem + env(safe-area-inset-left)) !important;
        z-index: 2147483647 !important;
        pointer-events: auto !important;
        background: rgba(255, 255, 255, 0.96) !important;
        border-radius: 999px !important;
        box-shadow: 0 2px 12px rgba(0,0,0,0.22) !important;
    }

    section[data-testid="stSidebar"] {
        width: min(78vw, 300px) !important;
        min-width: min(78vw, 300px) !important;
        max-width: min(78vw, 300px) !important;
        height: 100dvh !important;
        max-height: 100dvh !important;
        overflow: hidden !important;
        overscroll-behavior: contain !important;
        -webkit-overflow-scrolling: touch !important;
        z-index: 2147483645 !important;
    }

    section[data-testid="stSidebar"] > div,
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
        height: 100dvh !important;
        max-height: 100dvh !important;
        min-height: 0 !important;
        overflow-y: auto !important;
        overflow-x: hidden !important;
        overscroll-behavior-y: contain !important;
        -webkit-overflow-scrolling: touch !important;
        touch-action: pan-y !important;
        padding-top: calc(0.6rem + env(safe-area-inset-top)) !important;
        padding-right: 0.55rem !important;
        padding-left: 0.55rem !important;
        padding-bottom: calc(5.5rem + env(safe-area-inset-bottom)) !important;
        box-sizing: border-box !important;
    }

    section[data-testid="stSidebar"] * {
        max-width: 100% !important;
        box-sizing: border-box !important;
    }

    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] div[role="radiogroup"] label,
    section[data-testid="stSidebar"] [role="radio"],
    section[data-testid="stSidebar"] button {
        white-space: normal !important;
        word-break: break-word !important;
        overflow-wrap: anywhere !important;
        line-height: 1.2 !important;
        min-height: 38px !important;
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

    [data-testid="stDataFrame"],
    [data-testid="stTable"],
    iframe,
    canvas,
    img,
    video {
        max-width: 100% !important;
    }

    body.pb-mobile-sidebar-closing section[data-testid="stSidebar"] {
        width: 0 !important;
        min-width: 0 !important;
        max-width: 0 !important;
        transform: translateX(-120vw) !important;
        visibility: hidden !important;
        pointer-events: none !important;
        overflow: hidden !important;
    }

    body.pb-mobile-sidebar-closing [data-testid="stAppViewContainer"],
    body.pb-mobile-sidebar-closing [data-testid="stMain"],
    body.pb-mobile-sidebar-closing .main,
    body.pb-mobile-sidebar-closing .block-container {
        margin-left: 0 !important;
        left: 0 !important;
        width: 100vw !important;
        max-width: 100vw !important;
    }
}

@media (max-width: 390px) {
    section[data-testid="stSidebar"] {
        width: min(74vw, 280px) !important;
        min-width: min(74vw, 280px) !important;
        max-width: min(74vw, 280px) !important;
    }
}
</style>
"""


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
        _inject_mobile_css(streamlit_module)
        return result

    pb_mobile_sidebar_markdown._pb_mobile_sidebar_guard = True
    pb_mobile_sidebar_markdown._pb_original_markdown = original
    streamlit_module.markdown = pb_mobile_sidebar_markdown
    return True


def _install_mobile_sidebar_html_guard(streamlit_module: Any) -> bool:
    original = getattr(streamlit_module, "html", None)
    if original is None or getattr(original, "_pb_mobile_sidebar_html_guard", False):
        return False

    script = """
<script id="pb-mobile-sidebar-autoclose-v5">
(() => {
  const rootWindow = (() => {
    try { if (window.parent && window.parent.document) return window.parent; } catch (error) {}
    return window;
  })();
  const rootDocument = rootWindow.document;
  if (!rootDocument || rootDocument.__pbMobileSidebarAutoCloseV5) return;
  rootDocument.__pbMobileSidebarAutoCloseV5 = true;

  function isPhone() {
    try { return rootWindow.matchMedia('(max-width: 768px)').matches; }
    catch (error) { return false; }
  }

  function body() {
    return rootDocument.body || rootDocument.documentElement;
  }

  function sidebar() {
    return rootDocument.querySelector('section[data-testid="stSidebar"]');
  }

  function findCloseButton() {
    const buttons = Array.from(rootDocument.querySelectorAll('button'));
    return buttons.find((button) => {
      const label = `${button.getAttribute('aria-label') || ''} ${button.title || ''} ${button.textContent || ''}`.toLowerCase();
      return label.includes('close sidebar')
        || label.includes('collapse sidebar')
        || label.includes('close menu')
        || label.trim() === '×'
        || label.trim() === 'x';
    });
  }

  function temporarilyHide(milliseconds = 1200) {
    if (!isPhone()) return;
    const b = body();
    if (!b) return;
    b.classList.add('pb-mobile-sidebar-closing');
    rootWindow.setTimeout(() => b.classList.remove('pb-mobile-sidebar-closing'), milliseconds);
  }

  function closeSidebar(delay = 90) {
    if (!isPhone()) return;
    rootWindow.setTimeout(() => {
      const close = findCloseButton();
      if (close) {
        try { close.click(); } catch (error) { temporarilyHide(); }
      } else if (sidebar()) {
        temporarilyHide();
      }
    }, delay);
  }

  rootDocument.addEventListener('click', (event) => {
    if (!isPhone()) return;
    const side = sidebar();
    const target = event.target;
    if (!side || !target) return;
    const inside = side.contains(target);
    if (!inside) {
      closeSidebar(15);
      return;
    }
    const nav = target.closest
      ? target.closest('label, button, a, [role="radio"], [role="option"], [data-testid="stSidebarNavLink"]')
      : null;
    if (nav) {
      closeSidebar(120);
      closeSidebar(500);
      closeSidebar(1100);
    }
  }, true);

  closeSidebar(600);
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
    return bool(page_config_installed or markdown_installed or html_installed)
