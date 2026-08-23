"""Keep Recent Uploads at the bottom of Job Folder pages on desktop and mobile.

The legacy Job Folder renderer calls the Recent Uploads panel immediately after
its heading.  Rewriting the large monolithic page solely to move that panel is
unnecessary.  This small browser-side guard marks the rendered Recent Uploads
container with a high flex order, so it remains the final section in the page's
native Streamlit vertical layout while preserving all existing upload actions.
"""
from __future__ import annotations

import sys
from typing import Any


PATCH_MARKER = "_pb_job_folder_recent_uploads_bottom_guard"

_SCRIPT = r"""
<script id="pb-job-folder-recent-uploads-bottom-v1">
(() => {
  const rootWindow = (() => {
    try { if (window.parent && window.parent.document) return window.parent; } catch (error) {}
    return window;
  })();
  const doc = rootWindow.document;
  if (!doc) return;

  const normalise = (value) => String(value || '').replace(/\s+/g, ' ').trim();

  function isJobFolderPage() {
    return Array.from(doc.querySelectorAll('h1,h2,h3')).some(
      (node) => normalise(node.textContent) === 'Job Folders'
    );
  }

  function placeRecentUploadsLast() {
    if (!isJobFolderPage()) return;
    const heading = Array.from(doc.querySelectorAll('h1,h2,h3,h4,h5')).find(
      (node) => normalise(node.textContent) === 'Recent Uploads'
    );
    if (!heading) return;

    let panel = heading.closest('[data-testid="stVerticalBlockBorderWrapper"]');
    if (!panel) panel = heading.closest('[data-testid="stVerticalBlock"] > div');
    if (!panel) panel = heading.parentElement;
    if (!panel || !panel.parentElement) return;

    const parent = panel.parentElement;
    try {
      const computed = rootWindow.getComputedStyle(parent);
      if (computed.display !== 'flex') parent.style.display = 'flex';
      if (!computed.flexDirection || computed.flexDirection === 'row') parent.style.flexDirection = 'column';
      panel.style.order = '99999';
      panel.dataset.pbRecentUploadsBottom = 'true';
    } catch (error) {}
  }

  placeRecentUploadsLast();
  [40, 120, 300, 700, 1400, 2500].forEach((delay) =>
    rootWindow.setTimeout(placeRecentUploadsLast, delay)
  );

  if (!doc.__pbRecentUploadsBottomObserver) {
    doc.__pbRecentUploadsBottomObserver = true;
    try {
      const observer = new MutationObserver(() => placeRecentUploadsLast());
      observer.observe(doc.body || doc.documentElement, {childList: true, subtree: true});
      rootWindow.setTimeout(() => {
        observer.disconnect();
        doc.__pbRecentUploadsBottomObserver = false;
      }, 5000);
    } catch (error) {}
  }
})();
</script>
"""


def install_job_folder_recent_uploads_bottom_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False
    original = getattr(st, "markdown", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def markdown_with_bottom_recent_uploads(body: Any, *args: Any, **kwargs: Any):
        result = original(body, *args, **kwargs)
        if str(body or "").strip().lstrip("#").strip() == "Recent Uploads":
            renderer = getattr(st, "html", None)
            if callable(renderer):
                try:
                    renderer(_SCRIPT, unsafe_allow_javascript=True)
                except TypeError:
                    try:
                        renderer(_SCRIPT)
                    except Exception:
                        pass
                except Exception:
                    pass
        return result

    markdown_with_bottom_recent_uploads._pb_original_markdown = original
    setattr(markdown_with_bottom_recent_uploads, PATCH_MARKER, True)
    st.markdown = markdown_with_bottom_recent_uploads
    return True
