"""Mobile sidebar safeguards for the Streamlit JobHub app.

The main app imports the ``jobhub`` package before calling ``st.set_page_config``
and before applying the long custom CSS theme.  This module installs lightweight
wrappers without rendering anything immediately, so Streamlit still gets page
configuration first.  When the app later applies the theme, the wrapper adds one
final mobile-only CSS block after the theme so it wins over older sidebar rules.
"""

from __future__ import annotations

import sys
from typing import Any


_MOBILE_SIDEBAR_FIX_CSS = """
<style id="pb-mobile-sidebar-final-fix">
/* PB_JOBHUB_MOBILE_SIDEBAR_FINAL_FIX_V1
   Keep the installed mobile app usable even when Streamlit leaves the sidebar
   drawer open after login/rerun. This is deliberately mobile-only so desktop
   still keeps the wider Premier Brushworks navigation. */
@media (max-width: 768px) {
    html,
    body,
    .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    .main {
        width: 100vw !important;
        max-width: 100vw !important;
        overflow-x: hidden !important;
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
    }

    section[data-testid="stSidebar"] {
        width: min(86vw, 340px) !important;
        min-width: min(86vw, 340px) !important;
        max-width: min(86vw, 340px) !important;
        height: 100svh !important;
        max-height: 100svh !important;
        overflow: visible !important;
        overscroll-behavior: contain !important;
        -webkit-overflow-scrolling: touch !important;
        transform: translateZ(0) !important;
    }

    section[data-testid="stSidebar"] > div,
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
        height: 100svh !important;
        max-height: 100svh !important;
        min-height: 0 !important;
        overflow-y: auto !important;
        overflow-x: hidden !important;
        overscroll-behavior-y: contain !important;
        -webkit-overflow-scrolling: touch !important;
        touch-action: pan-y !important;
        padding-bottom: calc(4.5rem + env(safe-area-inset-bottom)) !important;
    }

    .block-container {
        max-width: 100vw !important;
        padding-left: max(0.85rem, env(safe-area-inset-left)) !important;
        padding-right: max(0.85rem, env(safe-area-inset-right)) !important;
    }

    div[data-baseweb="popover"] {
        max-width: calc(100vw - 18px) !important;
        z-index: 2147483646 !important;
    }
}
</style>
"""


def _install_mobile_sidebar_markdown_guard(streamlit_module: Any) -> bool:
    """Append the final mobile sidebar override after the app theme is rendered."""
    original = getattr(streamlit_module, "markdown", None)
    if original is None or getattr(original, "_pb_mobile_sidebar_guard", False):
        return False

    injecting = False

    def pb_mobile_sidebar_markdown(body: Any, *args: Any, **kwargs: Any):
        nonlocal injecting
        result = original(body, *args, **kwargs)
        if not injecting and isinstance(body, str) and "PB_JOBHUB_SIDEBAR_SCROLL_GUARD" in body:
            try:
                injecting = True
                original(_MOBILE_SIDEBAR_FIX_CSS, unsafe_allow_html=True)
            finally:
                injecting = False
        return result

    pb_mobile_sidebar_markdown._pb_mobile_sidebar_guard = True
    pb_mobile_sidebar_markdown._pb_original_markdown = original
    streamlit_module.markdown = pb_mobile_sidebar_markdown
    return True


def _install_mobile_sidebar_html_guard(streamlit_module: Any) -> bool:
    """Auto-close the mobile drawer after a menu item is tapped.

    Streamlit's installed iOS/Android app shell can keep the drawer open across
    reruns. This listener only reacts after a user taps a navigation control
    inside the sidebar; it does not force-close the menu when the employee opens
    it just to look around.
    """
    original = getattr(streamlit_module, "html", None)
    if original is None or getattr(original, "_pb_mobile_sidebar_html_guard", False):
        return False

    script = """
<script id="pb-mobile-sidebar-auto-close">
(() => {
  if (window.__pbMobileSidebarAutoCloseInstalled) return;
  window.__pbMobileSidebarAutoCloseInstalled = true;

  function isSmallScreen() {
    return window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
  }

  function findCloseButton() {
    const buttons = Array.from(document.querySelectorAll('button'));
    return buttons.find((button) => {
      const label = `${button.getAttribute('aria-label') || ''} ${button.title || ''} ${button.textContent || ''}`.toLowerCase();
      return label.includes('close sidebar') || label.includes('collapse sidebar') || label.includes('close menu');
    });
  }

  function closeSidebarSoon() {
    if (!isSmallScreen()) return;
    window.setTimeout(() => {
      const button = findCloseButton();
      if (button) button.click();
    }, 160);
  }

  document.addEventListener('click', (event) => {
    if (!isSmallScreen()) return;
    const sidebar = document.querySelector('section[data-testid="stSidebar"]');
    if (!sidebar || !sidebar.contains(event.target)) return;
    const target = event.target.closest('label, button, a, [role="radio"], [role="option"], [data-testid="stSidebarNavLink"]');
    if (target) closeSidebarSoon();
  }, true);
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
    markdown_installed = _install_mobile_sidebar_markdown_guard(streamlit_module)
    html_installed = _install_mobile_sidebar_html_guard(streamlit_module)
    return bool(markdown_installed or html_installed)
