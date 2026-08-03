"""Phone-only top navigation that mirrors JobHub's native sidebar menu.

The control is implemented entirely in the browser. It never patches Streamlit
widgets or changes their Python return values, so desktop routing remains native.
"""

from __future__ import annotations

import sys
from typing import Any


_MOBILE_TOP_NAV_SCRIPT = r"""
<script id="pb-mobile-top-navigation-v1">
(() => {
  const rootWindow = (() => {
    try { if (window.parent && window.parent.document) return window.parent; } catch (error) {}
    return window;
  })();
  const doc = rootWindow.document;
  if (!doc || doc.__pbMobileTopNavigationV1) return;
  doc.__pbMobileTopNavigationV1 = true;

  const isPhone = () => {
    try { return rootWindow.matchMedia('(max-width: 768px)').matches; }
    catch (error) { return false; }
  };

  const style = doc.createElement('style');
  style.id = 'pb-mobile-top-navigation-style-v1';
  style.textContent = `
    #pb-mobile-top-nav { display: none; }
    @media (max-width: 768px) {
      #pb-mobile-top-nav {
        display: flex;
        position: fixed;
        top: calc(0.38rem + env(safe-area-inset-top));
        left: calc(3.25rem + env(safe-area-inset-left));
        right: calc(0.55rem + env(safe-area-inset-right));
        z-index: 2147483646;
        align-items: center;
        gap: 0.45rem;
        pointer-events: none;
      }
      #pb-mobile-top-nav-button {
        width: 100%;
        min-height: 42px;
        padding: 0.55rem 2.35rem 0.55rem 0.85rem;
        border: 1px solid rgba(122,104,86,0.24);
        border-radius: 12px;
        background: rgba(255,255,255,0.97);
        color: #1f1f1f;
        box-shadow: 0 3px 14px rgba(0,0,0,0.16);
        font: 700 14px/1.2 Poppins, Segoe UI, Arial, sans-serif;
        text-align: left;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        pointer-events: auto;
        position: relative;
      }
      #pb-mobile-top-nav-button::after {
        content: '▾';
        position: absolute;
        right: 0.85rem;
        top: 50%;
        transform: translateY(-50%);
      }
      #pb-mobile-top-nav-menu {
        display: none;
        position: absolute;
        top: calc(100% + 0.35rem);
        left: 0;
        right: 0;
        max-height: min(70dvh, 520px);
        overflow-y: auto;
        padding: 0.38rem;
        border: 1px solid rgba(122,104,86,0.22);
        border-radius: 12px;
        background: rgba(255,255,255,0.99);
        box-shadow: 0 10px 30px rgba(0,0,0,0.22);
        pointer-events: auto;
        -webkit-overflow-scrolling: touch;
      }
      #pb-mobile-top-nav.pb-open #pb-mobile-top-nav-menu { display: block; }
      .pb-mobile-top-nav-item {
        display: block;
        width: 100%;
        min-height: 42px;
        padding: 0.62rem 0.72rem;
        border: 0;
        border-radius: 9px;
        background: transparent;
        color: #1f1f1f;
        font: 600 14px/1.25 Poppins, Segoe UI, Arial, sans-serif;
        text-align: left;
      }
      .pb-mobile-top-nav-item:active,
      .pb-mobile-top-nav-item.pb-current { background: #eee5dc; }
      .block-container {
        padding-top: calc(3.55rem + env(safe-area-inset-top)) !important;
      }
    }
  `;
  (doc.head || doc.documentElement).appendChild(style);

  const shell = doc.createElement('div');
  shell.id = 'pb-mobile-top-nav';
  shell.innerHTML = '<button id="pb-mobile-top-nav-button" type="button" aria-expanded="false">Menu</button><div id="pb-mobile-top-nav-menu" role="menu"></div>';
  (doc.body || doc.documentElement).appendChild(shell);

  const toggle = shell.querySelector('#pb-mobile-top-nav-button');
  const menu = shell.querySelector('#pb-mobile-top-nav-menu');

  function normalise(text) {
    return String(text || '').replace(/\s+/g, ' ').trim();
  }

  function mainRadioLabels() {
    const sidebar = doc.querySelector('section[data-testid="stSidebar"]');
    if (!sidebar) return [];
    const groups = Array.from(sidebar.querySelectorAll('[role="radiogroup"]'));
    const group = groups.find((candidate) => candidate.querySelectorAll('label').length >= 2);
    return group ? Array.from(group.querySelectorAll('label')) : [];
  }

  function selectedText(labels) {
    const selected = labels.find((label) => {
      const radio = label.querySelector('[role="radio"], input[type="radio"]');
      return radio && (radio.getAttribute('aria-checked') === 'true' || radio.checked);
    });
    return normalise(selected ? selected.textContent : '') || 'Menu';
  }

  function rebuild() {
    if (!isPhone()) {
      shell.classList.remove('pb-open');
      return;
    }
    const labels = mainRadioLabels();
    if (!labels.length) return;
    const current = selectedText(labels);
    toggle.textContent = current;
    menu.replaceChildren();

    labels.forEach((label) => {
      const text = normalise(label.textContent);
      if (!text) return;
      const item = doc.createElement('button');
      item.type = 'button';
      item.className = 'pb-mobile-top-nav-item' + (text === current ? ' pb-current' : '');
      item.textContent = text;
      item.setAttribute('role', 'menuitem');
      item.addEventListener('click', () => {
        shell.classList.remove('pb-open');
        toggle.setAttribute('aria-expanded', 'false');
        try { label.click(); }
        catch (error) {
          const radio = label.querySelector('[role="radio"], input[type="radio"]');
          if (radio) radio.click();
        }
      });
      menu.appendChild(item);
    });
  }

  toggle.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    rebuild();
    const open = shell.classList.toggle('pb-open');
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  });

  doc.addEventListener('click', (event) => {
    if (!shell.contains(event.target)) {
      shell.classList.remove('pb-open');
      toggle.setAttribute('aria-expanded', 'false');
    }
  }, true);

  const observer = new MutationObserver(() => rootWindow.requestAnimationFrame(rebuild));
  observer.observe(doc.body || doc.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-checked'] });
  rootWindow.addEventListener('resize', rebuild, { passive: true });
  rebuild();
})();
</script>
"""


def install_mobile_top_navigation_guard() -> bool:
    """Inject the phone top menu when JobHub installs its mobile app shell."""
    streamlit_module = sys.modules.get("streamlit")
    if streamlit_module is None:
        return False

    original = getattr(streamlit_module, "html", None)
    if original is None or getattr(original, "_pb_mobile_top_navigation_guard", False):
        return False

    injecting = False

    def pb_mobile_top_navigation_html(body: Any, *args: Any, **kwargs: Any):
        nonlocal injecting
        result = original(body, *args, **kwargs)
        if not injecting and isinstance(body, str) and "apple-mobile-web-app-title" in body:
            try:
                injecting = True
                original(_MOBILE_TOP_NAV_SCRIPT, unsafe_allow_javascript=True)
            finally:
                injecting = False
        return result

    pb_mobile_top_navigation_html._pb_mobile_top_navigation_guard = True
    pb_mobile_top_navigation_html._pb_original_html = original
    streamlit_module.html = pb_mobile_top_navigation_html
    return True
