"""Preserve user-selected Streamlit tabs across reruns.

Streamlit re-runs the script after many normal interactions. Native ``st.tabs``
then reopens the first tab, which makes JobHub feel like it keeps jumping back
to Add Job after an employee or manager selects a job in Job Register.  This
small browser-side guard remembers the last clicked tracked tab and restores it
after the rerun finishes rendering.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, Iterable


_TRACKED_TAB_SETS = (
    frozenset({
        "Add Job",
        "Edit Job",
        "Remove / Archive",
        "Archived Jobs",
        "Search by Builder",
        "Job Register",
    }),
)


def _labels_from_tabs(tabs: Any) -> list[str]:
    if isinstance(tabs, str):
        return [tabs]
    try:
        return [str(value) for value in list(tabs)]
    except Exception:
        return []


def _safe_id(labels: Iterable[str]) -> str:
    joined = "-".join(str(label) for label in labels)
    return re.sub(r"[^A-Za-z0-9_-]+", "-", joined).strip("-")[:80] or "tabs"


def _is_tracked(labels: list[str]) -> bool:
    label_set = frozenset(labels)
    return any(required.issubset(label_set) for required in _TRACKED_TAB_SETS)


def _restore_tabs_script(labels: list[str]) -> str:
    labels_json = json.dumps(labels)
    guard_id = json.dumps(f"pb-tab-state-{_safe_id(labels)}")
    return f"""
<script id={guard_id}>
(() => {{
  const trackedLabels = {labels_json};
  const labelSet = new Set(trackedLabels.map((value) => String(value).trim()));
  const storageKey = 'pb-jobhub-active-tab::' + trackedLabels.join('|');
  const normalise = (value) => String(value || '').replace(/\\s+/g, ' ').trim();

  function getRootDocument() {{
    try {{
      if (window.parent && window.parent.document) return window.parent.document;
    }} catch (error) {{}}
    return document;
  }}

  function trackedTabs() {{
    const doc = getRootDocument();
    return Array.from(doc.querySelectorAll('[role="tab"]')).filter((tab) =>
      labelSet.has(normalise(tab.textContent))
    );
  }}

  function remember(label) {{
    if (!labelSet.has(label)) return;
    try {{
      window.localStorage.setItem(storageKey, label);
    }} catch (error) {{}}
  }}

  function restore() {{
    let desired = '';
    try {{
      desired = normalise(window.localStorage.getItem(storageKey));
    }} catch (error) {{
      desired = '';
    }}
    if (!labelSet.has(desired)) return false;

    const tab = trackedTabs().find((candidate) => normalise(candidate.textContent) === desired);
    if (!tab) return false;
    const selected = String(tab.getAttribute('aria-selected') || '').toLowerCase() === 'true';
    if (!selected && typeof tab.click === 'function') {{
      tab.click();
    }}
    return true;
  }}

  function installClickListener() {{
    const doc = getRootDocument();
    if (doc.__pbJobHubTabStateClickListener) return;
    doc.__pbJobHubTabStateClickListener = true;
    doc.addEventListener('click', (event) => {{
      const tab = event.target && event.target.closest ? event.target.closest('[role="tab"]') : null;
      if (!tab) return;
      const label = normalise(tab.textContent);
      remember(label);
    }}, true);
  }}

  installClickListener();

  let attempts = 0;
  const maxAttempts = 40;
  const interval = window.setInterval(() => {{
    attempts += 1;
    restore();
    if (attempts >= maxAttempts) window.clearInterval(interval);
  }}, 80);

  try {{
    const doc = getRootDocument();
    const observer = new MutationObserver(() => restore());
    observer.observe(doc.body || doc.documentElement, {{ childList: true, subtree: true }});
    window.setTimeout(() => observer.disconnect(), 5000);
  }} catch (error) {{}}
}})();
</script>
"""


def install_navigation_state_guard() -> bool:
    streamlit_module = sys.modules.get("streamlit")
    if streamlit_module is None:
        return False
    original = getattr(streamlit_module, "tabs", None)
    if original is None or getattr(original, "_pb_navigation_state_guard", False):
        return False

    def pb_stateful_tabs(tabs: Any, *args: Any, **kwargs: Any):
        labels = _labels_from_tabs(tabs)
        result = original(tabs, *args, **kwargs)
        if labels and _is_tracked(labels):
            html_renderer = getattr(streamlit_module, "html", None)
            if html_renderer is not None:
                try:
                    html_renderer(_restore_tabs_script(labels), unsafe_allow_javascript=True)
                except Exception:
                    pass
        return result

    pb_stateful_tabs._pb_navigation_state_guard = True  # type: ignore[attr-defined]
    pb_stateful_tabs._pb_original_tabs = original  # type: ignore[attr-defined]
    streamlit_module.tabs = pb_stateful_tabs
    return True
