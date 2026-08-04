from __future__ import annotations

import html
from typing import Any

import pandas as pd
import streamlit as st


CSS = """
<style>
:root{--pb-bg:#f7f3ee;--pb-card:#fff;--pb-text:#222;--pb-muted:#71695f;--pb-border:#e4d8ca;--pb-accent:#7a6856}
html,body,[class*="css"]{font-family:Poppins,"Segoe UI",Arial,sans-serif}
.stApp{background:var(--pb-bg);color:var(--pb-text)}
.block-container{max-width:1500px;padding-top:1rem;padding-bottom:3rem}
section[data-testid="stSidebar"]{background:#1e1b18}
section[data-testid="stSidebar"] *{color:#f8f2ea}
section[data-testid="stSidebar"] div[data-baseweb="select"] *{color:#111!important}
.pb-hero{padding:1.25rem 1.4rem;border-radius:18px;background:linear-gradient(135deg,#222,#4b4036);color:#fff;margin-bottom:1rem}
.pb-hero h1{color:#fff;margin:0;font-size:1.85rem}.pb-hero p{margin:.35rem 0 0;color:#eee2d6}
.pb-card{background:var(--pb-card);border:1px solid var(--pb-border);border-radius:14px;padding:1rem;box-shadow:0 7px 22px rgba(0,0,0,.05)}
div[data-testid="stMetric"],div[data-testid="stForm"],details[data-testid="stExpander"]{background:#fff;border:1px solid var(--pb-border);border-radius:14px;padding:.45rem}
.stButton>button,.stDownloadButton>button{border-radius:999px;font-weight:650}
[data-testid="stDataFrame"],[data-testid="stDataEditor"]{border:1px solid var(--pb-border);border-radius:12px;overflow:hidden}
@media(max-width:768px){.block-container{padding:.65rem}.pb-hero{padding:1rem}.pb-hero h1{font-size:1.45rem}input,textarea,select{font-size:16px!important}}
</style>
"""


def install_style() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def header(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<div class="pb-hero"><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>',
        unsafe_allow_html=True,
    )


def selected_row(
    frame: pd.DataFrame,
    *,
    key: str,
    hide: tuple[str, ...] = (),
    column_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if frame is None or frame.empty:
        st.info("No records found.")
        return None
    visible = frame.drop(columns=list(hide), errors="ignore")
    event = st.dataframe(
        visible,
        hide_index=True,
        use_container_width=True,
        key=key,
        on_select="rerun",
        selection_mode="single-row",
        column_config=column_config,
    )
    rows = list(getattr(getattr(event, "selection", None), "rows", []) or [])
    if not rows:
        return None
    index = int(rows[0])
    if index < 0 or index >= len(frame):
        return None
    return frame.iloc[index].to_dict()


def rerun_success(message: str) -> None:
    st.session_state["_lean_notice"] = ("success", message)
    st.rerun()


def rerun_error(message: str) -> None:
    st.session_state["_lean_notice"] = ("error", message)
    st.rerun()


def show_notice() -> None:
    notice = st.session_state.pop("_lean_notice", None)
    if not notice:
        return
    level, message = notice
    getattr(st, level, st.info)(message)
    try:
        st.toast(message, icon="✅" if level == "success" else "❌")
    except Exception:
        pass
