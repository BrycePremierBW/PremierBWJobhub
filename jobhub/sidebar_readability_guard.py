"""Sidebar readability and toggle positioning guard for JobHub."""

from __future__ import annotations

import sys
from typing import Any


SIDEBAR_READABILITY_CSS = """
<style id="pb-sidebar-readability-guard">
/* PB_JOBHUB_SIDEBAR_READABILITY_V1 */
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] div,
section[data-testid="stSidebar"] [role="radio"],
section[data-testid="stSidebar"] [role="option"],
section[data-testid="stSidebar"] button {
    color: #111827 !important;
    font-weight: 650 !important;
    text-shadow: none !important;
}

section[data-testid="stSidebar"] input,
section[data-testid="stSidebar"] textarea,
section[data-testid="stSidebar"] [data-baseweb="select"] > div,
section[data-testid="stSidebar"] div[data-testid="stTextInput"] input,
section[data-testid="stSidebar"] div[data-testid="stNumberInput"] input,
section[data-testid="stSidebar"] div[data-testid="stTextArea"] textarea {
    color: #0f172a !important;
    background: #ffffff !important;
    font-weight: 700 !important;
    border: 1.5px solid rgba(15, 23, 42, 0.55) !important;
    box-shadow: 0 1px 4px rgba(15, 23, 42, 0.12) !important;
}

section[data-testid="stSidebar"] input::placeholder,
section[data-testid="stSidebar"] textarea::placeholder {
    color: #475569 !important;
    font-weight: 600 !important;
    opacity: 1 !important;
}

section[data-testid="stSidebar"] [data-baseweb="select"] span,
section[data-testid="stSidebar"] [data-baseweb="select"] div {
    color: #0f172a !important;
    font-weight: 700 !important;
}

section[data-testid="stSidebar"] [role="radio"] {
    border-radius: 10px !important;
    padding: 0.22rem 0.2rem !important;
}

section[data-testid="stSidebar"] [aria-checked="true"],
section[data-testid="stSidebar"] label:has(input:checked) {
    background: rgba(15, 23, 42, 0.08) !important;
    border-radius: 10px !important;
}

[data-testid="collapsedControl"] {
    top: calc(3.4rem + env(safe-area-inset-top, 0px)) !important;
    z-index: 2147483647 !important;
    background: rgba(255, 255, 255, 0.98) !important;
    border: 1.5px solid rgba(15, 23, 42, 0.35) !important;
    border-radius: 999px !important;
    box-shadow: 0 3px 14px rgba(15, 23, 42, 0.25) !important;
}

[data-testid="collapsedControl"] button,
[data-testid="collapsedControl"] svg {
    color: #0f172a !important;
    fill: #0f172a !important;
    stroke: #0f172a !important;
}

section[data-testid="stSidebar"] button[aria-label*="Close"],
section[data-testid="stSidebar"] button[aria-label*="close"],
section[data-testid="stSidebar"] button[aria-label*="Collapse"],
section[data-testid="stSidebar"] button[aria-label*="collapse"] {
    margin-top: 2.8rem !important;
    color: #0f172a !important;
    background: rgba(255,255,255,0.98) !important;
    border: 1.5px solid rgba(15, 23, 42, 0.35) !important;
    border-radius: 999px !important;
    box-shadow: 0 3px 14px rgba(15, 23, 42, 0.20) !important;
}

@media (max-width: 768px) {
    [data-testid="collapsedControl"] {
        top: calc(4.25rem + env(safe-area-inset-top, 0px)) !important;
        left: calc(0.7rem + env(safe-area-inset-left, 0px)) !important;
    }

    section[data-testid="stSidebar"] button[aria-label*="Close"],
    section[data-testid="stSidebar"] button[aria-label*="close"],
    section[data-testid="stSidebar"] button[aria-label*="Collapse"],
    section[data-testid="stSidebar"] button[aria-label*="collapse"] {
        margin-top: 3.4rem !important;
    }
}
</style>
"""


def _inject_css(streamlit_module: Any) -> None:
    if bool(getattr(streamlit_module, "_pb_sidebar_readability_done", False)):
        return
    try:
        setattr(streamlit_module, "_pb_sidebar_readability_done", True)
        markdown = getattr(streamlit_module, "markdown", None)
        original = getattr(markdown, "_pb_original_markdown", markdown)
        if callable(original):
            original(SIDEBAR_READABILITY_CSS, unsafe_allow_html=True)
    except Exception:
        pass


def install_sidebar_readability_guard() -> bool:
    st = sys.modules.get("streamlit")
    if st is None:
        return False
    original = getattr(st, "markdown", None)
    if original is None or getattr(original, "_pb_sidebar_readability_guard", False):
        _inject_css(st)
        return False

    def markdown_with_sidebar_readability(body: Any, *args: Any, **kwargs: Any):
        result = original(body, *args, **kwargs)
        _inject_css(st)
        return result

    markdown_with_sidebar_readability._pb_sidebar_readability_guard = True
    markdown_with_sidebar_readability._pb_original_markdown = original
    st.markdown = markdown_with_sidebar_readability
    _inject_css(st)
    return True
