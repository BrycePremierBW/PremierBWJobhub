"""Mobile sidebar safeguards for the Streamlit JobHub app.

The main app imports the ``jobhub`` package before calling ``st.set_page_config``
and before applying the long custom CSS theme. This module installs lightweight
wrappers without rendering anything immediately, so Streamlit still gets page
configuration first. When the app later applies the theme, the wrapper adds one
final mobile-only CSS block after the theme so it wins over older sidebar rules.
"""

from __future__ import annotations

import sys
from typing import Any


_MOBILE_SIDEBAR_FIX_CSS = """
<style id="pb-mobile-sidebar-final-fix">
/* PB_JOBHUB_MOBILE_SIDEBAR_FINAL_FIX_V3
   Phone-first sidebar behaviour:
   - keep app content full width
   - make the drawer narrower and scrollable on iPhone
   - keep the native hamburger visible above everything
   - allow long radio/menu labels to wrap instead of forcing horizontal width
   - hide the sidebar while navigation is settling after a tap */
@media (max-width: 768px) {
    :root {
        --pb-mobile-sidebar-width: min(78vw, 300px);
    }

    html,
    body,
    .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    .main {
        width: 100vw !important;
        max-width: 100vw !important;
        min-width: 0 !important;
        overflow-x: hidden !important;
        overscroll-behavior-x: none !important;
    }

    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    .main,
    .block-container {
        margin-left: 0 !important;
        left: 0 !important;
    }

    .block-container {
        max-width: 100vw !important;
        min-width: 0 !important;
        padding-top: calc(2.75rem + env(safe-area-inset-top)) !important;
        padding-left: max(0.75rem, env(safe-area-inset-left)) !important;
        padding-right: max(0.75rem, env(safe-area-inset-right)) !important;
        padding-bottom: calc(2rem + env(safe-area-inset-bottom)) !important;
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
        width: var(--pb-mobile-sidebar-width) !important;
        min-width: var(--pb-mobile-sidebar-width) !important;
        max-width: var(--pb-mobile-sidebar-width) !important;
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

    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        gap: 0.28rem !important;
    }

    div[data-baseweb="popover"],
    div[data-baseweb="menu"] {
        max-width: calc(100vw - 18px) !important;
        z-index: 2147483646 !important;
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
    body.pb-mobile-sidebar-closing .main,
    body.pb-mobile-sidebar-closing .block-container {
        margin-left: 0 !important;
        left: 0 !important;
        width: 100vw !important;
        max-width: 100vw !important;
    }
}

@media (max-width: 390px) {
    :root {
        --pb-mobile-sidebar-width: min(74vw, 280px);
    }
}
</style>
"""


def _install_page_config_guard(streamlit_module: Any) -> bool:
    """Start JobHub collapsed so the native drawer does not cover phones."""
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
    """Auto-close the mobile drawer after reruns and menu taps."""
    original = getattr(streamlit_module, "html", None)
    if original is None or getattr(original, "_pb_mobile_sidebar_html_guard", False):
        return False

    script = """
<script id="pb-mobile-sidebar-auto-close-v3">
(() => {
  const rootWindow = (() => {
    try {
      if (window.parent && window.parent.document) return window.parent;
    } catch (error) {}
    return window;
  })();
  const rootDocument = rootWindow.document;
  if (!rootDocument || rootDocument.__pbMobileSidebarAutoCloseInstalledV3) return;
  rootDocument.__pbMobileSidebarAutoCloseInstalledV3 = true;

  function isPhone() {
    try {
      return rootWindow.matchMedia('(max-width: 768px)').matches;
    } catch (error) {
      return false;
    }
  }

  function body() {
    return rootDocument.body || rootDocument.documentElement;
  }

  function sidebar() {
    return rootDocument.querySelector('section[data-testid="stSidebar"]');
  }

  function collapsedControl() {
    return rootDocument.querySelector('[data-testid="collapsedControl"] button, [data-testid="collapsedControl"]');
  }

  function findSidebarToggle() {
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

  function forceHide(milliseconds = 1300) {
    if (!isPhone()) return;
    const b = body();
    if (!b) return;
    b.classList.add('pb-mobile-sidebar-closing');
    rootWindow.setTimeout(() => b.classList.remove('pb-mobile-sidebar-closing'), milliseconds);
  }

  function closeSidebar(delay = 90) {
    if (!isPhone()) return;
    rootWindow.setTimeout(() => {
      const toggle = findSidebarToggle();
      if (toggle) {
        try { toggle.click(); } catch (error) { forceHide(); }
      } else if (sidebar()) {
        forceHide();
      }
    }, delay);
  }

  function closeAfterNavigation() {
    if (!isPhone()) return;
    closeSidebar(80);
    closeSidebar(280);
    closeSidebar(850);
  }

  rootDocument.addEventListener('click', (event) => {
    if (!isPhone()) return;
    const side = sidebar();
    const target = event.target;
    if (!side || !target) return;

    const clickedToggle = collapsedControl() && collapsedControl().contains && collapsedControl().contains(target);
    if (clickedToggle) return;

    const clickedInsideSidebar = side.contains(target);
    if (!clickedInsideSidebar) {
      closeSidebar(15);
      return;
    }

    const navTarget = target.closest
      ? target.closest('label, button, a, [role="radio"], [role="option"], [data-testid="stSidebarNavLink"], [data-testid="stMarkdownContainer"]')
      : null;
    if (navTarget) closeAfterNavigation();
  }, true);

  rootDocument.addEventListener('touchend', (event) => {
    if (!isPhone()) return;
    const side = sidebar();
    if (!side || !event.target || side.contains(event.target)) return;
    closeSidebar(10);
  }, true);

  closeAfterNavigation();

  try {
    const observer = new MutationObserver(() => {
      if (!isPhone()) return;
      const side = sidebar();
      if (!side) return;
      side.style.maxHeight = '100dvh';
      closeSidebar(420);
    });
    observer.observe(body(), { childList: true, subtree: true });
    rootWindow.setTimeout(() => observer.disconnect(), 6500);
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
