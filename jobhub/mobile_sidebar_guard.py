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
/* PB_JOBHUB_MOBILE_SIDEBAR_FINAL_FIX_V2
   The mobile app must prioritise page content.  Streamlit can leave the native
   sidebar drawer open after login/rerun, so phones get a collapsed-first layout
   and a temporary forced-hide class while navigation changes are settling. */
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

    body.pb-mobile-sidebar-closing section[data-testid="stSidebar"] {
        width: 0 !important;
        min-width: 0 !important;
        max-width: 0 !important;
        transform: translateX(calc(-110vw - env(safe-area-inset-left))) !important;
        visibility: hidden !important;
        pointer-events: none !important;
        overflow: hidden !important;
    }

    body.pb-mobile-sidebar-closing [data-testid="stAppViewContainer"],
    body.pb-mobile-sidebar-closing [data-testid="stMain"],
    body.pb-mobile-sidebar-closing .main {
        margin-left: 0 !important;
        left: 0 !important;
        width: 100vw !important;
        max-width: 100vw !important;
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


def _install_page_config_guard(streamlit_module: Any) -> bool:
    """Start JobHub collapsed so the native drawer does not cover phones.

    Streamlit does not let us choose ``initial_sidebar_state`` per viewport.
    Collapsed-first is safer for JobHub because desktop users can still open the
    navigation, while mobile users are no longer trapped behind the sidebar.
    """
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
    """Auto-close the mobile drawer after reruns and menu taps.

    ``st.html`` content may run inside a small embedded frame, so every DOM query
    deliberately targets ``window.parent.document`` when available.  The earlier
    version queried the frame document, which could not see the real sidebar.
    """
    original = getattr(streamlit_module, "html", None)
    if original is None or getattr(original, "_pb_mobile_sidebar_html_guard", False):
        return False

    script = """
<script id="pb-mobile-sidebar-auto-close-v2">
(() => {
  if (window.__pbMobileSidebarAutoCloseInstalledV2) return;
  window.__pbMobileSidebarAutoCloseInstalledV2 = true;

  function getRootWindow() {
    try {
      if (window.parent && window.parent.document) return window.parent;
    } catch (error) {}
    return window;
  }

  function getRootDocument() {
    return getRootWindow().document;
  }

  function isSmallScreen() {
    const root = getRootWindow();
    return root.matchMedia && root.matchMedia('(max-width: 768px)').matches;
  }

  function sidebar() {
    return getRootDocument().querySelector('section[data-testid="stSidebar"]');
  }

  function findCloseButton() {
    const doc = getRootDocument();
    const buttons = Array.from(doc.querySelectorAll('button'));
    return buttons.find((button) => {
      const label = `${button.getAttribute('aria-label') || ''} ${button.title || ''} ${button.textContent || ''}`.toLowerCase();
      return label.includes('close sidebar')
        || label.includes('collapse sidebar')
        || label.includes('close menu')
        || label.includes('×')
        || label.includes('x');
    });
  }

  function temporarilyHideSidebar(milliseconds = 1100) {
    if (!isSmallScreen()) return;
    const doc = getRootDocument();
    const body = doc.body;
    if (!body) return;
    body.classList.add('pb-mobile-sidebar-closing');
    getRootWindow().setTimeout(() => {
      body.classList.remove('pb-mobile-sidebar-closing');
    }, milliseconds);
  }

  function closeSidebarSoon(delay = 140) {
    if (!isSmallScreen()) return;
    const root = getRootWindow();
    root.setTimeout(() => {
      const button = findCloseButton();
      if (button) {
        button.click();
      } else if (sidebar()) {
        temporarilyHideSidebar();
      }
    }, delay);
  }

  function closeAfterRerender() {
    if (!isSmallScreen()) return;
    closeSidebarSoon(220);
    closeSidebarSoon(700);
    closeSidebarSoon(1500);
  }

  const doc = getRootDocument();
  if (!doc.__pbJobHubMobileSidebarClickGuardV2) {
    doc.__pbJobHubMobileSidebarClickGuardV2 = true;
    doc.addEventListener('click', (event) => {
      if (!isSmallScreen()) return;
      const side = sidebar();
      if (!side) return;
      const target = event.target;
      const clickedInsideSidebar = side.contains(target);
      const clickedMainPage = !clickedInsideSidebar;
      if (clickedMainPage) {
        closeSidebarSoon(20);
        return;
      }
      const navTarget = target && target.closest
        ? target.closest('label, button, a, [role="radio"], [role="option"], [data-testid="stSidebarNavLink"]')
        : null;
      if (navTarget) closeSidebarSoon(120);
    }, true);
  }

  closeAfterRerender();

  try {
    const observer = new MutationObserver(() => {
      if (isSmallScreen()) closeSidebarSoon(260);
    });
    observer.observe(doc.body || doc.documentElement, { childList: true, subtree: true });
    getRootWindow().setTimeout(() => observer.disconnect(), 4500);
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
    markdown_installed = _install_mobile_sidebar_markdown_guard(streamlit_module)
    html_installed = _install_mobile_sidebar_html_guard(streamlit_module)
    return bool(page_config_installed or markdown_installed or html_installed)
