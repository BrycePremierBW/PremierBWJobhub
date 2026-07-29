import sqlite3
import os
import tempfile
from urllib.parse import urljoin
import shutil
import base64
import hashlib
import html
import re
import json
import py_compile
import mimetypes
import zipfile
from pathlib import Path, PurePosixPath
from datetime import date, datetime, time, timedelta
from io import BytesIO

import pandas as pd
from PIL import Image, ImageOps
import requests
from psycopg2.pool import ThreadedConnectionPool
from pypdf import PdfReader, PdfWriter
import streamlit as st
import streamlit.components.v1 as components
from jobhub_feedback import error as pb_error, replay_pending as pb_replay_pending, rerun as pb_rerun, success as pb_success
from pb_jobhub_visual_scheduler import (
    init_linked_schema,
    render_jobhub_staff_scheduler,
    sync_linked_job_dates,
)
from jobhub_progress_tracker import render_progress_tracker, sync_all_linked_progress
from jobhub_enterprise import (
    ensure_enterprise_schema,
    ensure_daily_backup,
    render_field_mode,
    render_operations_hub,
)
from jobhub_v2.schema import ensure_v2_schema
from jobhub_v4.schema import ensure_v4_schema
from jobhub_v4.streamlit_painting import render_painting_intelligence
from jobhub_core import (
    calculate_estimate_pricing,
    calculate_shift_hours,
    hash_password as secure_hash_password,
    is_known_default_password_hash,
    next_scoped_number,
    password_needs_rehash,
    password_strength_errors,
    validate_public_http_url,
    verify_password,
)
# PB_FULL_VISUAL_STAFF_SCHEDULER_V1

MAX_PHOTO_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_PDF_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_CSV_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_CSV_IMPORT_ROWS = 10_000
MAX_TAKEOFF_PACK_BYTES = 150 * 1024 * 1024
MAX_TAKEOFF_PACK_EXTRACTED_BYTES = 350 * 1024 * 1024
MAX_TAKEOFF_PACK_FILES = 300
MAX_IMAGE_PIXELS = 25_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

PB_JOBHUB_BUILD = "2026.07.28-dataframe-id-fix-v1"
PLANNING_LABOUR_RATE = 60.0


# =============================
# APP PATHS / PERSISTENT STORAGE
# =============================

DATA_DIR = os.getenv("DATA_DIR", "/var/data")

DB_PATH = os.path.join(DATA_DIR, "jobhub.db")
JOB_FILES_DIR = os.path.join(DATA_DIR, "job_files")
PHOTOS_DIR = os.path.join(DATA_DIR, "photos")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
ASSET_DIR = os.path.join(os.path.dirname(__file__), "assets")
PB_LOGO_BACKGROUND_IMAGE = os.path.join(ASSET_DIR, "PB_Logo_Main_PNG.png")


def first_existing_app_file(directory, *file_names):
    """Return the first existing compatible app asset path.

    Older JobHub packages used both spaced and underscored template names.  The
    compatibility lookup prevents a valid template from appearing missing just
    because a prior update used the other filename.
    """
    for file_name in file_names:
        candidate = Path(directory) / file_name
        if candidate.exists():
            return str(candidate)
    return str(Path(directory) / file_names[0])


EQUIPMENT_TEMPLATE_PDF = first_existing_app_file(
    TEMPLATE_DIR,
    "PB Master Checklist FILLABLE INITIAL.pdf",
)

PAINT_ORDER_TEMPLATE_PDF = first_existing_app_file(
    TEMPLATE_DIR,
    "PB Paint and Materials Order Form fillable.pdf",
)

DAY_LABOUR_TEMPLATE_PDF = first_existing_app_file(
    TEMPLATE_DIR,
    "Day_Labour_Sheet_FILLABLE.pdf",
    "Day Labour Sheet FILLABLE.pdf",
)

VARIATION_TEMPLATE_PDF = first_existing_app_file(
    TEMPLATE_DIR,
    "PB Variation Form fillable.pdf",
)


def safe_job_storage_segment(job_number):
    segment = re.sub(r"[^A-Za-z0-9._ -]", "_", str(job_number or "").strip())
    segment = segment.strip(" .")
    if not segment or segment in {".", ".."}:
        raise ValueError("Job number cannot be used as a storage folder.")
    return segment[:100]


def get_job_folder(job_number):
    root = Path(JOB_FILES_DIR).resolve()
    folder = (root / safe_job_storage_segment(job_number)).resolve()
    if root not in folder.parents:
        raise ValueError("Unsafe job storage path.")
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


st.set_page_config(
    page_title="Premier Brushworks JobHub",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded",
)


# Make row selection consistent across every JobHub dataframe, including
# supporting modules that share Streamlit's module object. Streamlit keeps the
# patched module function between reruns, so install this wrapper only once.
_pb_existing_dataframe = st.dataframe
if not getattr(_pb_existing_dataframe, "_pb_selectable_wrapper", False):
    _pb_original_dataframe = _pb_existing_dataframe

    def pb_selectable_dataframe(*args, **kwargs):
        if "on_select" not in kwargs:
            kwargs["on_select"] = "rerun"
            kwargs["selection_mode"] = "single-row"
        return _pb_original_dataframe(*args, **kwargs)

    pb_selectable_dataframe._pb_selectable_wrapper = True
    pb_selectable_dataframe._pb_original_dataframe = _pb_original_dataframe
    st.dataframe = pb_selectable_dataframe
else:
    pb_selectable_dataframe = _pb_existing_dataframe
    _pb_original_dataframe = _pb_existing_dataframe._pb_original_dataframe


pb_replay_pending()


@st.cache_resource(show_spinner=False)
def ensure_runtime_storage():
    """Create and verify JobHub's persistent storage folders once at startup."""
    storage_paths = {
        "Data": DATA_DIR,
        "Job files": JOB_FILES_DIR,
        "Photos": PHOTOS_DIR,
        "Exports": EXPORTS_DIR,
    }
    failures = []
    for label, folder_path in storage_paths.items():
        try:
            Path(folder_path).mkdir(parents=True, exist_ok=True)
            probe = Path(folder_path) / ".pb_jobhub_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            failures.append(f"{label}: {folder_path} — {exc}")

    if failures:
        pb_error("JobHub cannot write to its configured persistent storage.")
        st.code("\n".join(failures))
        st.info(
            "On Render, confirm the persistent disk is mounted at /var/data and "
            "that DATA_DIR points to the mounted path."
        )
        st.stop()


ensure_runtime_storage()


# =============================
# PREMIER BRUSHWORKS VISUAL THEME
# =============================

PB_MENU_ICONS = {
    "Dashboard": "🏠",
    "Control Centre": "🎯",
    "Jobs": "🧾",
    "Job Folders": "📁",
    "Estimating": "💰",
    "Site Operations": "🛠️",
    "Reports": "📊",
    "Management": "⚙️",
    "AI Assistant": "🤖",
    "3D Building Mapper": "🏗️",
    "Building Progress Mapper": "🗺️",
    "Employee Portal": "👷",
}


@st.cache_data(show_spinner=False, ttl=3600)
def pb_logo_data_uri():
    """Return the Premier Brushworks logo as a browser-safe data URI."""
    possible_paths = [
        PB_LOGO_BACKGROUND_IMAGE,
        os.path.join(os.path.dirname(__file__), "PB_Logo_Main_PNG.png"),
    ]
    for logo_path in possible_paths:
        try:
            if os.path.exists(logo_path):
                with open(logo_path, "rb") as logo_file:
                    encoded_logo = base64.b64encode(logo_file.read()).decode("utf-8")
                return f"data:image/png;base64,{encoded_logo}"
        except Exception:
            pass
    return ""


def pb_html(value):
    return html.escape(str(value or ""))


def pb_money(value):
    try:
        return f"${float(value or 0):,.0f}"
    except Exception:
        return "$0"


def apply_pb_branding():
    """Restore the last Premier Brushworks logo, colours and layout theme."""
    logo_data_uri = pb_logo_data_uri()
    logo_background_css = ""
    if logo_data_uri:
        logo_background_css = f"""
        .stApp {{
            background-image:
                linear-gradient(rgba(247, 243, 238, 0.89), rgba(247, 243, 238, 0.89)),
                url("{logo_data_uri}");
            background-size: cover, min(72vw, 760px) auto;
            background-position: center center;
            background-repeat: no-repeat;
            background-attachment: fixed;
            background-color: var(--pb-bg);
        }}

        [data-testid="stAppViewContainer"] {{
            background: transparent !important;
        }}
        """

    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&display=swap');

        :root {
            --pb-bg: #f7f3ee;
            --pb-card: #ffffff;
            --pb-charcoal: #1f1f1f;
            --pb-muted: #6f6a63;
            --pb-border: #e8ded3;
            --pb-accent: #d8c8b8;
            --pb-accent-dark: #7a6856;
            --pb-success: #1f7a4d;
            --pb-warning: #b7791f;
            --pb-danger: #b42318;
            --pb-info: #2f5f8f;
        }

        html, body, [class*="css"] {
            font-family: 'Poppins', 'Segoe UI', Arial, sans-serif;
        }

        .stApp {
            background:
                radial-gradient(circle at top left, rgba(216, 200, 184, 0.36), rgba(247, 243, 238, 0) 34rem),
                var(--pb-bg);
            color: var(--pb-charcoal);
        }

        """ + logo_background_css + """

        [data-testid="stHeader"] {
            background: transparent;
        }

        .block-container {
            max-width: 1560px;
            padding-top: 1.15rem;
            padding-bottom: 2.5rem;
        }

        section[data-testid="stSidebar"] {
            background: linear-gradient(180deg, #171717 0%, #29231f 100%);
            border-right: 1px solid rgba(255,255,255,0.08);
        }

        section[data-testid="stSidebar"] * {
            color: #f6efe7;
        }

        section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] span {
            color: #f6efe7;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] label {
            border-radius: 10px;
            padding: 0.4rem 0.5rem;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] label:hover {
            background: rgba(255,255,255,0.08);
        }

        /* PB_JOBHUB_SIDEBAR_MENU_FIX */
        section[data-testid="stSidebar"] [role="radiogroup"] {
            gap: 0.28rem !important;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] label {
            min-height: 42px !important;
            width: 100% !important;
            align-items: center !important;
            border: 1px solid rgba(255,255,255,0.08) !important;
            background: rgba(255,255,255,0.035) !important;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
            background: rgba(216,200,184,0.24) !important;
            border-color: rgba(216,200,184,0.55) !important;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] label p {
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: clip !important;
            line-height: 1.25 !important;
        }

        section[data-testid="stSidebar"] div[data-baseweb="select"],
        section[data-testid="stSidebar"] div[data-baseweb="select"] > div,
        section[data-testid="stSidebar"] div[data-baseweb="select"] *,
        section[data-testid="stSidebar"] div[data-baseweb="select"] input,
        section[data-testid="stSidebar"] div[data-baseweb="select"] span,
        section[data-testid="stSidebar"] div[data-baseweb="select"] svg {
            color: #111111 !important;
            -webkit-text-fill-color: #111111 !important;
            fill: #111111 !important;
        }

        section[data-testid="stSidebar"] [role="listbox"],
        section[data-testid="stSidebar"] [role="listbox"] *,
        section[data-testid="stSidebar"] [data-baseweb="popover"],
        section[data-testid="stSidebar"] [data-baseweb="popover"] * {
            color: #111111 !important;
            -webkit-text-fill-color: #111111 !important;
        }

        section[data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] div {
            background-color: #ffffff !important;
        }

        section[data-testid="stSidebar"] [data-testid="stSelectbox"] label {
            color: #f6efe7 !important;
            -webkit-text-fill-color: #f6efe7 !important;
        }


        /* PB_JOBHUB_DROPDOWN_VISIBILITY_FIX */
        div[data-baseweb="popover"] {
            z-index: 1000000 !important;
            max-width: min(560px, calc(100vw - 20px)) !important;
        }
        div[data-baseweb="popover"] [role="listbox"],
        div[data-baseweb="popover"] ul {
            max-height: min(62vh, 560px) !important;
            overflow-y: auto !important;
            overflow-x: hidden !important;
            overscroll-behavior: contain !important;
            scrollbar-gutter: stable !important;
            padding: 0.35rem !important;
            background: #ffffff !important;
            border-radius: 12px !important;
        }
        div[data-baseweb="popover"] [role="option"],
        div[data-baseweb="popover"] li,
        div[data-baseweb="popover"] [role="option"] *,
        div[data-baseweb="popover"] li * {
            min-height: 42px !important;
            height: auto !important;
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: clip !important;
            line-height: 1.3 !important;
            color: #111111 !important;
            -webkit-text-fill-color: #111111 !important;
        }
        div[data-baseweb="popover"] [role="option"],
        div[data-baseweb="popover"] li {
            padding: 0.65rem 0.75rem !important;
            align-items: flex-start !important;
            border-radius: 8px !important;
        }
        section[data-testid="stSidebar"] [data-testid="stSelectbox"],
        section[data-testid="stSidebar"] [data-testid="stMultiSelect"] {
            margin-bottom: 0.75rem !important;
        }
        section[data-testid="stSidebar"] [data-baseweb="select"] > div {
            min-height: 48px !important;
            height: auto !important;
            border-radius: 11px !important;
        }
        section[data-testid="stSidebar"] [data-baseweb="select"] span,
        section[data-testid="stSidebar"] [data-baseweb="select"] input {
            white-space: normal !important;
            overflow: visible !important;
            text-overflow: clip !important;
            line-height: 1.25 !important;
        }
        @media (min-width: 769px) {
            section[data-testid="stSidebar"],
            section[data-testid="stSidebar"] > div {
                min-width: 330px !important;
                width: 330px !important;
            }
        }
        @media (max-width: 768px) {
            section[data-testid="stSidebar"] {
                width: min(92vw, 360px) !important;
                min-width: min(92vw, 360px) !important;
            }
            div[data-baseweb="popover"] [role="listbox"],
            div[data-baseweb="popover"] ul {
                max-height: 56vh !important;
            }
        }


        /* PB_JOBHUB_SIDEBAR_SCROLL_GUARD_V2
           Keep the complete navigation readable and independently scrollable. */
        section[data-testid="stSidebar"] {
            height: 100svh !important;
            max-height: 100svh !important;
            overflow: hidden !important;
        }

        section[data-testid="stSidebar"] > div,
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
            height: 100svh !important;
            max-height: 100svh !important;
            overflow-y: auto !important;
            overflow-x: hidden !important;
            overscroll-behavior-y: contain !important;
            scrollbar-gutter: stable !important;
            scroll-behavior: auto !important;
            padding-bottom: 2rem !important;
        }

        section[data-testid="stSidebar"] > div::-webkit-scrollbar,
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"]::-webkit-scrollbar {
            width: 11px;
        }

        section[data-testid="stSidebar"] > div::-webkit-scrollbar-thumb,
        section[data-testid="stSidebar"] [data-testid="stSidebarContent"]::-webkit-scrollbar-thumb {
            background: rgba(216, 200, 184, 0.58);
            border: 3px solid transparent;
            border-radius: 999px;
            background-clip: padding-box;
        }

        section[data-testid="stSidebar"] .pb-sidebar-logo {
            position: relative;
            z-index: 1;
            background: rgba(255,255,255,0.08);
        }

        section[data-testid="stSidebar"] .pb-sidebar-build {
            color: #bfae9d;
            font-size: 10px;
            margin-top: 8px;
            letter-spacing: 0.04em;
        }

        section[data-testid="stSidebar"] .st-key-sidebar_reset_navigation {
            position: relative;
            z-index: 1;
            padding: 0.25rem 0 0.5rem 0;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] label,
        section[data-testid="stSidebar"] [role="radiogroup"] label > div {
            max-width: 100% !important;
            overflow: visible !important;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] label p {
            word-break: normal !important;
            overflow-wrap: anywhere !important;
        }

        @media (min-width: 769px) {
            section[data-testid="stSidebar"],
            section[data-testid="stSidebar"] > div {
                width: clamp(300px, 23vw, 360px) !important;
                min-width: clamp(300px, 23vw, 360px) !important;
            }
        }

        h1, h2, h3, h4 {
            color: var(--pb-charcoal);
            letter-spacing: -0.02em;
        }

        div[data-testid="stMetric"],
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: rgba(255,255,255,0.96);
            border: 1px solid var(--pb-border);
            border-radius: 18px;
            box-shadow: 0 10px 28px rgba(31,31,31,0.06);
        }

        div[data-testid="stMetric"] {
            padding: 16px 18px;
        }

        div[data-testid="stMetric"] label {
            color: var(--pb-muted) !important;
            font-weight: 600;
        }

        .pb-sidebar-logo {
            background: rgba(255,255,255,0.08);
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 22px;
            padding: 18px 16px;
            margin: 0 0 18px 0;
            text-align: left;
        }

        .pb-logo-image {
            display: block;
            width: min(190px, 100%);
            max-height: 92px;
            object-fit: contain;
            object-position: left center;
            margin-bottom: 10px;
            filter: drop-shadow(0 4px 8px rgba(0,0,0,0.16));
        }

        .pb-logo-mark {
            width: 48px;
            height: 48px;
            border-radius: 15px;
            background: #f6efe7;
            color: #1f1f1f;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 20px;
            margin-bottom: 10px;
        }

        .pb-sidebar-title {
            font-size: 17px;
            font-weight: 700;
            line-height: 1.15;
            color: #ffffff;
        }

        .pb-sidebar-subtitle {
            font-size: 12px;
            color: #d8c8b8;
            margin-top: 4px;
        }

        .pb-page-hero {
            background: linear-gradient(135deg, #1f1f1f 0%, #463a30 62%, #d8c8b8 140%);
            color: white;
            border-radius: 26px;
            padding: 26px 30px;
            margin: 8px 0 22px 0;
            box-shadow: 0 16px 34px rgba(31,31,31,0.16);
        }

        .pb-page-eyebrow {
            color: #d8c8b8;
            text-transform: uppercase;
            letter-spacing: 0.16em;
            font-size: 12px;
            font-weight: 700;
            margin-bottom: 8px;
        }

        .pb-page-title {
            font-size: 34px;
            font-weight: 800;
            line-height: 1.1;
            margin-bottom: 8px;
            color: #ffffff;
        }

        .pb-page-subtitle {
            color: #f4ebe1;
            font-size: 15px;
            max-width: 920px;
        }

        .pb-card {
            background: rgba(255,255,255,0.97);
            border: 1px solid var(--pb-border);
            border-radius: 20px;
            padding: 18px;
            box-shadow: 0 10px 26px rgba(31,31,31,0.06);
            min-height: 120px;
            margin-bottom: 12px;
        }

        .pb-card-label {
            color: var(--pb-muted);
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            margin-bottom: 8px;
        }

        .pb-card-value {
            color: var(--pb-charcoal);
            font-size: 31px;
            font-weight: 800;
            line-height: 1;
            margin-bottom: 8px;
        }

        .pb-card-subtitle {
            color: var(--pb-muted);
            font-size: 13px;
            line-height: 1.35;
        }

        .pb-card.green { border-left: 7px solid var(--pb-success); }
        .pb-card.orange { border-left: 7px solid var(--pb-warning); }
        .pb-card.red { border-left: 7px solid var(--pb-danger); }
        .pb-card.blue { border-left: 7px solid var(--pb-info); }
        .pb-card.taupe { border-left: 7px solid var(--pb-accent-dark); }

        .stButton > button, .stDownloadButton > button {
            border-radius: 999px !important;
            border: 1px solid var(--pb-accent) !important;
            background: #ffffff !important;
            color: #1f1f1f !important;
            font-weight: 700 !important;
            padding: 0.55rem 1rem !important;
            box-shadow: 0 6px 14px rgba(31,31,31,0.05);
        }

        .stButton > button:hover, .stDownloadButton > button:hover {
            border-color: var(--pb-accent-dark) !important;
            color: #000000 !important;
            transform: translateY(-1px);
        }

        [data-testid="stDataFrame"], [data-testid="stTable"] {
            border: 1px solid var(--pb-border);
            border-radius: 18px;
            overflow: hidden;
            background: rgba(255,255,255,0.98);
        }

        div[data-testid="stTabs"] button {
            font-weight: 700;
        }

        @media (max-width: 760px) {
            .pb-page-title { font-size: 26px; }
            .pb-card { min-height: auto; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def pb_sidebar_header():
    logo_data_uri = pb_logo_data_uri()
    logo_html = (
        f'<img class="pb-logo-image" src="{logo_data_uri}" alt="Premier Brushworks logo">'
        if logo_data_uri
        else '<div class="pb-logo-mark">PB</div>'
    )
    st.sidebar.markdown(
        f"""
        <div class="pb-sidebar-logo">
            {logo_html}
            <div class="pb-sidebar-title">Premier Brushworks<br>JobHub</div>
            <div class="pb-sidebar-subtitle">Commercial painting operations</div>
            <div class="pb-sidebar-build">Build {pb_html(PB_JOBHUB_BUILD)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def pb_scroll_sidebar_to_top():
    """Best-effort browser scroll reset after a navigation change.

    The CSS scroll guard remains the primary fix.  This helper simply prevents
    Streamlit from preserving an awkward sidebar scroll offset between pages.
    """
    components.html(
        """
        <script>
        (() => {
          const reset = () => {
            try {
              const doc = window.parent.document;
              const sidebar = doc.querySelector('section[data-testid="stSidebar"]');
              if (!sidebar) return;
              const candidates = [sidebar, sidebar.firstElementChild,
                sidebar.querySelector('[data-testid="stSidebarContent"]')];
              candidates.filter(Boolean).forEach((node) => {
                node.scrollTop = 0;
                if (node.scrollTo) node.scrollTo({top: 0, left: 0, behavior: 'auto'});
              });
            } catch (error) {
              // Browser sandbox restrictions are harmless; CSS still keeps the menu usable.
            }
          };
          reset();
          setTimeout(reset, 80);
          setTimeout(reset, 250);
        })();
        </script>
        """,
        height=0,
        width=0,
    )


def pb_page_header(title, subtitle="", eyebrow="Premier Brushworks"):
    st.markdown(
        f"""
        <div class="pb-page-hero">
            <div class="pb-page-eyebrow">{pb_html(eyebrow)}</div>
            <div class="pb-page-title">{pb_html(title)}</div>
            <div class="pb-page-subtitle">{pb_html(subtitle)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def pb_metric_card(label, value, subtitle="", tone="taupe"):
    st.markdown(
        f"""
        <div class="pb-card {pb_html(tone)}">
            <div class="pb-card-label">{pb_html(label)}</div>
            <div class="pb-card-value">{pb_html(value)}</div>
            <div class="pb-card-subtitle">{pb_html(subtitle)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


apply_pb_branding()


def get_database_url():
    # Streamlit Cloud: add DATABASE_URL under App > Settings > Secrets.
    try:
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass

    # Local/server fallback: environment variable.
    return os.environ.get("DATABASE_URL", "")


DATABASE_URL = get_database_url()
USE_POSTGRES = bool(DATABASE_URL)


# =============================
# OPTIONAL STORAGE DIAGNOSTICS
# =============================

if str(os.getenv("SHOW_STORAGE_CHECK", "")).strip().lower() in ["1", "true", "yes", "on"]:
    with st.sidebar.expander("Storage Check", expanded=False):
        st.write("DATA_DIR:", DATA_DIR)
        st.write("DB_PATH:", DB_PATH)
        st.write("Using Postgres:", USE_POSTGRES)
        st.write("DATA_DIR exists:", os.path.exists(DATA_DIR))
        st.write("JOB_FILES_DIR exists:", os.path.exists(JOB_FILES_DIR))

        test_file_path = os.path.join(DATA_DIR, "persistent_test.txt")
        if st.button("Test Persistent Disk", key="storage_check_test_button"):
            with open(test_file_path, "a", encoding="utf-8") as test_file:
                test_file.write(f"Test saved at {datetime.now()}\n")
            pb_success("Test file saved.")

        if os.path.exists(test_file_path):
            with open(test_file_path, "r", encoding="utf-8") as test_file:
                lines = test_file.readlines()
            pb_success(f"Persistent test file exists with {len(lines)} saved test line(s).")
        else:
            st.warning("No persistent test file found yet.")



@st.cache_resource
def get_postgres_pool():
    """
    Reusable Supabase/PostgreSQL connection pool.
    This avoids opening a brand new database connection for every query.
    """
    if not DATABASE_URL:
        return None

    return ThreadedConnectionPool(
        minconn=1,
        maxconn=8,
        dsn=DATABASE_URL,
        sslmode="require",
    )


# =============================
# DATABASE
# =============================

def normalise_seed_rows(rows, expected_columns):
    fixed_rows = []
    for row in rows:
        row = list(row)
        if len(row) < expected_columns:
            row = row + [""] * (expected_columns - len(row))
        elif len(row) > expected_columns:
            row = row[:expected_columns]
        fixed_rows.append(tuple(row))
    return fixed_rows


def get_app_setting(key, default=""):
    conn = None
    try:
        conn = connect()
        cur = conn.cursor()
        cur.execute("SELECT setting_value FROM app_settings WHERE setting_key = ?", (key,))
        row = cur.fetchone()
        if row:
            return row[0]
    except Exception:
        return default
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return default


def set_app_setting(key, value):
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value
        """, (key, value))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def starter_data_already_seeded():
    return get_app_setting("starter_data_seeded", "") == "yes"



def adapt_sql_for_postgres(sql):
    if not USE_POSTGRES:
        return sql

    original_sql = sql
    s = sql.strip()

    # PostgreSQL alias names with spaces need double quotes, not single quotes.
    s = re.sub(r"AS '([^']+)'", r'AS "\1"', s)

    # SQLite autoincrement syntax -> PostgreSQL serial syntax.
    s = s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")

    # PostgreSQL ROUND(double precision, integer) is not valid; cast simple expressions to numeric.
    s = re.sub(
        r"ROUND\(([^()]+),\s*2\)",
        r"ROUND(CAST(\1 AS numeric), 2)",
        s
    )

    # Convert INSERT OR IGNORE to PostgreSQL ON CONFLICT DO NOTHING.
    if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", s, flags=re.IGNORECASE):
        s = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", s, flags=re.IGNORECASE)
        if "ON CONFLICT" not in s.upper():
            s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    # Convert INSERT OR REPLACE to PostgreSQL upsert.
    if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO", s, flags=re.IGNORECASE):
        m = re.match(
            r"INSERT\s+OR\s+REPLACE\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*$",
            s,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if m:
            table = m.group(1)
            columns_text = m.group(2)
            values_text = m.group(3)

            columns = [c.strip() for c in columns_text.replace("\n", " ").split(",")]
            conflict_targets = {
                "app_settings": "setting_key",
                "jobs": "job_no",
                "builders_clients": "name",
                "employees": "name",
                "products": "product_code",
                "equipment_checklist_items": "item_name",
                "app_users": "username",
            }
            conflict_col = conflict_targets.get(table)

            if conflict_col:
                updates = [
                    f"{col} = EXCLUDED.{col}"
                    for col in columns
                    if col != conflict_col
                ]
                s = (
                    f"INSERT INTO {table} ({', '.join(columns)}) "
                    f"VALUES ({values_text}) "
                    f"ON CONFLICT ({conflict_col}) DO UPDATE SET {', '.join(updates)}"
                )
            else:
                s = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", s, flags=re.IGNORECASE)
                s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        else:
            s = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", s, flags=re.IGNORECASE)

    # SQLite placeholders ? -> psycopg2 placeholders %s.
    s = s.replace("?", "%s")

    # Psycopg2 uses % for parameter formatting. Any literal % in SQL, such as
    # a column alias "Rate + 10%", must be escaped as %% or psycopg2 can crash
    # with "IndexError: tuple index out of range".
    s = re.sub(r"%(?!s)", "%%", s)

    return s


class PostgresCursorAdapter:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, sql, params=()):
        return self.cursor.execute(adapt_sql_for_postgres(sql), params)

    def executemany(self, sql, rows):
        return self.cursor.executemany(adapt_sql_for_postgres(sql), rows)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def description(self):
        return self.cursor.description

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def __iter__(self):
        return iter(self.cursor)

    def __getattr__(self, name):
        return getattr(self.cursor, name)


class PostgresConnectionAdapter:
    def __init__(self, conn, pool=None):
        self.conn = conn
        self.pool = pool
        self._closed = False

    def cursor(self):
        return PostgresCursorAdapter(self.conn.cursor())

    def commit(self):
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def close(self):
        """
        In Supabase mode this returns the connection to the cached pool instead
        of closing it completely.
        """
        if self._closed:
            return

        self._closed = True

        if self.pool is not None:
            try:
                self.pool.putconn(self.conn)
            except Exception:
                try:
                    self.pool.putconn(self.conn, close=True)
                except Exception:
                    pass
        else:
            self.conn.close()

    def __getattr__(self, name):
        return getattr(self.conn, name)


def connect():
    if USE_POSTGRES:
        pool = get_postgres_pool()
        raw_conn = pool.getconn()
        return PostgresConnectionAdapter(raw_conn, pool)

    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@st.cache_resource
def init_db():
    conn = connect()
    cur = conn.cursor()
    if not USE_POSTGRES:
        # WAL lets normal reads continue while another request commits a write.
        # It is persistent for this database, so this runs only during startup.
        cur.execute("PRAGMA journal_mode = WAL")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS builders_clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT,
        name TEXT UNIQUE,
        contact_name TEXT,
        phone TEXT,
        email TEXT,
        address TEXT,
        qbcc TEXT,
        abn TEXT,
        terms TEXT,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_no TEXT UNIQUE,
        job_name TEXT,
        builder_client_id INTEGER,
        site_address TEXT,
        status TEXT,
        leading_hand TEXT,
        start_date TEXT,
        end_date TEXT,
        contract_value REAL,
        notes TEXT,
        FOREIGN KEY(builder_client_id) REFERENCES builders_clients(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_code TEXT UNIQUE,
        product_name TEXT,
        supplier TEXT,
        unit TEXT,
        price_ex_gst REAL,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE,
        role TEXT,
        phone TEXT,
        base_hourly_rate REAL,
        rate_plus_10 REAL,
        status TEXT,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS material_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        product_id INTEGER,
        qty_required REAL,
        qty_received REAL,
        date_ordered TEXT,
        supplier TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(product_id) REFERENCES products(id)
    )
    """)
    def ensure_column(table, column, definition):
        if USE_POSTGRES:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
        else:
            cur.execute(f"PRAGMA table_info({table})")
            existing_columns = [row[1] for row in cur.fetchall()]
            if column not in existing_columns:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    ensure_column("material_entries", "custom_product_code", "TEXT")
    ensure_column("material_entries", "custom_product_name", "TEXT")
    ensure_column("material_entries", "custom_supplier", "TEXT")
    ensure_column("material_entries", "custom_unit", "TEXT")
    ensure_column("material_entries", "custom_unit_price", "REAL")
    ensure_column("material_entries", "custom_colour", "TEXT")
    ensure_column("jobs", "restrict_material_products", "INTEGER DEFAULT 0")
    ensure_column("jobs", "allowed_material_suppliers", "TEXT")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS wage_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        employee_id INTEGER,
        work_date TEXT,
        hours REAL,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS equipment_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_item TEXT,
        category TEXT,
        serial_no TEXT,
        job_id INTEGER,
        date_out TEXT,
        date_in TEXT,
        condition_out TEXT,
        condition_in TEXT,
        assigned_to TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS equipment_checklist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT,
        item_name TEXT UNIQUE,
        default_qty REAL,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS equipment_checklist_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        checklist_item_id INTEGER NOT NULL,
        qty_required REAL DEFAULT 0,
        qty_taken REAL DEFAULT 0,
        qty_returned REAL DEFAULT 0,
        is_required INTEGER DEFAULT 0,
        is_packed INTEGER DEFAULT 0,
        is_returned INTEGER DEFAULT 0,
        date_out TEXT,
        date_in TEXT,
        taken_by TEXT,
        returned_by TEXT,
        condition_out TEXT,
        condition_in TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(checklist_item_id) REFERENCES equipment_checklist_items(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS imported_material_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        product TEXT,
        colour TEXT,
        qty_required TEXT,
        qty_loaded TEXT,
        source_file TEXT,
        imported_at TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)



    cur.execute("""
    CREATE TABLE IF NOT EXISTS timesheet_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        employee_id INTEGER NOT NULL,
        work_date TEXT,
        start_time TEXT,
        finish_time TEXT,
        break_minutes REAL DEFAULT 0,
        total_hours REAL DEFAULT 0,
        work_type TEXT,
        submitted_by TEXT,
        submitted_at TEXT,
        status TEXT DEFAULT 'Submitted',
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    )
    """)






    # PB_JOBHUB_ESTIMATING_RATE_LIBRARY_V1 - reusable estimating rate library
    cur.execute("""
    CREATE TABLE IF NOT EXISTS estimating_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rate_code TEXT UNIQUE,
        category TEXT,
        item_description TEXT,
        project_type TEXT,
        work_type TEXT,
        unit TEXT,
        rate_min_ex_gst REAL DEFAULT 0,
        recommended_rate_ex_gst REAL DEFAULT 0,
        rate_max_ex_gst REAL DEFAULT 0,
        adjustment_type TEXT,
        rate_basis TEXT,
        notes TEXT,
        effective_date TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_estimating_rates_code ON estimating_rates(rate_code)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_estimating_rates_filters ON estimating_rates(project_type, work_type, category, active)")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS estimate_working_sheets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        estimate_no TEXT,
        estimate_date TEXT,
        revision TEXT,
        status TEXT,
        labour_hours REAL DEFAULT 0,
        labour_rate REAL DEFAULT 0,
        material_allowance REAL DEFAULT 0,
        access_equipment_allowance REAL DEFAULT 0,
        subcontractor_allowance REAL DEFAULT 0,
        sundries_allowance REAL DEFAULT 0,
        margin_percent REAL DEFAULT 0,
        contingency_percent REAL DEFAULT 0,
        gst_percent REAL DEFAULT 10,
        total_ex_gst REAL DEFAULT 0,
        gst_amount REAL DEFAULT 0,
        total_inc_gst REAL DEFAULT 0,
        created_at TEXT,
        updated_at TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    ensure_column("estimate_working_sheets", "archived", "INTEGER DEFAULT 0")
    ensure_column("estimate_working_sheets", "archived_at", "TEXT")
    ensure_column("estimate_working_sheets", "archived_by", "TEXT")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS estimate_line_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        estimate_id INTEGER NOT NULL,
        section TEXT,
        item_description TEXT,
        qty REAL DEFAULT 0,
        unit TEXT,
        unit_rate REAL DEFAULT 0,
        line_total REAL DEFAULT 0,
        notes TEXT,
        FOREIGN KEY(estimate_id) REFERENCES estimate_working_sheets(id)
    )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_timesheet_entries_job_id ON timesheet_entries(job_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_estimate_working_sheets_job_id ON estimate_working_sheets(job_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_estimate_line_items_estimate_id ON estimate_line_items(estimate_id)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS job_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        photo_name TEXT,
        photo_type TEXT,
        photo_data TEXT,
        category TEXT,
        caption TEXT,
        uploaded_by TEXT,
        uploaded_at TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)
   
    cur.execute("""
    CREATE TABLE IF NOT EXISTS job_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        document_type TEXT,
        file_name TEXT,
        file_path TEXT,
        created_at TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
   
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password_hash TEXT,
        role TEXT,
        employee_id INTEGER,
        active INTEGER DEFAULT 1,
        notes TEXT,
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    )
    """)


    cur.execute("""
    CREATE TABLE IF NOT EXISTS job_budgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER UNIQUE,
        quoted_labour_hours REAL DEFAULT 0,
        quoted_labour_cost REAL DEFAULT 0,
        quoted_materials REAL DEFAULT 0,
        quoted_access_equipment REAL DEFAULT 0,
        quoted_subcontractors REAL DEFAULT 0,
        quoted_sundries REAL DEFAULT 0,
        target_gp_percent REAL DEFAULT 35,
        locked_at TEXT,
        locked_by TEXT,
        notes TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS job_variations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        variation_no TEXT,
        description TEXT,
        reason TEXT,
        amount_ex_gst REAL DEFAULT 0,
        status TEXT DEFAULT 'Draft',
        sent_date TEXT,
        approved_date TEXT,
        approved_by TEXT,
        notes TEXT,
        created_at TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS invoice_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        claim_no TEXT,
        description TEXT,
        amount_ex_gst REAL DEFAULT 0,
        invoice_date TEXT,
        due_date TEXT,
        paid_date TEXT,
        status TEXT DEFAULT 'Draft',
        notes TEXT,
        created_at TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS staff_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        employee_id INTEGER,
        schedule_date TEXT,
        start_time TEXT,
        finish_time TEXT,
        site_role TEXT,
        notes TEXT,
        created_at TEXT,
        FOREIGN KEY(job_id) REFERENCES jobs(id),
        FOREIGN KEY(employee_id) REFERENCES employees(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_code_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        request TEXT,
        ai_response TEXT,
        patch_json TEXT,
        target_files TEXT,
        status TEXT,
        created_at TEXT,
        applied_at TEXT,
        result_message TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_learning_sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT,
        url TEXT,
        active INTEGER DEFAULT 1,
        last_checked TEXT,
        last_summary TEXT,
        notes TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_builder_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        topic TEXT,
        note TEXT,
        source TEXT,
        created_at TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient_user_id INTEGER NOT NULL,
        event_type TEXT,
        title TEXT,
        message TEXT,
        job_id INTEGER,
        entity_type TEXT,
        entity_id TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL,
        read_at TEXT,
        FOREIGN KEY(recipient_user_id) REFERENCES app_users(id),
        FOREIGN KEY(job_id) REFERENCES jobs(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS app_settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT
    )
    """)

    conn.commit()
    conn.close()


def _migration_ensure_column(cur, table, column, definition):
    """Add a column without relying on runtime string-patch installers."""
    if USE_POSTGRES:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
        return
    cur.execute(f"PRAGMA table_info({table})")
    if column not in {str(row[1]) for row in cur.fetchall()}:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migration_has_duplicates(cur, table, columns, where_clause=""):
    grouped = ", ".join(columns)
    cur.execute(
        f"""
        SELECT 1
        FROM {table}
        {where_clause}
        GROUP BY {grouped}
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    return cur.fetchone() is not None


@st.cache_resource
def apply_schema_migrations():
    """Apply versioned, restart-safe schema upgrades once per app process."""
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)
        cur.execute("SELECT migration_id FROM schema_migrations")
        applied = {str(row[0]) for row in cur.fetchall()}

        migration_id = "20260724_security_integrity_v1"
        if migration_id not in applied:
            for column, definition in [
                ("failed_login_count", "INTEGER DEFAULT 0"),
                ("locked_until", "TEXT"),
                ("must_change_password", "INTEGER DEFAULT 0"),
                ("password_changed_at", "TEXT"),
                ("last_login_at", "TEXT"),
            ]:
                _migration_ensure_column(cur, "app_users", column, definition)

            for column, definition in [
                ("approved_by", "TEXT"),
                ("approved_at", "TEXT"),
            ]:
                _migration_ensure_column(cur, "timesheet_entries", column, definition)

            for column, definition in [
                ("timesheet_id", "INTEGER"),
                ("hourly_rate_snapshot", "REAL DEFAULT 0"),
                ("source", "TEXT DEFAULT 'Manual'"),
            ]:
                _migration_ensure_column(cur, "wage_entries", column, definition)

            _migration_ensure_column(
                cur,
                "estimate_working_sheets",
                "pricing_method",
                "TEXT DEFAULT 'Markup'",
            )
            _migration_ensure_column(cur, "jobs", "row_version", "INTEGER DEFAULT 1")
            _migration_ensure_column(cur, "jobs", "archived_at", "TEXT")
            _migration_ensure_column(cur, "jobs", "archived_by", "TEXT")
            _migration_ensure_column(cur, "builders_clients", "normalised_name", "TEXT")
            _migration_ensure_column(cur, "job_documents", "storage_key", "TEXT")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    username TEXT,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    details TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS login_audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    success INTEGER DEFAULT 0,
                    reason TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS job_employee_access (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    employee_id INTEGER NOT NULL,
                    access_role TEXT DEFAULT 'Assigned',
                    granted_by TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, employee_id),
                    FOREIGN KEY(job_id) REFERENCES jobs(id),
                    FOREIGN KEY(employee_id) REFERENCES employees(id)
                )
            """)

            # Reconcile only unambiguous wage rows created by the legacy
            # "post wages on timesheet submission" behaviour. Ambiguous matches
            # are deliberately left untouched for administrator review.
            legacy_linked = 0
            legacy_reversed = 0
            legacy_ambiguous = 0
            cur.execute("""
                SELECT t.id, t.job_id, t.employee_id, t.work_date, t.total_hours,
                       COALESCE(t.status, 'Submitted'),
                       COALESCE(NULLIF(e.rate_plus_10, 0), e.base_hourly_rate, 0)
                FROM timesheet_entries t
                LEFT JOIN employees e ON e.id = t.employee_id
                WHERE NOT EXISTS (
                    SELECT 1 FROM wage_entries w WHERE w.timesheet_id = t.id
                )
                ORDER BY t.id
            """)
            legacy_timesheets = cur.fetchall()
            for (
                timesheet_id,
                job_id,
                employee_id,
                work_date,
                total_hours,
                timesheet_status,
                hourly_rate,
            ) in legacy_timesheets:
                cur.execute("""
                    SELECT id
                    FROM wage_entries
                    WHERE timesheet_id IS NULL
                      AND job_id = ?
                      AND employee_id = ?
                      AND COALESCE(work_date, '') = COALESCE(?, '')
                      AND ABS(COALESCE(hours, 0) - COALESCE(?, 0)) < 0.0001
                      AND notes LIKE 'Timesheet:%'
                    ORDER BY id
                """, (job_id, employee_id, work_date, total_hours))
                matching_wages = [int(row[0]) for row in cur.fetchall()]
                if len(matching_wages) != 1:
                    if matching_wages:
                        legacy_ambiguous += 1
                    continue
                wage_id = matching_wages[0]
                normalised_status = str(timesheet_status or "Submitted")
                if normalised_status == "Processed":
                    normalised_status = "Paid"
                    cur.execute(
                        "UPDATE timesheet_entries SET status = 'Paid' WHERE id = ?",
                        (timesheet_id,),
                    )
                if normalised_status in {"Approved", "Paid"}:
                    cur.execute("""
                        UPDATE wage_entries
                        SET timesheet_id = ?, hourly_rate_snapshot = ?,
                            source = 'Legacy Timesheet'
                        WHERE id = ?
                    """, (timesheet_id, float(hourly_rate or 0), wage_id))
                    legacy_linked += 1
                else:
                    cur.execute("DELETE FROM wage_entries WHERE id = ?", (wage_id,))
                    legacy_reversed += 1

            if legacy_linked or legacy_reversed or legacy_ambiguous:
                cur.execute("""
                    INSERT INTO audit_events
                        (user_id, username, action, entity_type, entity_id, details, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    None,
                    "system",
                    "legacy_timesheet_wages_reconciled",
                    "migration",
                    migration_id,
                    json.dumps({
                        "linked": legacy_linked,
                        "reversed_unapproved": legacy_reversed,
                        "ambiguous_for_review": legacy_ambiguous,
                    }, sort_keys=True),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ))

            cur.execute(
                "UPDATE builders_clients SET normalised_name = LOWER(TRIM(name)) "
                "WHERE COALESCE(normalised_name, '') = ''"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_builders_clients_normalised_name "
                "ON builders_clients(normalised_name)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_events_entity "
                "ON audit_events(entity_type, entity_id, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_login_audit_username_created "
                "ON login_audit_events(username, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_employee_access_employee "
                "ON job_employee_access(employee_id, job_id)"
            )
            if not _migration_has_duplicates(
                cur,
                "app_users",
                ["LOWER(TRIM(username))"],
            ):
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_username_ci_unique "
                    "ON app_users(LOWER(TRIM(username)))"
                )
            if not _migration_has_duplicates(
                cur,
                "app_users",
                ["employee_id"],
                "WHERE employee_id IS NOT NULL",
            ):
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_employee_unique "
                    "ON app_users(employee_id) WHERE employee_id IS NOT NULL"
                )
            if not _migration_has_duplicates(
                cur,
                "builders_clients",
                ["normalised_name"],
                "WHERE COALESCE(normalised_name, '') <> ''",
            ):
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_builders_clients_name_ci_unique "
                    "ON builders_clients(normalised_name) "
                    "WHERE COALESCE(normalised_name, '') <> ''"
                )
            for index_name, table_name, columns in [
                ("idx_timesheet_entries_job", "timesheet_entries", "job_id, work_date"),
                ("idx_wage_entries_job", "wage_entries", "job_id, work_date"),
                ("idx_material_entries_job", "material_entries", "job_id"),
                ("idx_imported_material_entries_job", "imported_material_entries", "job_id"),
                ("idx_equipment_records_job", "equipment_checklist_records", "job_id"),
                ("idx_job_photos_job", "job_photos", "job_id, uploaded_at"),
                ("idx_job_documents_job", "job_documents", "job_id, created_at"),
                ("idx_job_variations_job", "job_variations", "job_id"),
                ("idx_invoice_claims_job", "invoice_claims", "job_id"),
                ("idx_staff_schedule_job_date", "staff_schedule", "job_id, schedule_date"),
                ("idx_staff_schedule_employee_date", "staff_schedule", "employee_id, schedule_date"),
            ]:
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {index_name} "
                    f"ON {table_name}({columns})"
                )

            if not _migration_has_duplicates(
                cur,
                "wage_entries",
                ["timesheet_id"],
                "WHERE timesheet_id IS NOT NULL",
            ):
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wage_entries_timesheet_unique "
                    "ON wage_entries(timesheet_id)"
                )
            if not _migration_has_duplicates(cur, "job_variations", ["job_id", "variation_no"]):
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_job_variations_number_unique "
                    "ON job_variations(job_id, variation_no)"
                )
            if not _migration_has_duplicates(cur, "invoice_claims", ["job_id", "claim_no"]):
                cur.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_claims_number_unique "
                    "ON invoice_claims(job_id, claim_no)"
                )

            cur.execute(
                "INSERT INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
                (migration_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )

        takeoff_migration_id = "20260725_takeoff_job_pack_v1"
        if takeoff_migration_id not in applied:
            for column, definition in [
                ("estimated_labour_hours", "REAL DEFAULT 0"),
                ("material_allowance", "REAL DEFAULT 0"),
                ("substrate", "TEXT"),
                ("work_location", "TEXT"),
                ("coating_system", "TEXT"),
                ("colour_finish", "TEXT"),
                ("source_pack", "TEXT"),
            ]:
                _migration_ensure_column(cur, "estimate_line_items", column, definition)

            _migration_ensure_column(cur, "job_documents", "mime_type", "TEXT")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS takeoff_pack_imports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    pack_id TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    source_file TEXT,
                    imported_at TEXT NOT NULL,
                    imported_by TEXT,
                    estimate_id INTEGER,
                    line_count INTEGER DEFAULT 0,
                    material_count INTEGER DEFAULT 0,
                    document_count INTEGER DEFAULT 0,
                    manifest_json TEXT,
                    UNIQUE(job_id, pack_id, revision),
                    FOREIGN KEY(job_id) REFERENCES jobs(id),
                    FOREIGN KEY(estimate_id) REFERENCES estimate_working_sheets(id)
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_takeoff_pack_imports_job "
                "ON takeoff_pack_imports(job_id, imported_at)"
            )
            cur.execute(
                "INSERT INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
                (takeoff_migration_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )

        material_policy_migration_id = "20260727_job_material_policy_notifications_v1"
        if material_policy_migration_id not in applied:
            _migration_ensure_column(
                cur,
                "jobs",
                "restrict_material_products",
                "INTEGER DEFAULT 0",
            )
            _migration_ensure_column(
                cur,
                "jobs",
                "allowed_material_suppliers",
                "TEXT",
            )
            cur.execute("""
                CREATE TABLE IF NOT EXISTS app_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recipient_user_id INTEGER NOT NULL,
                    event_type TEXT,
                    title TEXT,
                    message TEXT,
                    job_id INTEGER,
                    entity_type TEXT,
                    entity_id TEXT,
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    read_at TEXT,
                    FOREIGN KEY(recipient_user_id) REFERENCES app_users(id),
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_notifications_recipient_unread "
                "ON app_notifications(recipient_user_id, read_at, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_notifications_job "
                "ON app_notifications(job_id, created_at)"
            )
            cur.execute(
                "INSERT INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
                (material_policy_migration_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )

        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def df_query(sql, params=()):
    """
    Query helper.
    In Supabase mode this uses the cached connection pool through connect().
    """
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description] if cur.description else []
        return pd.DataFrame(rows, columns=columns)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def execute(sql, params=()):
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def execute_with_rowcount(sql, params=()):
    """Execute a write and return the number of affected rows."""
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rowcount = int(cur.rowcount or 0)
        conn.commit()
        return rowcount
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def record_audit_event(action, entity_type, entity_id="", details=None):
    """Best-effort application audit for sensitive and destructive actions."""
    try:
        user = st.session_state.get("user") or {}
        execute("""
            INSERT INTO audit_events
            (user_id, username, action, entity_type, entity_id, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            user.get("id"),
            str(user.get("username") or ""),
            str(action or ""),
            str(entity_type or ""),
            str(entity_id or ""),
            json.dumps(details or {}, default=str, sort_keys=True),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
    except Exception:
        # Auditing must not hide the original user action or error.
        pass


MANAGEMENT_NOTIFICATION_TARGETS = {
    "nick": {"nick", "nick martin"},
    "bryce": {"bryce", "bryce curran"},
}


def normalise_notification_identity(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).split())


def management_notification_recipients():
    """Resolve Nick and Bryce's active JobHub accounts without hard-coding user IDs."""
    users = df_query("""
        SELECT u.id, u.username, u.role,
               COALESCE(e.name, '') AS employee_name
        FROM app_users u
        LEFT JOIN employees e ON e.id = u.employee_id
        WHERE COALESCE(u.active, 0) = 1
        ORDER BY u.id
    """)
    if users.empty:
        return []

    selected = {}
    for _, row in users.iterrows():
        username = normalise_notification_identity(row["username"])
        employee_name = normalise_notification_identity(row["employee_name"])
        identities = {username, employee_name}
        combined = f"{username} {employee_name}".strip()
        for target, aliases in MANAGEMENT_NOTIFICATION_TARGETS.items():
            if target in selected:
                continue
            if any(
                identity in aliases
                or any(alias and alias in identity.split() for alias in aliases if " " not in alias)
                for identity in identities
                if identity
            ) or any(alias and alias in combined for alias in aliases):
                selected[target] = {
                    "id": int(row["id"]),
                    "username": str(row["username"] or ""),
                    "employee_name": str(row["employee_name"] or ""),
                }

    # Conservative role fallback for older databases where the accounts were
    # created without linked employee names.
    if "nick" not in selected:
        admins = users[users["role"].astype(str).str.casefold() == "admin"]
        if not admins.empty:
            row = admins.iloc[0]
            selected["nick"] = {
                "id": int(row["id"]),
                "username": str(row["username"] or ""),
                "employee_name": str(row["employee_name"] or ""),
            }
    if "bryce" not in selected:
        managers = users[users["role"].astype(str).str.casefold() == "manager"]
        if not managers.empty:
            row = managers.iloc[0]
            selected["bryce"] = {
                "id": int(row["id"]),
                "username": str(row["username"] or ""),
                "employee_name": str(row["employee_name"] or ""),
            }

    recipients = []
    seen_ids = set()
    for target in ("nick", "bryce"):
        recipient = selected.get(target)
        if recipient and recipient["id"] not in seen_ids:
            recipients.append(recipient)
            seen_ids.add(recipient["id"])
    return recipients


def create_management_notifications(
    event_type,
    title,
    message,
    job_id=None,
    entity_type="",
    entity_id="",
):
    """Create persistent in-app notifications for Nick and Bryce."""
    try:
        recipients = management_notification_recipients()
        if not recipients:
            return 0
        user = get_current_user() or {}
        created_by = str(user.get("employee_name") or user.get("username") or "JobHub")
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = connect()
        try:
            cur = conn.cursor()
            rows = [
                (
                    recipient["id"],
                    str(event_type or ""),
                    str(title or "Notification")[:200],
                    str(message or "")[:2000],
                    int(job_id) if job_id is not None else None,
                    str(entity_type or ""),
                    str(entity_id or ""),
                    created_by,
                    created_at,
                    "",
                )
                for recipient in recipients
            ]
            cur.executemany("""
                INSERT INTO app_notifications
                (recipient_user_id, event_type, title, message, job_id, entity_type,
                 entity_id, created_by, created_at, read_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()
        return len(recipients)
    except Exception:
        # A notification problem must never prevent a timesheet or material
        # request from being saved.
        return 0


def mark_notification_read(notification_id, user_id):
    execute("""
        UPDATE app_notifications
        SET read_at = ?
        WHERE id = ? AND recipient_user_id = ?
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        int(notification_id),
        int(user_id),
    ))


def render_sidebar_notifications():
    user = get_current_user() or {}
    user_id = user.get("id")
    if not user_id:
        return

    notifications = df_query("""
        SELECT id, event_type, title, message, created_by, created_at
        FROM app_notifications
        WHERE recipient_user_id = ?
          AND COALESCE(read_at, '') = ''
        ORDER BY created_at DESC, id DESC
        LIMIT 12
    """, (int(user_id),))
    unread_count_df = df_query("""
        SELECT COUNT(*) AS c
        FROM app_notifications
        WHERE recipient_user_id = ?
          AND COALESCE(read_at, '') = ''
    """, (int(user_id),))
    unread_count = int(unread_count_df.iloc[0]["c"] or 0) if not unread_count_df.empty else 0

    if not notifications.empty:
        newest = notifications.iloc[0]
        toast_key = f"_pb_notification_toast_{user_id}"
        newest_id = int(newest["id"])
        if st.session_state.get(toast_key) != newest_id:
            st.session_state[toast_key] = newest_id
            try:
                st.toast(
                    f"{newest['title']}: {newest['message']}",
                    icon="🔔",
                    duration=8,
                )
            except Exception:
                pass

    with st.sidebar.expander(f"🔔 Notifications ({unread_count})", expanded=False):
        if notifications.empty:
            st.caption("No unread notifications.")
            return

        if st.button(
            "Mark all as read",
            key=f"notification_mark_all_{user_id}",
            width="stretch",
        ):
            execute("""
                UPDATE app_notifications
                SET read_at = ?
                WHERE recipient_user_id = ?
                  AND COALESCE(read_at, '') = ''
            """, (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                int(user_id),
            ))
            pb_success("All notifications marked as read.")
            pb_rerun()

        for _, note in notifications.iterrows():
            note_id = int(note["id"])
            st.markdown(f"**{note['title']}**")
            st.caption(str(note["message"] or ""))
            st.caption(f"{note['created_at']} · {note['created_by'] or 'JobHub'}")
            if st.button(
                "Mark read",
                key=f"notification_mark_read_{user_id}_{note_id}",
                width="stretch",
            ):
                mark_notification_read(note_id, user_id)
                pb_rerun()
            st.divider()



# =============================
# BUILDERS / CLIENTS - SAFE CONTACT MERGE
# =============================
BUILDER_CLIENT_MERGE_FIELDS = (
    "type", "name", "contact_name", "phone", "email",
    "address", "qbcc", "abn", "terms", "notes",
)


def clean_contact_merge_value(value):
    """Return a stable string for database values, including pandas NaN."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def builder_client_merge_defaults(records_df, primary_id):
    """
    Build conservative merge defaults.

    The primary record wins for populated fields. Blank primary fields are
    filled from the selected duplicate records. Notes are combined without
    repeating identical text.
    """
    if records_df is None or records_df.empty:
        raise ValueError("No contact records were supplied for merging.")

    work = records_df.copy()
    work["id"] = work["id"].astype(int)
    primary_rows = work[work["id"] == int(primary_id)]
    if primary_rows.empty:
        raise ValueError("The selected primary contact could not be found.")

    primary = primary_rows.iloc[0]
    duplicates = work[work["id"] != int(primary_id)]
    ordered = pd.concat([primary_rows, duplicates], ignore_index=True)

    defaults = {}
    for field in BUILDER_CLIENT_MERGE_FIELDS:
        if field == "notes":
            continue
        values = [clean_contact_merge_value(v) for v in ordered[field].tolist()]
        defaults[field] = next((v for v in values if v), "")

    note_values = []
    seen_notes = set()
    for value in ordered["notes"].tolist():
        note = clean_contact_merge_value(value)
        key = note.casefold()
        if note and key not in seen_notes:
            note_values.append(note)
            seen_notes.add(key)
    defaults["notes"] = "\n\n".join(note_values)
    defaults["type"] = defaults.get("type") or "Builder"
    defaults["name"] = defaults.get("name") or clean_contact_merge_value(primary.get("name"))
    return defaults


def merge_builder_client_records(primary_id, duplicate_ids, final_values):
    """
    Merge duplicate builder/client records inside one database transaction.

    - Keeps the primary record and its ID.
    - Moves all linked jobs from duplicate records to the primary record.
    - Updates the primary record with the reviewed final values.
    - Deletes only the selected duplicate records.
    """
    primary_id = int(primary_id)
    duplicate_ids = sorted({int(x) for x in duplicate_ids if int(x) != primary_id})
    if not duplicate_ids:
        raise ValueError("Select at least one duplicate contact to merge.")

    selected_ids = [primary_id] + duplicate_ids
    placeholders = ", ".join(["?"] * len(selected_ids))
    duplicate_placeholders = ", ".join(["?"] * len(duplicate_ids))

    values = {
        field: clean_contact_merge_value(final_values.get(field, ""))
        for field in BUILDER_CLIENT_MERGE_FIELDS
    }
    if not values["name"]:
        raise ValueError("The merged company/client name cannot be blank.")

    conn = connect()
    try:
        cur = conn.cursor()
        if not USE_POSTGRES:
            cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            f"SELECT id, name FROM builders_clients WHERE id IN ({placeholders})"
            + (" FOR UPDATE" if USE_POSTGRES else ""),
            tuple(selected_ids),
        )
        found = {int(row[0]): clean_contact_merge_value(row[1]) for row in cur.fetchall()}
        missing = [record_id for record_id in selected_ids if record_id not in found]
        if missing:
            raise ValueError("One or more selected contacts no longer exist. Refresh and try again.")

        # Do not allow the reviewed final name to collide with an unrelated record.
        cur.execute(
            f"""
            SELECT id, name
            FROM builders_clients
            WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
              AND id NOT IN ({placeholders})
            LIMIT 1
            """,
            tuple([values["name"]] + selected_ids),
        )
        conflict = cur.fetchone()
        if conflict:
            raise ValueError(
                f'The final name "{values["name"]}" already belongs to another contact. '
                "Include that record in this merge or choose a different final name."
            )

        cur.execute(
            f"SELECT COUNT(*) FROM jobs WHERE builder_client_id IN ({duplicate_placeholders})",
            tuple(duplicate_ids),
        )
        moved_job_count = int((cur.fetchone() or [0])[0] or 0)

        cur.execute(
            """
            UPDATE builders_clients
            SET type = ?, name = ?, contact_name = ?, phone = ?, email = ?,
                address = ?, qbcc = ?, abn = ?, terms = ?, notes = ?,
                normalised_name = LOWER(TRIM(?))
            WHERE id = ?
            """,
            (
                values["type"], values["name"], values["contact_name"],
                values["phone"], values["email"], values["address"],
                values["qbcc"], values["abn"], values["terms"],
                values["notes"], values["name"], primary_id,
            ),
        )

        cur.execute(
            f"UPDATE jobs SET builder_client_id = ? WHERE builder_client_id IN ({duplicate_placeholders})",
            tuple([primary_id] + duplicate_ids),
        )
        cur.execute(
            f"DELETE FROM builders_clients WHERE id IN ({duplicate_placeholders})",
            tuple(duplicate_ids),
        )

        conn.commit()
        result = {
            "primary_id": primary_id,
            "primary_name": values["name"],
            "duplicates_removed": len(duplicate_ids),
            "jobs_moved": moved_job_count,
        }
        record_audit_event("contacts_merged", "builder_client", primary_id, result)
        return result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def execute_many(sql, rows):
    conn = connect()
    try:
        cur = conn.cursor()
        cur.executemany(sql, rows)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def refresh():
    pb_rerun()


def get_builder_options():
    df = df_query("SELECT id, name FROM builders_clients ORDER BY name")
    return {str(row["name"]): int(row["id"]) for _, row in df.iterrows()}


def get_employee_options(active_only=False):
    where = "WHERE status = 'Active'" if active_only else ""
    df = df_query(f"SELECT id, name FROM employees {where} ORDER BY name")
    return {str(row["name"]): int(row["id"]) for _, row in df.iterrows()}


def get_job_options():
    df = df_query("""
        SELECT id, job_no || ' - ' || COALESCE(job_name, '') AS label
        FROM jobs
        ORDER BY job_no
    """)
    return {str(row["label"]): int(row["id"]) for _, row in df.iterrows()}


def get_employee_job_options(employee_id):
    """Return only jobs the employee leads, is scheduled on, or was granted."""
    if not employee_id:
        return {}
    df = df_query("""
        SELECT DISTINCT j.id,
               j.job_no || ' - ' || COALESCE(j.job_name, '') AS label
        FROM jobs j
        JOIN employees e ON e.id = ?
        LEFT JOIN staff_schedule s
               ON s.job_id = j.id AND s.employee_id = e.id
        LEFT JOIN job_employee_access a
               ON a.job_id = j.id AND a.employee_id = e.id
        WHERE (
            s.employee_id IS NOT NULL
            OR a.employee_id IS NOT NULL
            OR LOWER(TRIM(COALESCE(j.leading_hand, ''))) = LOWER(TRIM(e.name))
        )
          AND COALESCE(j.archived_at, '') = ''
          AND COALESCE(j.status, '') <> 'Archived'
        ORDER BY label
    """, (int(employee_id),))
    return {str(row["label"]): int(row["id"]) for _, row in df.iterrows()}


def normalise_supplier_name(value):
    return " ".join(str(value or "").strip().split())


def parse_material_supplier_list(value):
    if isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        text_value = str(value or "").strip()
        if not text_value:
            return []
        try:
            parsed = json.loads(text_value)
            if isinstance(parsed, list):
                values = parsed
            else:
                values = [parsed]
        except Exception:
            values = re.split(r"[,;|\n]+", text_value)

    output = []
    seen = set()
    for value in values:
        supplier = normalise_supplier_name(value)
        key = supplier.casefold()
        if supplier and key not in seen:
            output.append(supplier)
            seen.add(key)
    return output


def serialise_material_supplier_list(values):
    return json.dumps(parse_material_supplier_list(values), ensure_ascii=False)


def get_product_supplier_options():
    df = df_query("""
        SELECT supplier
        FROM (
            SELECT DISTINCT TRIM(COALESCE(supplier, '')) AS supplier
            FROM products
            WHERE TRIM(COALESCE(supplier, '')) <> ''
        ) AS supplier_list
        ORDER BY LOWER(supplier), supplier
    """)
    return [normalise_supplier_name(value) for value in df.get("supplier", pd.Series(dtype=str)).tolist() if normalise_supplier_name(value)]


def get_job_material_policy(job_id):
    df = df_query("""
        SELECT job_no, job_name,
               COALESCE(restrict_material_products, 0) AS restrict_material_products,
               COALESCE(allowed_material_suppliers, '') AS allowed_material_suppliers
        FROM jobs
        WHERE id = ?
    """, (int(job_id),))
    if df.empty:
        return {
            "restricted": False,
            "suppliers": [],
            "job_no": "",
            "job_name": "",
        }
    row = df.iloc[0]
    return {
        "restricted": bool(int(row["restrict_material_products"] or 0)),
        "suppliers": parse_material_supplier_list(row["allowed_material_suppliers"]),
        "job_no": str(row["job_no"] or ""),
        "job_name": str(row["job_name"] or ""),
    }


def _filtered_products_dataframe(allowed_suppliers=None):
    if allowed_suppliers is None:
        return df_query("""
            SELECT id, product_code, product_name, supplier
            FROM products
            ORDER BY product_code
        """)

    suppliers = parse_material_supplier_list(allowed_suppliers)
    if not suppliers:
        return pd.DataFrame(columns=["id", "product_code", "product_name", "supplier"])

    placeholders = ", ".join(["?"] * len(suppliers))
    return df_query(
        f"""
        SELECT id, product_code, product_name, supplier
        FROM products
        WHERE LOWER(TRIM(COALESCE(supplier, ''))) IN ({placeholders})
        ORDER BY product_code
        """,
        tuple(supplier.casefold() for supplier in suppliers),
    )


def get_product_options(allowed_suppliers=None):
    df = _filtered_products_dataframe(allowed_suppliers)
    return {str(row["product_code"]): int(row["id"]) for _, row in df.iterrows()}


def get_product_name_options(allowed_suppliers=None):
    df = _filtered_products_dataframe(allowed_suppliers)
    if not df.empty:
        df = df.sort_values(["product_name", "product_code"], kind="stable")
    return {f"{row['product_name']} ({row['product_code']})": int(row["id"]) for _, row in df.iterrows()}


def next_job_no():
    df = df_query("SELECT job_no FROM jobs WHERE job_no LIKE 'PB%' ORDER BY job_no DESC LIMIT 1")
    if df.empty:
        return "PB25001"

    last = str(df.iloc[0]["job_no"])
    digits = "".join(c for c in last if c.isdigit())
    prefix = "".join(c for c in last if not c.isdigit())

    if not digits:
        return "PB25001"

    return f"{prefix}{int(digits) + 1:05d}"


def has_related_records(table, field, record_id):
    df = df_query(f"SELECT COUNT(*) AS c FROM {table} WHERE {field} = ?", (record_id,))
    return int(df.iloc[0]["c"]) > 0


# =============================
# STARTER DATA
# =============================
def seed_data():
    """Do not seed real customers, staff, jobs or payroll data from source code."""
    if not starter_data_already_seeded():
        set_app_setting("starter_data_seeded", "yes")




# =============================
# PDF CHECKLIST IMPORT HELPERS
# =============================
PDF_CHECKLIST_ITEMS = {
    "access": ("Access Equipment", [
        "4ft Step Ladder",
        "6ft Step Ladder",
        "8ft Step Ladder",
        "10ft Step Ladder",
        "3m Extension Ladder",
        "4.8m Extension Ladder",
        "6m Extension Ladder",
        "Door Stackers",
        "600mm Trestles",
        "900mm Trestles",
        "4m Planks",
        "5m Planks",
        "6m Planks",
    ]),
    "prep": ("Preparation Equipment", [
        "Mirka Dustless Sander",
        "Mirka Extractor",
        "Pole Sander",
        "Pressure Cleaner",
        "PowerShot",
        "Saw Stools",
        "Paper Machine",
        "Mixing Paddle",
        "Broom",
        "Dustpan",
        "Brush",
    ]),
    "painting": ("Painting Equipment", [
        "Graco Sprayguns",
        "Fine Finish Tips",
        "Standard Spray Tips",
        "Roller Frames 270mm",
        "Mini Roller Frames",
        "Roller Sleeves 270mm",
        "Mini Roller Sleeves",
        "Brushes",
        "Paint Trays",
        "Paint Pots",
    ]),
    "poles": ("Extension Poles", [
        "600mm Pole",
        "1200mm Pole",
        "1800mm Pole",
        "2400mm Pole",
        "Adjustable Pole",
    ]),
    "dewalt": ("DeWalt Electrical Tools", [
        "Impact Driver",
        "Hammer Drill",
        "Blower",
        "Sheet Sander",
        "Orbital Sander",
        "Grinder",
        "Work Light",
        "Bluetooth Speaker",
        "Battery Charger",
        "5Ah Battery",
        "Extension Leads",
        "RCD",
    ]),
    "cons": ("Consumables", [
        "Green Tape",
        "Yellow Tape",
        "Plastic Masking Film",
        "Black Plastic",
        "Canvas Drop Sheets",
        "Floor Protection Paper",
        "Gap Filler",
        "Plaster Filler",
        "Timber Filler",
        "Putty",
        "Bog",
        "Sugar Soap",
        "Mixing Sticks",
        "Sandpaper 80G",
        "Sandpaper 120G",
        "Sandpaper 180G",
        "Sandpaper 240G",
    ]),
}


def clean_pdf_value(value):
    if value is None:
        return ""
    text = str(value)
    if text in ["/Off", "Off", "None", "nan"]:
        return ""
    if text.startswith("/"):
        text = text[1:]
    return text.strip()


def pdf_field_value(fields, name):
    field = fields.get(name)
    if not field:
        return ""
    return clean_pdf_value(field.get("/V", ""))


def qty_to_float(value):
    text = clean_pdf_value(value)
    if not text:
        return 0.0
    match = re.search(r"[-+]?\d*\.?\d+", text)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0


def is_pdf_tick(value):
    text = clean_pdf_value(value).lower()
    return bool(text and text not in ["off", "false", "0", "no"])


def uploaded_file_size(uploaded_file):
    size = getattr(uploaded_file, "size", None)
    if size is not None:
        return int(size)
    try:
        return int(uploaded_file.getbuffer().nbytes)
    except Exception:
        current_position = uploaded_file.tell()
        uploaded_file.seek(0, os.SEEK_END)
        size = uploaded_file.tell()
        uploaded_file.seek(current_position)
        return int(size)


def parse_master_checklist_pdf(uploaded_file):
    if uploaded_file_size(uploaded_file) > MAX_PDF_UPLOAD_BYTES:
        raise ValueError("PDF is larger than the 25 MB upload limit.")
    uploaded_file.seek(0)
    reader = PdfReader(uploaded_file)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs cannot be imported.")
    fields = reader.get_fields() or {}

    job_info = {
        "job_number": pdf_field_value(fields, "p1_job_0"),
        "job_name": pdf_field_value(fields, "p1_job_1"),
        "site_address": pdf_field_value(fields, "p1_job_2"),
        "client_builder": pdf_field_value(fields, "p1_job_3"),
        "leading_hand": pdf_field_value(fields, "p1_team_0"),
        "crew_members": pdf_field_value(fields, "p1_team_1"),
        "team_extra": pdf_field_value(fields, "p1_team_extra"),
    }

    equipment_rows = []

    for prefix, (category, item_names) in PDF_CHECKLIST_ITEMS.items():
        for idx, item_name in enumerate(item_names):
            req = pdf_field_value(fields, f"{prefix}_{idx}_req")
            loaded = pdf_field_value(fields, f"{prefix}_{idx}_loaded")
            returned = pdf_field_value(fields, f"{prefix}_{idx}_returned")
            tick = pdf_field_value(fields, f"{prefix}_{idx}_tick")
            missing = pdf_field_value(fields, f"{prefix}_{idx}_missing")

            has_anything = any([req, loaded, returned, is_pdf_tick(tick), missing])
            if not has_anything:
                continue

            equipment_rows.append({
                "Category": category,
                "Equipment Item": item_name,
                "Qty Required Raw": req,
                "Qty Loaded Raw": loaded,
                "Qty Returned Raw": returned,
                "Qty Required": qty_to_float(req),
                "Qty Loaded": qty_to_float(loaded),
                "Qty Returned": qty_to_float(returned),
                "Ticked": "Yes" if is_pdf_tick(tick) else "",
                "Missing / Damaged": missing,
            })

    material_rows = []
    for idx in range(5):
        product = pdf_field_value(fields, f"paintreg_{idx}_product")
        colour = pdf_field_value(fields, f"paintreg_{idx}_colour")
        qty_req = pdf_field_value(fields, f"paintreg_{idx}_qty_req")
        qty_loaded = pdf_field_value(fields, f"paintreg_{idx}_qty_loaded")

        if any([product, colour, qty_req, qty_loaded]):
            material_rows.append({
                "Product": product,
                "Colour": colour,
                "Qty Required": qty_req,
                "Qty Loaded": qty_loaded,
            })

    return job_info, pd.DataFrame(equipment_rows), pd.DataFrame(material_rows)


def find_or_create_builder_client(cur, name):
    name = clean_pdf_value(name)
    if not name:
        return None
    cur.execute(
        "SELECT id FROM builders_clients WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (name,),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""
        INSERT INTO builders_clients
        (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes, normalised_name)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        "Client / Builder", name, "", "", "", "", "", "", "",
        "Created from imported PDF checklist", name.strip().casefold(),
    ))

    cur.execute(
        "SELECT id FROM builders_clients WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))",
        (name,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def find_or_create_checklist_item(cur, category, item_name):
    cur.execute("SELECT id FROM equipment_checklist_items WHERE item_name = ?", (item_name,))
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute("""
        INSERT INTO equipment_checklist_items
        (category, item_name, default_qty, notes)
        VALUES (?, ?, ?, ?)
    """, (category, item_name, 0, "Created from imported PDF checklist"))

    cur.execute("SELECT id FROM equipment_checklist_items WHERE item_name = ?", (item_name,))
    row = cur.fetchone()
    return row[0] if row else None


def import_master_checklist_to_job(job_id, job_info, equipment_df, materials_df, source_file, update_job=True, replace_imported_materials=True):
    conn = connect()
    cur = conn.cursor()

    imported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if update_job:
        update_fields = []
        params = []

        if job_info.get("job_number"):
            update_fields.append("job_no = ?")
            params.append(job_info["job_number"])

        if job_info.get("job_name"):
            update_fields.append("job_name = ?")
            params.append(job_info["job_name"])

        if job_info.get("site_address"):
            update_fields.append("site_address = ?")
            params.append(job_info["site_address"])

        if job_info.get("leading_hand"):
            update_fields.append("leading_hand = ?")
            params.append(job_info["leading_hand"])

        if job_info.get("client_builder"):
            builder_id = find_or_create_builder_client(cur, job_info["client_builder"])
            if builder_id:
                update_fields.append("builder_client_id = ?")
                params.append(builder_id)

        crew_notes = []
        if job_info.get("crew_members"):
            crew_notes.append(f"Crew Members from checklist: {job_info['crew_members']}")
        if job_info.get("team_extra"):
            crew_notes.append(f"Team Notes from checklist: {job_info['team_extra']}")

        if crew_notes:
            cur.execute("SELECT notes FROM jobs WHERE id = ?", (job_id,))
            current_notes_row = cur.fetchone()
            current_notes = current_notes_row[0] if current_notes_row and current_notes_row[0] else ""
            new_notes = (current_notes + "\n" if current_notes else "") + "\n".join(crew_notes)
            update_fields.append("notes = ?")
            params.append(new_notes)

        if update_fields:
            params.append(job_id)
            cur.execute(f"UPDATE jobs SET {', '.join(update_fields)} WHERE id = ?", params)

    imported_equipment_count = 0

    for _, row in equipment_df.iterrows():
        category = str(row.get("Category", "")).strip()
        item_name = str(row.get("Equipment Item", "")).strip()
        if not item_name:
            continue

        item_id = find_or_create_checklist_item(cur, category, item_name)

        qty_required = float(row.get("Qty Required", 0) or 0)
        qty_loaded = float(row.get("Qty Loaded", 0) or 0)
        qty_returned = float(row.get("Qty Returned", 0) or 0)

        raw_req = str(row.get("Qty Required Raw", "") or "").strip()
        raw_loaded = str(row.get("Qty Loaded Raw", "") or "").strip()
        raw_returned = str(row.get("Qty Returned Raw", "") or "").strip()
        missing = str(row.get("Missing / Damaged", "") or "").strip()
        ticked = str(row.get("Ticked", "") or "").strip()

        notes_parts = []
        if raw_req and qty_required == 0:
            notes_parts.append(f"Original required qty: {raw_req}")
        if raw_loaded and qty_loaded == 0:
            notes_parts.append(f"Original loaded qty: {raw_loaded}")
        if raw_returned and qty_returned == 0:
            notes_parts.append(f"Original returned qty: {raw_returned}")
        if missing:
            notes_parts.append(f"Missing/damaged: {missing}")
        if ticked:
            notes_parts.append("Checklist ticked")
        notes_parts.append(f"Imported from {source_file} at {imported_at}")
        notes = " | ".join(notes_parts)

        is_required = 1 if (qty_required > 0 or raw_req) else 0
        is_packed = 1 if (qty_loaded > 0 or raw_loaded or ticked) else 0
        is_returned = 1 if (qty_returned > 0 or raw_returned) else 0

        cur.execute("""
            SELECT id FROM equipment_checklist_records
            WHERE job_id = ? AND checklist_item_id = ?
            ORDER BY id ASC
        """, (job_id, item_id))
        existing = cur.fetchall()

        if existing:
            keep_id = existing[0][0]
            cur.execute("""
                UPDATE equipment_checklist_records
                SET qty_required = ?, qty_taken = ?, qty_returned = ?,
                    is_required = ?, is_packed = ?, is_returned = ?,
                    date_out = ?, date_in = ?, taken_by = ?, returned_by = ?,
                    condition_out = ?, condition_in = ?, notes = ?
                WHERE id = ?
            """, (
                qty_required, qty_loaded, qty_returned,
                is_required, is_packed, is_returned,
                imported_at.split(" ")[0], "", "", "",
                "", missing, notes, keep_id
            ))

            for duplicate in existing[1:]:
                cur.execute("DELETE FROM equipment_checklist_records WHERE id = ?", (duplicate[0],))
        else:
            cur.execute("""
                INSERT INTO equipment_checklist_records
                (job_id, checklist_item_id, qty_required, qty_taken, qty_returned,
                 is_required, is_packed, is_returned, date_out, date_in, taken_by, returned_by,
                 condition_out, condition_in, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id, item_id, qty_required, qty_loaded, qty_returned,
                is_required, is_packed, is_returned,
                imported_at.split(" ")[0], "", "", "",
                "", missing, notes
            ))

        imported_equipment_count += 1

    imported_material_count = 0

    if replace_imported_materials:
        cur.execute("DELETE FROM imported_material_entries WHERE job_id = ?", (job_id,))

    for _, row in materials_df.iterrows():
        product = str(row.get("Product", "") or "").strip()
        colour = str(row.get("Colour", "") or "").strip()
        qty_required = str(row.get("Qty Required", "") or "").strip()
        qty_loaded = str(row.get("Qty Loaded", "") or "").strip()

        if not any([product, colour, qty_required, qty_loaded]):
            continue

        cur.execute("""
            INSERT INTO imported_material_entries
            (job_id, product, colour, qty_required, qty_loaded, source_file, imported_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, product, colour, qty_required, qty_loaded, source_file, imported_at, "Imported from PDF master checklist"))

        imported_material_count += 1

    conn.commit()
    conn.close()

    return imported_equipment_count, imported_material_count

# =============================
# PDF GENERATION HELPERS
# =============================



def get_job_details_for_pdf(job_id):
    df = df_query("""
        SELECT j.id,
               j.job_no,
               j.job_name,
               j.site_address,
               j.leading_hand,
               COALESCE(bc.name, '') AS builder_client
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.id = ?
    """, (job_id,))

    if df.empty:
        return None

    return df.iloc[0].to_dict()


def fill_pdf_template(template_path, output_path, field_values):
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"PDF template not found: {os.path.basename(template_path)}")

    reader = PdfReader(template_path)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDF templates cannot be generated by JobHub.")
    source_fields = reader.get_fields() or {}
    values = {
        str(name): "" if value is None else str(value)
        for name, value in dict(field_values or {}).items()
    }
    missing_fields = sorted(set(values) - set(source_fields))
    if missing_fields:
        raise ValueError(
            "PDF template is missing required fields: " + ", ".join(missing_fields)
        )

    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer.update_page_form_field_values(
        None,
        values,
        auto_regenerate=False,
    )

    with open(output_path, "wb") as f:
        writer.write(f)

    # Reopen and verify both the canonical field tree and page widgets.
    written_reader = PdfReader(output_path)
    written_fields = written_reader.get_fields() or {}
    for name, expected in values.items():
        if name not in written_fields:
            raise ValueError(f"Generated PDF lost required field: {name}")
        actual = str(written_fields[name].get("/V", "") or "")
        if actual != expected:
            raise ValueError(f"Generated PDF field verification failed: {name}")

    verified_widget_names = set()
    for page in written_reader.pages:
        for annotation_ref in page.get("/Annots", []) or []:
            annotation = annotation_ref.get_object()
            if annotation.get("/Subtype") != "/Widget":
                continue
            parent_ref = annotation.get("/Parent")
            effective = parent_ref.get_object() if parent_ref else annotation
            name = str(effective.get("/T", "") or "")
            if name not in values:
                continue
            verified_widget_names.add(name)
            actual = str(effective.get("/V", "") or "")
            if actual != values[name]:
                raise ValueError(f"Generated PDF widget verification failed: {name}")
            appearance = annotation.get("/AP")
            if not appearance or not appearance.get("/N"):
                raise ValueError(f"Generated PDF field has no appearance stream: {name}")

    missing_widgets = sorted(set(values) - verified_widget_names)
    if missing_widgets:
        raise ValueError(
            "Generated PDF is missing field widgets: " + ", ".join(missing_widgets)
        )

    return output_path


def attach_document_to_job(job_id, document_type, file_path, notes="Generated from JobHub", mime_type=""):
    resolved_mime = str(mime_type or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream")
    execute("""
        INSERT INTO job_documents
        (job_id, document_type, file_name, file_path, created_at, notes, mime_type)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        document_type,
        os.path.basename(file_path),
        file_path,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        notes,
        resolved_mime,
    ))


def generate_equipment_checklist_pdf(job_id):
    job = get_job_details_for_pdf(job_id)

    if not job:
        raise ValueError("Job not found.")

    job_no = str(job.get("job_no") or f"job_{job_id}")
    job_folder = get_job_folder(job_no)

    output_path = os.path.join(
        job_folder,
        f"{safe_file_name(job_no)}_equipment_checklist_fillable.pdf"
    )

    fields = {
        "p1_job_0": job.get("job_no", ""),
        "p1_job_1": job.get("job_name", ""),
        "p1_job_2": job.get("site_address", ""),
        "p1_job_3": job.get("builder_client", ""),
        "p1_team_0": job.get("leading_hand", ""),
        "p1_team_1": "",
        "p1_team_extra": "",
    }

    try:
        equipment_df = df_query("""
            SELECT i.item_name,
                   COALESCE(r.qty_required, 0) AS qty_required,
                   COALESCE(r.qty_taken, 0) AS qty_taken,
                   COALESCE(r.qty_returned, 0) AS qty_returned,
                   COALESCE(r.condition_in, '') AS condition_in,
                   COALESCE(r.notes, '') AS notes
            FROM equipment_checklist_items i
            LEFT JOIN equipment_checklist_records r
                ON r.checklist_item_id = i.id
               AND r.job_id = ?
        """, (job_id,))

        record_map = {
            str(row["item_name"]).strip().lower(): row
            for _, row in equipment_df.iterrows()
        }

        for prefix, (_, item_names) in PDF_CHECKLIST_ITEMS.items():
            for idx, item_name in enumerate(item_names):
                row = record_map.get(item_name.strip().lower())
                if row is None:
                    continue

                qty_required = float(row["qty_required"] or 0)
                qty_taken = float(row["qty_taken"] or 0)
                qty_returned = float(row["qty_returned"] or 0)

                fields[f"{prefix}_{idx}_req"] = "" if qty_required == 0 else str(qty_required)
                fields[f"{prefix}_{idx}_loaded"] = "" if qty_taken == 0 else str(qty_taken)
                fields[f"{prefix}_{idx}_returned"] = "" if qty_returned == 0 else str(qty_returned)
                fields[f"{prefix}_{idx}_missing"] = str(row["condition_in"] or row["notes"] or "")

    except Exception:
        pass

    fill_pdf_template(EQUIPMENT_TEMPLATE_PDF, output_path, fields)
    attach_document_to_job(job_id, "Equipment Checklist", output_path)

    return output_path


def generate_paint_order_pdf(job_id):
    job = get_job_details_for_pdf(job_id)

    if not job:
        raise ValueError("Job not found.")

    job_no = str(job.get("job_no") or f"job_{job_id}")
    job_folder = get_job_folder(job_no)

    output_path = os.path.join(
        job_folder,
        f"{safe_file_name(job_no)}_paint_materials_order_fillable.pdf"
    )

    fields = {
        "Project": f"{job.get('job_no', '')} - {job.get('job_name', '')}".strip(" -"),
        "Builder__Client": job.get("builder_client", ""),
        "Site_Address": job.get("site_address", ""),
        "Required_Delivery_Date": "",
        "Ordered_By": "",
    }

    material_rows = []

    try:
        imported_df = df_query("""
            SELECT product,
                   colour,
                   qty_required,
                   qty_loaded
            FROM imported_material_entries
            WHERE job_id = ?
            ORDER BY id
            LIMIT 10
        """, (job_id,))

        for _, row in imported_df.iterrows():
            material_rows.append({
                "product": row["product"],
                "colour": row["colour"],
                "qty_required": row["qty_required"],
                "qty_received": row["qty_loaded"],
            })
    except Exception:
        pass

    if not material_rows:
        try:
            entries_df = df_query("""
                SELECT COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS product,
                       COALESCE(NULLIF(m.custom_colour, ''), '') AS colour,
                       m.qty_required,
                       m.qty_received
                FROM material_entries m
                LEFT JOIN products p ON p.id = m.product_id
                WHERE m.job_id = ?
                ORDER BY m.id
                LIMIT 10
            """, (job_id,))

            for _, row in entries_df.iterrows():
                material_rows.append({
                    "product": row["product"],
                    "colour": row["colour"],
                    "qty_required": row["qty_required"],
                    "qty_received": row["qty_received"],
                })
        except Exception:
            pass

    for idx, row in enumerate(material_rows[:10]):
        fields[f"product_{idx}"] = str(row.get("product", "") or "")
        fields[f"colour_{idx}"] = str(row.get("colour", "") or "")
        fields[f"qtyreq_{idx}"] = str(row.get("qty_required", "") or "")
        fields[f"qtyrec_{idx}"] = str(row.get("qty_received", "") or "")

    fill_pdf_template(PAINT_ORDER_TEMPLATE_PDF, output_path, fields)
    attach_document_to_job(job_id, "Paint & Materials Order Form", output_path)

    return output_path


def generate_day_labour_sheet_pdf(job_id):
    job = get_job_details_for_pdf(job_id)
    if not job:
        raise ValueError("Job not found.")

    job_no = str(job.get("job_no") or f"job_{job_id}")
    job_folder = get_job_folder(job_no)
    output_path = os.path.join(
        job_folder,
        f"{safe_file_name(job_no)}_day_labour_sheet_fillable.pdf",
    )

    fields = {
        "job_number": job.get("job_no", ""),
        "project_name": job.get("job_name", ""),
        "site_address": job.get("site_address", ""),
        "builder_client": job.get("builder_client", ""),
        "leading_hand": job.get("leading_hand", ""),
    }

    labour_rows = df_query("""
        SELECT t.work_date,
               t.work_type,
               t.total_hours,
               t.notes,
               e.name AS employee_name
        FROM timesheet_entries t
        JOIN employees e ON e.id = t.employee_id
        WHERE t.job_id = ?
          AND COALESCE(t.status, 'Submitted') <> 'Rejected'
        ORDER BY t.work_date, t.id
        LIMIT 18
    """, (job_id,))

    for index, (_, row) in enumerate(labour_rows.iterrows(), start=1):
        suffix = f"{index:02d}"
        work_type = str(row.get("work_type") or "").strip()
        notes = str(row.get("notes") or "").strip()
        task = work_type if not notes else f"{work_type}: {notes}".strip(": ")
        fields[f"task_{suffix}"] = task[:140]
        fields[f"date_completed_{suffix}"] = str(row.get("work_date") or "")
        hours = float(row.get("total_hours") or 0)
        fields[f"hours_{suffix}"] = f"{hours:g}" if hours else ""
        fields[f"signed_{suffix}"] = str(row.get("employee_name") or "")

    fill_pdf_template(DAY_LABOUR_TEMPLATE_PDF, output_path, fields)
    attach_document_to_job(
        job_id,
        "Day Labour Sheet",
        output_path,
        notes="Generated from JobHub job details and non-rejected timesheets.",
    )
    return output_path


def generate_variation_form_pdf(job_id, requested_by="", description="", reason="", notes=""):
    job = get_job_details_for_pdf(job_id)

    if not job:
        raise ValueError("Job not found.")

    if not os.path.exists(VARIATION_TEMPLATE_PDF):
        raise FileNotFoundError(
            "Variation template PDF not found. Add 'PB Variation Form fillable.pdf' to the templates folder."
        )

    job_no = str(job.get("job_no") or f"job_{job_id}")
    job_folder = get_job_folder(job_no)

    count_df = df_query("""
        SELECT variation_no
        FROM job_variations
        WHERE job_id = ?
    """, (job_id,))

    variation_no = next_scoped_number(
        count_df["variation_no"].tolist() if not count_df.empty else [],
        "VAR",
    )

    output_path = os.path.join(
        job_folder,
        f"{safe_file_name(job_no)}_{variation_no}_variation_form_fillable.pdf"
    )

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_text = str(date.today())

    execute("""
        INSERT INTO job_variations
        (
            job_id,
            variation_no,
            description,
            reason,
            amount_ex_gst,
            status,
            sent_date,
            approved_date,
            approved_by,
            notes,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        variation_no,
        description,
        reason,
        0,
        "Draft",
        "",
        "",
        "",
        f"Employee/request form generated by {requested_by}. {notes}",
        created_at,
    ))

    fields = {
        "Variation_No": variation_no,
        "Job_No": job.get("job_no", ""),
        "Job_Name": job.get("job_name", ""),
        "Project": f"{job.get('job_no', '')} - {job.get('job_name', '')}".strip(" -"),
        "Builder_Client": job.get("builder_client", ""),
        "Site_Address": job.get("site_address", ""),
        "Requested_By": requested_by,
        "Date": today_text,
        "Description": description,
        "Reason": reason,
        "Notes": notes,
        "Amount_Ex_GST": "",
        "Approved_By": "",
        "Approved_Date": "",
    }

    fill_pdf_template(VARIATION_TEMPLATE_PDF, output_path, fields)

    attach_document_to_job(
        job_id,
        "Variation Form",
        output_path,
        notes=f"Generated by employee/user: {requested_by}"
    )

    return output_path, variation_no

JOB_DIRECT_CHILD_TABLES = (
    "wage_entries",
    "timesheet_entries",
    "material_entries",
    "equipment_entries",
    "equipment_checklist_records",
    "imported_material_entries",
    "job_photos",
    "job_documents",
    "job_budgets",
    "job_variations",
    "invoice_claims",
    "staff_schedule",
    "job_employee_access",
)


def linked_job_counts(job_id):
    counts = {}
    for table in JOB_DIRECT_CHILD_TABLES:
        df = df_query(f"SELECT COUNT(*) AS c FROM {table} WHERE job_id = ?", (job_id,))
        counts[table] = int(df.iloc[0]["c"] or 0) if not df.empty else 0
    line_df = df_query("""
        SELECT COUNT(*) AS c
        FROM estimate_line_items li
        JOIN estimate_working_sheets e ON e.id = li.estimate_id
        WHERE e.job_id = ?
    """, (job_id,))
    counts["estimate_line_items"] = int(line_df.iloc[0]["c"] or 0) if not line_df.empty else 0
    estimate_df = df_query(
        "SELECT COUNT(*) AS c FROM estimate_working_sheets WHERE job_id = ?",
        (job_id,),
    )
    counts["estimate_working_sheets"] = (
        int(estimate_df.iloc[0]["c"] or 0) if not estimate_df.empty else 0
    )
    return counts


def _delete_job_rows(cur, job_id):
    cur.execute("""
        DELETE FROM estimate_line_items
        WHERE estimate_id IN (
            SELECT id FROM estimate_working_sheets WHERE job_id = ?
        )
    """, (job_id,))
    cur.execute("DELETE FROM estimate_working_sheets WHERE job_id = ?", (job_id,))
    for table in JOB_DIRECT_CHILD_TABLES:
        cur.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
    cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


def _archive_deleted_job_files(job_number):
    """Move deleted job files into a recoverable archive instead of erasing them."""
    if not job_number:
        return ""
    source = (Path(JOB_FILES_DIR) / safe_job_storage_segment(job_number)).resolve()
    allowed_root = Path(JOB_FILES_DIR).resolve()
    try:
        source.relative_to(allowed_root)
    except ValueError:
        return ""
    if not source.exists() or not source.is_dir():
        return ""

    archive_root = Path(EXPORTS_DIR).resolve() / "deleted_jobs"
    archive_root.mkdir(parents=True, exist_ok=True)
    destination = archive_root / (
        f"{safe_file_name(job_number)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    shutil.move(str(source), str(destination))
    return str(destination)


def permanently_delete_job_and_linked_data(job_id):
    conn = connect()
    job_number = ""
    counts = linked_job_counts(job_id)
    try:
        cur = conn.cursor()
        cur.execute("SELECT job_no FROM jobs WHERE id = ?", (job_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError("Job not found.")
        job_number = str(row[0] or "")
        _delete_job_rows(cur, job_id)
        cur.execute("""
            INSERT INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value
        """, ("starter_data_seeded", "yes"))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    archived_files = _archive_deleted_job_files(job_number)
    record_audit_event(
        "job_permanently_deleted",
        "job",
        job_id,
        {
            "job_number": job_number,
            "deleted_counts": counts,
            "archived_files": archived_files,
        },
    )
    return {"counts": counts, "archived_files": archived_files}
    
# =============================
# LOGIN / ACCESS CONTROL
# =============================
def hash_password(password):
    return secure_hash_password(password)


def check_password(password, password_hash):
    return verify_password(password, password_hash)


def username_from_employee_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def seed_app_users():
    """Securely bootstrap the first admin; never create shared default users."""
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM app_users")
        user_count = int((cur.fetchone() or [0])[0] or 0)

        bootstrap_username = str(
            os.getenv("JOBHUB_BOOTSTRAP_ADMIN_USERNAME", "admin")
        ).strip() or "admin"
        bootstrap_password = str(os.getenv("JOBHUB_BOOTSTRAP_ADMIN_PASSWORD", ""))
        bootstrap_errors = (
            password_strength_errors(bootstrap_password, bootstrap_username)
            if bootstrap_password
            else []
        )

        if user_count == 0 and bootstrap_password and not bootstrap_errors:
            cur.execute("""
                INSERT INTO app_users
                (username, password_hash, role, employee_id, active, notes,
                 failed_login_count, locked_until, must_change_password, password_changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bootstrap_username,
                hash_password(bootstrap_password),
                "admin",
                None,
                1,
                "Secure bootstrap administrator",
                0,
                "",
                1,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))
        elif bootstrap_password and not bootstrap_errors:
            # A secure environment value can recover an existing admin account
            # that still has one of the disabled historical default passwords.
            cur.execute("""
                SELECT id, password_hash
                FROM app_users
                WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
                  AND role = 'admin'
                LIMIT 1
            """, (bootstrap_username,))
            existing = cur.fetchone()
            if existing and is_known_default_password_hash(existing[1]):
                cur.execute("""
                    UPDATE app_users
                    SET password_hash = ?, active = 1, failed_login_count = 0,
                        locked_until = '', must_change_password = 1,
                        password_changed_at = ?
                    WHERE id = ?
                """, (
                    hash_password(bootstrap_password),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    int(existing[0]),
                ))

        # Known shared passwords are disabled rather than silently left active.
        default_hashes = tuple(
            hashlib.sha256(value.encode("utf-8")).hexdigest()
            for value in ("admin123", "manager123", "changeme123")
        )
        placeholders = ", ".join(["?"] * len(default_hashes))
        cur.execute(
            f"""
            UPDATE app_users
            SET must_change_password = 1
            WHERE password_hash IN ({placeholders})
            """,
            default_hashes,
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def get_current_user():
    return st.session_state.get("user")


def current_role():
    user = get_current_user()
    if not user:
        return ""
    return user.get("role", "")


def is_admin():
    return current_role() == "admin"


def is_manager_or_admin():
    return current_role() in ["admin", "manager"]


def _login_audit(username, success, reason):
    try:
        execute("""
            INSERT INTO login_audit_events (username, success, reason, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            str(username or "").strip(),
            1 if success else 0,
            str(reason or "")[:250],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
    except Exception:
        pass


def _parse_timestamp(value):
    try:
        return datetime.fromisoformat(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def force_password_change():
    user = get_current_user() or {}
    st.title("Secure your JobHub account")
    st.warning("A new private password is required before this account can continue.")
    with st.form("required_password_change_form"):
        new_password = st.text_input("New password", type="password")
        confirm_password = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Save secure password")
    if submitted:
        errors = password_strength_errors(new_password, user.get("username", ""))
        if new_password != confirm_password:
            errors.append("The two passwords do not match.")
        if errors:
            for error in errors:
                pb_error(error)
        else:
            execute("""
                UPDATE app_users
                SET password_hash = ?, must_change_password = 0,
                    failed_login_count = 0, locked_until = '', password_changed_at = ?
                WHERE id = ?
            """, (
                hash_password(new_password),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                int(user["id"]),
            ))
            st.session_state["user"]["must_change_password"] = False
            record_audit_event("password_changed", "app_user", user["id"])
            pb_success("Password updated.")
            pb_rerun()


def require_login():
    if "user" not in st.session_state:
        st.session_state["user"] = None

    if st.session_state["user"]:
        if st.session_state["user"].get("must_change_password"):
            force_password_change()
            st.stop()
        return True

    st.title("Premier Brushworks JobHub")
    st.subheader("Login")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

        if submitted:
            user_df = df_query("""
                SELECT u.id, u.username, u.password_hash, u.role, u.employee_id, u.active,
                       COALESCE(u.failed_login_count, 0) AS failed_login_count,
                       COALESCE(u.locked_until, '') AS locked_until,
                       COALESCE(u.must_change_password, 0) AS must_change_password,
                       e.name AS employee_name
                FROM app_users u
                LEFT JOIN employees e ON e.id = u.employee_id
                WHERE LOWER(TRIM(u.username)) = LOWER(TRIM(?))
            """, (username.strip(),))

            if user_df.empty:
                _login_audit(username, False, "unknown_username")
                unknown_failures = int(st.session_state.get("unknown_login_failures", 0)) + 1
                st.session_state["unknown_login_failures"] = unknown_failures
                pb_error("Invalid username or password.")
            else:
                row = user_df.iloc[0]
                locked_until = _parse_timestamp(row["locked_until"])
                if locked_until and locked_until > datetime.now():
                    _login_audit(username, False, "temporarily_locked")
                    pb_error("This account is temporarily locked. Try again later or contact an administrator.")
                elif int(row["active"] or 0) != 1:
                    _login_audit(username, False, "inactive")
                    pb_error("This user account is inactive.")
                elif is_known_default_password_hash(row["password_hash"]):
                    _login_audit(username, False, "disabled_default_password")
                    pb_error(
                        "This historical default password has been disabled. "
                        "An administrator must reset the account securely."
                    )
                elif not check_password(password, row["password_hash"]):
                    failed_count = int(row["failed_login_count"] or 0) + 1
                    new_lock = ""
                    if failed_count >= 5:
                        new_lock = (datetime.now() + timedelta(minutes=15)).isoformat(timespec="seconds")
                        failed_count = 0
                    execute("""
                        UPDATE app_users
                        SET failed_login_count = ?, locked_until = ?
                        WHERE id = ?
                    """, (failed_count, new_lock, int(row["id"])))
                    _login_audit(username, False, "invalid_password")
                    pb_error("Invalid username or password.")
                else:
                    upgraded_hash = (
                        hash_password(password)
                        if password_needs_rehash(row["password_hash"])
                        else row["password_hash"]
                    )
                    execute("""
                        UPDATE app_users
                        SET password_hash = ?, failed_login_count = 0,
                            locked_until = '', last_login_at = ?
                        WHERE id = ?
                    """, (
                        upgraded_hash,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        int(row["id"]),
                    ))
                    st.session_state["user"] = {
                        "id": int(row["id"]),
                        "username": str(row["username"]),
                        "role": str(row["role"]),
                        "employee_id": int(row["employee_id"]) if not pd.isna(row["employee_id"]) else None,
                        "employee_name": "" if pd.isna(row["employee_name"]) else str(row["employee_name"]),
                        "must_change_password": bool(int(row["must_change_password"] or 0)),
                    }
                    st.session_state["unknown_login_failures"] = 0
                    _login_audit(username, True, "success")
                    pb_success("Logged in.")
                    pb_rerun()

    user_count_df = df_query("SELECT COUNT(*) AS c FROM app_users")
    user_count = int(user_count_df.iloc[0]["c"] or 0) if not user_count_df.empty else 0
    if user_count == 0:
        st.info(
            "No administrator exists yet. Set JOBHUB_BOOTSTRAP_ADMIN_PASSWORD "
            "to a strong temporary password in the hosting environment, restart once, "
            "then remove that environment value after signing in."
        )
    st.stop()


def logout_button():
    user = get_current_user()
    if user:
        st.sidebar.write(f"Logged in as **{user['username']}**")
        st.sidebar.caption(f"Role: {user['role']}")
        if st.sidebar.button("Logout"):
            st.session_state["user"] = None
            pb_rerun()


def employee_portal():
    user = get_current_user()
    employee_id = user.get("employee_id")
    employee_name = user.get("employee_name") or user.get("username")

    st.header("Employee Portal")
    st.caption("Restricted staff access for job details, equipment and your own hours.")

    if not employee_id:
        st.warning("This login is not linked to an employee record. Ask admin to link it in User Access.")
        return

    tab_jobs, tab_hours, tab_equipment, tab_forms, tab_photos, tab_password = st.tabs([
        "My Job Info",
        "Submit Timesheet",
        "View Equipment",
        "Generate Forms",
        "Upload Photos",
        "Change Password",
    ])

    job_options = get_employee_job_options(employee_id)

    with tab_jobs:
        st.subheader("Job Information")
        if not job_options:
            st.info("No jobs available.")
        else:
            selected_job = st.selectbox("Select Job", list(job_options.keys()), key="employee_job_info")
            selected_job_id = job_options[selected_job]

            job_df = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       bc.name AS 'Builder / Client',
                       bc.contact_name AS 'Contact',
                       bc.phone AS 'Phone',
                       bc.email AS 'Email',
                       j.site_address AS 'Site Address',
                       j.status AS 'Status',
                       j.leading_hand AS 'Leading Hand',
                       j.start_date AS 'Start Date',
                       j.end_date AS 'End Date',
                       j.notes AS 'Notes'
                FROM jobs j
                LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
                WHERE j.id = ?
            """, (selected_job_id,))
            st.dataframe(job_df, width="stretch", hide_index=True)

            st.markdown("### Job Schedule")
            employee_schedule_df = df_query("""
                SELECT s.schedule_date AS 'Date',
                       s.start_time AS 'Start',
                       s.finish_time AS 'Finish',
                       e.name AS 'Employee',
                       s.site_role AS 'Role',
                       s.notes AS 'Notes'
                FROM staff_schedule s
                LEFT JOIN employees e ON e.id = s.employee_id
                WHERE s.job_id = ?
                ORDER BY s.schedule_date, s.start_time
            """, (selected_job_id,))
            if employee_schedule_df.empty:
                st.info("No staff schedule has been saved for this job yet.")
            else:
                st.dataframe(employee_schedule_df, width="stretch", hide_index=True)

            st.markdown("### Colours / Materials Schedule")
            employee_materials_df = df_query("""
                SELECT COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS 'Product / Material',
                       COALESCE(NULLIF(m.custom_colour, ''), '') AS 'Colour / Finish',
                       COALESCE(NULLIF(m.custom_unit, ''), p.unit, '') AS 'Unit',
                       m.qty_required AS 'Qty Required',
                       m.qty_received AS 'Qty Received',
                       COALESCE(NULLIF(m.supplier, ''), NULLIF(m.custom_supplier, ''), p.supplier, '') AS 'Supplier',
                       m.date_ordered AS 'Date Ordered',
                       m.notes AS 'Notes'
                FROM material_entries m
                LEFT JOIN products p ON p.id = m.product_id
                WHERE m.job_id = ?
                ORDER BY m.id
            """, (selected_job_id,))
            employee_imported_materials_df = df_query("""
                SELECT product AS 'Product / Material',
                       colour AS 'Colour / Finish',
                       qty_required AS 'Qty Required',
                       qty_loaded AS 'Qty Loaded',
                       source_file AS 'Source File',
                       notes AS 'Notes'
                FROM imported_material_entries
                WHERE job_id = ?
                ORDER BY id
            """, (selected_job_id,))
            if employee_materials_df.empty and employee_imported_materials_df.empty:
                st.info("No colours or material schedule lines are saved for this job yet.")
            else:
                if not employee_materials_df.empty:
                    st.dataframe(employee_materials_df, width="stretch", hide_index=True)
                if not employee_imported_materials_df.empty:
                    st.markdown("#### Imported PDF material lines")
                    st.dataframe(employee_imported_materials_df, width="stretch", hide_index=True)

            st.markdown("### Job Documents / Plans / Specs")
            employee_documents_df = df_query("""
                SELECT id,
                       document_type AS 'Document Type',
                       file_name AS 'File Name',
                       file_path,
                       created_at AS 'Created At',
                       notes AS 'Notes',
                       COALESCE(mime_type, 'application/octet-stream') AS 'Mime Type'
                FROM job_documents
                WHERE job_id = ?
                ORDER BY id DESC
            """, (selected_job_id,))
            if employee_documents_df.empty:
                st.info("No job documents, plans or specs have been attached to this job yet.")
            else:
                for _, doc in employee_documents_df.iterrows():
                    st.write(f"**{doc['Document Type']}** - {doc['File Name']}")
                    st.caption(f"Created: {doc['Created At']}")
                    file_path = str(doc["file_path"])
                    try:
                        trusted_path = resolve_trusted_storage_file(file_path)
                    except ValueError:
                        trusted_path = None
                    if trusted_path and trusted_path.exists():
                        with open(trusted_path, "rb") as f:
                            st.download_button(
                                label=f"Download {doc['File Name']}",
                                data=f,
                                file_name=doc["File Name"],
                                mime=str(doc.get("Mime Type") or "application/octet-stream"),
                                key=f"employee_download_job_doc_{doc['id']}",
                            )
                    else:
                        st.warning("File path not found on disk.")

    with tab_hours:
        timesheets_page(employee_restricted=True)

    with tab_equipment:
        st.subheader("View Job Equipment Master List")
        if not job_options:
            st.info("No jobs available.")
        else:
            selected_job = st.selectbox("Select Job", list(job_options.keys()), key="employee_equipment_job")
            selected_job_id = job_options[selected_job]

            equipment_df = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       i.category AS 'Category',
                       i.item_name AS 'Equipment Item',
                       COALESCE(SUM(r.qty_required), 0) AS 'Total Required',
                       COALESCE(SUM(r.qty_taken), 0) AS 'Total Taken',
                       COALESCE(SUM(r.qty_returned), 0) AS 'Total Returned',
                       COALESCE(SUM(r.qty_taken - r.qty_returned), 0) AS 'Still Out'
                FROM equipment_checklist_items i
                CROSS JOIN jobs j
                LEFT JOIN equipment_checklist_records r
                    ON r.checklist_item_id = i.id
                   AND r.job_id = j.id
                WHERE j.id = ?
                GROUP BY j.job_no, j.job_name, i.category, i.item_name
                ORDER BY i.category, i.item_name
            """, (selected_job_id,))
            st.dataframe(equipment_df, width="stretch", hide_index=True)

    with tab_photos:
        job_photos_page(employee_restricted=True)
    with tab_forms:
        st.subheader("Generate Job Forms")
        st.caption("Employees can generate job forms without seeing pricing, contract values or financial reports.")

        if not job_options:
            st.info("No assigned jobs are available.")
        else:
            selected_job = st.selectbox(
                "Select Job",
                list(job_options.keys()),
                key="employee_generate_forms_job"
            )
            selected_job_id = job_options[selected_job]

            st.markdown("### Add Material Request to Job Register")
            st.caption("Employees can request materials without seeing pricing. Saved products apply their stored cost to the job material register automatically.")

            material_policy = get_job_material_policy(selected_job_id)
            material_override = False
            material_override_reason = ""
            if material_policy["restricted"]:
                supplier_text = ", ".join(material_policy["suppliers"]) or "No suppliers selected"
                st.info(f"This job is restricted to: {supplier_text}.")
                material_override = st.checkbox(
                    "Override job product restriction",
                    key=f"employee_material_override_{selected_job_id}",
                    help="Use only when the required product is outside the job's approved brand list.",
                )
                if material_override:
                    st.warning("Override enabled. All saved products and one-off materials are temporarily available for this request.")
                    material_override_reason = st.text_input(
                        "Override reason",
                        key=f"employee_material_override_reason_{selected_job_id}",
                        placeholder="Explain why a product outside the approved job brand is required.",
                    )

            allowed_suppliers = None
            if material_policy["restricted"] and not material_override:
                allowed_suppliers = material_policy["suppliers"]

            employee_product_code_options = get_product_options(allowed_suppliers)
            employee_product_name_options = get_product_name_options(allowed_suppliers)

            material_request_type_options = []
            if employee_product_code_options:
                material_request_type_options.append("Saved Product")
            if not material_policy["restricted"] or material_override:
                material_request_type_options.append("One-off / Not Listed")

            if not material_request_type_options:
                st.warning(
                    "No saved products match this job's approved suppliers. Use the override and enter a reason, "
                    "or ask Nick/Bryce to update the job's supplier allocation."
                )
                material_request_type = None
            else:
                material_request_type = st.radio(
                    "Material request type",
                    material_request_type_options,
                    horizontal=True,
                    key=f"employee_material_request_type_{selected_job_id}",
                )

            employee_product_id = None
            request_product_name = ""
            request_supplier = ""
            request_unit = ""
            request_colour = ""

            if material_request_type == "Saved Product":
                product_search_type = st.radio(
                    "Select product by",
                    ["Product Code", "Product Name"],
                    horizontal=True,
                    key=f"employee_material_product_search_{selected_job_id}",
                )
                if product_search_type == "Product Code":
                    selected_product = st.selectbox(
                        "Product Code",
                        list(employee_product_code_options.keys()),
                        key=f"employee_material_product_code_{selected_job_id}",
                    )
                    employee_product_id = employee_product_code_options[selected_product]
                else:
                    selected_product = st.selectbox(
                        "Product Name",
                        list(employee_product_name_options.keys()),
                        key=f"employee_material_product_name_{selected_job_id}",
                    )
                    employee_product_id = employee_product_name_options[selected_product]
                selected_product_df = df_query("""
                    SELECT product_name, supplier, unit
                    FROM products
                    WHERE id = ?
                """, (employee_product_id,))
                if not selected_product_df.empty:
                    request_product_name = str(selected_product_df.iloc[0]["product_name"] or "")
                    request_supplier = str(selected_product_df.iloc[0]["supplier"] or "")
                    request_unit = str(selected_product_df.iloc[0]["unit"] or "")
                    st.info(f"Selected: {request_product_name} · {request_supplier}")
                request_colour = st.text_input("Colour / Finish", key=f"employee_saved_product_colour_{selected_job_id}")
            elif material_request_type == "One-off / Not Listed":
                c_req1, c_req2 = st.columns(2)
                request_product_name = c_req1.text_input("Product / Material Name", key=f"employee_custom_product_name_{selected_job_id}")
                request_colour = c_req2.text_input("Colour / Finish", key=f"employee_custom_colour_{selected_job_id}")
                c_req3, c_req4 = st.columns(2)
                request_supplier = c_req3.text_input("Supplier", key=f"employee_custom_supplier_{selected_job_id}")
                request_unit = c_req4.text_input("Unit", value="each", key=f"employee_custom_unit_{selected_job_id}")

            with st.form(f"employee_material_request_form_{selected_job_id}"):
                c_qty1, c_qty2, c_qty3 = st.columns(3)
                qty_required = c_qty1.number_input("Qty Required", min_value=0.0, step=1.0, key=f"employee_material_qty_required_{selected_job_id}")
                qty_received = c_qty2.number_input("Qty Received / Loaded", min_value=0.0, step=1.0, key=f"employee_material_qty_received_{selected_job_id}")
                date_ordered = c_qty3.text_input("Date", value=str(date.today()), key=f"employee_material_date_{selected_job_id}")
                material_notes = st.text_area("Notes", key=f"employee_material_notes_{selected_job_id}")
                save_material_request = st.form_submit_button("Save Material Request to Job Register")

                if save_material_request:
                    if material_request_type is None:
                        pb_error("No product is available under this job's current supplier allocation.")
                    elif material_override and not material_override_reason.strip():
                        pb_error("Enter an override reason before requesting a product outside the approved job supplier list.")
                    elif material_request_type == "Saved Product" and not employee_product_id:
                        pb_error("Select a saved product first.")
                    elif not str(request_product_name or "").strip() and material_request_type == "One-off / Not Listed":
                        pb_error("Enter a product/material name.")
                    else:
                        override_note = ""
                        if material_override:
                            override_note = f" Product filter override: {material_override_reason.strip()}."
                        execute("""
                            INSERT INTO material_entries
                            (
                                job_id,
                                product_id,
                                qty_required,
                                qty_received,
                                date_ordered,
                                supplier,
                                notes,
                                custom_product_code,
                                custom_product_name,
                                custom_supplier,
                                custom_unit,
                                custom_unit_price,
                                custom_colour
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            selected_job_id,
                            employee_product_id,
                            qty_required,
                            qty_received,
                            date_ordered,
                            request_supplier,
                            f"Employee material request by {employee_name}. {material_notes}{override_note}",
                            "CUSTOM" if material_request_type == "One-off / Not Listed" else "",
                            request_product_name if material_request_type == "One-off / Not Listed" else "",
                            request_supplier if material_request_type == "One-off / Not Listed" else "",
                            request_unit if material_request_type == "One-off / Not Listed" else "",
                            0 if material_request_type == "One-off / Not Listed" else None,
                            request_colour,
                        ))
                        if material_override:
                            record_audit_event(
                                "job_material_filter_overridden",
                                "job",
                                selected_job_id,
                                {
                                    "reason": material_override_reason.strip(),
                                    "product": request_product_name,
                                    "supplier": request_supplier,
                                },
                            )
                        create_management_notifications(
                            "paint_order_requested",
                            "Paint/material request submitted",
                            (
                                f"{employee_name} requested {float(qty_required or 0):g} {request_unit or 'unit(s)'} "
                                f"of {request_product_name or 'material'} for {selected_job}. "
                                f"Supplier: {request_supplier or 'Unspecified'}."
                                + (f" Override reason: {material_override_reason.strip()}." if material_override else "")
                            ),
                            job_id=selected_job_id,
                            entity_type="material_request",
                            entity_id="",
                        )
                        pb_success(f"Material request saved. Nick and Bryce were notified for {selected_job}.")
                        st.info("The Paint & Materials Order Form can now be generated with this material included.")
                        refresh()

            st.divider()

            st.markdown("### Day Labour Sheet")
            st.caption(
                "Generates a fillable project task log using the job details and "
                "up to 18 non-rejected timesheet entries."
            )
            if st.button(
                "Generate Day Labour Sheet",
                key=f"employee_generate_day_labour_{selected_job_id}",
            ):
                try:
                    pdf_path = generate_day_labour_sheet_pdf(selected_job_id)
                    pb_success("Day Labour Sheet generated and attached to this job.")
                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            "Download Day Labour Sheet",
                            data=f,
                            file_name=os.path.basename(pdf_path),
                            mime="application/pdf",
                            key=f"employee_download_day_labour_{selected_job_id}",
                        )
                except Exception as e:
                    pb_error(f"Could not generate Day Labour Sheet: {e}")

            st.divider()

            st.markdown("### Paint & Materials Order Form")
            st.caption("Generates a fillable paint/materials order PDF and attaches it to the selected job.")

            if st.button("Generate Paint & Materials Order Form", key=f"employee_generate_paint_order_{selected_job_id}"):
                try:
                    pdf_path = generate_paint_order_pdf(selected_job_id)
                    create_management_notifications(
                        "paint_order_form_generated",
                        "Paint order form generated",
                        f"{employee_name} generated the Paint & Materials Order Form for {selected_job}.",
                        job_id=selected_job_id,
                        entity_type="job_document",
                        entity_id=os.path.basename(pdf_path),
                    )
                    pb_success("Paint & Materials Order Form generated and Nick/Bryce were notified.")
                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            "Download Paint & Materials Order Form",
                            data=f,
                            file_name=os.path.basename(pdf_path),
                            mime="application/pdf",
                            key=f"employee_download_paint_order_{selected_job_id}",
                        )

                except Exception as e:
                    pb_error(f"Could not generate Paint & Materials Order Form: {e}")

            st.divider()

            st.markdown("### Equipment Checklist")
            st.caption("Generates a fillable equipment checklist PDF and attaches it to the selected job.")

            if st.button("Generate Equipment Checklist", key=f"employee_generate_equipment_{selected_job_id}"):
                try:
                    pdf_path = generate_equipment_checklist_pdf(selected_job_id)
                    pb_success("Equipment Checklist generated and attached to this job.")

                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            "Download Equipment Checklist",
                            data=f,
                            file_name=os.path.basename(pdf_path),
                            mime="application/pdf",
                            key=f"employee_download_equipment_{selected_job_id}",
                        )

                except Exception as e:
                    pb_error(f"Could not generate Equipment Checklist: {e}")

            st.divider()

            st.markdown("### Variation Form")
            st.caption("Creates a draft variation request and generates a fillable variation form for the selected job.")

            variation_result_key = f"employee_variation_result_{selected_job_id}"

            with st.form(f"employee_variation_form_generator_{selected_job_id}"):
                variation_description = st.text_area("Variation Description")
                variation_reason = st.text_area("Reason / Details")
                variation_notes = st.text_area("Notes")
                generate_variation = st.form_submit_button("Generate Variation Form")

                if generate_variation:
                    try:
                        requested_by = employee_name or user.get("username", "")
                        pdf_path, variation_no = generate_variation_form_pdf(
                            selected_job_id,
                            requested_by=requested_by,
                            description=variation_description,
                            reason=variation_reason,
                            notes=variation_notes,
                        )
                        st.session_state[variation_result_key] = {
                            "pdf_path": pdf_path,
                            "variation_no": variation_no,
                        }
                    except Exception as e:
                        pb_error(f"Could not generate Variation Form: {e}")

            if variation_result_key in st.session_state:
                variation_result = st.session_state[variation_result_key]
                pdf_path = variation_result["pdf_path"]
                variation_no = variation_result["variation_no"]

                pb_success(f"Variation Form {variation_no} generated and attached to this job.")

                with open(pdf_path, "rb") as f:
                    st.download_button(
                        "Download Variation Form",
                        data=f,
                        file_name=os.path.basename(pdf_path),
                        mime="application/pdf",
                        key=f"employee_download_variation_{selected_job_id}_{variation_no}",
                    )
    with tab_password:
        st.subheader("Change My Password")
        with st.form("employee_change_password"):
            old_password = st.text_input("Current Password", type="password")
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm New Password", type="password")
            submitted = st.form_submit_button("Change Password")

            if submitted:
                user_df = df_query("SELECT password_hash FROM app_users WHERE id = ?", (user["id"],))
                if user_df.empty:
                    pb_error("User account not found.")
                elif not check_password(old_password, user_df.iloc[0]["password_hash"]):
                    pb_error("Current password is incorrect.")
                elif new_password != confirm_password:
                    pb_error("New passwords do not match.")
                else:
                    errors = password_strength_errors(new_password, user.get("username", ""))
                    if errors:
                        for error in errors:
                            pb_error(error)
                    else:
                        execute("""
                            UPDATE app_users
                            SET password_hash = ?, must_change_password = 0,
                                failed_login_count = 0, locked_until = '', password_changed_at = ?
                            WHERE id = ?
                        """, (
                            hash_password(new_password),
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            user["id"],
                        ))
                        record_audit_event("password_changed", "app_user", user["id"])
                        pb_success("Password changed.")


def import_protected_master_csv(uploaded_file, record_type):
    """Admin-only replacement for hard-coded customer and payroll seed data."""
    if uploaded_file is None:
        raise ValueError("Choose a CSV file first.")
    if uploaded_file_size(uploaded_file) > MAX_CSV_UPLOAD_BYTES:
        raise ValueError("The CSV is larger than the 5 MB safety limit.")

    uploaded_file.seek(0)
    frame = pd.read_csv(uploaded_file).fillna("")
    frame.columns = [
        re.sub(r"[^a-z0-9]+", "_", str(column).strip().casefold()).strip("_")
        for column in frame.columns
    ]
    if "name" not in frame.columns:
        raise ValueError("The CSV must contain a Name column.")
    frame = frame[frame["name"].astype(str).str.strip() != ""].copy()
    if frame.empty:
        raise ValueError("The CSV does not contain any named records.")
    if len(frame) > 5000:
        raise ValueError("The CSV exceeds the 5,000-row import limit.")

    conn = connect()
    try:
        cur = conn.cursor()
        if record_type == "Builders / Clients":
            columns = [
                "type", "name", "contact_name", "phone", "email", "address",
                "qbcc", "abn", "terms", "notes",
            ]
            for column in columns:
                if column not in frame.columns:
                    frame[column] = ""
            rows = [
                tuple(str(row[column]).strip() for column in columns)
                + (str(row["name"]).strip().casefold(),)
                for _, row in frame.iterrows()
            ]
            cur.executemany("""
                INSERT INTO builders_clients
                (type, name, contact_name, phone, email, address, qbcc, abn,
                 terms, notes, normalised_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    type = excluded.type,
                    contact_name = excluded.contact_name,
                    phone = excluded.phone,
                    email = excluded.email,
                    address = excluded.address,
                    qbcc = excluded.qbcc,
                    abn = excluded.abn,
                    terms = excluded.terms,
                    notes = excluded.notes,
                    normalised_name = excluded.normalised_name
            """, rows)
        elif record_type == "Employees":
            text_columns = ["name", "role", "phone", "status", "notes"]
            for column in text_columns:
                if column not in frame.columns:
                    frame[column] = ""
            for column in ["base_hourly_rate", "rate_plus_10"]:
                if column not in frame.columns:
                    frame[column] = 0
                frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
            rows = [
                (
                    str(row["name"]).strip(),
                    str(row["role"]).strip(),
                    str(row["phone"]).strip(),
                    float(row["base_hourly_rate"]),
                    float(row["rate_plus_10"]),
                    str(row["status"]).strip() or "Active",
                    str(row["notes"]).strip(),
                )
                for _, row in frame.iterrows()
            ]
            cur.executemany("""
                INSERT INTO employees
                (name, role, phone, base_hourly_rate, rate_plus_10, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    role = excluded.role,
                    phone = excluded.phone,
                    base_hourly_rate = excluded.base_hourly_rate,
                    rate_plus_10 = excluded.rate_plus_10,
                    status = excluded.status,
                    notes = excluded.notes
            """, rows)
        else:
            raise ValueError("Unsupported master-data type.")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    record_audit_event(
        "protected_master_csv_imported",
        record_type.casefold().replace(" ", "_"),
        "",
        {"rows": len(rows), "file_name": safe_file_name(uploaded_file.name)},
    )
    return len(rows)


def user_access_page():
    st.header("User Access")
    st.caption("Admin only. Create logins and control who can access the app.")

    if not is_admin():
        pb_error("Only admin users can access this page.")
        return

    st.markdown("### Restore / Update Haymes & Taubmans Product Lists")
    st.caption("One button to restore/update both saved paint product lists. Existing matching product codes are updated instead of duplicated.")

    pc1, pc2, pc3 = st.columns(3)
    pc1.metric("Haymes products", haymes_product_count())
    pc2.metric("Taubmans products", taubmans_product_count())
    pc3.metric("Combined saved paint products", combined_paint_product_count())

    paint_confirm = st.text_input(
        "To restore/update Haymes and Taubmans products, type: RESTORE PAINT LISTS",
        key="restore_combined_paint_lists_confirm"
    )

    if st.button("Restore / Update Haymes & Taubmans Product Lists", key="restore_haymes_taubmans_products_btn"):
        if paint_confirm.strip().upper() != "RESTORE PAINT LISTS":
            pb_error("Type RESTORE PAINT LISTS exactly before restoring.")
        else:
            restored = restore_haymes_and_taubmans_product_lists()
            pb_success(f"Restored/updated {restored} Haymes and Taubmans products.")
            refresh()


    st.divider()

    st.markdown("### Protected Master-Data Import")
    st.caption(
        "Real customer and payroll data is no longer embedded in the app source. "
        "Import an administrator-controlled CSV when master data needs to be restored."
    )

    rc1, rc2 = st.columns(2)
    rc1.metric("Builders/clients currently in database", builders_clients_count())
    rc2.metric("Employees currently in database", employees_count())

    master_record_type = st.radio(
        "Import type",
        ["Builders / Clients", "Employees"],
        horizontal=True,
        key="protected_master_import_type",
    )
    st.caption(
        "Required column: Name. Optional builder columns: Type, Contact Name, Phone, Email, "
        "Address, QBCC, ABN, Terms, Notes. Optional employee columns: Role, Phone, "
        "Base Hourly Rate, Rate Plus 10, Status, Notes. Passwords are never imported."
    )
    master_upload = st.file_uploader(
        "Choose protected master-data CSV",
        type=["csv"],
        key="protected_master_data_csv",
    )
    master_confirm = st.text_input(
        "Type IMPORT MASTER DATA to continue",
        key="protected_master_data_confirm",
    )
    if st.button("Validate and Import Master Data", key="protected_master_import_button"):
        if master_confirm.strip().upper() != "IMPORT MASTER DATA":
            pb_error("Type IMPORT MASTER DATA exactly before importing.")
        elif master_upload is None:
            pb_error("Choose a CSV file first.")
        else:
            try:
                imported_rows = import_protected_master_csv(master_upload, master_record_type)
                pb_success(f"Imported or updated {imported_rows} {master_record_type.lower()} record(s).")
                refresh()
            except Exception as exc:
                pb_error(f"Master-data import failed: {exc}")

    st.divider()

    st.markdown("### Employee Job Access")
    st.caption(
        "Employees automatically see jobs where they are the leading hand or appear on the staff schedule. "
        "Use this section for additional explicit access."
    )
    access_employee_options = get_employee_options(active_only=True)
    access_job_options = get_job_options()
    if not access_employee_options or not access_job_options:
        st.info("Create at least one active employee and one job to manage explicit access.")
    else:
        access_col1, access_col2 = st.columns(2)
        access_employee_label = access_col1.selectbox(
            "Employee",
            list(access_employee_options.keys()),
            key="explicit_job_access_employee",
        )
        access_job_label = access_col2.selectbox(
            "Job",
            list(access_job_options.keys()),
            key="explicit_job_access_job",
        )
        selected_access_employee_id = access_employee_options[access_employee_label]
        selected_access_job_id = access_job_options[access_job_label]
        grant_col, revoke_col = st.columns(2)
        if grant_col.button("Grant Additional Access", key="grant_explicit_job_access"):
            current_user = get_current_user() or {}
            execute("""
                INSERT INTO job_employee_access
                    (job_id, employee_id, access_role, granted_by, created_at)
                VALUES (?, ?, 'Assigned', ?, ?)
                ON CONFLICT(job_id, employee_id) DO UPDATE SET
                    access_role = excluded.access_role,
                    granted_by = excluded.granted_by,
                    created_at = excluded.created_at
            """, (
                selected_access_job_id,
                selected_access_employee_id,
                current_user.get("username", ""),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))
            record_audit_event(
                "employee_job_access_granted",
                "job",
                selected_access_job_id,
                {"employee_id": selected_access_employee_id},
            )
            pb_success("Additional job access granted.")
            refresh()
        if revoke_col.button("Revoke Additional Access", key="revoke_explicit_job_access"):
            execute(
                "DELETE FROM job_employee_access WHERE job_id = ? AND employee_id = ?",
                (selected_access_job_id, selected_access_employee_id),
            )
            record_audit_event(
                "employee_job_access_revoked",
                "job",
                selected_access_job_id,
                {"employee_id": selected_access_employee_id},
            )
            pb_success("Additional access removed. Schedule or leading-hand access may still apply.")
            refresh()

        explicit_access_df = df_query("""
            SELECT j.job_no AS 'Job No',
                   j.job_name AS 'Job Name',
                   e.name AS 'Employee',
                   a.access_role AS 'Access',
                   a.granted_by AS 'Granted By',
                   a.created_at AS 'Granted At'
            FROM job_employee_access a
            JOIN jobs j ON j.id = a.job_id
            JOIN employees e ON e.id = a.employee_id
            ORDER BY j.job_no, e.name
        """)
        if not explicit_access_df.empty:
            st.dataframe(explicit_access_df, width="stretch", hide_index=True)

    st.divider()

    st.markdown("### Clean Up Duplicate User Accounts")
    st.caption("Use this if the same employee/user login appears more than once.")

    duplicates_df = user_duplicate_summary()

    if duplicates_df.empty:
        pb_success("No duplicate user accounts detected.")
    else:
        st.warning(f"Found {len(duplicates_df)} duplicate/suspect user account rows.")
        st.dataframe(
            duplicates_df[["id", "username", "role", "employee_name", "active", "notes"]],
            width="stretch",
            hide_index=True,
        )

        clean_confirm = st.text_input(
            "To clean duplicate user accounts, type: CLEAN USERS",
            key="clean_duplicate_users_confirm"
        )

        if st.button("Clean Duplicate User Accounts", key="clean_duplicate_users_button"):
            if clean_confirm.strip().upper() != "CLEAN USERS":
                pb_error("Type CLEAN USERS exactly before cleaning duplicate accounts.")
            else:
                result = clean_duplicate_user_accounts()
                pb_success(
                    f"Duplicate cleanup complete. Deleted {result['deleted']} duplicate login(s). "
                    f"Skipped/disabled {result['skipped']}."
                )
                refresh()

    st.divider()

    tab_add, tab_edit, tab_list = st.tabs(["Add User", "Edit / Disable / Delete User", "User List"])

    employee_options = get_employee_options(active_only=False)
    employee_labels = ["Not linked"] + list(employee_options.keys())

    with tab_add:
        st.subheader("Add User")
        with st.form("add_user_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            role = st.selectbox("Role", ["employee", "manager", "admin"])
            employee_label = st.selectbox("Link to Employee", employee_labels)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Create User")

            if submitted:
                if not username or not password:
                    pb_error("Username and password are required.")
                else:
                    errors = password_strength_errors(password, username)
                    if errors:
                        for error in errors:
                            pb_error(error)
                    else:
                        employee_id = employee_options.get(employee_label) if employee_label != "Not linked" else None
                        try:
                            username_match = df_query("""
                                SELECT id
                                FROM app_users
                                WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
                                LIMIT 1
                            """, (username.strip(),))
                            employee_match = (
                                df_query(
                                    "SELECT id FROM app_users WHERE employee_id = ? LIMIT 1",
                                    (employee_id,),
                                )
                                if employee_id is not None
                                else pd.DataFrame()
                            )
                            if not username_match.empty:
                                pb_error("That username is already in use.")
                            elif not employee_match.empty:
                                pb_error("That employee is already linked to another login.")
                            else:
                                execute("""
                                    INSERT INTO app_users
                                    (username, password_hash, role, employee_id, active, notes,
                                     must_change_password, password_changed_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    username.strip(),
                                    hash_password(password),
                                    role,
                                    employee_id,
                                    1,
                                    notes,
                                    1,
                                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                ))
                                record_audit_event(
                                    "user_created",
                                    "app_user",
                                    username.strip(),
                                    {"role": role, "employee_id": employee_id},
                                )
                                pb_success(f"Created user {username}. They must change the password at first login.")
                                refresh()
                        except Exception as e:
                            pb_error(f"Could not create user: {e}")

    with tab_edit:
        st.subheader("Edit / Disable User")
        users_df = df_query("""
            SELECT u.id, u.username, u.role, u.employee_id, u.active, u.notes,
                   COALESCE(e.name, '') AS employee_name
            FROM app_users u
            LEFT JOIN employees e ON e.id = u.employee_id
            ORDER BY u.username
        """)

        if users_df.empty:
            st.info("No users.")
        else:
            user_map = {row["username"]: int(row["id"]) for _, row in users_df.iterrows()}
            selected_username = st.selectbox("Select User", list(user_map.keys()))
            selected_user_id = user_map[selected_username]
            current = users_df[users_df["id"] == selected_user_id].iloc[0]

            current_employee = str(current["employee_name"] or "Not linked")
            employee_index = employee_labels.index(current_employee) if current_employee in employee_labels else 0
            roles = ["employee", "manager", "admin"]
            role_index = roles.index(str(current["role"])) if str(current["role"]) in roles else 0
            active_options = ["Active", "Inactive"]
            active_index = 0 if int(current["active"] or 0) == 1 else 1

            with st.form("edit_user_form"):
                username = st.text_input("Username", value=str(current["username"]))
                new_password = st.text_input("New Password (leave blank to keep current)", type="password")
                role = st.selectbox("Role", roles, index=role_index)
                employee_label = st.selectbox("Link to Employee", employee_labels, index=employee_index)
                active_label = st.selectbox("Status", active_options, index=active_index)
                notes = st.text_area("Notes", value=str(current["notes"] or ""))
                submitted = st.form_submit_button("Update User")

                if submitted:
                    employee_id = employee_options.get(employee_label) if employee_label != "Not linked" else None
                    active = 1 if active_label == "Active" else 0

                    password_errors = (
                        password_strength_errors(new_password, username)
                        if new_password
                        else []
                    )
                    if password_errors:
                        for error in password_errors:
                            pb_error(error)
                    else:
                        success, message = safe_update_user_account(
                            selected_user_id=selected_user_id,
                            username=username,
                            role=role,
                            employee_id=employee_id,
                            active=active,
                            notes=notes,
                        )

                        if success:
                            if new_password:
                                execute("""
                                    UPDATE app_users
                                    SET password_hash = ?, must_change_password = 1,
                                        failed_login_count = 0, locked_until = '',
                                        password_changed_at = ?
                                    WHERE id = ?
                                """, (
                                    hash_password(new_password),
                                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    selected_user_id,
                                ))
                                record_audit_event(
                                    "password_reset_by_admin",
                                    "app_user",
                                    selected_user_id,
                                )
                            pb_success(message)
                            refresh()
                        else:
                            pb_error(message)

            st.markdown("### Delete User Account")
            st.warning(
                "This deletes the selected login account and will also delete the linked employee record where safe. "
                "If the employee has wages, timesheets or job history, they will be marked Inactive instead."
            )

            admin_count_df = df_query("""
                SELECT COUNT(*) AS 'count'
                FROM app_users
                WHERE role = 'admin' AND active = 1
            """)
            active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0

            current_user = get_current_user() or {}
            selected_is_current_user = int(current_user.get("id", -1)) == int(selected_user_id)
            selected_is_last_active_admin = (
                str(current["role"]) == "admin"
                and int(current["active"] or 0) == 1
                and active_admin_count <= 1
            )

            delete_confirm = st.text_input(
                "To delete this user login, type: DELETE USER",
                key=f"delete_user_confirm_{selected_user_id}"
            )

            if st.button("Delete Selected User Account", key=f"delete_user_button_{selected_user_id}"):
                if delete_confirm.strip().upper() != "DELETE USER":
                    pb_error("Type DELETE USER exactly before deleting this account.")
                elif selected_is_current_user:
                    pb_error("You cannot delete the account you are currently logged in with.")
                elif selected_is_last_active_admin:
                    pb_error("You cannot delete the last active admin account. Create another admin first, then delete this one.")
                else:
                    result = delete_user_and_linked_employee(selected_user_id)

                    if result["deleted_users"]:
                        pb_success(f"Deleted {result['deleted_users']} user login account(s).")

                    if result["deleted_employee"]:
                        pb_success(f"Deleted {result['deleted_employee']} linked employee record(s).")

                    if result["deactivated_employee"]:
                        st.info(f"Marked {result['deactivated_employee']} linked employee(s) as Inactive because they had job history or other linked records.")

                    if result["skipped"]:
                        st.warning(f"Skipped {result['skipped']} item(s).")

                    with st.expander("Delete details"):
                        for msg in result["messages"]:
                            st.write(msg)

                    refresh()

            st.markdown("### Unlink Employee From This User")
            st.caption("Use this if this login is incorrectly linked to the wrong employee.")
            if st.button("Unlink Employee From Selected User", key=f"unlink_employee_user_{selected_user_id}"):
                execute("UPDATE app_users SET employee_id = NULL WHERE id = ?", (selected_user_id,))
                pb_success("Employee link removed from this user account.")
                refresh()

    st.markdown("### Start Fresh / Clear All Jobs")
    st.warning(
        "This permanently deletes all jobs and all job-linked data, including materials, wages, "
        "equipment checklist records and imported checklist materials. Builders, employees, products, "
        "users and checklist item templates will stay."
    )
    clear_confirm = st.text_input("To clear all jobs, type: CLEAR JOBS", key="clear_jobs_confirm")
    if st.button("Clear All Jobs and Start at 0"):
        if clear_confirm.strip().upper() != "CLEAR JOBS":
            pb_error("Type CLEAR JOBS exactly before clearing the job register.")
        else:
            clear_all_jobs_and_linked_data()
            pb_success("All jobs and job-linked data have been cleared. Job Register is now at 0.")
            refresh()


    with tab_list:
        st.subheader("User List")

        users_df = df_query("""
            SELECT u.id AS 'ID',
                   u.username AS 'Username',
                   u.role AS 'Role',
                   COALESCE(e.name, '') AS 'Linked Employee',
                   CASE WHEN u.active = 1 THEN 'Active' ELSE 'Inactive' END AS 'Status',
                   u.notes AS 'Notes'
            FROM app_users u
            LEFT JOIN employees e ON e.id = u.employee_id
            ORDER BY u.role, u.username, u.id
        """)

        if users_df.empty:
            st.info("No user accounts found.")
        else:
            st.dataframe(users_df, width="stretch", hide_index=True)

            st.markdown("### Remove Multiple User Accounts")
            st.warning(
                "This deletes selected user login accounts. If a selected login is linked to an employee, "
                "the linked employee will also be deleted where safe. If that employee has wages/timesheets, "
                "they will be marked Inactive instead to protect history."
            )

            delete_options = {
                f"{row['Username']} | {row['Role']} | {row['Linked Employee'] or 'No Employee'} | {row['Status']} | ID {row['ID']}": int(row["ID"])
                for _, row in users_df.iterrows()
            }

            selected_delete_labels = st.multiselect(
                "Select user login accounts to delete",
                list(delete_options.keys()),
                key="bulk_user_delete_multiselect"
            )

            selected_delete_ids = [delete_options[label] for label in selected_delete_labels]

            if selected_delete_ids:
                selected_preview = users_df[users_df["ID"].astype(int).isin(selected_delete_ids)]
                st.markdown("Selected accounts:")
                st.dataframe(selected_preview, width="stretch", hide_index=True)

            bulk_confirm = st.text_input(
                "To delete the selected user login accounts, type: DELETE SELECTED USERS",
                key="bulk_user_delete_confirm"
            )

            if st.button("Delete Selected User Accounts", key="bulk_user_delete_button"):
                if not selected_delete_ids:
                    pb_error("Select at least one user account first.")
                elif bulk_confirm.strip().upper() != "DELETE SELECTED USERS":
                    pb_error("Type DELETE SELECTED USERS exactly before deleting multiple accounts.")
                else:
                    result = delete_selected_user_accounts(selected_delete_ids)

                    if result["deleted_users"]:
                        pb_success(f"Deleted {result['deleted_users']} selected user login account(s).")

                    if result["deleted_employee"]:
                        pb_success(f"Deleted {result['deleted_employee']} linked employee record(s).")

                    if result["deactivated_employee"]:
                        st.info(f"Marked {result['deactivated_employee']} linked employee(s) as Inactive because they had job history or other linked records.")

                    if result["skipped"]:
                        st.warning(f"Skipped {result['skipped']} item(s).")

                    with st.expander("Deletion details"):
                        for msg in result["messages"]:
                            st.write(msg)

                    refresh()

    st.divider()
    st.markdown("### Security and Change Audit")
    audit_tab, login_tab = st.tabs(["Application Changes", "Login Activity"])
    with audit_tab:
        audit_df = df_query("""
            SELECT created_at AS 'Time',
                   username AS 'User',
                   action AS 'Action',
                   entity_type AS 'Record Type',
                   entity_id AS 'Record ID',
                   details AS 'Details'
            FROM audit_events
            ORDER BY created_at DESC, id DESC
            LIMIT 500
        """)
        if audit_df.empty:
            st.info("No application audit events have been recorded yet.")
        else:
            st.dataframe(audit_df, width="stretch", hide_index=True)
    with login_tab:
        login_audit_df = df_query("""
            SELECT created_at AS 'Time',
                   username AS 'Username',
                   CASE WHEN success = 1 THEN 'Success' ELSE 'Failed' END AS 'Result',
                   reason AS 'Reason'
            FROM login_audit_events
            ORDER BY created_at DESC, id DESC
            LIMIT 500
        """)
        if login_audit_df.empty:
            st.info("No login events have been recorded yet.")
        else:
            st.dataframe(login_audit_df, width="stretch", hide_index=True)



def mark_seeded_if_existing_data_present():
    try:
        if starter_data_already_seeded():
            return

        conn = connect()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM jobs")
        job_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM builders_clients")
        builder_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM employees")
        employee_count = cur.fetchone()[0]

        # If this database already has data, assume starter data has already been seeded.
        # This stops old/deleted jobs reappearing on first run after this update.
        if job_count > 0 or builder_count > 0 or employee_count > 0:
            cur.execute("""
                INSERT INTO app_settings (setting_key, setting_value)
                VALUES (?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value
            """, ("starter_data_seeded", "yes"))
            conn.commit()

        conn.close()
    except Exception:
        pass



def clear_all_jobs_and_linked_data():
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, job_no FROM jobs ORDER BY id")
        jobs_to_delete = [(int(row[0]), str(row[1] or "")) for row in cur.fetchall()]
        for job_id, _job_number in jobs_to_delete:
            _delete_job_rows(cur, job_id)
        cur.execute("""
            INSERT INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value
        """, ("starter_data_seeded", "yes"))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    file_archives = [
        _archive_deleted_job_files(job_number)
        for _job_id, job_number in jobs_to_delete
    ]
    record_audit_event(
        "all_jobs_permanently_deleted",
        "job_register",
        "",
        {
            "jobs_deleted": len(jobs_to_delete),
            "file_archives": [value for value in file_archives if value],
        },
    )
    return len(jobs_to_delete)



# =============================
# JOB PHOTO HELPERS
# =============================
def safe_file_name(name):
    name = str(name or "photo").strip()
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    return name[:120]


def get_job_no_for_id(job_id):
    df = df_query("SELECT job_no FROM jobs WHERE id = ?", (job_id,))
    if df.empty:
        return f"job_{job_id}"
    return str(df.iloc[0]["job_no"] or f"job_{job_id}")


def save_photo_to_job_folder(job_id, uploaded_file, max_size=(1600, 1600), quality=80):
    if uploaded_file_size(uploaded_file) > MAX_PHOTO_UPLOAD_BYTES:
        raise ValueError("Photo is larger than the 15 MB upload limit.")
    uploaded_file.seek(0)
    try:
        with Image.open(uploaded_file) as image_probe:
            # Some phones store portrait/live-photo JPEGs as an MPO container even
            # though the filename ends in .jpg or .jpeg.  Pillow can safely read
            # the first frame, so accept it and normalise it to a standard JPEG.
            if image_probe.format not in {"JPEG", "MPO", "PNG", "WEBP"}:
                raise ValueError(
                    "Only genuine JPEG/JPG (including phone MPO), PNG or WebP images are accepted."
                )
            if int(image_probe.width) * int(image_probe.height) > MAX_IMAGE_PIXELS:
                raise ValueError("The image dimensions are too large to process safely.")
            image_probe.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("The image dimensions are too large to process safely.") from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The uploaded file is not a valid supported image.") from exc
    uploaded_file.seek(0)

    job_no = get_job_no_for_id(job_id)

    job_folder = get_job_folder(job_no)
    photos_folder = os.path.join(job_folder, "photos")
    os.makedirs(photos_folder, exist_ok=True)

    image = Image.open(uploaded_file)
    try:
        image.seek(0)
    except (EOFError, AttributeError):
        pass
    image.load()
    image = ImageOps.exif_transpose(image)

    if image.mode not in ["RGB", "L"]:
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")

    image.thumbnail(max_size)

    original_name = safe_file_name(uploaded_file.name)
    base_name = os.path.splitext(original_name)[0]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    file_name = f"{timestamp}_{base_name}.jpg"
    file_path = os.path.join(photos_folder, file_name)

    image.save(file_path, format="JPEG", quality=quality, optimize=True)

    return file_path, "image/jpeg"


def resolve_trusted_storage_file(file_path):
    resolved = Path(str(file_path or "")).resolve()
    allowed_roots = [Path(JOB_FILES_DIR).resolve(), Path(PHOTOS_DIR).resolve()]
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise ValueError("Stored file path is outside JobHub storage.")
    return resolved


def photo_data_to_bytes(photo_data):
    """
    Supports both:
    - old photos saved as base64 in database
    - new photos saved as files with FILEPATH:/var/data/...
    """
    if not photo_data:
        return b""

    photo_data = str(photo_data)

    if photo_data.startswith("FILEPATH:"):
        file_path = photo_data.replace("FILEPATH:", "", 1)
        trusted_path = resolve_trusted_storage_file(file_path)
        with open(trusted_path, "rb") as f:
            return f.read()

    return base64.b64decode(photo_data.encode("utf-8"))


def save_job_photo(job_id, uploaded_file, category, caption, notes):
    uploaded_by = ""
    try:
        user = get_current_user()
        if user:
            uploaded_by = user.get("username", "")
    except Exception:
        uploaded_by = ""

    file_path, photo_type = save_photo_to_job_folder(job_id, uploaded_file)

    execute("""
        INSERT INTO job_photos
        (job_id, photo_name, photo_type, photo_data, category, caption, uploaded_by, uploaded_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        uploaded_file.name,
        photo_type,
        f"FILEPATH:{file_path}",
        category,
        caption,
        uploaded_by,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        notes,
    ))


def delete_job_photo(photo_id):
    try:
        photo_df = df_query("SELECT photo_data FROM job_photos WHERE id = ?", (photo_id,))
        if not photo_df.empty:
            photo_data = str(photo_df.iloc[0]["photo_data"] or "")
            if photo_data.startswith("FILEPATH:"):
                file_path = resolve_trusted_storage_file(photo_data.replace("FILEPATH:", "", 1))
                if file_path.exists():
                    file_path.unlink()
    except Exception:
        pass

    execute("DELETE FROM job_photos WHERE id = ?", (photo_id,))


def job_photos_page(employee_restricted=False):
    st.header("Job Photos")
    st.caption("Upload photos against a specific job. Photos will appear in Job Pack reports.")

    if employee_restricted:
        current_user = get_current_user() or {}
        job_options = get_employee_job_options(current_user.get("employee_id"))
    else:
        job_options = get_job_options()

    if not job_options:
        st.info(
            "No assigned jobs are available."
            if employee_restricted
            else "Create a job first, then upload photos."
        )
        return

    tab_upload, tab_view = st.tabs(["Upload Photos", "View / Delete Photos"])

    with tab_upload:
        st.subheader("Upload Job Photos")

        with st.form("upload_job_photos_form"):
            selected_job = st.selectbox("Select Job", list(job_options.keys()), key="photo_upload_job")
            category = st.selectbox(
                "Photo Category",
                [
                    "Before",
                    "During Works",
                    "After",
                    "Defect / Damage",
                    "Access / Safety",
                    "Materials",
                    "Equipment",
                    "Completion / Sign-off",
                    "Other",
                ],
            )
            caption = st.text_input("Caption / Description")
            notes = st.text_area("Notes")
            uploaded_files = st.file_uploader(
                "Upload photos",
                type=["jpg", "jpeg", "jfif", "png", "webp"],
                accept_multiple_files=True,
            )
            submitted = st.form_submit_button("Save Photos to Job")

            if submitted:
                if not uploaded_files:
                    pb_error("Please select at least one photo.")
                else:
                    saved_count = 0
                    for uploaded_file in uploaded_files:
                        try:
                            save_job_photo(
                                job_id=job_options[selected_job],
                                uploaded_file=uploaded_file,
                                category=category,
                                caption=caption,
                                notes=notes,
                            )
                            saved_count += 1
                        except Exception as e:
                            pb_error(f"Could not save {uploaded_file.name}: {e}")

                    if saved_count:
                        pb_success(f"{saved_count} {'photo was' if saved_count == 1 else 'photos were'} successfully added to {selected_job}.")
                        refresh()

    with tab_view:
        st.subheader("View Job Photos")

        selected_job = st.selectbox("Select Job", list(job_options.keys()), key="photo_view_job")
        selected_job_id = job_options[selected_job]

        photos_df = df_query("""
            SELECT id, photo_name, photo_type, photo_data, category, caption, uploaded_by, uploaded_at, notes
            FROM job_photos
            WHERE job_id = ?
            ORDER BY uploaded_at DESC, id DESC
        """, (selected_job_id,))

        if photos_df.empty:
            st.info("No photos saved for this job.")
        else:
            for _, row in photos_df.iterrows():
                photo_id = int(row["id"])
                caption = str(row["caption"] or "")
                category = str(row["category"] or "")
                uploaded_at = str(row["uploaded_at"] or "")
                uploaded_by = str(row["uploaded_by"] or "")
                notes = str(row["notes"] or "")

                st.markdown(f"### {category} - {caption if caption else row['photo_name']}")
                try:
                    st.image(photo_data_to_bytes(row["photo_data"]), width="stretch")
                except Exception:
                    st.warning("Could not display this photo.")

                st.caption(f"Uploaded: {uploaded_at} by {uploaded_by}")
                if notes:
                    st.write(notes)

                if not employee_restricted:
                    delete_confirm = st.checkbox(f"Delete this photo", key=f"delete_photo_confirm_{photo_id}")
                    if st.button("Delete Photo", key=f"delete_photo_{photo_id}"):
                        if not delete_confirm:
                            pb_error("Tick the delete checkbox first.")
                        else:
                            delete_job_photo(photo_id)
                            pb_success("Photo deleted.")
                            refresh()

                st.divider()



# =============================
# TIMESHEETS
# =============================
def calculate_hours_from_times(start_time, finish_time, break_minutes):
    try:
        return calculate_shift_hours(start_time, finish_time, break_minutes)
    except ValueError:
        return 0.0


def review_acceptance_checkbox(key_prefix, payload, label):
    """Reset confirmation whenever any reviewed value changes."""
    fingerprint = hashlib.sha256(
        json.dumps(payload, default=str, sort_keys=True).encode("utf-8")
    ).hexdigest()
    fingerprint_key = f"{key_prefix}_review_fingerprint"
    accepted_key = f"{key_prefix}_review_accepted"
    if st.session_state.get(fingerprint_key) != fingerprint:
        st.session_state[fingerprint_key] = fingerprint
        st.session_state[accepted_key] = False
    return st.checkbox(label, key=accepted_key)


def save_timesheet_entry(job_id, employee_id, work_date, start_time, finish_time, break_minutes, total_hours, work_type, notes):
    user = get_current_user() or {}
    submitted_by = user.get("username", "")
    submitted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = connect()
    try:
        cur = conn.cursor()
        insert_sql = """
            INSERT INTO timesheet_entries
            (job_id, employee_id, work_date, start_time, finish_time, break_minutes, total_hours,
             work_type, submitted_by, submitted_at, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        if USE_POSTGRES:
            insert_sql += " RETURNING id"
        cur.execute(insert_sql, (
            job_id,
            employee_id,
            work_date,
            start_time,
            finish_time,
            break_minutes,
            total_hours,
            work_type,
            submitted_by,
            submitted_at,
            "Submitted",
            notes,
        ))
        timesheet_id = int(cur.fetchone()[0]) if USE_POSTGRES else int(cur.lastrowid)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    record_audit_event(
        "timesheet_submitted",
        "timesheet",
        timesheet_id,
        {"job_id": job_id, "employee_id": employee_id, "hours": total_hours},
    )
    try:
        summary = df_query("""
            SELECT j.job_no, j.job_name, e.name AS employee_name
            FROM jobs j
            JOIN employees e ON e.id = ?
            WHERE j.id = ?
        """, (int(employee_id), int(job_id)))
        if summary.empty:
            employee_label = f"Employee #{employee_id}"
            job_label = f"Job #{job_id}"
        else:
            row = summary.iloc[0]
            employee_label = str(row["employee_name"] or f"Employee #{employee_id}")
            job_label = f"{row['job_no']} - {row['job_name']}".strip(" -")
        create_management_notifications(
            "timesheet_submitted",
            "New timesheet submitted",
            f"{employee_label} submitted {float(total_hours or 0):g} hours for {job_label} on {work_date}.",
            job_id=job_id,
            entity_type="timesheet",
            entity_id=timesheet_id,
        )
    except Exception:
        pass
    return timesheet_id


def set_timesheet_status(timesheet_id, status):
    """Approve/pay/reject a timesheet and keep its labour-cost posting consistent."""
    status = str(status or "").title()
    if status not in {"Approved", "Paid", "Rejected"}:
        raise ValueError("Unsupported timesheet status.")

    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT t.job_id, t.employee_id, t.work_date, t.start_time, t.finish_time,
                   t.break_minutes, t.total_hours, t.notes,
                   COALESCE(e.rate_plus_10, e.base_hourly_rate, 0)
            FROM timesheet_entries t
            JOIN employees e ON e.id = t.employee_id
            WHERE t.id = ?
        """, (int(timesheet_id),))
        row = cur.fetchone()
        if not row:
            raise ValueError("Timesheet not found.")

        job_id, employee_id, work_date, start_time, finish_time, break_minutes, total_hours, notes, hourly_rate = row
        user = get_current_user() or {}
        approved_by = str(user.get("username") or "")
        approved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            UPDATE timesheet_entries
            SET status = ?, approved_by = ?, approved_at = ?
            WHERE id = ?
        """, (status, approved_by, approved_at, int(timesheet_id)))

        if status in {"Approved", "Paid"}:
            cur.execute("""
                INSERT INTO wage_entries
                (job_id, employee_id, work_date, hours, notes, timesheet_id,
                 hourly_rate_snapshot, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(timesheet_id) DO UPDATE SET
                    job_id = excluded.job_id,
                    employee_id = excluded.employee_id,
                    work_date = excluded.work_date,
                    hours = excluded.hours,
                    notes = excluded.notes,
                    hourly_rate_snapshot = excluded.hourly_rate_snapshot,
                    source = excluded.source
            """, (
                int(job_id),
                int(employee_id),
                work_date,
                float(total_hours or 0),
                f"Approved timesheet {timesheet_id}: {start_time}-{finish_time}, "
                f"break {break_minutes} min. {notes or ''}".strip(),
                int(timesheet_id),
                float(hourly_rate or 0),
                "Approved Timesheet",
            ))
        else:
            cur.execute("DELETE FROM wage_entries WHERE timesheet_id = ?", (int(timesheet_id),))

        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    record_audit_event(
        f"timesheet_{status.casefold()}",
        "timesheet",
        timesheet_id,
        {"hours": total_hours, "hourly_rate_snapshot": hourly_rate},
    )


def update_timesheet_entry(
    timesheet_id,
    job_id,
    employee_id,
    work_date,
    start_time,
    finish_time,
    break_minutes,
    total_hours,
    work_type,
    notes,
):
    """Edit a timesheet and transactionally keep its wage posting consistent."""
    timesheet_id = int(timesheet_id)
    job_id = int(job_id)
    employee_id = int(employee_id)
    work_date = str(work_date)
    start_time = str(start_time)
    finish_time = str(finish_time)
    break_minutes = int(break_minutes)
    total_hours = float(total_hours)
    work_type = str(work_type or "")
    notes = str(notes or "")

    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT job_id, employee_id, work_date, start_time, finish_time,
                   break_minutes, total_hours, work_type,
                   COALESCE(status, 'Submitted'), notes
            FROM timesheet_entries
            WHERE id = ?
        """, (timesheet_id,))
        original = cur.fetchone()
        if not original:
            raise ValueError("Timesheet not found.")

        (
            original_job_id,
            original_employee_id,
            original_work_date,
            original_start_time,
            original_finish_time,
            original_break_minutes,
            original_total_hours,
            original_work_type,
            original_status,
            original_notes,
        ) = original

        cur.execute("""
            SELECT id
            FROM timesheet_entries
            WHERE job_id = ?
              AND employee_id = ?
              AND work_date = ?
              AND start_time = ?
              AND finish_time = ?
              AND id <> ?
              AND COALESCE(status, 'Submitted') <> 'Rejected'
            LIMIT 1
        """, (
            job_id,
            employee_id,
            work_date,
            start_time,
            finish_time,
            timesheet_id,
        ))
        if cur.fetchone():
            raise ValueError(
                "A matching timesheet already exists for this employee, job, date and shift."
            )

        status = str(original_status or "Submitted").title()
        if status == "Processed":
            status = "Paid"

        cur.execute("""
            UPDATE timesheet_entries
            SET job_id = ?, employee_id = ?, work_date = ?,
                start_time = ?, finish_time = ?, break_minutes = ?,
                total_hours = ?, work_type = ?, notes = ?, status = ?
            WHERE id = ?
        """, (
            job_id,
            employee_id,
            work_date,
            start_time,
            finish_time,
            break_minutes,
            total_hours,
            work_type,
            notes,
            status,
            timesheet_id,
        ))

        hourly_rate_snapshot = None
        if status in {"Approved", "Paid"}:
            cur.execute("""
                SELECT hourly_rate_snapshot
                FROM wage_entries
                WHERE timesheet_id = ?
                LIMIT 1
            """, (timesheet_id,))
            existing_wage = cur.fetchone()

            if (
                int(original_employee_id) == employee_id
                and existing_wage
                and existing_wage[0] is not None
            ):
                hourly_rate_snapshot = float(existing_wage[0])
            else:
                cur.execute("""
                    SELECT COALESCE(rate_plus_10, base_hourly_rate, 0)
                    FROM employees
                    WHERE id = ?
                """, (employee_id,))
                employee_rate = cur.fetchone()
                hourly_rate_snapshot = float(employee_rate[0] or 0) if employee_rate else 0.0

            cur.execute("""
                INSERT INTO wage_entries
                (job_id, employee_id, work_date, hours, notes, timesheet_id,
                 hourly_rate_snapshot, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(timesheet_id) DO UPDATE SET
                    job_id = excluded.job_id,
                    employee_id = excluded.employee_id,
                    work_date = excluded.work_date,
                    hours = excluded.hours,
                    notes = excluded.notes,
                    hourly_rate_snapshot = excluded.hourly_rate_snapshot,
                    source = excluded.source
            """, (
                job_id,
                employee_id,
                work_date,
                total_hours,
                f"Edited {status.lower()} timesheet {timesheet_id}: "
                f"{start_time}-{finish_time}, break {break_minutes} min. {notes}".strip(),
                timesheet_id,
                hourly_rate_snapshot,
                "Edited Timesheet",
            ))
        else:
            cur.execute(
                "DELETE FROM wage_entries WHERE timesheet_id = ?",
                (timesheet_id,),
            )

        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    before = {
        "job_id": original_job_id,
        "employee_id": original_employee_id,
        "work_date": original_work_date,
        "start_time": original_start_time,
        "finish_time": original_finish_time,
        "break_minutes": original_break_minutes,
        "total_hours": original_total_hours,
        "work_type": original_work_type,
        "notes": original_notes,
        "status": original_status,
    }
    after = {
        "job_id": job_id,
        "employee_id": employee_id,
        "work_date": work_date,
        "start_time": start_time,
        "finish_time": finish_time,
        "break_minutes": break_minutes,
        "total_hours": total_hours,
        "work_type": work_type,
        "notes": notes,
        "status": status,
        "hourly_rate_snapshot": hourly_rate_snapshot,
    }
    record_audit_event(
        "timesheet_edited",
        "timesheet",
        timesheet_id,
        {"before": before, "after": after},
    )


def delete_timesheet_entry(timesheet_id):
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM wage_entries WHERE timesheet_id = ?", (int(timesheet_id),))
        cur.execute("DELETE FROM timesheet_entries WHERE id = ?", (int(timesheet_id),))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
    record_audit_event("timesheet_deleted", "timesheet", timesheet_id)




# PB_JOBHUB_BULK_TIMESHEET_STATUS_FILTERS_V1
TIMESHEET_STATUS_OPTIONS = ["All", "Submitted", "Approved", "Rejected", "Paid"]


def normalise_timesheet_statuses(timesheet_df):
    """Return a copy with blank/null timesheet statuses treated as Submitted."""
    work = timesheet_df.copy()
    if "Status" in work.columns:
        work["Status"] = (
            work["Status"]
            .fillna("Submitted")
            .astype(str)
            .str.strip()
            .replace("", "Submitted")
            .str.title()
        )
    return work


def filter_timesheets_by_status(timesheet_df, selected_status):
    work = normalise_timesheet_statuses(timesheet_df)
    if selected_status != "All" and "Status" in work.columns:
        work = work[work["Status"].astype(str) == selected_status]
    return work.reset_index(drop=True)


def timesheet_status_filter(label, key, default="All"):
    default_index = (
        TIMESHEET_STATUS_OPTIONS.index(default)
        if default in TIMESHEET_STATUS_OPTIONS
        else 0
    )
    return st.selectbox(
        label,
        TIMESHEET_STATUS_OPTIONS,
        index=default_index,
        key=key,
    )


def render_timesheet_edit_form(timesheet_id, key_prefix):
    """Render a reviewed edit form for one existing timesheet."""
    timesheet = df_query("""
        SELECT t.id, t.job_id, t.employee_id, t.work_date, t.start_time,
               t.finish_time, t.break_minutes, t.total_hours, t.work_type,
               COALESCE(t.status, 'Submitted') AS status, t.notes,
               j.job_no, j.job_name, e.name AS employee_name
        FROM timesheet_entries t
        JOIN jobs j ON j.id = t.job_id
        JOIN employees e ON e.id = t.employee_id
        WHERE t.id = ?
    """, (int(timesheet_id),))
    if timesheet.empty:
        pb_error("The selected timesheet could not be found.")
        return

    row = timesheet.iloc[0]
    job_options = get_job_options()
    employee_options = get_employee_options(active_only=False)
    if not job_options or not employee_options:
        pb_error("Jobs and employees must be available before a timesheet can be edited.")
        return

    def option_index(options, selected_id):
        labels = list(options.keys())
        matching = [
            index for index, label in enumerate(labels)
            if int(options[label]) == int(selected_id)
        ]
        return matching[0] if matching else 0

    def stored_time(value, fallback):
        text_value = str(value or "").strip()
        for time_format in ("%H:%M:%S", "%H:%M"):
            try:
                return datetime.strptime(text_value, time_format).time()
            except ValueError:
                continue
        return fallback

    parsed_date = pd.to_datetime(row["work_date"], errors="coerce")
    current_date = date.today() if pd.isna(parsed_date) else parsed_date.date()
    current_start = stored_time(row["start_time"], time(7, 0))
    current_finish = stored_time(row["finish_time"], time(15, 0))
    current_break = int(float(row["break_minutes"] or 0))
    current_work_type = str(row["work_type"] or "Painting")
    work_types = [
        "Painting", "Prep", "Spraying", "Touch-ups",
        "Travel", "Site Setup", "Other",
    ]
    if current_work_type not in work_types:
        work_types.append(current_work_type)

    st.markdown(f"### Edit Timesheet #{int(timesheet_id)}")
    st.caption(
        f"Current status: {row['status']}. Approved or paid edits automatically "
        "update the linked actual labour-cost posting."
    )

    job_labels = list(job_options.keys())
    employee_labels = list(employee_options.keys())
    selected_job = st.selectbox(
        "Job",
        job_labels,
        index=option_index(job_options, row["job_id"]),
        key=f"{key_prefix}_edit_job_{timesheet_id}",
    )
    selected_employee = st.selectbox(
        "Employee",
        employee_labels,
        index=option_index(employee_options, row["employee_id"]),
        key=f"{key_prefix}_edit_employee_{timesheet_id}",
    )

    date_col, start_col, finish_col, break_col = st.columns(4)
    edited_date = date_col.date_input(
        "Date",
        value=current_date,
        key=f"{key_prefix}_edit_date_{timesheet_id}",
    )
    edited_start = start_col.time_input(
        "Start Time",
        value=current_start,
        step=timedelta(minutes=15),
        key=f"{key_prefix}_edit_start_{timesheet_id}",
    )
    edited_finish = finish_col.time_input(
        "Finish Time",
        value=current_finish,
        step=timedelta(minutes=15),
        key=f"{key_prefix}_edit_finish_{timesheet_id}",
    )
    edited_break = int(break_col.number_input(
        "Break Minutes",
        min_value=0,
        max_value=300,
        step=15,
        value=current_break,
        key=f"{key_prefix}_edit_break_{timesheet_id}",
    ))

    calculation_error = ""
    try:
        edited_hours = calculate_shift_hours(
            edited_start,
            edited_finish,
            edited_break,
        )
    except ValueError as exc:
        edited_hours = 0.0
        calculation_error = str(exc)

    work_type_index = work_types.index(current_work_type)
    edited_work_type = st.selectbox(
        "Work Type",
        work_types,
        index=work_type_index,
        key=f"{key_prefix}_edit_work_type_{timesheet_id}",
    )
    edited_notes = st.text_area(
        "Notes",
        value=str(row["notes"] or ""),
        key=f"{key_prefix}_edit_notes_{timesheet_id}",
    )

    st.metric("Recalculated Hours", f"{edited_hours:.2f}")
    if calculation_error:
        pb_error(calculation_error)

    edit_payload = {
        "timesheet_id": int(timesheet_id),
        "job_id": int(job_options[selected_job]),
        "employee_id": int(employee_options[selected_employee]),
        "date": edited_date.isoformat(),
        "start": edited_start.strftime("%H:%M"),
        "finish": edited_finish.strftime("%H:%M"),
        "break_minutes": edited_break,
        "hours": edited_hours,
        "work_type": edited_work_type,
        "notes": edited_notes,
        "status": str(row["status"]),
    }

    st.markdown("#### Review Changes")
    st.dataframe(
        pd.DataFrame([{
            "Job": selected_job,
            "Employee": selected_employee,
            "Date": edited_date.isoformat(),
            "Start": edited_start.strftime("%H:%M"),
            "Finish": edited_finish.strftime("%H:%M"),
            "Break": f"{edited_break} min",
            "Hours": f"{edited_hours:.2f}",
            "Work Type": edited_work_type,
            "Status": str(row["status"]),
            "Notes": edited_notes,
        }]),
        width="stretch",
        hide_index=True,
    )
    accepted = review_acceptance_checkbox(
        f"{key_prefix}_edit_{timesheet_id}",
        edit_payload,
        (
            "I have reviewed these changes and accept that approved or paid "
            "labour-cost postings will also be updated."
        ),
    )

    save_col, cancel_col = st.columns(2)
    if save_col.button(
        "Save Timesheet Changes",
        key=f"{key_prefix}_edit_save_{timesheet_id}",
        type="primary",
        disabled=not accepted or edited_hours <= 0 or bool(calculation_error),
    ):
        try:
            update_timesheet_entry(
                timesheet_id,
                job_options[selected_job],
                employee_options[selected_employee],
                edited_date.isoformat(),
                edited_start.strftime("%H:%M"),
                edited_finish.strftime("%H:%M"),
                edited_break,
                edited_hours,
                edited_work_type,
                edited_notes,
            )
        except Exception as exc:
            pb_error(str(exc))
        else:
            st.session_state.pop(f"{key_prefix}_active_edit_id", None)
            st.session_state.pop(
                f"{key_prefix}_edit_{timesheet_id}_review_fingerprint",
                None,
            )
            st.session_state.pop(
                f"{key_prefix}_edit_{timesheet_id}_review_accepted",
                None,
            )
            pb_success(f"Timesheet #{int(timesheet_id)} updated successfully.")
            refresh()

    if cancel_col.button(
        "Cancel Editing",
        key=f"{key_prefix}_edit_cancel_{timesheet_id}",
    ):
        st.session_state.pop(f"{key_prefix}_active_edit_id", None)
        st.session_state.pop(
            f"{key_prefix}_edit_{timesheet_id}_review_fingerprint",
            None,
        )
        st.session_state.pop(
            f"{key_prefix}_edit_{timesheet_id}_review_accepted",
            None,
        )
        refresh()


def render_timesheet_bulk_actions(timesheet_df, key_prefix, empty_message="No timesheets match this selection."):
    """Render a checkbox column and safe bulk status/delete actions."""
    if timesheet_df.empty:
        st.info(empty_message)
        return []

    work = normalise_timesheet_statuses(timesheet_df)
    if "id" not in work.columns:
        pb_error("Timesheet IDs are unavailable, so bulk actions cannot be performed.")
        return []

    table_fingerprint = hashlib.sha256(
        json.dumps(
            work[["id", "Status"]].to_dict(orient="records"),
            default=str,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]

    selector_df = work.copy()
    selector_df.insert(0, "Select", False)
    ordered_columns = ["Select"] + [column for column in selector_df.columns if column != "Select"]

    edited_df = st.data_editor(
        selector_df[ordered_columns],
        width="stretch",
        hide_index=True,
        disabled=[column for column in ordered_columns if column != "Select"],
        column_config={
            "Select": st.column_config.CheckboxColumn(
                "Select",
                help="Tick each timesheet to include in the bulk action.",
                default=False,
            ),
            "id": None,
        },
        key=f"{key_prefix}_checkbox_table_{table_fingerprint}",
    )

    selected_ids = (
        edited_df.loc[edited_df["Select"].fillna(False), "id"]
        .astype(int)
        .tolist()
    )

    if not selected_ids:
        st.info("Tick one or more timesheets in the Select column.")
        return []

    selected_review = work[work["id"].astype(int).isin(selected_ids)].copy()
    visible_review = selected_review.drop(columns=["id"], errors="ignore")
    selected_hours = float(visible_review["Hours"].fillna(0).sum()) if "Hours" in visible_review.columns else 0.0

    st.markdown("### Selected Timesheets Review")
    st.dataframe(visible_review, width="stretch", hide_index=True)
    st.caption(
        f"{len(selected_ids)} timesheet(s) selected, "
        f"{selected_hours:.2f} total calculated hours."
    )

    accepted_action = review_acceptance_checkbox(
        f"{key_prefix}_bulk_action",
        selected_review.to_dict(orient="records"),
        (
            "I have reviewed every selected timesheet and accept that the chosen "
            "bulk action will update statuses and labour-cost postings."
        ),
    )

    selected_fingerprint = hashlib.sha256(
        json.dumps(sorted(selected_ids)).encode("utf-8")
    ).hexdigest()
    delete_fingerprint_key = f"{key_prefix}_delete_selection_fingerprint"
    delete_confirm_key = f"{key_prefix}_delete_confirmed"
    if st.session_state.get(delete_fingerprint_key) != selected_fingerprint:
        st.session_state[delete_fingerprint_key] = selected_fingerprint
        st.session_state[delete_confirm_key] = False

    delete_confirmed = st.checkbox(
        "I understand deleting selected timesheets also removes their linked wage postings.",
        key=delete_confirm_key,
    )

    edit_col, approve_col, paid_col, reject_col, delete_col = st.columns(5)
    active_edit_key = f"{key_prefix}_active_edit_id"

    if edit_col.button(
        "Edit Selected",
        key=f"{key_prefix}_edit",
        disabled=not accepted_action or len(selected_ids) != 1,
        help="Select exactly one timesheet to edit it without deleting it.",
    ):
        st.session_state[active_edit_key] = int(selected_ids[0])

    if approve_col.button(
        "Approve Selected",
        key=f"{key_prefix}_approve",
        type="primary",
        disabled=not accepted_action,
    ):
        for timesheet_id in selected_ids:
            set_timesheet_status(timesheet_id, "Approved")
        pb_success(f"Approved {len(selected_ids)} selected timesheet(s).")
        refresh()

    if paid_col.button(
        "Mark Selected Paid",
        key=f"{key_prefix}_paid",
        disabled=not accepted_action,
    ):
        for timesheet_id in selected_ids:
            set_timesheet_status(timesheet_id, "Paid")
        pb_success(f"Marked {len(selected_ids)} selected timesheet(s) as paid.")
        refresh()

    if reject_col.button(
        "Reject Selected",
        key=f"{key_prefix}_reject",
        disabled=not accepted_action,
    ):
        for timesheet_id in selected_ids:
            set_timesheet_status(timesheet_id, "Rejected")
        st.warning(f"Rejected {len(selected_ids)} selected timesheet(s).")
        refresh()

    if delete_col.button(
        "Delete Selected",
        key=f"{key_prefix}_delete",
        disabled=not accepted_action or not delete_confirmed,
    ):
        for timesheet_id in selected_ids:
            delete_timesheet_entry(timesheet_id)
        pb_success(f"Deleted {len(selected_ids)} selected timesheet(s).")
        refresh()

    if len(selected_ids) != 1:
        st.caption("Select exactly one timesheet to use Edit Selected.")

    active_edit_id = st.session_state.get(active_edit_key)
    if active_edit_id and int(active_edit_id) in selected_ids:
        with st.container(border=True):
            render_timesheet_edit_form(int(active_edit_id), key_prefix)
    elif active_edit_id:
        st.session_state.pop(active_edit_key, None)

    return selected_ids

def timesheet_entry_form(employee_id=None, employee_restricted=False, key_prefix="timesheet"):
    job_options = (
        get_employee_job_options(employee_id)
        if employee_restricted and employee_id is not None
        else get_job_options()
    )
    if not job_options:
        st.info(
            "No assigned jobs are available for timesheet submission."
            if employee_restricted
            else "Create a job first, then timesheets can be submitted."
        )
        return

    if employee_id is None:
        employee_options = get_employee_options(active_only=True)
        if not employee_options:
            st.info("Create employees first.")
            return
    else:
        employee_options = None

    selected_job = st.selectbox(
        "Job",
        list(job_options.keys()),
        key=f"{key_prefix}_job",
    )

    if employee_restricted and employee_id is not None:
        employee_df = df_query("SELECT name FROM employees WHERE id = ?", (employee_id,))
        employee_name = (
            str(employee_df.iloc[0]["name"])
            if not employee_df.empty
            else "Current Employee"
        )
        st.text_input(
            "Employee",
            value=employee_name,
            disabled=True,
            key=f"{key_prefix}_employee_name",
        )
        selected_employee_ids = [int(employee_id)]
        selected_employee_labels = [employee_name]
    else:
        selected_employee_labels = st.multiselect(
            "Employees",
            list(employee_options.keys()),
            key=f"{key_prefix}_employee",
            help=(
                "Select one employee or a whole crew. Each selected date and the "
                "same shift details will be applied to every selected person."
            ),
        )
        selected_employee_ids = [
            int(employee_options[label])
            for label in selected_employee_labels
        ]

    date_entry_mode = st.radio(
        "Date Entry",
        ["Single Date", "Multiple Dates"],
        horizontal=True,
        key=f"{key_prefix}_date_entry_mode",
        help=(
            "Choose Multiple Dates to create one timesheet for every selected "
            "employee on every selected date."
        ),
    )

    selected_work_dates = []
    if date_entry_mode == "Single Date":
        selected_work_dates = [
            st.date_input(
                "Date",
                value=date.today(),
                key=f"{key_prefix}_date",
            )
        ]
    else:
        default_from = date.today() - timedelta(days=date.today().weekday())
        default_to = default_from + timedelta(days=4)
        date_window = st.date_input(
            "Date Range",
            value=(default_from, default_to),
            key=f"{key_prefix}_date_window",
            help="Choose the full range, then select the exact dates to submit below.",
        )

        if isinstance(date_window, (tuple, list)) and len(date_window) == 2:
            range_start, range_end = date_window
            if range_end < range_start:
                range_start, range_end = range_end, range_start
            available_dates = [
                range_start + timedelta(days=offset)
                for offset in range((range_end - range_start).days + 1)
            ]
            date_options = {
                work_date.strftime("%a %d %b %Y"): work_date
                for work_date in available_dates
            }
            weekday_defaults = [
                label
                for label, work_date in date_options.items()
                if work_date.weekday() < 5
            ]
            if not weekday_defaults:
                weekday_defaults = list(date_options.keys())

            selected_date_labels = st.multiselect(
                "Dates to Submit",
                list(date_options.keys()),
                default=weekday_defaults,
                key=(
                    f"{key_prefix}_selected_dates_"
                    f"{range_start.isoformat()}_{range_end.isoformat()}"
                ),
                help=(
                    "Weekdays are selected automatically. Add or remove any date "
                    "before reviewing the batch."
                ),
            )
            selected_work_dates = [
                date_options[label]
                for label in selected_date_labels
            ]
        else:
            st.info("Select both the start and finish date to build the date collection.")

    col1, col2, col3 = st.columns(3)
    start_time_value = col1.time_input(
        "Start Time",
        value=time(7, 0),
        step=timedelta(minutes=15),
        key=f"{key_prefix}_start",
    )
    finish_time_value = col2.time_input(
        "Finish Time",
        value=time(15, 0),
        step=timedelta(minutes=15),
        key=f"{key_prefix}_finish",
    )
    break_minutes = int(col3.number_input(
        "Break Minutes",
        min_value=0,
        max_value=300,
        step=15,
        value=0,
        key=f"{key_prefix}_break",
    ))

    start_text = start_time_value.strftime("%H:%M")
    finish_text = finish_time_value.strftime("%H:%M")
    calculation_error = ""
    try:
        total_hours = calculate_shift_hours(
            start_time_value,
            finish_time_value,
            break_minutes,
        )
    except ValueError as exc:
        total_hours = 0.0
        calculation_error = str(exc)

    overnight = finish_time_value < start_time_value
    batch_timesheet_count = len(selected_employee_ids) * len(selected_work_dates)
    metric_col1, metric_col2, metric_col3 = st.columns(3)
    metric_col1.metric("Hours Per Timesheet", f"{total_hours:.2f}")
    metric_col2.metric("Shift Type", "Overnight" if overnight else "Same day")
    metric_col3.metric("Timesheets in Batch", batch_timesheet_count)
    st.caption(
        f"Automatic calculation: {start_text} to {finish_text}, less "
        f"{break_minutes} break minutes. Total hours cannot be manually overwritten."
    )
    if calculation_error:
        pb_error(calculation_error)

    work_type = st.selectbox(
        "Work Type",
        ["Painting", "Prep", "Spraying", "Touch-ups", "Travel", "Site Setup", "Other"],
        key=f"{key_prefix}_work_type",
    )
    notes = st.text_area("Notes", key=f"{key_prefix}_notes")

    review_payload = {
        "job_id": job_options[selected_job],
        "job": selected_job,
        "employee_ids": selected_employee_ids,
        "employees": selected_employee_labels,
        "dates": [work_date.isoformat() for work_date in selected_work_dates],
        "start": start_text,
        "finish": finish_text,
        "break_minutes": break_minutes,
        "calculated_hours": total_hours,
        "work_type": work_type,
        "notes": notes,
    }

    st.markdown("### Review Timesheet Batch")
    with st.container(border=True):
        review_rows = [
            {
                "Job": selected_job,
                "Employee": employee_label,
                "Date": work_date.isoformat(),
                "Start": start_text,
                "Finish": finish_text,
                "Break": f"{break_minutes} min",
                "Calculated Hours": f"{total_hours:.2f}",
                "Work Type": work_type,
                "Notes": notes,
            }
            for work_date in selected_work_dates
            for employee_label in selected_employee_labels
        ]
        if review_rows:
            st.dataframe(
                pd.DataFrame(review_rows),
                width="stretch",
                hide_index=True,
            )
            st.caption(
                f"{len(selected_employee_ids)} staff × {len(selected_work_dates)} "
                f"date(s) = {batch_timesheet_count} timesheet(s), "
                f"{batch_timesheet_count * total_hours:.2f} total hours."
            )
        elif not selected_employee_ids:
            st.info("Select at least one employee to build the batch.")
        else:
            st.info("Select at least one date to build the batch.")

        accepted = review_acceptance_checkbox(
            key_prefix,
            review_payload,
            "I have reviewed every employee and date and accept that this batch is correct.",
        )

    submit_label = (
        "Submit Timesheet"
        if batch_timesheet_count == 1
        else f"Submit {batch_timesheet_count} Timesheets"
    )
    submitted = st.button(
        submit_label,
        key=f"{key_prefix}_submit",
        type="primary",
        disabled=(
            not selected_employee_ids
            or not selected_work_dates
            or not accepted
            or total_hours <= 0
            or bool(calculation_error)
        ),
    )
    if submitted:
        created_ids = []
        skipped = []
        for work_date_value in selected_work_dates:
            for employee_label, selected_employee_id in zip(
                selected_employee_labels,
                selected_employee_ids,
            ):
                duplicate = df_query("""
                    SELECT id
                    FROM timesheet_entries
                    WHERE job_id = ?
                      AND employee_id = ?
                      AND work_date = ?
                      AND start_time = ?
                      AND finish_time = ?
                      AND COALESCE(status, 'Submitted') <> 'Rejected'
                    LIMIT 1
                """, (
                    job_options[selected_job],
                    selected_employee_id,
                    work_date_value.isoformat(),
                    start_text,
                    finish_text,
                ))
                if not duplicate.empty:
                    skipped.append(
                        f"{employee_label} on {work_date_value.isoformat()}: "
                        "matching timesheet already exists"
                    )
                    continue
                try:
                    created_ids.append(save_timesheet_entry(
                        job_options[selected_job],
                        selected_employee_id,
                        work_date_value.isoformat(),
                        start_text,
                        finish_text,
                        break_minutes,
                        total_hours,
                        work_type,
                        notes,
                    ))
                except Exception as exc:
                    skipped.append(
                        f"{employee_label} on {work_date_value.isoformat()}: {exc}"
                    )
        st.session_state[f"{key_prefix}_review_fingerprint"] = ""
        if created_ids:
            pb_success(
                f"Created {len(created_ids)} timesheet{'s' if len(created_ids) != 1 else ''} "
                f"and linked {'them' if len(created_ids) != 1 else 'it'} to {selected_job}."
            )
        if skipped:
            st.warning("Skipped:\n\n" + "\n\n".join(f"• {item}" for item in skipped))
        if created_ids:
            refresh()


def timesheets_page(employee_restricted=False):
    st.header("Timesheets")
    st.caption("Employee hours linked directly to specific jobs.")
    user = get_current_user() or {}
    current_employee_id = user.get("employee_id")

    if employee_restricted:
        if not current_employee_id:
            st.warning("Your login is not linked to an employee record. Ask admin to link your user to your employee profile.")
            return
        tab_submit, tab_my = st.tabs(["Submit Timesheet", "My Timesheets"])
        with tab_submit:
            timesheet_entry_form(employee_id=current_employee_id, employee_restricted=True, key_prefix="employee_timesheet")
        with tab_my:
            my_df = df_query("""
                SELECT t.id, t.work_date AS 'Date', j.job_no AS 'Job No', j.job_name AS 'Job Name',
                       t.start_time AS 'Start', t.finish_time AS 'Finish', t.break_minutes AS 'Break Minutes',
                       t.total_hours AS 'Hours', t.work_type AS 'Work Type',
                       COALESCE(t.status, 'Submitted') AS 'Status', t.notes AS 'Notes'
                FROM timesheet_entries t
                JOIN jobs j ON j.id = t.job_id
                WHERE t.employee_id = ?
                ORDER BY t.work_date DESC, t.id DESC
                LIMIT 100
            """, (current_employee_id,))
            selected_status = timesheet_status_filter(
                "Show My Timesheets",
                "employee_timesheet_status_filter",
                default="All",
            )
            my_filtered = filter_timesheets_by_status(my_df, selected_status)
            st.caption(f"{len(my_filtered)} timesheet(s) shown.")
            st.dataframe(
                my_filtered.drop(columns=["id"], errors="ignore"),
                width="stretch",
                hide_index=True,
            )
        return

    tab_submit, tab_review, tab_by_job = st.tabs(["Add Timesheet", "Review Timesheets", "Timesheets by Job"])

    with tab_submit:
        timesheet_entry_form(key_prefix="admin_timesheet")

    with tab_review:
        df = df_query("""
            SELECT t.id, t.work_date AS 'Date', j.job_no AS 'Job No', j.job_name AS 'Job Name', e.name AS 'Employee',
                   t.start_time AS 'Start', t.finish_time AS 'Finish', t.break_minutes AS 'Break Minutes',
                   t.total_hours AS 'Hours', t.work_type AS 'Work Type',
                   COALESCE(t.status, 'Submitted') AS 'Status',
                   t.submitted_by AS 'Submitted By', t.submitted_at AS 'Submitted At', t.notes AS 'Notes'
            FROM timesheet_entries t
            JOIN jobs j ON j.id = t.job_id
            JOIN employees e ON e.id = t.employee_id
            ORDER BY t.work_date DESC, t.id DESC
            LIMIT 500
        """)

        if df.empty:
            st.info("No timesheets submitted yet.")
        else:
            selected_status = timesheet_status_filter(
                "Show Timesheets With Status",
                "admin_timesheet_status_filter",
                default="Submitted",
            )
            filtered_df = filter_timesheets_by_status(df, selected_status)
            st.caption(
                f"Showing {len(filtered_df)} of {len(df)} timesheet(s): {selected_status}."
            )
            render_timesheet_bulk_actions(
                filtered_df,
                key_prefix=f"admin_timesheet_review_{selected_status.lower().replace(' ', '_')}",
                empty_message=f"No {selected_status.lower()} timesheets found.",
            )

    with tab_by_job:
        job_options = get_job_options()
        if not job_options:
            st.info("No jobs found.")
        else:
            selected_job = st.selectbox(
                "Select Job",
                list(job_options.keys()),
                key="timesheet_by_job_select",
            )
            selected_job_id = job_options[selected_job]
            selected_status = timesheet_status_filter(
                "Show Timesheets With Status",
                "timesheet_by_job_status_filter",
                default="All",
            )
            by_job = df_query("""
                SELECT t.id, t.work_date AS 'Date', e.name AS 'Employee',
                       t.start_time AS 'Start', t.finish_time AS 'Finish',
                       t.break_minutes AS 'Break Minutes', t.total_hours AS 'Hours',
                       t.work_type AS 'Work Type',
                       COALESCE(t.status, 'Submitted') AS 'Status',
                       t.notes AS 'Notes'
                FROM timesheet_entries t
                JOIN employees e ON e.id = t.employee_id
                WHERE t.job_id = ?
                ORDER BY t.work_date DESC, t.id DESC
            """, (selected_job_id,))
            filtered_by_job = filter_timesheets_by_status(by_job, selected_status)

            if filtered_by_job.empty:
                st.info(
                    f"No {selected_status.lower()} timesheets are saved for this job."
                    if selected_status != "All"
                    else "No timesheets are saved for this job."
                )
            else:
                total_job_hours = float(filtered_by_job["Hours"].fillna(0).sum())
                st.markdown("### Selection Review")
                st.dataframe(
                    pd.DataFrame([{
                        "Selected Job": selected_job,
                        "Status Filter": selected_status,
                        "Timesheet Count": len(filtered_by_job),
                        "Total Hours": f"{total_job_hours:.2f}",
                    }]),
                    width="stretch",
                    hide_index=True,
                )
                accepted_job_review = review_acceptance_checkbox(
                    "timesheet_by_job_review",
                    {
                        "job_id": selected_job_id,
                        "status_filter": selected_status,
                        "timesheet_ids": filtered_by_job["id"].astype(int).tolist(),
                        "timesheet_count": len(filtered_by_job),
                        "total_hours": total_job_hours,
                    },
                    "I have reviewed and accept this job and status selection.",
                )

                if accepted_job_review:
                    st.metric("Total Hours for Job", f"{total_job_hours:.2f}")
                    render_timesheet_bulk_actions(
                        filtered_by_job,
                        key_prefix=(
                            f"timesheet_by_job_{selected_job_id}_"
                            f"{selected_status.lower().replace(' ', '_')}"
                        ),
                    )
                else:
                    st.info("Accept the selected job review to display and select its timesheets.")

# =============================
# ESTIMATE WORKING SHEET
# =============================


# =============================
# ESTIMATING RATE LIBRARY
# PB_JOBHUB_ESTIMATING_RATE_LIBRARY_V1
# =============================
ESTIMATING_RATE_LIBRARY_FILENAME = "PB_JobHub_Estimating_Rate_Library_Import.csv"
ESTIMATING_RATE_LIBRARY_PATH = os.path.join(os.path.dirname(__file__), ESTIMATING_RATE_LIBRARY_FILENAME)

ESTIMATING_RATE_COLUMNS = {
    "Rate Code": "rate_code",
    "Category": "category",
    "Item Description": "item_description",
    "Project Type": "project_type",
    "Work Type": "work_type",
    "Unit": "unit",
    "Rate Min Ex GST": "rate_min_ex_gst",
    "Recommended Rate Ex GST": "recommended_rate_ex_gst",
    "Rate Max Ex GST": "rate_max_ex_gst",
    "Adjustment Type": "adjustment_type",
    "Rate Basis": "rate_basis",
    "Included Scope / Notes": "notes",
    "Effective Date": "effective_date",
    "Active": "active",
}


def _rate_library_clean_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _rate_library_float(value):
    text = _rate_library_clean_text(value).replace("$", "").replace(",", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except Exception:
        return 0.0


def _rate_library_active(value):
    text = _rate_library_clean_text(value).lower()
    return 0 if text in {"0", "no", "n", "false", "inactive", "disabled"} else 1


def normalise_estimating_rate_dataframe(source_df):
    if source_df is None or source_df.empty:
        raise ValueError("The rate library file is empty.")

    work = source_df.copy()
    work.columns = [str(c).strip() for c in work.columns]
    missing = [name for name in ESTIMATING_RATE_COLUMNS if name not in work.columns]
    if missing:
        raise ValueError("Missing required column(s): " + ", ".join(missing))

    work = work[list(ESTIMATING_RATE_COLUMNS.keys())].rename(columns=ESTIMATING_RATE_COLUMNS)
    output_rows = []
    seen_codes = set()

    for _, row in work.iterrows():
        rate_code = _rate_library_clean_text(row.get("rate_code"))
        item_description = _rate_library_clean_text(row.get("item_description"))
        if not rate_code or not item_description:
            continue
        if rate_code in seen_codes:
            # The last row for a duplicate code wins, matching a normal update import.
            output_rows = [r for r in output_rows if r[0] != rate_code]
        seen_codes.add(rate_code)
        output_rows.append((
            rate_code,
            _rate_library_clean_text(row.get("category")),
            item_description,
            _rate_library_clean_text(row.get("project_type")),
            _rate_library_clean_text(row.get("work_type")),
            _rate_library_clean_text(row.get("unit")) or "item",
            _rate_library_float(row.get("rate_min_ex_gst")),
            _rate_library_float(row.get("recommended_rate_ex_gst")),
            _rate_library_float(row.get("rate_max_ex_gst")),
            _rate_library_clean_text(row.get("adjustment_type")) or "Fixed",
            _rate_library_clean_text(row.get("rate_basis")),
            _rate_library_clean_text(row.get("notes")),
            _rate_library_clean_text(row.get("effective_date")),
            _rate_library_active(row.get("active")),
        ))

    if not output_rows:
        raise ValueError("No valid rate records were found. Rate Code and Item Description are required.")
    return output_rows


def import_estimating_rate_dataframe(source_df, replace_all=False):
    rows = normalise_estimating_rate_dataframe(source_df)
    conn = connect()
    inserted = 0
    updated = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        cur = conn.cursor()
        if replace_all:
            cur.execute("DELETE FROM estimating_rates")

        for row in rows:
            rate_code = row[0]
            cur.execute("SELECT id FROM estimating_rates WHERE rate_code = ?", (rate_code,))
            existing = cur.fetchone()
            if existing:
                cur.execute("""
                    UPDATE estimating_rates
                    SET category = ?, item_description = ?, project_type = ?, work_type = ?, unit = ?,
                        rate_min_ex_gst = ?, recommended_rate_ex_gst = ?, rate_max_ex_gst = ?,
                        adjustment_type = ?, rate_basis = ?, notes = ?, effective_date = ?, active = ?, updated_at = ?
                    WHERE rate_code = ?
                """, (
                    row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8],
                    row[9], row[10], row[11], row[12], row[13], now, rate_code,
                ))
                updated += 1
            else:
                cur.execute("""
                    INSERT INTO estimating_rates
                    (rate_code, category, item_description, project_type, work_type, unit,
                     rate_min_ex_gst, recommended_rate_ex_gst, rate_max_ex_gst,
                     adjustment_type, rate_basis, notes, effective_date, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (*row, now, now))
                inserted += 1
        conn.commit()
        return {"inserted": inserted, "updated": updated, "total": len(rows)}
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def seed_packaged_estimating_rates_if_empty():
    try:
        count_df = df_query("SELECT COUNT(*) AS c FROM estimating_rates")
        existing_count = int(count_df.iloc[0]["c"] or 0) if not count_df.empty else 0
        if existing_count > 0 or not os.path.exists(ESTIMATING_RATE_LIBRARY_PATH):
            return None
        packaged_df = pd.read_csv(ESTIMATING_RATE_LIBRARY_PATH)
        return import_estimating_rate_dataframe(packaged_df, replace_all=False)
    except Exception:
        # Do not prevent JobHub loading if a packaged CSV was removed or malformed.
        return None


def estimating_rates_dataframe(active_only=False):
    where_clause = "WHERE active = 1" if active_only else ""
    return df_query(f"""
        SELECT id,
               rate_code AS 'Rate Code',
               category AS 'Category',
               item_description AS 'Item Description',
               project_type AS 'Project Type',
               work_type AS 'Work Type',
               unit AS 'Unit',
               rate_min_ex_gst AS 'Rate Min Ex GST',
               recommended_rate_ex_gst AS 'Recommended Rate Ex GST',
               rate_max_ex_gst AS 'Rate Max Ex GST',
               adjustment_type AS 'Adjustment Type',
               rate_basis AS 'Rate Basis',
               notes AS 'Included Scope / Notes',
               effective_date AS 'Effective Date',
               active AS 'Active'
        FROM estimating_rates
        {where_clause}
        ORDER BY project_type, work_type, category, item_description, rate_code
    """)


def _filter_estimating_rates(rate_df, key_prefix):
    if rate_df.empty:
        return rate_df

    c1, c2, c3 = st.columns(3)
    project_values = ["All"] + sorted([v for v in rate_df["Project Type"].dropna().astype(str).unique() if v])
    selected_project = c1.selectbox("Project Type", project_values, key=f"{key_prefix}_project")

    project_df = rate_df if selected_project == "All" else rate_df[rate_df["Project Type"].astype(str) == selected_project]
    work_values = ["All"] + sorted([v for v in project_df["Work Type"].dropna().astype(str).unique() if v])
    selected_work = c2.selectbox("Work Type", work_values, key=f"{key_prefix}_work")

    work_df = project_df if selected_work == "All" else project_df[project_df["Work Type"].astype(str) == selected_work]
    category_values = ["All"] + sorted([v for v in work_df["Category"].dropna().astype(str).unique() if v])
    selected_category = c3.selectbox("Category", category_values, key=f"{key_prefix}_category")

    filtered = work_df if selected_category == "All" else work_df[work_df["Category"].astype(str) == selected_category]
    search_text = st.text_input(
        "Search rate code, substrate or description",
        key=f"{key_prefix}_search",
        placeholder="e.g. blockwork, epoxy, plasterboard, downpipe",
    ).strip().lower()
    if search_text:
        haystack = (
            filtered["Rate Code"].fillna("").astype(str) + " " +
            filtered["Category"].fillna("").astype(str) + " " +
            filtered["Item Description"].fillna("").astype(str) + " " +
            filtered["Project Type"].fillna("").astype(str) + " " +
            filtered["Work Type"].fillna("").astype(str) + " " +
            filtered["Included Scope / Notes"].fillna("").astype(str)
        ).str.lower()
        filtered = filtered[haystack.str.contains(search_text, na=False, regex=False)]
    return filtered


def estimating_rate_library_page():
    st.header("Estimating Rate Library")
    st.caption("Import, search and maintain the reusable Premier Brushworks estimating rates used by Estimate Working Sheets.")

    seeded = seed_packaged_estimating_rates_if_empty()
    if seeded:
        pb_success(f"Loaded {seeded['total']} packaged Premier Brushworks estimating rates.")

    full_df = estimating_rates_dataframe(active_only=False)
    active_count = int((full_df["Active"].fillna(0).astype(float) == 1).sum()) if not full_df.empty else 0
    c1, c2, c3 = st.columns(3)
    c1.metric("Total rates", len(full_df))
    c2.metric("Active rates", active_count)
    c3.metric("Inactive rates", max(len(full_df) - active_count, 0))

    tab_import, tab_browse, tab_manage = st.tabs(["Import / Export", "Browse Rates", "Manage Rates"])

    with tab_import:
        st.subheader("Import Rate Library CSV")
        st.caption(f"Expected file: {ESTIMATING_RATE_LIBRARY_FILENAME}. Merge/update matches records by Rate Code.")
        uploaded = st.file_uploader("Choose rate-library CSV", type=["csv"], key="estimating_rate_library_upload")
        import_mode = st.radio(
            "Import mode",
            ["Merge / update existing rates", "Replace the complete rate library"],
            horizontal=True,
            key="estimating_rate_library_mode",
        )
        if uploaded is not None:
            try:
                if uploaded_file_size(uploaded) > MAX_CSV_UPLOAD_BYTES:
                    raise ValueError("CSV is larger than the 5 MB upload limit.")
                uploaded.seek(0)
                preview_df = pd.read_csv(uploaded)
                if len(preview_df) > MAX_CSV_IMPORT_ROWS:
                    raise ValueError("CSV contains more than 10,000 rows.")
                st.dataframe(preview_df.head(25), width="stretch", hide_index=True)
                st.caption(f"{len(preview_df):,} row(s) detected.")
                if st.button("Import Rate Library", type="primary", key="estimating_rate_library_import_button"):
                    result = import_estimating_rate_dataframe(
                        preview_df,
                        replace_all=import_mode.startswith("Replace"),
                    )
                    pb_success(
                        f"Rate library imported: {result['inserted']} inserted, "
                        f"{result['updated']} updated, {result['total']} processed."
                    )
                    pb_rerun()
            except Exception as exc:
                pb_error(f"Could not read or import this CSV: {exc}")

        if os.path.exists(ESTIMATING_RATE_LIBRARY_PATH):
            with open(ESTIMATING_RATE_LIBRARY_PATH, "rb") as rate_file:
                st.download_button(
                    "Download Packaged Premier Brushworks Rate CSV",
                    data=rate_file.read(),
                    file_name=ESTIMATING_RATE_LIBRARY_FILENAME,
                    mime="text/csv",
                    key="download_packaged_rate_library",
                )

        export_df = estimating_rates_dataframe(active_only=False)
        if not export_df.empty:
            export_columns = [c for c in export_df.columns if c != "id"]
            st.download_button(
                "Export Current JobHub Rate Library",
                data=export_df[export_columns].to_csv(index=False).encode("utf-8-sig"),
                file_name=f"PB_JobHub_Rate_Library_Export_{date.today().isoformat()}.csv",
                mime="text/csv",
                key="export_current_rate_library",
            )

    with tab_browse:
        browse_df = estimating_rates_dataframe(active_only=False)
        show_inactive = st.checkbox("Include inactive rates", value=False, key="rate_library_show_inactive")
        if not show_inactive and not browse_df.empty:
            browse_df = browse_df[browse_df["Active"].fillna(0).astype(float) == 1]
        filtered_df = _filter_estimating_rates(browse_df, "rate_library_browse")
        st.caption(f"Showing {len(filtered_df):,} rate(s). All rates exclude GST.")
        if filtered_df.empty:
            st.info("No rates match the selected filters.")
        else:
            display_columns = [
                "Rate Code", "Category", "Item Description", "Project Type", "Work Type", "Unit",
                "Recommended Rate Ex GST", "Rate Min Ex GST", "Rate Max Ex GST", "Adjustment Type",
                "Included Scope / Notes", "Effective Date", "Active",
            ]
            st.dataframe(filtered_df[display_columns], width="stretch", hide_index=True)

    with tab_manage:
        st.subheader("Add or update one rate")
        existing = estimating_rates_dataframe(active_only=False)
        with st.form("manual_estimating_rate_form"):
            c1, c2 = st.columns(2)
            rate_code = c1.text_input("Rate Code")
            item_description = c2.text_input("Item Description")
            c3, c4, c5 = st.columns(3)
            category = c3.text_input("Category")
            project_type = c4.text_input("Project Type", value="Commercial")
            work_type = c5.text_input("Work Type", value="New")
            c6, c7, c8, c9 = st.columns(4)
            unit = c6.text_input("Unit", value="m²")
            rate_min = c7.number_input("Minimum", min_value=0.0, step=1.0)
            rate_rec = c8.number_input("Recommended", min_value=0.0, step=1.0)
            rate_max = c9.number_input("Maximum", min_value=0.0, step=1.0)
            adjustment_type = st.selectbox(
                "Adjustment Type",
                ["Fixed", "Fixed Range", "Percentage Add", "Cost Plus Percentage"],
            )
            rate_basis = st.text_input("Rate Basis", value="Base substrate/item rate")
            notes = st.text_area("Included Scope / Notes")
            effective_date = st.text_input("Effective Date", value=str(date.today()))
            active = st.checkbox("Active", value=True)
            save_rate = st.form_submit_button("Save / Update Rate")
            if save_rate:
                if not rate_code.strip() or not item_description.strip():
                    pb_error("Rate Code and Item Description are required.")
                else:
                    manual_df = pd.DataFrame([{
                        "Rate Code": rate_code,
                        "Category": category,
                        "Item Description": item_description,
                        "Project Type": project_type,
                        "Work Type": work_type,
                        "Unit": unit,
                        "Rate Min Ex GST": rate_min,
                        "Recommended Rate Ex GST": rate_rec,
                        "Rate Max Ex GST": rate_max,
                        "Adjustment Type": adjustment_type,
                        "Rate Basis": rate_basis,
                        "Included Scope / Notes": notes,
                        "Effective Date": effective_date,
                        "Active": "Yes" if active else "No",
                    }])
                    result = import_estimating_rate_dataframe(manual_df, replace_all=False)
                    pb_success(f"Rate saved: {result['inserted']} inserted, {result['updated']} updated.")
                    pb_rerun()

        if not existing.empty:
            st.divider()
            st.subheader("Activate, deactivate or delete")
            rate_options = {
                f"{row['Rate Code']} | {row['Item Description']} | {row['Project Type']} / {row['Work Type']}": int(row["id"])
                for _, row in existing.iterrows()
            }
            selected_label = st.selectbox("Select rate", list(rate_options.keys()), key="manage_rate_selection")
            selected_id = rate_options[selected_label]
            b1, b2, b3 = st.columns(3)
            if b1.button("Set Active", key="set_rate_active"):
                execute("UPDATE estimating_rates SET active = 1, updated_at = ? WHERE id = ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), selected_id))
                pb_rerun()
            if b2.button("Set Inactive", key="set_rate_inactive"):
                execute("UPDATE estimating_rates SET active = 0, updated_at = ? WHERE id = ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), selected_id))
                pb_rerun()
            confirm_delete = st.checkbox("Confirm permanent deletion", key="confirm_rate_delete")
            if b3.button("Delete Rate", key="delete_rate_button"):
                if not confirm_delete:
                    pb_error("Tick confirm permanent deletion first.")
                else:
                    execute("DELETE FROM estimating_rates WHERE id = ?", (selected_id,))
                    pb_rerun()


def render_rate_library_estimate_adder(selected_estimate_id):
    seed_packaged_estimating_rates_if_empty()
    rate_df = estimating_rates_dataframe(active_only=True)

    st.markdown("#### Add from Estimating Rate Library")
    if rate_df.empty:
        st.info("The estimating rate library is empty. Open Estimating → Rate Library and import the Premier Brushworks CSV.")
        return

    with st.expander("Search and add a saved rate", expanded=True):
        filtered_df = _filter_estimating_rates(rate_df, f"estimate_rate_{selected_estimate_id}")
        if filtered_df.empty:
            st.info("No rates match the selected filters.")
            return

        option_map = {
            (
                f"{row['Item Description']} | {row['Project Type']} / {row['Work Type']} | "
                f"{row['Unit']} @ ${float(row['Recommended Rate Ex GST'] or 0):,.2f}"
            ): int(row["id"])
            for _, row in filtered_df.iterrows()
        }
        selected_label = st.selectbox(
            "Select saved rate",
            list(option_map.keys()),
            key=f"estimate_saved_rate_{selected_estimate_id}",
        )
        selected_rate_id = option_map[selected_label]
        selected_row = filtered_df[filtered_df["id"] == selected_rate_id].iloc[0]

        rate_basis_choice = st.radio(
            "Rate to use",
            ["Recommended", "Minimum", "Maximum"],
            horizontal=True,
            key=f"estimate_rate_basis_{selected_estimate_id}",
        )
        rate_column = {
            "Recommended": "Recommended Rate Ex GST",
            "Minimum": "Rate Min Ex GST",
            "Maximum": "Rate Max Ex GST",
        }[rate_basis_choice]
        selected_rate = float(selected_row[rate_column] or 0)

        c1, c2, c3 = st.columns(3)
        qty = c1.number_input(
            "Quantity",
            min_value=0.0,
            value=1.0,
            step=1.0,
            key=f"estimate_rate_qty_{selected_estimate_id}",
        )
        override_rate = c2.checkbox("Override rate", value=False, key=f"estimate_rate_override_{selected_estimate_id}")
        final_rate = c3.number_input(
            "Unit Rate Ex GST",
            min_value=0.0,
            value=float(selected_rate),
            step=1.0,
            disabled=not override_rate,
            key=f"estimate_rate_value_{selected_estimate_id}_{selected_rate_id}_{rate_basis_choice}",
        )
        if not override_rate:
            final_rate = selected_rate

        category = _rate_library_clean_text(selected_row["Category"])
        section_options = []
        for value in [category, "Painting Works", "Floor Coatings", "Preliminaries", "Labour", "Materials", "Access / Equipment", "Subcontractor", "Variations", "Other"]:
            if value and value not in section_options:
                section_options.append(value)
        section = st.selectbox(
            "Estimate Section",
            section_options,
            key=f"estimate_rate_section_{selected_estimate_id}",
        )
        default_notes = (
            f"Rate library: {selected_row['Rate Code']} | {rate_basis_choice} rate. "
            f"{_rate_library_clean_text(selected_row['Included Scope / Notes'])}"
        ).strip()
        line_notes = st.text_area(
            "Line Notes",
            value=default_notes,
            key=f"estimate_rate_notes_{selected_estimate_id}_{selected_rate_id}",
        )

        preview_total = round(float(qty or 0) * float(final_rate or 0), 2)
        m1, m2, m3 = st.columns(3)
        m1.metric("Unit", _rate_library_clean_text(selected_row["Unit"]) or "item")
        m2.metric("Rate Ex GST", f"${float(final_rate or 0):,.2f}")
        m3.metric("Line Total Ex GST", f"${preview_total:,.2f}")

        if st.button("Add Saved Rate to Estimate", type="primary", key=f"add_saved_rate_{selected_estimate_id}"):
            if float(qty or 0) <= 0:
                pb_error("Quantity must be greater than zero.")
            else:
                execute("""
                    INSERT INTO estimate_line_items
                    (estimate_id, section, item_description, qty, unit, unit_rate, line_total, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    selected_estimate_id,
                    section,
                    _rate_library_clean_text(selected_row["Item Description"]),
                    float(qty),
                    _rate_library_clean_text(selected_row["Unit"]) or "item",
                    float(final_rate),
                    preview_total,
                    line_notes,
                ))
                recalc_estimate_totals(selected_estimate_id)
                pb_success("Saved estimating rate added to the estimate.")
                pb_rerun()

# PB_ESTIMATE_LINE_IMPORTER_V1
def _normalise_estimate_import_header(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _prepare_estimate_line_import_dataframe(source_df):
    if source_df is None or source_df.empty:
        raise ValueError("The uploaded CSV does not contain any line items.")

    aliases = {
        "section": "Section",
        "itemdescription": "Item Description",
        "description": "Item Description",
        "item": "Item Description",
        "qty": "Qty",
        "quantity": "Qty",
        "unit": "Unit",
        "unitrate": "Unit Rate",
        "rate": "Unit Rate",
        "linetotal": "Line Total",
        "total": "Line Total",
        "notes": "Notes",
        "linenotes": "Notes",
    }

    rename_map = {}
    for column in source_df.columns:
        key = _normalise_estimate_import_header(column)
        if key in aliases:
            rename_map[column] = aliases[key]

    work = source_df.rename(columns=rename_map).copy()
    required = ["Section", "Item Description", "Qty", "Unit", "Unit Rate"]
    missing = [column for column in required if column not in work.columns]
    if missing:
        raise ValueError("Missing required column(s): " + ", ".join(missing))

    if "Line Total" not in work.columns:
        work["Line Total"] = None
    if "Notes" not in work.columns:
        work["Notes"] = ""

    work["Section"] = work["Section"].fillna("").astype(str).str.strip()
    work["Item Description"] = work["Item Description"].fillna("").astype(str).str.strip()
    work["Unit"] = work["Unit"].fillna("").astype(str).str.strip()
    work["Notes"] = work["Notes"].fillna("").astype(str)

    for column in ["Qty", "Unit Rate", "Line Total"]:
        work[column] = pd.to_numeric(work[column], errors="coerce")

    work["Qty"] = work["Qty"].fillna(0.0)
    work["Unit Rate"] = work["Unit Rate"].fillna(0.0)
    calculated_total = (work["Qty"] * work["Unit Rate"]).round(2)
    work["Line Total"] = work["Line Total"].where(work["Line Total"].notna(), calculated_total)
    work["Line Total"] = work["Line Total"].round(2)

    work = work[work["Item Description"] != ""].copy()
    if work.empty:
        raise ValueError("No valid line items were found after removing blank descriptions.")

    return work[["Section", "Item Description", "Qty", "Unit", "Unit Rate", "Line Total", "Notes"]]


def render_estimate_line_item_csv_importer(selected_estimate_id):
    with st.expander("Import Estimate Working Sheet CSV", expanded=False):
        st.caption(
            "Upload line items with these columns: Section, Item Description, Qty, Unit, "
            "Unit Rate, Line Total and Notes."
        )
        uploaded = st.file_uploader(
            "Choose estimate working-sheet CSV",
            type=["csv"],
            key=f"estimate_line_import_file_{selected_estimate_id}",
        )
        import_mode = st.radio(
            "Import mode",
            ["Append to existing line items", "Replace existing line items"],
            horizontal=True,
            key=f"estimate_line_import_mode_{selected_estimate_id}",
        )

        if uploaded is None:
            return

        try:
            if uploaded_file_size(uploaded) > MAX_CSV_UPLOAD_BYTES:
                raise ValueError("CSV is larger than the 5 MB upload limit.")
            uploaded.seek(0)
            source_df = pd.read_csv(uploaded)
            if len(source_df) > MAX_CSV_IMPORT_ROWS:
                raise ValueError("CSV contains more than 10,000 rows.")
            prepared_df = _prepare_estimate_line_import_dataframe(source_df)
            st.dataframe(prepared_df, width="stretch", hide_index=True)
            st.metric(
                "Import Total Ex GST",
                f"${float(prepared_df['Line Total'].sum()):,.2f}",
            )

            confirm_replace = True
            if import_mode.startswith("Replace"):
                confirm_replace = st.checkbox(
                    "Confirm replacement of all existing line items",
                    key=f"estimate_line_import_confirm_{selected_estimate_id}",
                )

            if st.button(
                "Import Working Sheet",
                type="primary",
                key=f"estimate_line_import_button_{selected_estimate_id}",
            ):
                if not confirm_replace:
                    pb_error("Tick the replacement confirmation box first.")
                    return

                conn = connect()
                try:
                    cur = conn.cursor()
                    if import_mode.startswith("Replace"):
                        cur.execute(
                            "DELETE FROM estimate_line_items WHERE estimate_id = ?",
                            (selected_estimate_id,),
                        )

                    insert_sql = (
                        "INSERT INTO estimate_line_items "
                        "(estimate_id, section, item_description, qty, unit, unit_rate, line_total, notes) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    )
                    for _, row in prepared_df.iterrows():
                        cur.execute(insert_sql, (
                            selected_estimate_id,
                            str(row["Section"]),
                            str(row["Item Description"]),
                            float(row["Qty"] or 0),
                            str(row["Unit"]),
                            float(row["Unit Rate"] or 0),
                            float(row["Line Total"] or 0),
                            str(row["Notes"]),
                        ))
                    conn.commit()
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    raise
                finally:
                    conn.close()

                recalc_estimate_totals(selected_estimate_id)
                pb_success(f"Imported {len(prepared_df)} estimate line item(s).")
                pb_rerun()

        except Exception as exc:
            pb_error(f"Could not import this estimate working sheet: {exc}")


# =============================
# TAKE-OFF JOB PACK IMPORTER
# PB_JOBHUB_TAKEOFF_JOB_PACK_V1
# PB_JOBHUB_JOB_PACK_CREATE_UPDATE_V2
# =============================
TAKEOFF_PACK_VERSION = "2.0"
TAKEOFF_PACK_DATA_FILES = {
    "job_manifest.json",
    "takeoff_lines.csv",
    "labour_budget.csv",
    "material_allowances.csv",
    "colour_schedule.csv",
}
TAKEOFF_PACK_DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt",
    ".jpg", ".jpeg", ".png", ".webp",
}


def _takeoff_text(value, default=""):
    if value is None:
        return default
    try:
        if pd.isna(value):
            return default
    except Exception:
        pass
    value = str(value).strip()
    return value if value else default


def _takeoff_float(value, default=0.0):
    if value is None:
        return float(default)
    try:
        if pd.isna(value):
            return float(default)
    except Exception:
        pass
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace("$", "").replace(",", "")
    cleaned = cleaned.replace(" hrs", "").replace(" hr", "")
    if not cleaned:
        return float(default)
    try:
        return float(cleaned)
    except Exception:
        match = re.search(r"[-+]?\d*\.?\d+", cleaned)
        return float(match.group(0)) if match else float(default)


def _takeoff_header(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _takeoff_value(mapping, *keys, default=""):
    if not isinstance(mapping, dict):
        return default
    normalised = {_takeoff_header(key): value for key, value in mapping.items()}
    for key in keys:
        lookup = _takeoff_header(key)
        if lookup in normalised and normalised[lookup] not in (None, ""):
            return normalised[lookup]
    return default


def _takeoff_rename_columns(source_df, aliases):
    work = source_df.copy()
    rename_map = {}
    for column in work.columns:
        key = _takeoff_header(column)
        if key in aliases:
            rename_map[column] = aliases[key]
    return work.rename(columns=rename_map)


def _prepare_takeoff_lines_dataframe(source_df):
    columns = [
        "Section", "Item Description", "Qty", "Unit", "Unit Rate", "Line Total",
        "Estimated Labour Hours", "Material Allowance", "Substrate", "Location",
        "Coating System", "Colour / Finish", "Notes",
    ]
    if source_df is None or source_df.empty:
        return pd.DataFrame(columns=columns)

    aliases = {
        "section": "Section",
        "category": "Section",
        "itemdescription": "Item Description",
        "description": "Item Description",
        "item": "Item Description",
        "scopeitem": "Item Description",
        "qty": "Qty",
        "quantity": "Qty",
        "measure": "Qty",
        "unit": "Unit",
        "unitrate": "Unit Rate",
        "rate": "Unit Rate",
        "linetotal": "Line Total",
        "total": "Line Total",
        "estimatedlabourhours": "Estimated Labour Hours",
        "estimatedlaborhours": "Estimated Labour Hours",
        "labourhours": "Estimated Labour Hours",
        "laborhours": "Estimated Labour Hours",
        "hours": "Estimated Labour Hours",
        "materialallowance": "Material Allowance",
        "materialcostallowance": "Material Allowance",
        "substrate": "Substrate",
        "location": "Location",
        "area": "Location",
        "coatingsystem": "Coating System",
        "paintsystem": "Coating System",
        "colourfinish": "Colour / Finish",
        "colorfinish": "Colour / Finish",
        "colour": "Colour / Finish",
        "color": "Colour / Finish",
        "notes": "Notes",
        "scopenotes": "Notes",
    }
    work = _takeoff_rename_columns(source_df, aliases)
    if "Item Description" not in work.columns:
        raise ValueError("takeoff_lines.csv requires an Item Description or Description column.")

    defaults = {
        "Section": "Take-off",
        "Qty": 0.0,
        "Unit": "item",
        "Unit Rate": 0.0,
        "Line Total": None,
        "Estimated Labour Hours": 0.0,
        "Material Allowance": 0.0,
        "Substrate": "",
        "Location": "",
        "Coating System": "",
        "Colour / Finish": "",
        "Notes": "",
    }
    for column, default in defaults.items():
        if column not in work.columns:
            work[column] = default

    for column in ["Qty", "Unit Rate", "Line Total", "Estimated Labour Hours", "Material Allowance"]:
        work[column] = work[column].map(_takeoff_float)
    calculated_total = (work["Qty"] * work["Unit Rate"]).round(2)
    work["Line Total"] = work["Line Total"].where(work["Line Total"] != 0, calculated_total)

    for column in [
        "Section", "Item Description", "Unit", "Substrate", "Location",
        "Coating System", "Colour / Finish", "Notes",
    ]:
        work[column] = work[column].map(_takeoff_text)

    work["Section"] = work["Section"].replace("", "Take-off")
    work["Unit"] = work["Unit"].replace("", "item")
    work = work[work["Item Description"] != ""].copy()
    if len(work) > MAX_CSV_IMPORT_ROWS:
        raise ValueError("takeoff_lines.csv contains more than 10,000 valid rows.")
    return work[columns]


def _prepare_takeoff_labour_dataframe(source_df):
    columns = ["Item Description", "Estimated Labour Hours", "Labour Rate", "Notes"]
    if source_df is None or source_df.empty:
        return pd.DataFrame(columns=columns)
    aliases = {
        "itemdescription": "Item Description",
        "description": "Item Description",
        "item": "Item Description",
        "estimatedlabourhours": "Estimated Labour Hours",
        "estimatedlaborhours": "Estimated Labour Hours",
        "labourhours": "Estimated Labour Hours",
        "laborhours": "Estimated Labour Hours",
        "hours": "Estimated Labour Hours",
        "labourrate": "Labour Rate",
        "laborrate": "Labour Rate",
        "hourlyrate": "Labour Rate",
        "notes": "Notes",
    }
    work = _takeoff_rename_columns(source_df, aliases)
    if "Estimated Labour Hours" not in work.columns:
        raise ValueError("labour_budget.csv requires an Estimated Labour Hours or Hours column.")
    if "Item Description" not in work.columns:
        work["Item Description"] = "Labour budget"
    if "Labour Rate" not in work.columns:
        work["Labour Rate"] = 0.0
    if "Notes" not in work.columns:
        work["Notes"] = ""
    work["Estimated Labour Hours"] = work["Estimated Labour Hours"].map(_takeoff_float)
    work["Labour Rate"] = work["Labour Rate"].map(_takeoff_float)
    work["Item Description"] = work["Item Description"].map(_takeoff_text)
    work["Notes"] = work["Notes"].map(_takeoff_text)
    return work[columns]


def _prepare_takeoff_material_dataframe(source_df):
    columns = [
        "Product Code / Ref", "Product / Material Name", "Supplier", "Unit",
        "Unit Price Ex GST", "Colour / Finish", "Qty Required", "Location",
        "Substrate", "Coating System", "Notes", "Line Cost Ex GST",
    ]
    if source_df is None or source_df.empty:
        return pd.DataFrame(columns=columns)
    aliases = {
        "productcoderef": "Product Code / Ref",
        "productcode": "Product Code / Ref",
        "code": "Product Code / Ref",
        "productmaterialname": "Product / Material Name",
        "productname": "Product / Material Name",
        "material": "Product / Material Name",
        "product": "Product / Material Name",
        "supplier": "Supplier",
        "unit": "Unit",
        "unitpriceexgst": "Unit Price Ex GST",
        "unitprice": "Unit Price Ex GST",
        "priceexgst": "Unit Price Ex GST",
        "price": "Unit Price Ex GST",
        "colourfinish": "Colour / Finish",
        "colorfinish": "Colour / Finish",
        "colour": "Colour / Finish",
        "color": "Colour / Finish",
        "qtyrequired": "Qty Required",
        "quantity": "Qty Required",
        "qty": "Qty Required",
        "location": "Location",
        "area": "Location",
        "substrate": "Substrate",
        "coatingsystem": "Coating System",
        "paintsystem": "Coating System",
        "notes": "Notes",
    }
    work = _takeoff_rename_columns(source_df, aliases)
    if "Product / Material Name" not in work.columns:
        raise ValueError("material_allowances.csv requires a Product / Material Name or Material column.")
    defaults = {
        "Product Code / Ref": "CUSTOM",
        "Supplier": "",
        "Unit": "each",
        "Unit Price Ex GST": 0.0,
        "Colour / Finish": "",
        "Qty Required": 0.0,
        "Location": "",
        "Substrate": "",
        "Coating System": "",
        "Notes": "",
    }
    for column, default in defaults.items():
        if column not in work.columns:
            work[column] = default
    for column in ["Unit Price Ex GST", "Qty Required"]:
        work[column] = work[column].map(_takeoff_float)
    for column in [
        "Product Code / Ref", "Product / Material Name", "Supplier", "Unit",
        "Colour / Finish", "Location", "Substrate", "Coating System", "Notes",
    ]:
        work[column] = work[column].map(_takeoff_text)
    work["Product Code / Ref"] = work["Product Code / Ref"].replace("", "CUSTOM")
    work["Unit"] = work["Unit"].replace("", "each")
    work["Line Cost Ex GST"] = (work["Unit Price Ex GST"] * work["Qty Required"]).round(2)
    work = work[work["Product / Material Name"] != ""].copy()
    return work[columns]


def _prepare_takeoff_colour_dataframe(source_df):
    columns = ["Location", "Substrate", "Product / Material Name", "Colour / Finish", "Coating System", "Notes"]
    if source_df is None or source_df.empty:
        return pd.DataFrame(columns=columns)
    aliases = {
        "location": "Location",
        "area": "Location",
        "room": "Location",
        "substrate": "Substrate",
        "productmaterialname": "Product / Material Name",
        "product": "Product / Material Name",
        "material": "Product / Material Name",
        "paint": "Product / Material Name",
        "colourfinish": "Colour / Finish",
        "colorfinish": "Colour / Finish",
        "colour": "Colour / Finish",
        "color": "Colour / Finish",
        "coatingsystem": "Coating System",
        "paintsystem": "Coating System",
        "finish": "Coating System",
        "notes": "Notes",
    }
    work = _takeoff_rename_columns(source_df, aliases)
    defaults = {
        "Location": "",
        "Substrate": "",
        "Product / Material Name": "Colour schedule",
        "Colour / Finish": "",
        "Coating System": "",
        "Notes": "",
    }
    for column, default in defaults.items():
        if column not in work.columns:
            work[column] = default
    for column in columns:
        work[column] = work[column].map(_takeoff_text)
    work["Product / Material Name"] = work["Product / Material Name"].replace("", "Colour schedule")
    work = work[(work["Colour / Finish"] != "") | (work["Coating System"] != "") | (work["Location"] != "")].copy()
    return work[columns]


def _takeoff_safe_member_path(member_name):
    clean_name = str(member_name or "").replace("\\", "/")
    path = PurePosixPath(clean_name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe ZIP member path: {member_name}")
    return path


def _takeoff_document_type(member_name):
    lower = str(member_name or "").lower().replace("\\", "/")
    name = PurePosixPath(lower).name
    if "internal_job_sheet" in name or "job_sheet" in name:
        return "Internal Job Sheet"
    if "marked_up" in name or "markup" in name or "mark_up" in name:
        return "Marked-up Plans"
    if "/original_plans/" in f"/{lower}" or "plan" in name or "drawing" in name:
        return "Plans / Drawings"
    if "/specifications/" in f"/{lower}" or "specification" in name or "scope" in name:
        return "Specification / Scope"
    if "/colour_schedules/" in f"/{lower}" or "colour" in name or "color" in name or "finish_schedule" in name:
        return "Colour / Finish Schedule"
    if "/purchase_orders/" in f"/{lower}" or "purchase_order" in name or re.search(r"(^|[_-])po([_-]|\.)", name):
        return "Purchase Order"
    if "takeoff_report" in name or "take_off_report" in name:
        return "Take-off Report"
    return "Take-off Pack Document"


def _takeoff_find_member(member_names, expected_name):
    expected = expected_name.lower()
    for member in member_names:
        if member.lower() == expected or PurePosixPath(member).name.lower() == expected:
            return member
    return None


def _takeoff_read_csv_from_zip(zf, member_names, expected_name):
    member = _takeoff_find_member(member_names, expected_name)
    if not member:
        return pd.DataFrame()
    info = zf.getinfo(member)
    if info.file_size > MAX_CSV_UPLOAD_BYTES:
        raise ValueError(f"{expected_name} is larger than the 5 MB CSV limit.")
    raw = zf.read(member)
    if not raw.strip():
        return pd.DataFrame()
    return pd.read_csv(BytesIO(raw))


def _takeoff_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text_value = str(value).strip().casefold()
    if text_value in {"1", "true", "yes", "y", "on", "enabled", "restrict", "restricted"}:
        return True
    if text_value in {"0", "false", "no", "n", "off", "disabled", "unrestricted"}:
        return False
    return bool(default)


def _takeoff_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    elif isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        raw_values = re.split(r"[|;,\n]+", value)
    else:
        raw_values = [value]

    result = []
    seen = set()
    for item in raw_values:
        clean = _takeoff_text(item)
        key = clean.casefold()
        if clean and key not in seen:
            result.append(clean)
            seen.add(key)
    return result


def _takeoff_manifest_section(manifest, job_manifest, *keys):
    for key in keys:
        candidate = manifest.get(key) if isinstance(manifest, dict) else None
        if isinstance(candidate, dict):
            return candidate
        candidate = job_manifest.get(key) if isinstance(job_manifest, dict) else None
        if isinstance(candidate, dict):
            return candidate
    return {}


def _takeoff_insert_id(cur, sql, params):
    if USE_POSTGRES:
        cur.execute(sql.rstrip().rstrip(";") + " RETURNING id", params)
        return int(cur.fetchone()[0])
    cur.execute(sql, params)
    return int(cur.lastrowid)


def _takeoff_resolve_builder_client(
    cur,
    job_details,
    create_missing=True,
    fill_blank_existing=True,
):
    builder_name = _takeoff_text(job_details.get("builder_client"))
    if not builder_name:
        return None, "none"

    fields = [
        "type", "name", "contact_name", "phone", "email",
        "address", "qbcc", "abn", "terms", "notes",
    ]
    cur.execute(
        """
        SELECT id, type, name, contact_name, phone, email, address, qbcc, abn, terms, notes
        FROM builders_clients
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
        LIMIT 1
        """,
        (builder_name,),
    )
    row = cur.fetchone()
    incoming = {
        "type": _takeoff_text(job_details.get("builder_client_type"), "Builder / Client"),
        "name": builder_name,
        "contact_name": _takeoff_text(job_details.get("builder_contact_name")),
        "phone": _takeoff_text(job_details.get("builder_phone")),
        "email": _takeoff_text(job_details.get("builder_email")),
        "address": _takeoff_text(job_details.get("builder_address")),
        "qbcc": _takeoff_text(job_details.get("builder_qbcc")),
        "abn": _takeoff_text(job_details.get("builder_abn")),
        "terms": _takeoff_text(job_details.get("builder_terms")),
        "notes": _takeoff_text(job_details.get("builder_notes")),
    }

    if row:
        builder_id = int(row[0])
        if fill_blank_existing:
            existing = dict(zip(["id"] + fields, row))
            updates = []
            params = []
            for column in ["type", "contact_name", "phone", "email", "address", "qbcc", "abn", "terms", "notes"]:
                if incoming[column] and not _takeoff_text(existing.get(column)):
                    updates.append(f"{column} = ?")
                    params.append(incoming[column])
            if updates:
                params.append(builder_id)
                cur.execute(
                    f"UPDATE builders_clients SET {', '.join(updates)} WHERE id = ?",
                    tuple(params),
                )
                return builder_id, "linked_and_completed_blanks"
        return builder_id, "linked_existing"

    if not create_missing:
        return None, "missing_not_created"

    builder_id = _takeoff_insert_id(
        cur,
        """
        INSERT INTO builders_clients
        (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(incoming[column] for column in fields),
    )
    return builder_id, "created"


def _takeoff_prepare_target_job(
    cur,
    job_mode,
    target_job_id,
    job_details,
    update_job_record=True,
    create_missing_builder=True,
    fill_blank_builder_details=True,
):
    mode = _takeoff_text(job_mode, "update").strip().casefold()
    details = dict(job_details or {})

    if mode == "create":
        job_no = _takeoff_text(details.get("job_no"))
        job_name = _takeoff_text(details.get("job_name"))
        if not job_no:
            raise ValueError("A job number is required to create a new job from the pack.")
        if not job_name:
            raise ValueError("A job name is required to create a new job from the pack.")

        cur.execute(
            "SELECT id FROM jobs WHERE LOWER(TRIM(job_no)) = LOWER(TRIM(?)) LIMIT 1",
            (job_no,),
        )
        if cur.fetchone():
            raise ValueError(
                f"Job number {job_no} already exists. Choose Update an existing job instead."
            )

        builder_id, builder_action = _takeoff_resolve_builder_client(
            cur,
            details,
            create_missing=create_missing_builder,
            fill_blank_existing=fill_blank_builder_details,
        )
        job_id = _takeoff_insert_id(
            cur,
            """
            INSERT INTO jobs
            (job_no, job_name, builder_client_id, site_address, status, leading_hand,
             start_date, end_date, contract_value, notes,
             restrict_material_products, allowed_material_suppliers)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_no,
                job_name,
                builder_id,
                _takeoff_text(details.get("site_address")),
                _takeoff_text(details.get("status"), "Not Started"),
                _takeoff_text(details.get("leading_hand")),
                _takeoff_text(details.get("start_date")),
                _takeoff_text(details.get("end_date")),
                _takeoff_float(details.get("contract_value_ex_gst")),
                _takeoff_text(details.get("job_notes")),
                1 if _takeoff_bool(details.get("restrict_material_products")) else 0,
                serialise_material_supplier_list(_takeoff_list(details.get("allowed_material_suppliers"))),
            ),
        )
        return job_id, job_no, "created", builder_action

    if not target_job_id:
        raise ValueError("Choose the existing job that this pack should update.")
    job_id = int(target_job_id)
    cur.execute(
        """
        SELECT id, job_no, job_name, builder_client_id, site_address, status, leading_hand,
               start_date, end_date, contract_value, notes,
               restrict_material_products, allowed_material_suppliers
        FROM jobs
        WHERE id = ?
        """,
        (job_id,),
    )
    existing = cur.fetchone()
    if not existing:
        raise ValueError("The selected existing job could not be found.")

    existing_job_no = _takeoff_text(existing[1], f"job_{job_id}")
    if not update_job_record:
        return job_id, existing_job_no, "linked_without_job_changes", "unchanged"

    builder_id, builder_action = _takeoff_resolve_builder_client(
        cur,
        details,
        create_missing=create_missing_builder,
        fill_blank_existing=fill_blank_builder_details,
    )
    cur.execute(
        """
        UPDATE jobs
        SET job_no = ?, job_name = ?, builder_client_id = ?, site_address = ?, status = ?,
            leading_hand = ?, start_date = ?, end_date = ?, contract_value = ?, notes = ?,
            restrict_material_products = ?, allowed_material_suppliers = ?,
            row_version = COALESCE(row_version, 1) + 1
        WHERE id = ?
        """,
        (
            _takeoff_text(details.get("job_no"), existing_job_no),
            _takeoff_text(details.get("job_name"), _takeoff_text(existing[2])),
            builder_id if _takeoff_text(details.get("builder_client")) else existing[3],
            _takeoff_text(details.get("site_address")),
            _takeoff_text(details.get("status"), "Not Started"),
            _takeoff_text(details.get("leading_hand")),
            _takeoff_text(details.get("start_date")),
            _takeoff_text(details.get("end_date")),
            _takeoff_float(details.get("contract_value_ex_gst")),
            _takeoff_text(details.get("job_notes")),
            1 if _takeoff_bool(details.get("restrict_material_products")) else 0,
            serialise_material_supplier_list(_takeoff_list(details.get("allowed_material_suppliers"))),
            job_id,
        ),
    )
    return job_id, _takeoff_text(details.get("job_no"), existing_job_no), "updated", builder_action

def parse_takeoff_job_pack(uploaded_file):
    if uploaded_file is None:
        raise ValueError("Choose a Job Pack ZIP first.")
    if uploaded_file_size(uploaded_file) > MAX_TAKEOFF_PACK_BYTES:
        raise ValueError("Job Pack is larger than the 150 MB upload limit.")
    uploaded_file.seek(0)
    source_bytes = uploaded_file.read()
    uploaded_file.seek(0)

    try:
        zf = zipfile.ZipFile(BytesIO(source_bytes), "r")
    except zipfile.BadZipFile as exc:
        raise ValueError("The uploaded file is not a valid ZIP archive.") from exc

    with zf:
        file_infos = [info for info in zf.infolist() if not info.is_dir()]
        if not file_infos:
            raise ValueError("The ZIP does not contain any files.")
        if len(file_infos) > MAX_TAKEOFF_PACK_FILES:
            raise ValueError(f"The ZIP contains more than {MAX_TAKEOFF_PACK_FILES} files.")

        total_uncompressed = 0
        member_names = []
        for info in file_infos:
            _takeoff_safe_member_path(info.filename)
            if info.flag_bits & 0x1:
                raise ValueError(f"Encrypted ZIP member is not supported: {info.filename}")
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == 0o120000:
                raise ValueError(f"Symbolic links are not allowed in Job Packs: {info.filename}")
            total_uncompressed += int(info.file_size or 0)
            if total_uncompressed > MAX_TAKEOFF_PACK_EXTRACTED_BYTES:
                raise ValueError("The uncompressed Job Pack is larger than 350 MB.")
            member_names.append(info.filename)

        manifest = {}
        manifest_member = _takeoff_find_member(member_names, "job_manifest.json")
        if manifest_member:
            raw_manifest = zf.read(manifest_member)
            if len(raw_manifest) > 2 * 1024 * 1024:
                raise ValueError("job_manifest.json is larger than 2 MB.")
            try:
                manifest = json.loads(raw_manifest.decode("utf-8-sig"))
            except Exception as exc:
                raise ValueError("job_manifest.json is not valid UTF-8 JSON.") from exc
            if not isinstance(manifest, dict):
                raise ValueError("job_manifest.json must contain one JSON object.")

        lines_df = _prepare_takeoff_lines_dataframe(
            _takeoff_read_csv_from_zip(zf, member_names, "takeoff_lines.csv")
        )
        labour_df = _prepare_takeoff_labour_dataframe(
            _takeoff_read_csv_from_zip(zf, member_names, "labour_budget.csv")
        )
        materials_df = _prepare_takeoff_material_dataframe(
            _takeoff_read_csv_from_zip(zf, member_names, "material_allowances.csv")
        )
        colours_df = _prepare_takeoff_colour_dataframe(
            _takeoff_read_csv_from_zip(zf, member_names, "colour_schedule.csv")
        )

        if not lines_df.empty and not labour_df.empty:
            labour_by_item = {}
            for _, labour_row in labour_df.iterrows():
                item_key = _takeoff_header(labour_row.get("Item Description"))
                if item_key:
                    labour_by_item[item_key] = labour_by_item.get(item_key, 0.0) + _takeoff_float(
                        labour_row.get("Estimated Labour Hours")
                    )
            for line_index, line_row in lines_df.iterrows():
                if _takeoff_float(line_row.get("Estimated Labour Hours")) > 0:
                    continue
                matched_hours = labour_by_item.get(_takeoff_header(line_row.get("Item Description")), 0.0)
                if matched_hours > 0:
                    lines_df.at[line_index, "Estimated Labour Hours"] = matched_hours

        ignored_names = TAKEOFF_PACK_DATA_FILES | {"readme.txt", "readme.md"}
        documents = []
        for member in member_names:
            path = PurePosixPath(member)
            if path.name.lower() in ignored_names or path.name.upper().startswith("PLACE_"):
                continue
            if path.suffix.lower() not in TAKEOFF_PACK_DOCUMENT_EXTENSIONS:
                continue
            documents.append({
                "member": member,
                "file_name": path.name,
                "document_type": _takeoff_document_type(member),
                "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "size_bytes": int(zf.getinfo(member).file_size or 0),
            })

    job_manifest = manifest.get("job") if isinstance(manifest.get("job"), dict) else {}
    estimate_manifest = manifest.get("estimate") if isinstance(manifest.get("estimate"), dict) else {}
    budget_manifest = manifest.get("budget") if isinstance(manifest.get("budget"), dict) else {}
    builder_manifest = _takeoff_manifest_section(
        manifest,
        job_manifest,
        "builder_client",
        "builder",
        "client",
    )

    scalar_builder_name = ""
    for candidate in [
        job_manifest.get("builder_client"),
        job_manifest.get("builder"),
        job_manifest.get("client"),
        manifest.get("builder_client"),
        manifest.get("builder"),
        manifest.get("client"),
    ]:
        if candidate and not isinstance(candidate, dict):
            scalar_builder_name = _takeoff_text(candidate)
            if scalar_builder_name:
                break

    source_stem = Path(str(getattr(uploaded_file, "name", "job_pack.zip"))).stem
    pack_id = _takeoff_text(
        _takeoff_value(manifest, "pack_id", "takeoff_id", "job_pack_id", default=source_stem),
        source_stem,
    )
    revision = _takeoff_text(
        _takeoff_value(manifest, "revision", default=_takeoff_value(estimate_manifest, "revision", default="1")),
        "1",
    )

    line_hours = float(lines_df["Estimated Labour Hours"].sum()) if not lines_df.empty else 0.0
    labour_file_hours = float(labour_df["Estimated Labour Hours"].sum()) if not labour_df.empty else 0.0
    manifest_hours = _takeoff_float(
        _takeoff_value(estimate_manifest, "labour_hours", "labor_hours", default=_takeoff_value(budget_manifest, "labour_hours", "labor_hours", default=0))
    )
    total_labour_hours = manifest_hours or line_hours or labour_file_hours

    labour_rate = PLANNING_LABOUR_RATE

    material_file_total = float(materials_df["Line Cost Ex GST"].sum()) if not materials_df.empty else 0.0
    line_material_total = float(lines_df["Material Allowance"].sum()) if not lines_df.empty else 0.0
    manifest_materials = _takeoff_float(
        _takeoff_value(estimate_manifest, "material_allowance", "materials", default=_takeoff_value(budget_manifest, "material_allowance", "materials", default=0))
    )
    material_allowance = manifest_materials or material_file_total or line_material_total

    raw_restrict = _takeoff_value(
        job_manifest,
        "restrict_material_products",
        "restrict_products",
        "restrict_to_approved_suppliers",
        default=None,
    )
    allowed_suppliers = _takeoff_list(
        _takeoff_value(
            job_manifest,
            "allowed_material_suppliers",
            "approved_suppliers",
            "approved_brands",
            "material_suppliers",
            default=[],
        )
    )
    restrict_supplied = raw_restrict is not None or bool(allowed_suppliers)
    restrict_products = _takeoff_bool(raw_restrict, bool(allowed_suppliers))

    summary = {
        "pack_id": pack_id,
        "revision": revision,
        "pack_version": _takeoff_text(_takeoff_value(manifest, "pack_version", default=TAKEOFF_PACK_VERSION), TAKEOFF_PACK_VERSION),
        "job_no": _takeoff_text(_takeoff_value(job_manifest, "job_no", "job_number")),
        "job_name": _takeoff_text(_takeoff_value(job_manifest, "job_name", "name", "project_name")),
        "site_address": _takeoff_text(_takeoff_value(job_manifest, "site_address", "address", "project_address")),
        "builder_client": _takeoff_text(_takeoff_value(builder_manifest, "name", "builder_client", "builder", "client")) or scalar_builder_name,
        "builder_client_type": _takeoff_text(_takeoff_value(builder_manifest, "type", "contact_type"), "Builder / Client"),
        "builder_contact_name": _takeoff_text(_takeoff_value(builder_manifest, "contact_name", "contact", "primary_contact")),
        "builder_phone": _takeoff_text(_takeoff_value(builder_manifest, "phone", "telephone", "mobile")),
        "builder_email": _takeoff_text(_takeoff_value(builder_manifest, "email")),
        "builder_address": _takeoff_text(_takeoff_value(builder_manifest, "address")),
        "builder_qbcc": _takeoff_text(_takeoff_value(builder_manifest, "qbcc", "qbcc_licence", "qbcc_license")),
        "builder_abn": _takeoff_text(_takeoff_value(builder_manifest, "abn")),
        "builder_terms": _takeoff_text(_takeoff_value(builder_manifest, "terms", "payment_terms")),
        "builder_notes": _takeoff_text(_takeoff_value(builder_manifest, "notes")),
        "status": _takeoff_text(_takeoff_value(job_manifest, "status", "job_status")),
        "leading_hand": _takeoff_text(_takeoff_value(job_manifest, "leading_hand", "supervisor", "site_supervisor")),
        "start_date": _takeoff_text(_takeoff_value(job_manifest, "start_date", "commencement_date")),
        "end_date": _takeoff_text(_takeoff_value(job_manifest, "end_date", "completion_date")),
        "contract_value_ex_gst": _takeoff_float(_takeoff_value(
            job_manifest,
            "contract_value_ex_gst",
            "contract_value",
            "price_ex_gst",
            "quoted_price_ex_gst",
            "tender_price_ex_gst",
            "accepted_price_ex_gst",
            default=_takeoff_value(estimate_manifest, "total_ex_gst", "tender_price_ex_gst", "quoted_price_ex_gst", default=0),
        )),
        "job_notes": _takeoff_text(_takeoff_value(job_manifest, "notes", "scope_summary", "job_notes")),
        "restrict_material_products": restrict_products,
        "restrict_material_products_supplied": restrict_supplied,
        "allowed_material_suppliers": allowed_suppliers,
        "estimate_no": _takeoff_text(_takeoff_value(estimate_manifest, "estimate_no", "estimate_number")),
        "estimate_date": _takeoff_text(_takeoff_value(estimate_manifest, "estimate_date", "date"), str(date.today())),
        "estimate_status": _takeoff_text(_takeoff_value(estimate_manifest, "status"), "Draft"),
        "labour_hours": total_labour_hours,
        "labour_rate": labour_rate,
        "material_allowance": material_allowance,
        "access_equipment_allowance": _takeoff_float(_takeoff_value(estimate_manifest, "access_equipment_allowance", "access_allowance", default=_takeoff_value(budget_manifest, "access_equipment_allowance", "access_allowance", default=0))),
        "subcontractor_allowance": _takeoff_float(_takeoff_value(estimate_manifest, "subcontractor_allowance", default=_takeoff_value(budget_manifest, "subcontractor_allowance", default=0))),
        "sundries_allowance": _takeoff_float(_takeoff_value(estimate_manifest, "sundries_allowance", "consumables_allowance", default=_takeoff_value(budget_manifest, "sundries_allowance", "consumables_allowance", default=0))),
        "target_gp_percent": _takeoff_float(_takeoff_value(estimate_manifest, "target_gp_percent", "margin_percent", default=_takeoff_value(budget_manifest, "target_gp_percent", "margin_percent", default=35)), 35),
        "contingency_percent": _takeoff_float(_takeoff_value(estimate_manifest, "contingency_percent", default=0)),
        "gst_percent": _takeoff_float(_takeoff_value(estimate_manifest, "gst_percent", default=10), 10),
        "pricing_method": _takeoff_text(_takeoff_value(estimate_manifest, "pricing_method"), "Target Gross Margin"),
        "notes": _takeoff_text(_takeoff_value(estimate_manifest, "notes", default=_takeoff_value(manifest, "notes"))),
        "line_pricing_total": float(lines_df["Line Total"].sum()) if not lines_df.empty else 0.0,
    }
    if summary["pricing_method"] not in {"Target Gross Margin", "Markup"}:
        summary["pricing_method"] = "Target Gross Margin"

    has_job_details = any(
        _takeoff_text(summary.get(key))
        for key in ["job_no", "job_name", "site_address", "builder_client", "job_notes"]
    ) or summary["contract_value_ex_gst"] > 0
    if lines_df.empty and labour_df.empty and materials_df.empty and colours_df.empty and not documents and not has_job_details:
        raise ValueError(
            "The ZIP has no usable job details, take-off data, colours, materials or supported documents."
        )

    return {
        "source_bytes": source_bytes,
        "source_name": safe_file_name(str(getattr(uploaded_file, "name", "job_pack.zip"))),
        "member_names": member_names,
        "manifest": manifest,
        "summary": summary,
        "lines": lines_df,
        "labour": labour_df,
        "materials": materials_df,
        "colours": colours_df,
        "documents": documents,
    }



def build_takeoff_job_pack_template():
    manifest = {
        "pack_version": TAKEOFF_PACK_VERSION,
        "pack_id": "PB-JOBNO-JOB-PACK",
        "revision": "1",
        "job": {
            "job_no": "PB00000",
            "job_name": "Example Project",
            "site_address": "",
            "status": "Quoted",
            "leading_hand": "",
            "start_date": "",
            "end_date": "",
            "contract_value_ex_gst": 0,
            "notes": "Scope summary and important job notes.",
            "restrict_material_products": True,
            "allowed_material_suppliers": ["Haymes"],
        },
        "builder_client": {
            "type": "Builder",
            "name": "Example Builder Pty Ltd",
            "contact_name": "",
            "phone": "",
            "email": "",
            "address": "",
            "qbcc": "",
            "abn": "",
            "terms": "",
            "notes": "",
        },
        "estimate": {
            "estimate_no": "PB00000-TO-01",
            "estimate_date": str(date.today()),
            "revision": "1",
            "status": "Draft",
            "labour_hours": 0,
            "labour_rate": 60,
            "material_allowance": 0,
            "access_equipment_allowance": 0,
            "subcontractor_allowance": 0,
            "sundries_allowance": 0,
            "target_gp_percent": 35,
            "contingency_percent": 0,
            "gst_percent": 10,
            "pricing_method": "Target Gross Margin",
            "notes": "Generated from Premier Brushworks Job Pack.",
        },
    }
    lines = pd.DataFrame([{
        "Section": "Internal",
        "Item Description": "Walls - plasterboard",
        "Qty": 100,
        "Unit": "m2",
        "Unit Rate": 0,
        "Line Total": 0,
        "Estimated Labour Hours": 12,
        "Material Allowance": 450,
        "Substrate": "Plasterboard",
        "Location": "Ground floor",
        "Coating System": "1 sealer + 2 finish coats",
        "Colour / Finish": "To colour schedule",
        "Notes": "Example only - replace before importing.",
    }])
    labour = pd.DataFrame([{
        "Item Description": "Walls - plasterboard",
        "Estimated Labour Hours": 12,
        "Labour Rate": 60,
        "Notes": "Example only",
    }])
    materials = pd.DataFrame([{
        "Product Code / Ref": "CUSTOM",
        "Product / Material Name": "Interior low sheen",
        "Supplier": "Haymes",
        "Unit": "15L",
        "Unit Price Ex GST": 150,
        "Colour / Finish": "To colour schedule",
        "Qty Required": 3,
        "Location": "Ground floor walls",
        "Substrate": "Plasterboard",
        "Coating System": "2 finish coats",
        "Notes": "Preliminary allowance",
    }])
    colours = pd.DataFrame([{
        "Location": "Ground floor walls",
        "Substrate": "Plasterboard",
        "Product / Material Name": "Interior low sheen",
        "Colour / Finish": "To be confirmed",
        "Coating System": "2 finish coats",
        "Notes": "Example only",
    }])
    readme = """Premier Brushworks Job Pack Template

This ZIP can CREATE A NEW JOB or UPDATE AN EXISTING JOB.

Core file:
- job_manifest.json: job number/name, builder/client, contract price, address, status, dates, notes and approved paint suppliers.

Optional estimating files:
- takeoff_lines.csv
- labour_budget.csv
- material_allowances.csv
- colour_schedule.csv

Optional document folders/files:
- original_plans/
- specifications/
- colour_schedules/
- purchase_orders/
- correspondence/
- internal_job_sheet.pdf
- takeoff_report.pdf
- marked_up_plans.pdf

A job-details-and-documents-only pack is valid; take-off CSV files are optional.
Increase the revision each time the same pack is reissued to the same job.
"""
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("job_manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("takeoff_lines.csv", lines.to_csv(index=False))
        zf.writestr("labour_budget.csv", labour.to_csv(index=False))
        zf.writestr("material_allowances.csv", materials.to_csv(index=False))
        zf.writestr("colour_schedule.csv", colours.to_csv(index=False))
        zf.writestr("original_plans/PLACE_PLANS_HERE.txt", "Place architectural plans and drawings in this folder.")
        zf.writestr("specifications/PLACE_SPECIFICATIONS_HERE.txt", "Place specifications and scopes in this folder.")
        zf.writestr("colour_schedules/PLACE_COLOUR_SCHEDULES_HERE.txt", "Place colour schedules in this folder.")
        zf.writestr("purchase_orders/PLACE_PURCHASE_ORDERS_HERE.txt", "Place purchase orders or subcontracts in this folder.")
        zf.writestr("README.txt", readme)
    output.seek(0)
    return output.getvalue()



def _takeoff_safe_extract_target(pack_folder, member_name):
    member_path = _takeoff_safe_member_path(member_name)
    safe_parts = [safe_file_name(part) for part in member_path.parts]
    target = (Path(pack_folder) / Path(*safe_parts)).resolve()
    root = Path(pack_folder).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"Unsafe extracted path: {member_name}")
    return target


def import_takeoff_job_pack(
    job_id,
    parsed,
    create_estimate=True,
    update_budget=True,
    import_materials=True,
    attach_documents=True,
    use_imported_line_pricing=False,
    job_mode="update",
    job_details=None,
    update_job_record=True,
    create_missing_builder=True,
    fill_blank_builder_details=True,
):
    summary = parsed["summary"]
    pack_id = _takeoff_text(summary.get("pack_id"), "job-pack")[:120]
    revision = _takeoff_text(summary.get("revision"), "1")[:60]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    imported_by = current_username()
    pack_folder = None
    extracted_paths = {}
    estimate_id = None
    line_count = 0
    material_count = 0
    document_count = 0
    job_action = "linked"
    builder_action = "unchanged"

    conn = connect()
    try:
        cur = conn.cursor()
        job_id, job_no, job_action, builder_action = _takeoff_prepare_target_job(
            cur,
            job_mode,
            job_id,
            job_details or summary,
            update_job_record=update_job_record,
            create_missing_builder=create_missing_builder,
            fill_blank_builder_details=fill_blank_builder_details,
        )
        job_id = int(job_id)

        cur.execute(
            "SELECT id, imported_at FROM takeoff_pack_imports WHERE job_id = ? AND pack_id = ? AND revision = ?",
            (job_id, pack_id, revision),
        )
        if cur.fetchone():
            raise ValueError(
                f"Pack {pack_id}, revision {revision}, has already been imported into this job. "
                "Increase the revision in job_manifest.json before importing an updated pack."
            )

        safe_pack = safe_file_name(pack_id)[:80]
        safe_revision = safe_file_name(revision)[:40]
        pack_folder = Path(get_job_folder(job_no)) / "job_packs" / f"{safe_pack}_rev_{safe_revision}"
        if pack_folder.exists():
            pack_folder = pack_folder.parent / f"{pack_folder.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        pack_folder.mkdir(parents=True, exist_ok=False)

        source_zip_path = pack_folder / safe_file_name(parsed.get("source_name") or "job_pack.zip")
        source_zip_path.write_bytes(parsed["source_bytes"])
        with zipfile.ZipFile(BytesIO(parsed["source_bytes"]), "r") as zf:
            for member in parsed["member_names"]:
                target = _takeoff_safe_extract_target(pack_folder, member)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
                extracted_paths[member] = target

        labour_hours = _takeoff_float(summary.get("labour_hours"))
        labour_rate = PLANNING_LABOUR_RATE
        material_allowance = _takeoff_float(summary.get("material_allowance"))
        access_allowance = _takeoff_float(summary.get("access_equipment_allowance"))
        subcontractor_allowance = _takeoff_float(summary.get("subcontractor_allowance"))
        sundries_allowance = _takeoff_float(summary.get("sundries_allowance"))
        target_gp = _takeoff_float(summary.get("target_gp_percent"), 35.0)
        contingency = _takeoff_float(summary.get("contingency_percent"))
        gst_percent = _takeoff_float(summary.get("gst_percent"), 10.0)
        pricing_method = _takeoff_text(summary.get("pricing_method"), "Target Gross Margin")
        line_total = _takeoff_float(summary.get("line_pricing_total")) if use_imported_line_pricing else 0.0
        totals = calculate_estimate_pricing(
            line_total=line_total,
            labour_hours=labour_hours,
            labour_rate=labour_rate,
            material_allowance=material_allowance,
            access_equipment_allowance=access_allowance,
            subcontractor_allowance=subcontractor_allowance,
            sundries_allowance=sundries_allowance,
            pricing_percent=target_gp,
            contingency_percent=contingency,
            gst_percent=gst_percent,
            pricing_method=pricing_method,
        )

        if create_estimate:
            estimate_no = _takeoff_text(summary.get("estimate_no")) or f"{job_no}-TO-{safe_revision}"
            estimate_notes = "\n".join(filter(None, [
                _takeoff_text(summary.get("notes")),
                f"Imported from Job Pack {pack_id}, revision {revision}.",
                f"Source folder: {pack_folder}",
            ]))
            estimate_id = _takeoff_insert_id(
                cur,
                """
                INSERT INTO estimate_working_sheets
                (job_id, estimate_no, estimate_date, revision, status, labour_hours, labour_rate,
                 material_allowance, access_equipment_allowance, subcontractor_allowance, sundries_allowance,
                 margin_percent, contingency_percent, gst_percent, pricing_method,
                 total_ex_gst, gst_amount, total_inc_gst, created_at, updated_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    estimate_no[:120],
                    _takeoff_text(summary.get("estimate_date"), str(date.today()))[:30],
                    revision,
                    _takeoff_text(summary.get("estimate_status"), "Draft")[:30],
                    labour_hours,
                    labour_rate,
                    material_allowance,
                    access_allowance,
                    subcontractor_allowance,
                    sundries_allowance,
                    target_gp,
                    contingency,
                    gst_percent,
                    pricing_method,
                    totals["total_ex_gst"],
                    totals["gst_amount"],
                    totals["total_inc_gst"],
                    now,
                    now,
                    estimate_notes,
                ),
            )

            insert_line_sql = """
                INSERT INTO estimate_line_items
                (estimate_id, section, item_description, qty, unit, unit_rate, line_total, notes,
                 estimated_labour_hours, material_allowance, substrate, work_location,
                 coating_system, colour_finish, source_pack)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            for _, row in parsed["lines"].iterrows():
                imported_rate = _takeoff_float(row.get("Unit Rate"))
                imported_total = _takeoff_float(row.get("Line Total"))
                notes = _takeoff_text(row.get("Notes"))
                if not use_imported_line_pricing and (imported_rate or imported_total):
                    notes = "\n".join(filter(None, [
                        notes,
                        f"Reference imported rate: ${imported_rate:,.2f}; reference line total: ${imported_total:,.2f}.",
                    ]))
                cur.execute(insert_line_sql, (
                    estimate_id,
                    _takeoff_text(row.get("Section"), "Take-off"),
                    _takeoff_text(row.get("Item Description")),
                    _takeoff_float(row.get("Qty")),
                    _takeoff_text(row.get("Unit"), "item"),
                    imported_rate if use_imported_line_pricing else 0.0,
                    imported_total if use_imported_line_pricing else 0.0,
                    notes,
                    _takeoff_float(row.get("Estimated Labour Hours")),
                    _takeoff_float(row.get("Material Allowance")),
                    _takeoff_text(row.get("Substrate")),
                    _takeoff_text(row.get("Location")),
                    _takeoff_text(row.get("Coating System")),
                    _takeoff_text(row.get("Colour / Finish")),
                    f"{pack_id} rev {revision}",
                ))
                line_count += 1

        if update_budget:
            cur.execute("SELECT notes FROM job_budgets WHERE job_id = ?", (job_id,))
            existing_budget = cur.fetchone()
            existing_notes = _takeoff_text(existing_budget[0]) if existing_budget else ""
            budget_note = "\n".join(filter(None, [
                existing_notes,
                f"Imported from Job Pack {pack_id}, revision {revision} on {now} by {imported_by}.",
            ]))
            cur.execute("""
                INSERT INTO job_budgets
                (job_id, quoted_labour_hours, quoted_labour_cost, quoted_materials,
                 quoted_access_equipment, quoted_subcontractors, quoted_sundries,
                 target_gp_percent, locked_at, locked_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    quoted_labour_hours = excluded.quoted_labour_hours,
                    quoted_labour_cost = excluded.quoted_labour_cost,
                    quoted_materials = excluded.quoted_materials,
                    quoted_access_equipment = excluded.quoted_access_equipment,
                    quoted_subcontractors = excluded.quoted_subcontractors,
                    quoted_sundries = excluded.quoted_sundries,
                    target_gp_percent = excluded.target_gp_percent,
                    locked_at = excluded.locked_at,
                    locked_by = excluded.locked_by,
                    notes = excluded.notes
            """, (
                job_id,
                labour_hours,
                round(labour_hours * labour_rate, 2),
                material_allowance,
                access_allowance,
                subcontractor_allowance,
                sundries_allowance,
                target_gp,
                now,
                imported_by,
                budget_note,
            ))

        if import_materials:
            material_rows = []
            for _, row in parsed["materials"].iterrows():
                material_rows.append({
                    "code": _takeoff_text(row.get("Product Code / Ref"), "CUSTOM"),
                    "name": _takeoff_text(row.get("Product / Material Name")),
                    "supplier": _takeoff_text(row.get("Supplier")),
                    "unit": _takeoff_text(row.get("Unit"), "each"),
                    "unit_price": _takeoff_float(row.get("Unit Price Ex GST")),
                    "colour": _takeoff_text(row.get("Colour / Finish")),
                    "qty": _takeoff_float(row.get("Qty Required")),
                    "location": _takeoff_text(row.get("Location")),
                    "substrate": _takeoff_text(row.get("Substrate")),
                    "system": _takeoff_text(row.get("Coating System")),
                    "notes": _takeoff_text(row.get("Notes")),
                })
            for _, row in parsed["colours"].iterrows():
                material_rows.append({
                    "code": "COLOUR-SCHEDULE",
                    "name": _takeoff_text(row.get("Product / Material Name"), "Colour schedule"),
                    "supplier": "",
                    "unit": "schedule",
                    "unit_price": 0.0,
                    "colour": _takeoff_text(row.get("Colour / Finish")),
                    "qty": 0.0,
                    "location": _takeoff_text(row.get("Location")),
                    "substrate": _takeoff_text(row.get("Substrate")),
                    "system": _takeoff_text(row.get("Coating System")),
                    "notes": _takeoff_text(row.get("Notes")),
                })

            seen_materials = set()
            for row in material_rows:
                dedupe_key = tuple(str(row.get(key, "")).strip().casefold() for key in ["code", "name", "colour", "location", "substrate", "system"])
                if dedupe_key in seen_materials:
                    continue
                seen_materials.add(dedupe_key)
                if not row["name"]:
                    continue
                cur.execute(
                    "SELECT id FROM products WHERE LOWER(TRIM(product_code)) = LOWER(TRIM(?)) LIMIT 1",
                    (row["code"],),
                )
                product_match = cur.fetchone()
                product_id = int(product_match[0]) if product_match else None
                detail_notes = " | ".join(filter(None, [
                    row["notes"],
                    f"Location: {row['location']}" if row["location"] else "",
                    f"Substrate: {row['substrate']}" if row["substrate"] else "",
                    f"System: {row['system']}" if row["system"] else "",
                    f"Job Pack: {pack_id} rev {revision}",
                ]))
                cur.execute("""
                    INSERT INTO material_entries
                    (job_id, product_id, qty_required, qty_received, date_ordered, supplier, notes,
                     custom_product_code, custom_product_name, custom_supplier, custom_unit,
                     custom_unit_price, custom_colour)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    job_id,
                    product_id,
                    row["qty"],
                    0.0,
                    "",
                    row["supplier"],
                    detail_notes,
                    "" if product_id else row["code"],
                    "" if product_id else row["name"],
                    "" if product_id else row["supplier"],
                    "" if product_id else row["unit"],
                    None if product_id else row["unit_price"],
                    row["colour"],
                ))
                material_count += 1

        if attach_documents:
            for document in parsed["documents"]:
                file_path = extracted_paths.get(document["member"])
                if not file_path or not Path(file_path).exists():
                    continue
                cur.execute("""
                    INSERT INTO job_documents
                    (job_id, document_type, file_name, file_path, created_at, notes, mime_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    job_id,
                    document["document_type"],
                    document["file_name"],
                    str(file_path),
                    now,
                    f"Imported from Job Pack {pack_id}, revision {revision}.",
                    document["mime_type"],
                ))
                document_count += 1

        cur.execute("""
            INSERT INTO takeoff_pack_imports
            (job_id, pack_id, revision, source_file, imported_at, imported_by,
             estimate_id, line_count, material_count, document_count, manifest_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id,
            pack_id,
            revision,
            parsed.get("source_name", ""),
            now,
            imported_by,
            estimate_id,
            line_count,
            material_count,
            document_count,
            json.dumps(parsed.get("manifest") or {}, default=str, sort_keys=True),
        ))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        if pack_folder is not None:
            shutil.rmtree(pack_folder, ignore_errors=True)
        raise
    finally:
        conn.close()

    record_audit_event(
        "job_pack_imported",
        "job",
        job_id,
        {
            "pack_id": pack_id,
            "revision": revision,
            "job_action": job_action,
            "builder_action": builder_action,
            "estimate_id": estimate_id,
            "line_count": line_count,
            "material_count": material_count,
            "document_count": document_count,
        },
    )
    return {
        "job_id": job_id,
        "job_no": job_no,
        "job_action": job_action,
        "builder_action": builder_action,
        "estimate_id": estimate_id,
        "line_count": line_count,
        "material_count": material_count,
        "document_count": document_count,
        "pack_folder": str(pack_folder),
        "labour_hours": _takeoff_float(summary.get("labour_hours")),
        "material_allowance": _takeoff_float(summary.get("material_allowance")),
    }



def takeoff_job_pack_import_page():
    st.header("Import / Create Job Pack")
    st.caption(
        "Upload one Premier Brushworks Job Pack ZIP. JobHub can create a complete new job or update an existing job, "
        "then import the builder/client, contract price, colours, take-off, budgets, plans, scope, specifications, "
        "purchase orders and supporting documents. Nothing is saved until the final review is confirmed."
    )

    st.download_button(
        "Download Job Pack Template",
        data=build_takeoff_job_pack_template(),
        file_name="PB_JobHub_Job_Pack_Template.zip",
        mime="application/zip",
        key="download_takeoff_job_pack_template",
    )

    uploaded_pack = st.file_uploader(
        "Choose a Premier Brushworks Job Pack ZIP",
        type=["zip"],
        key="takeoff_job_pack_upload",
    )
    if uploaded_pack is None:
        st.info("Choose a ZIP to preview the job details and documents before anything is saved.")
        return

    try:
        parsed = parse_takeoff_job_pack(uploaded_pack)
    except Exception as exc:
        pb_error(f"Could not read this Job Pack: {exc}")
        return

    summary = parsed["summary"]
    st.markdown("### Pack Preview")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Pack ID", summary["pack_id"])
    m2.metric("Revision", summary["revision"])
    m3.metric("Take-off Lines", len(parsed["lines"]))
    m4.metric("Colours / Materials", len(parsed["colours"]) + len(parsed["materials"]))
    m5.metric("Documents", len(parsed["documents"]))

    job_options = get_job_options()
    action_options = ["Create a new job from this pack"]
    if job_options:
        action_options.append("Update an existing job")
    default_action = 0
    manifest_job_no = _takeoff_text(summary.get("job_no"))
    manifest_match_label = None
    if manifest_job_no and job_options:
        for label in job_options:
            if label.split(" - ", 1)[0].strip().casefold() == manifest_job_no.casefold():
                manifest_match_label = label
                default_action = 1
                break

    job_action_label = st.radio(
        "What should JobHub do?",
        action_options,
        index=default_action if default_action < len(action_options) else 0,
        horizontal=True,
        key="job_pack_job_action",
    )
    create_new_job = job_action_label.startswith("Create")
    job_mode = "create" if create_new_job else "update"
    selected_job_id = None
    selected_job_label = "New job"
    current = None

    if not create_new_job:
        labels = list(job_options.keys())
        default_index = labels.index(manifest_match_label) if manifest_match_label in labels else 0
        selected_job_label = st.selectbox(
            "Existing job to update",
            labels,
            index=default_index,
            key="takeoff_pack_target_job",
        )
        selected_job_id = job_options[selected_job_label]
        current_df = df_query("""
            SELECT j.*, COALESCE(bc.type, '') AS builder_client_type,
                   COALESCE(bc.name, '') AS builder_name,
                   COALESCE(bc.contact_name, '') AS builder_contact_name,
                   COALESCE(bc.phone, '') AS builder_phone,
                   COALESCE(bc.email, '') AS builder_email,
                   COALESCE(bc.address, '') AS builder_address,
                   COALESCE(bc.qbcc, '') AS builder_qbcc,
                   COALESCE(bc.abn, '') AS builder_abn,
                   COALESCE(bc.terms, '') AS builder_terms,
                   COALESCE(bc.notes, '') AS builder_notes
            FROM jobs j
            LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
            WHERE j.id = ?
        """, (selected_job_id,))
        if current_df.empty:
            pb_error("The selected job could not be loaded.")
            return
        current = current_df.iloc[0]

        previous = df_query("""
            SELECT pack_id AS 'Pack ID', revision AS 'Revision', source_file AS 'Source File',
                   imported_at AS 'Imported At', imported_by AS 'Imported By',
                   line_count AS 'Lines', material_count AS 'Materials', document_count AS 'Documents'
            FROM takeoff_pack_imports
            WHERE job_id = ?
            ORDER BY id DESC
        """, (selected_job_id,))
        if not previous.empty:
            with st.expander("Previous Job Pack Imports", expanded=False):
                st.dataframe(previous, width="stretch", hide_index=True)

    update_job_record = True
    if not create_new_job:
        update_job_record = st.checkbox(
            "Update the existing job details from this pack",
            value=True,
            key="job_pack_update_existing_record",
            help="Untick this to attach the pack data and documents without changing the job number, builder, price, address or other job details.",
        )

    def pack_or_existing(pack_key, existing_key=None, default=""):
        pack_value = summary.get(pack_key)
        if isinstance(pack_value, (int, float)):
            if float(pack_value or 0) != 0:
                return pack_value
        elif _takeoff_text(pack_value):
            return pack_value
        if current is not None and existing_key:
            return current.get(existing_key, default)
        return default

    proposed_job_no = (
        _takeoff_text(summary.get("job_no")) or next_job_no()
        if create_new_job
        else _takeoff_text(current.get("job_no"))
    )
    if (
        not create_new_job
        and manifest_job_no
        and manifest_job_no.casefold() != proposed_job_no.casefold()
    ):
        st.warning(
            f"The pack job number is {manifest_job_no}, but you selected {proposed_job_no}. "
            "The existing job number will be preserved unless you manually change it below."
        )
    proposed_job_name = _takeoff_text(pack_or_existing("job_name", "job_name"))
    proposed_address = _takeoff_text(pack_or_existing("site_address", "site_address"))
    proposed_builder = _takeoff_text(pack_or_existing("builder_client", "builder_name"))
    proposed_contract = _takeoff_float(pack_or_existing("contract_value_ex_gst", "contract_value", 0))
    proposed_status = _takeoff_text(pack_or_existing("status", "status", "Quoted" if create_new_job else "Not Started"))
    proposed_leading_hand = _takeoff_text(pack_or_existing("leading_hand", "leading_hand"))
    proposed_start = _takeoff_text(pack_or_existing("start_date", "start_date"))
    proposed_end = _takeoff_text(pack_or_existing("end_date", "end_date"))
    existing_notes = _takeoff_text(current.get("notes")) if current is not None else ""
    pack_notes = _takeoff_text(summary.get("job_notes"))
    proposed_notes = "\n".join(dict.fromkeys(filter(None, [existing_notes, pack_notes])))

    st.markdown("### Job Details to Create / Update")
    disabled_job_fields = (not create_new_job and not update_job_record)
    c1, c2 = st.columns(2)
    final_job_no = c1.text_input(
        "Job Number",
        value=proposed_job_no,
        disabled=disabled_job_fields,
        key="job_pack_final_job_no",
    )
    final_job_name = c2.text_input(
        "Job Name",
        value=proposed_job_name,
        disabled=disabled_job_fields,
        key="job_pack_final_job_name",
    )
    final_site_address = st.text_input(
        "Site Address",
        value=proposed_address,
        disabled=disabled_job_fields,
        key="job_pack_final_site_address",
    )

    c3, c4, c5 = st.columns(3)
    status_options = ["Not Started", "Quoted", "Booked", "Active", "On Hold", "Completed", "Invoiced", "Paid", "Archived"]
    if proposed_status and proposed_status not in status_options:
        status_options.append(proposed_status)
    final_status = c3.selectbox(
        "Job Status",
        status_options,
        index=status_options.index(proposed_status) if proposed_status in status_options else 0,
        disabled=disabled_job_fields,
        key="job_pack_final_status",
    )
    employee_names = list(get_employee_options(active_only=False).keys())
    leading_options = [""] + employee_names
    if proposed_leading_hand and proposed_leading_hand not in leading_options:
        leading_options.append(proposed_leading_hand)
    final_leading_hand = c4.selectbox(
        "Leading Hand",
        leading_options,
        index=leading_options.index(proposed_leading_hand) if proposed_leading_hand in leading_options else 0,
        disabled=disabled_job_fields,
        key="job_pack_final_leading_hand",
    )
    final_contract_value = c5.number_input(
        "Contract Price Ex GST",
        min_value=0.0,
        step=100.0,
        value=float(proposed_contract),
        disabled=disabled_job_fields,
        key="job_pack_final_contract_value",
    )

    c6, c7 = st.columns(2)
    final_start_date = c6.text_input(
        "Start Date (YYYY-MM-DD)",
        value=proposed_start,
        disabled=disabled_job_fields,
        key="job_pack_final_start_date",
    )
    final_end_date = c7.text_input(
        "End Date (YYYY-MM-DD)",
        value=proposed_end,
        disabled=disabled_job_fields,
        key="job_pack_final_end_date",
    )
    final_job_notes = st.text_area(
        "Job Notes / Scope Summary",
        value=proposed_notes,
        disabled=disabled_job_fields,
        key="job_pack_final_job_notes",
    )

    st.markdown("#### Builder / Client")
    final_builder_name = st.text_input(
        "Builder / Client Name",
        value=proposed_builder,
        disabled=disabled_job_fields,
        key="job_pack_final_builder_name",
    )
    with st.expander("Builder / client contact details", expanded=bool(summary.get("builder_email") or summary.get("builder_phone"))):
        bc1, bc2 = st.columns(2)
        final_builder_type = bc1.text_input(
            "Type",
            value=_takeoff_text(pack_or_existing("builder_client_type", "builder_client_type", "Builder / Client")),
            disabled=disabled_job_fields,
            key="job_pack_builder_type",
        )
        final_builder_contact = bc2.text_input(
            "Contact Name",
            value=_takeoff_text(pack_or_existing("builder_contact_name", "builder_contact_name")),
            disabled=disabled_job_fields,
            key="job_pack_builder_contact",
        )
        bc3, bc4 = st.columns(2)
        final_builder_phone = bc3.text_input(
            "Phone",
            value=_takeoff_text(pack_or_existing("builder_phone", "builder_phone")),
            disabled=disabled_job_fields,
            key="job_pack_builder_phone",
        )
        final_builder_email = bc4.text_input(
            "Email",
            value=_takeoff_text(pack_or_existing("builder_email", "builder_email")),
            disabled=disabled_job_fields,
            key="job_pack_builder_email",
        )
        final_builder_address = st.text_input(
            "Builder / Client Address",
            value=_takeoff_text(pack_or_existing("builder_address", "builder_address")),
            disabled=disabled_job_fields,
            key="job_pack_builder_address",
        )
        bc5, bc6, bc7 = st.columns(3)
        final_builder_qbcc = bc5.text_input(
            "QBCC",
            value=_takeoff_text(pack_or_existing("builder_qbcc", "builder_qbcc")),
            disabled=disabled_job_fields,
            key="job_pack_builder_qbcc",
        )
        final_builder_abn = bc6.text_input(
            "ABN",
            value=_takeoff_text(pack_or_existing("builder_abn", "builder_abn")),
            disabled=disabled_job_fields,
            key="job_pack_builder_abn",
        )
        final_builder_terms = bc7.text_input(
            "Payment Terms",
            value=_takeoff_text(pack_or_existing("builder_terms", "builder_terms")),
            disabled=disabled_job_fields,
            key="job_pack_builder_terms",
        )
        final_builder_notes = st.text_area(
            "Builder / Client Notes",
            value=_takeoff_text(pack_or_existing("builder_notes", "builder_notes")),
            disabled=disabled_job_fields,
            key="job_pack_builder_notes",
        )

    create_missing_builder = st.checkbox(
        "Create this builder/client if it is not already in JobHub",
        value=True,
        disabled=disabled_job_fields,
        key="job_pack_create_missing_builder",
    )
    fill_blank_builder_details = st.checkbox(
        "Fill blank fields on an existing builder/client record from this pack",
        value=True,
        disabled=disabled_job_fields,
        key="job_pack_fill_builder_blanks",
        help="Existing non-blank contact details are not overwritten.",
    )

    st.markdown("#### Approved Paint Supplier / Brand")
    supplier_options = get_product_supplier_options()
    current_suppliers = parse_material_supplier_list(current.get("allowed_material_suppliers", "")) if current is not None else []
    pack_suppliers = _takeoff_list(summary.get("allowed_material_suppliers"))
    default_suppliers = pack_suppliers or current_suppliers
    for supplier in default_suppliers:
        if supplier not in supplier_options:
            supplier_options.append(supplier)
    if summary.get("restrict_material_products_supplied"):
        default_restrict = bool(summary.get("restrict_material_products"))
    elif current is not None:
        default_restrict = bool(int(current.get("restrict_material_products", 0) or 0))
    else:
        default_restrict = bool(default_suppliers)
    final_restrict_products = st.checkbox(
        "Only show approved suppliers/brands for this job",
        value=default_restrict,
        disabled=disabled_job_fields,
        key="job_pack_restrict_products",
    )
    final_allowed_suppliers = st.multiselect(
        "Approved suppliers / brands",
        supplier_options,
        default=default_suppliers,
        disabled=disabled_job_fields,
        key="job_pack_allowed_suppliers",
    )

    job_details = {
        "job_no": final_job_no,
        "job_name": final_job_name,
        "site_address": final_site_address,
        "status": final_status,
        "leading_hand": final_leading_hand,
        "start_date": final_start_date,
        "end_date": final_end_date,
        "contract_value_ex_gst": final_contract_value,
        "job_notes": final_job_notes,
        "builder_client": final_builder_name,
        "builder_client_type": final_builder_type,
        "builder_contact_name": final_builder_contact,
        "builder_phone": final_builder_phone,
        "builder_email": final_builder_email,
        "builder_address": final_builder_address,
        "builder_qbcc": final_builder_qbcc,
        "builder_abn": final_builder_abn,
        "builder_terms": final_builder_terms,
        "builder_notes": final_builder_notes,
        "restrict_material_products": final_restrict_products,
        "allowed_material_suppliers": final_allowed_suppliers,
    }

    preview_summary = pd.DataFrame([{
        "Action": "Create New Job" if create_new_job else f"Update {selected_job_label}",
        "Job Number": final_job_no,
        "Job Name": final_job_name,
        "Builder / Client": final_builder_name,
        "Site Address": final_site_address,
        "Contract Price Ex GST": f"${final_contract_value:,.2f}",
        "Status": final_status,
        "Leading Hand": final_leading_hand,
        "Approved Brands": ", ".join(final_allowed_suppliers),
        "Restrict Products": "Yes" if final_restrict_products else "No",
    }])
    st.dataframe(preview_summary, width="stretch", hide_index=True)

    if not parsed["lines"].empty:
        st.markdown("#### Take-off Lines and Item Labour Hours")
        st.dataframe(parsed["lines"], width="stretch", hide_index=True)
    if not parsed["labour"].empty:
        with st.expander("Labour Budget File", expanded=False):
            st.dataframe(parsed["labour"], width="stretch", hide_index=True)
    if not parsed["materials"].empty:
        st.markdown("#### Material Cost Allowances")
        st.dataframe(parsed["materials"], width="stretch", hide_index=True)
    if not parsed["colours"].empty:
        st.markdown("#### Colour / Finish Schedule")
        st.dataframe(parsed["colours"], width="stretch", hide_index=True)
    if parsed["documents"]:
        documents_preview = pd.DataFrame([{
            "Document Type": item["document_type"],
            "File Name": item["file_name"],
            "Size MB": round(item["size_bytes"] / (1024 * 1024), 2),
        } for item in parsed["documents"]])
        st.markdown("#### Plans and Documents to Attach")
        st.dataframe(documents_preview, width="stretch", hide_index=True)
    else:
        st.warning("No plans, specifications, colour schedule, purchase order or supporting documents were found in the ZIP.")

    st.markdown("### Import Options")
    has_estimate_data = (
        not parsed["lines"].empty
        or summary["labour_hours"] > 0
        or summary["material_allowance"] > 0
        or summary["access_equipment_allowance"] > 0
        or summary["subcontractor_allowance"] > 0
        or summary["sundries_allowance"] > 0
    )
    c8, c9 = st.columns(2)
    create_estimate = c8.checkbox(
        "Create draft Estimate Working Sheet",
        value=has_estimate_data,
        key="takeoff_import_create_estimate",
    )
    update_budget = c9.checkbox(
        "Update / lock Job Budget from imported allowances",
        value=has_estimate_data,
        key="takeoff_import_budget",
    )
    c10, c11 = st.columns(2)
    import_materials = c10.checkbox(
        "Import Materials and Colour Schedule",
        value=not parsed["materials"].empty or not parsed["colours"].empty,
        key="takeoff_import_materials",
    )
    attach_documents = c11.checkbox(
        "Attach supplied plans and documents to the Job Folder",
        value=bool(parsed["documents"]),
        key="takeoff_import_documents",
    )
    use_line_pricing = st.checkbox(
        "Use imported line rates and line totals in estimate pricing",
        value=False,
        help=(
            "Leave this off for normal internal Job Packs so labour and material allowances are not counted twice. "
            "The imported reference rates are retained in line notes."
        ),
        key="takeoff_import_line_pricing",
    )

    validation_messages = []
    if create_new_job and not _takeoff_text(final_job_no):
        validation_messages.append("Enter a job number.")
    if create_new_job and not _takeoff_text(final_job_name):
        validation_messages.append("Enter a job name.")
    if final_restrict_products and not final_allowed_suppliers:
        validation_messages.append("Select at least one approved supplier/brand or turn off the product restriction.")
    if validation_messages:
        for message in validation_messages:
            pb_error(message)

    confirmation_payload = {
        "job_mode": job_mode,
        "job_id": selected_job_id,
        "job_details": job_details,
        "pack_id": summary["pack_id"],
        "revision": summary["revision"],
        "update_job_record": update_job_record,
        "create_estimate": create_estimate,
        "update_budget": update_budget,
        "import_materials": import_materials,
        "attach_documents": attach_documents,
        "use_line_pricing": use_line_pricing,
    }
    accepted = review_acceptance_checkbox(
        "job_pack_import_create_update",
        confirmation_payload,
        "I have reviewed the job, builder/client, price, colours, plans, pack revision and import options and approve this action.",
    )

    if st.button(
        "Create / Update Job from Pack",
        type="primary",
        disabled=not accepted or bool(validation_messages),
        key="takeoff_job_pack_import_button",
    ):
        try:
            result = import_takeoff_job_pack(
                selected_job_id,
                parsed,
                create_estimate=create_estimate,
                update_budget=update_budget,
                import_materials=import_materials,
                attach_documents=attach_documents,
                use_imported_line_pricing=use_line_pricing,
                job_mode=job_mode,
                job_details=job_details,
                update_job_record=update_job_record,
                create_missing_builder=create_missing_builder,
                fill_blank_builder_details=fill_blank_builder_details,
            )
            action_text = {
                "created": "created",
                "updated": "updated",
                "linked_without_job_changes": "linked without changing its job details",
            }.get(result["job_action"], "updated")
            pb_success(
                f"Job {result['job_no']} was successfully {action_text}. "
                f"Added {result['line_count']} take-off line(s), "
                f"{result['material_count']} material/colour row(s), and "
                f"{result['document_count']} plan/document(s)."
            )
            st.info(
                f"Budget: {result['labour_hours']:.2f} estimated labour hours and "
                f"${result['material_allowance']:,.2f} material allowance."
            )
            st.caption(f"Saved Job Pack folder: {result['pack_folder']}")
        except Exception as exc:
            pb_error(f"The Job Pack was not imported: {exc}")



def estimate_totals(
    estimate_id,
    labour_hours,
    labour_rate,
    material_allowance,
    access_equipment_allowance,
    subcontractor_allowance,
    sundries_allowance,
    margin_percent,
    contingency_percent,
    gst_percent,
    pricing_method="Target Gross Margin",
):
    line_df = df_query("SELECT COALESCE(SUM(line_total), 0) AS line_total FROM estimate_line_items WHERE estimate_id = ?", (estimate_id,))
    line_total = float(line_df.iloc[0]["line_total"] or 0) if not line_df.empty else 0.0
    return calculate_estimate_pricing(
        line_total=line_total,
        labour_hours=labour_hours,
        labour_rate=PLANNING_LABOUR_RATE,
        material_allowance=material_allowance,
        access_equipment_allowance=access_equipment_allowance,
        subcontractor_allowance=subcontractor_allowance,
        sundries_allowance=sundries_allowance,
        pricing_percent=margin_percent,
        contingency_percent=contingency_percent,
        gst_percent=gst_percent,
        pricing_method=pricing_method,
    )


def recalc_estimate_totals(estimate_id):
    est = df_query("SELECT * FROM estimate_working_sheets WHERE id = ?", (estimate_id,))
    if est.empty:
        return
    r = est.iloc[0]
    totals = estimate_totals(
        estimate_id,
        r["labour_hours"], r["labour_rate"], r["material_allowance"], r["access_equipment_allowance"],
        r["subcontractor_allowance"], r["sundries_allowance"], r["margin_percent"],
        r["contingency_percent"], r["gst_percent"],
        r.get("pricing_method") or "Markup",
    )
    execute("""
        UPDATE estimate_working_sheets
        SET labour_rate = ?, total_ex_gst = ?, gst_amount = ?, total_inc_gst = ?, updated_at = ?
        WHERE id = ?
    """, (
        PLANNING_LABOUR_RATE,
        totals["total_ex_gst"],
        totals["gst_amount"],
        totals["total_inc_gst"],
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        estimate_id,
    ))


# PB_ESTIMATE_ARCHIVE_DELETE_V1
def permanently_delete_estimate_working_sheet(estimate_id):
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM estimate_line_items WHERE estimate_id = ?", (int(estimate_id),))
        cur.execute("DELETE FROM estimate_working_sheets WHERE id = ?", (int(estimate_id),))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def render_estimate_archive_delete_controls(selected_estimate_id, current):
    if not is_manager_or_admin():
        return

    is_archived = int(current.get("archived") or 0) == 1
    estimate_no = str(current.get("estimate_no") or f"Estimate {selected_estimate_id}")
    revision = str(current.get("revision") or "")
    estimate_label = f"{estimate_no} {revision}".strip()

    with st.expander("Manage Estimate Working Sheet", expanded=False):
        st.caption("Admin and Manager access only.")

        if is_archived:
            st.warning("This estimate is archived and hidden from the normal estimate list.")
            if st.button("Restore Estimate Working Sheet", key=f"restore_estimate_{selected_estimate_id}"):
                execute("""
                    UPDATE estimate_working_sheets
                    SET archived = 0, archived_at = '', archived_by = ''
                    WHERE id = ?
                """, (selected_estimate_id,))
                pb_success(f"{estimate_label} restored successfully.")
                st.session_state.pop("estimate_select", None)
                refresh()
        else:
            archive_confirmed = st.checkbox(
                "Confirm archive of this estimate working sheet",
                key=f"confirm_archive_estimate_{selected_estimate_id}",
            )
            if st.button("Archive Estimate Working Sheet", key=f"archive_estimate_{selected_estimate_id}"):
                if not archive_confirmed:
                    pb_error("Tick the archive confirmation box first.")
                else:
                    user = get_current_user() or {}
                    archived_by = str(user.get("username") or user.get("employee_name") or current_role() or "manager")
                    execute("""
                        UPDATE estimate_working_sheets
                        SET archived = 1, archived_at = ?, archived_by = ?
                        WHERE id = ?
                    """, (
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        archived_by,
                        selected_estimate_id,
                    ))
                    pb_success(f"{estimate_label} archived successfully.")
                    st.session_state.pop("estimate_select", None)
                    refresh()

        st.divider()
        pb_error("Permanent deletion cannot be undone. It removes the estimate and every linked line item.")
        delete_text = st.text_input(
            "Type DELETE to permanently remove this estimate",
            key=f"delete_estimate_text_{selected_estimate_id}",
        )
        delete_confirmed = st.checkbox(
            "I understand this estimate and its line items will be permanently deleted",
            key=f"delete_estimate_checkbox_{selected_estimate_id}",
        )
        if st.button("Delete Estimate Permanently", key=f"delete_estimate_permanently_{selected_estimate_id}"):
            if str(delete_text or "").strip().upper() != "DELETE":
                pb_error("Type DELETE exactly before permanently deleting the estimate.")
            elif not delete_confirmed:
                pb_error("Tick the permanent deletion confirmation box first.")
            else:
                permanently_delete_estimate_working_sheet(selected_estimate_id)
                st.session_state.pop("estimate_select", None)
                pb_success(f"{estimate_label} deleted permanently.")
                refresh()


def estimate_working_sheet_page():
    st.header("Estimate Working Sheet")
    st.caption("Build a working estimate and link it directly to the job it relates to.")

    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first, then you can create an estimate working sheet.")
        return

    selected_job = st.selectbox("Select Job", list(job_options.keys()), key="estimate_job_select")
    selected_job_id = job_options[selected_job]

    job_details = df_query("""
        SELECT j.job_no AS 'Job No', j.job_name AS 'Job Name', bc.name AS 'Builder / Client',
               j.site_address AS 'Site Address', j.status AS 'Status', j.contract_value AS 'Contract Value'
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.id = ?
    """, (selected_job_id,))
    if not job_details.empty:
        st.dataframe(job_details, width="stretch", hide_index=True)

    show_archived_estimates = st.checkbox(
        "Show archived estimate working sheets",
        value=False,
        key=f"show_archived_estimates_{selected_job_id}",
    )
    archived_estimate_filter = "" if show_archived_estimates else "AND COALESCE(archived, 0) = 0"

    estimates = df_query(f"""
        SELECT id, estimate_no, revision, estimate_date, status,
               total_ex_gst, total_inc_gst, COALESCE(archived, 0) AS archived
        FROM estimate_working_sheets
        WHERE job_id = ?
        {archived_estimate_filter}
        ORDER BY id DESC
    """, (selected_job_id,))

    with st.expander("Create New Estimate Working Sheet", expanded=estimates.empty):
        estimate_count_df = df_query(
            "SELECT COUNT(*) AS total FROM estimate_working_sheets WHERE job_id = ?",
            (selected_job_id,),
        )
        next_rev = int(estimate_count_df.iloc[0]["total"] or 0) + 1 if not estimate_count_df.empty else 1
        default_job_no = "EST"
        if not job_details.empty:
            default_job_no = str(job_details.iloc[0]["Job No"])
        with st.form("create_estimate_form"):
            col1, col2, col3 = st.columns(3)
            estimate_no = col1.text_input("Estimate No", value=f"{default_job_no}-EST-{next_rev:02d}")
            estimate_date = col2.text_input("Estimate Date", value=str(date.today()))
            revision = col3.text_input("Revision", value=f"Rev {next_rev}")
            notes = st.text_area("Initial Notes")
            created = st.form_submit_button("Create Estimate Working Sheet")
            if created:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                execute("""
                    INSERT INTO estimate_working_sheets
                    (job_id, estimate_no, estimate_date, revision, status, labour_hours, labour_rate,
                     material_allowance, access_equipment_allowance, subcontractor_allowance, sundries_allowance,
                     margin_percent, contingency_percent, gst_percent, pricing_method,
                     total_ex_gst, gst_amount, total_inc_gst,
                     created_at, updated_at, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    selected_job_id, estimate_no, estimate_date, revision, "Draft",
                    0, PLANNING_LABOUR_RATE, 0, 0, 0, 0, 35, 0, 10, "Target Gross Margin",
                    0, 0, 0, now, now, notes,
                ))
                record_audit_event("estimate_created", "estimate", estimate_no, {"job_id": selected_job_id})
                pb_success("Estimate working sheet created.")
                refresh()

    estimates = df_query(f"""
        SELECT id, estimate_no, revision, estimate_date, status,
               total_ex_gst, total_inc_gst, COALESCE(archived, 0) AS archived
        FROM estimate_working_sheets
        WHERE job_id = ?
        {archived_estimate_filter}
        ORDER BY id DESC
    """, (selected_job_id,))

    if estimates.empty:
        st.info("No estimate working sheets saved for this job yet.")
        return

    estimate_options = {
        (
            f"{row['estimate_no']} - {row['revision']} - {row['status']} - "
            f"${float(row['total_inc_gst'] or 0):,.2f} inc GST"
            f"{' - ARCHIVED' if int(row.get('archived') or 0) == 1 else ''}"
        ): int(row["id"])
        for _, row in estimates.iterrows()
    }
    selected_estimate_label = st.selectbox("Select Estimate Working Sheet", list(estimate_options.keys()), key="estimate_select")
    selected_estimate_id = estimate_options[selected_estimate_label]

    current = df_query("SELECT * FROM estimate_working_sheets WHERE id = ?", (selected_estimate_id,))
    if current.empty:
        st.warning("Selected estimate could not be found.")
        return
    current = current.iloc[0]

    render_estimate_archive_delete_controls(selected_estimate_id, current)

    tab_summary, tab_lines, tab_view = st.tabs(["Summary / Pricing", "Line Items", "View / Export"])

    with tab_summary:
        with st.form("estimate_summary_form"):
            col1, col2, col3, col4 = st.columns(4)
            estimate_no = col1.text_input("Estimate No", value=str(current["estimate_no"] or ""))
            estimate_date = col2.text_input("Estimate Date", value=str(current["estimate_date"] or str(date.today())))
            revision = col3.text_input("Revision", value=str(current["revision"] or ""))
            statuses = ["Draft", "Sent", "Approved", "Lost", "Superseded"]
            current_status = str(current["status"] or "Draft")
            status_index = statuses.index(current_status) if current_status in statuses else 0
            status = col4.selectbox("Status", statuses, index=status_index)

            col5, col6 = st.columns(2)
            labour_hours = col5.number_input("Labour Hours", min_value=0.0, step=1.0, value=float(current["labour_hours"] or 0))
            labour_rate = col6.number_input(
                "Planning Labour Rate",
                min_value=0.0,
                step=5.0,
                value=PLANNING_LABOUR_RATE,
                disabled=True,
                help="Estimates use the fixed planning rate. Actual job costs use each employee's recorded wage cost.",
            )

            col7, col8, col9, col10 = st.columns(4)
            material_allowance = col7.number_input("Material Allowance", min_value=0.0, step=100.0, value=float(current["material_allowance"] or 0))
            access_equipment_allowance = col8.number_input("Access / Equipment Allowance", min_value=0.0, step=100.0, value=float(current["access_equipment_allowance"] or 0))
            subcontractor_allowance = col9.number_input("Subcontractor Allowance", min_value=0.0, step=100.0, value=float(current["subcontractor_allowance"] or 0))
            sundries_allowance = col10.number_input("Sundries / Consumables", min_value=0.0, step=50.0, value=float(current["sundries_allowance"] or 0))

            col11, col12, col13, col14 = st.columns(4)
            pricing_methods = ["Target Gross Margin", "Markup"]
            current_pricing_method = str(current.get("pricing_method") or "Markup")
            if current_pricing_method not in pricing_methods:
                current_pricing_method = "Markup"
            pricing_method = col11.selectbox(
                "Pricing Method",
                pricing_methods,
                index=pricing_methods.index(current_pricing_method),
                help="Gross margin divides by 1 − margin rate. Markup adds a percentage to cost.",
            )
            pricing_label = (
                "Target Gross Margin %"
                if pricing_method == "Target Gross Margin"
                else "Markup %"
            )
            margin_percent = col12.number_input(
                pricing_label,
                min_value=0.0,
                max_value=99.0 if pricing_method == "Target Gross Margin" else 500.0,
                step=1.0,
                value=min(
                    float(current["margin_percent"] or 0),
                    99.0 if pricing_method == "Target Gross Margin" else 500.0,
                ),
            )
            contingency_percent = col13.number_input("Contingency %", min_value=0.0, max_value=100.0, step=1.0, value=float(current["contingency_percent"] or 0))
            gst_percent = col14.number_input("GST %", min_value=0.0, max_value=100.0, step=1.0, value=float(current["gst_percent"] or 10))
            notes = st.text_area("Notes / Scope Notes", value=str(current["notes"] or ""))

            preview = estimate_totals(
                selected_estimate_id, labour_hours, labour_rate, material_allowance,
                access_equipment_allowance, subcontractor_allowance, sundries_allowance,
                margin_percent, contingency_percent, gst_percent, pricing_method,
            )
            st.markdown("### Pricing Preview")
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Direct Cost", f"${preview['direct_total']:,.2f}")
            c2.metric("Pricing Addition", f"${preview['margin_amount']:,.2f}")
            c3.metric("Total Ex GST", f"${preview['total_ex_gst']:,.2f}")
            c4.metric("Total Inc GST", f"${preview['total_inc_gst']:,.2f}")
            c5.metric("Achieved Gross Margin", f"{preview['achieved_margin_percent']:,.2f}%")

            saved = st.form_submit_button("Save Estimate Summary")
            if saved:
                execute("""
                    UPDATE estimate_working_sheets
                    SET estimate_no = ?, estimate_date = ?, revision = ?, status = ?, labour_hours = ?, labour_rate = ?,
                        material_allowance = ?, access_equipment_allowance = ?, subcontractor_allowance = ?, sundries_allowance = ?,
                        margin_percent = ?, contingency_percent = ?, gst_percent = ?, pricing_method = ?,
                        total_ex_gst = ?, gst_amount = ?, total_inc_gst = ?,
                        updated_at = ?, notes = ?
                    WHERE id = ?
                """, (estimate_no, estimate_date, revision, status, labour_hours, labour_rate, material_allowance,
                      access_equipment_allowance, subcontractor_allowance, sundries_allowance, margin_percent, contingency_percent,
                      gst_percent, pricing_method, preview["total_ex_gst"], preview["gst_amount"], preview["total_inc_gst"],
                      datetime.now().strftime("%Y-%m-%d %H:%M:%S"), notes, selected_estimate_id))
                record_audit_event(
                    "estimate_pricing_updated",
                    "estimate",
                    selected_estimate_id,
                    {
                        "pricing_method": pricing_method,
                        "pricing_percent": margin_percent,
                        "total_ex_gst": preview["total_ex_gst"],
                    },
                )
                pb_success("Estimate summary saved.")
                refresh()

    with tab_lines:
        st.subheader("Estimate Line Items")
        render_estimate_line_item_csv_importer(selected_estimate_id)
        render_rate_library_estimate_adder(selected_estimate_id)

        st.markdown("#### Add Manual Line Item")
        with st.form("add_estimate_line_form"):
            col1, col2 = st.columns(2)
            section = col1.selectbox("Section", ["Preliminaries", "Labour", "Materials", "Access / Equipment", "Subcontractor", "Variations", "Other"])
            item_description = col2.text_input("Item Description")
            col3, col4, col5 = st.columns(3)
            qty = col3.number_input("Qty", min_value=0.0, step=1.0)
            unit = col4.text_input("Unit", value="item")
            unit_rate = col5.number_input("Unit Rate", min_value=0.0, step=10.0)
            line_notes = st.text_area("Line Notes")
            added = st.form_submit_button("Add Line Item")
            if added and item_description:
                line_total = round(float(qty or 0) * float(unit_rate or 0), 2)
                execute("""
                    INSERT INTO estimate_line_items
                    (estimate_id, section, item_description, qty, unit, unit_rate, line_total, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (selected_estimate_id, section, item_description, qty, unit, unit_rate, line_total, line_notes))
                recalc_estimate_totals(selected_estimate_id)
                pb_success("Line item added.")
                refresh()

        lines_df = df_query("""
            SELECT id, section AS 'Section', item_description AS 'Description', qty AS 'Qty', unit AS 'Unit',
                   estimated_labour_hours AS 'Estimated Labour Hours', material_allowance AS 'Material Allowance',
                   substrate AS 'Substrate', work_location AS 'Location', coating_system AS 'Coating System',
                   colour_finish AS 'Colour / Finish', unit_rate AS 'Unit Rate', line_total AS 'Line Total',
                   source_pack AS 'Source Pack', notes AS 'Notes'
            FROM estimate_line_items
            WHERE estimate_id = ?
            ORDER BY id
        """, (selected_estimate_id,))
        if lines_df.empty:
            st.info("No line items added yet.")
        else:
            st.dataframe(lines_df.drop(columns=["id"]), width="stretch", hide_index=True)
            st.metric("Line Item Total", f"${float(lines_df['Line Total'].fillna(0).sum()):,.2f}")
            delete_options = {f"{r['Section']} - {r['Description']} - ${float(r['Line Total'] or 0):,.2f}": int(r["id"]) for _, r in lines_df.iterrows()}
            selected_delete = st.selectbox("Line item to delete", list(delete_options.keys()))
            confirm = st.checkbox("Confirm delete selected line item")
            if st.button("Delete Selected Line Item"):
                if not confirm:
                    pb_error("Tick the confirm box first.")
                else:
                    execute("DELETE FROM estimate_line_items WHERE id = ?", (delete_options[selected_delete],))
                    recalc_estimate_totals(selected_estimate_id)
                    pb_success("Line item deleted.")
                    refresh()

    with tab_view:
        summary_df = df_query("""
            SELECT e.estimate_no AS 'Estimate No', e.revision AS 'Revision', e.estimate_date AS 'Date', e.status AS 'Status',
                   j.job_no AS 'Job No', j.job_name AS 'Job Name', e.labour_hours AS 'Labour Hours', e.labour_rate AS 'Labour Rate',
                   e.material_allowance AS 'Material Allowance', e.access_equipment_allowance AS 'Access / Equipment',
                   e.subcontractor_allowance AS 'Subcontractor', e.sundries_allowance AS 'Sundries', e.margin_percent AS 'Margin %',
                   e.contingency_percent AS 'Contingency %', e.total_ex_gst AS 'Total Ex GST', e.gst_amount AS 'GST',
                   e.total_inc_gst AS 'Total Inc GST', e.notes AS 'Notes'
            FROM estimate_working_sheets e
            JOIN jobs j ON j.id = e.job_id
            WHERE e.id = ?
        """, (selected_estimate_id,))
        lines_export = df_query("""
            SELECT section AS 'Section', item_description AS 'Description', qty AS 'Qty', unit AS 'Unit',
                   estimated_labour_hours AS 'Estimated Labour Hours', material_allowance AS 'Material Allowance',
                   substrate AS 'Substrate', work_location AS 'Location', coating_system AS 'Coating System',
                   colour_finish AS 'Colour / Finish', unit_rate AS 'Unit Rate', line_total AS 'Line Total',
                   source_pack AS 'Source Pack', notes AS 'Notes'
            FROM estimate_line_items
            WHERE estimate_id = ?
            ORDER BY id
        """, (selected_estimate_id,))
        st.markdown("### Estimate Summary")
        st.dataframe(summary_df, width="stretch", hide_index=True)
        st.markdown("### Estimate Lines")
        st.dataframe(lines_export, width="stretch", hide_index=True)

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            summary_df.to_excel(writer, index=False, sheet_name="Estimate Summary")
            lines_export.to_excel(writer, index=False, sheet_name="Estimate Lines")
            for ws in writer.book.worksheets:
                for column_cells in ws.columns:
                    max_len = 0
                    col_letter = column_cells[0].column_letter
                    for cell in column_cells:
                        value = "" if cell.value is None else str(cell.value)
                        max_len = max(max_len, len(value))
                    ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 45)
        output.seek(0)
        clean_name = str(summary_df.iloc[0]["Estimate No"] if not summary_df.empty else "estimate_working_sheet").replace("/", "-").replace("\\", "-")
        st.download_button(
            "Download Estimate Working Sheet Excel",
            data=output.getvalue(),
            file_name=f"{clean_name}_Estimate_Working_Sheet.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )



# =============================
# PRODUCT LIST RESTORE
# =============================
def restore_product_list():
    products = [('PB-H00001', 'Coverplus Interior L/S White', 'Haymes', '', 168.0, ''), ('PB-H00002', 'Elite Ceiling Toned White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00003', 'Elite Ceiling White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00004', 'Elite Interior Low Sheen White', 'Haymes', '', 118.0, ''), ('PB-H00005', 'Elite Interior Matt White, 15L', 'Haymes', '15L', 125.0, ''), ('PB-H00006', 'Elite Acrylic Sealer Undercoat', 'Haymes', '', 105.36, ''), ('PB-H00007', 'Elite Quick Dry Primer Undercoat', 'Haymes', '', 123.55, ''), ('PB-H00008', 'Expressions Low Sheen DKT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00009', 'Expressions Low Sheen EDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00010', 'Expressions Low Sheen UDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00011', 'Expressions Low Sheen White', 'Haymes', '', 107.48, ''), ('PB-H00012', 'Expressions Low Sheen White', 'Haymes', '', 145.0, ''), ('PB-H00013', 'Expressions Low Sheen White, 4L', 'Haymes', '4L', 67.26, ''), ('PB-H00014', 'Solashield Low Sheen DKT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00015', 'Solashield Low Sheen DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00016', 'Solashield Low Sheen DKT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00017', 'Solashield Low Sheen EDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00018', 'Solashield Low Sheen EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00019', 'Solashield Low Sheen EDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00020', 'Solashield Low Sheen UDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00021', 'Solashield Low Sheen UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00022', 'Solashield Low Sheen UDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00023', 'Solashield Low Sheen White, 10L', 'Haymes', '10L', 107.42, ''), ('PB-H00024', 'Solashield Low Sheen White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00025', 'Solashield Low Sheen White, 4L', 'Haymes', '4L', 67.4, ''), ('PB-H00026', 'R/Tex Roll On Coarse, 15L', 'Haymes', '15L', 175.0, ''), ('PB-H00027', 'Solashield Satin DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00028', 'Solashield Satin EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00029', 'Solashield Satin UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00030', 'Solashield Satin White, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00031', 'Solashield Satin White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00032', 'Ultra Premium Primer Sealer', 'Haymes', '', 167.46, ''), ('PB-H00033', 'Acrylic Sealer Undercoat', 'Haymes', '', 120.0, ''), ('PB-H00034', 'Ultratrim High Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00035', 'Ultratrim Semi Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00036', 'Woodcare Aqualac Floor Satin', 'Haymes', '', 250.44, '')]

    restored = 0
    for row in products:
        execute("""
            INSERT INTO products
            (product_code, product_name, supplier, unit, price_ex_gst, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_code) DO UPDATE SET
                product_name = excluded.product_name,
                supplier = excluded.supplier,
                unit = excluded.unit,
                price_ex_gst = excluded.price_ex_gst,
                notes = excluded.notes
        """, row)
        restored += 1

    return restored


def product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0


def restore_taubmans_product_list():
    products = [('T ALL WEATHER L/S W15L 18', '187200/15L', '30001918', '15L', 145.0), ('T ALL WEATHER L/S A15L 18', '187204/15L', '30001923', '15L', 150.0), ('T ALL WEATHER L/S N15L 18', '187205/15L', '30001928', '15L', 150.0), ('T ALL WEATHER L/S D15L 18', '187209/15L', '30001942', '15L', 150.0), ('T ALL WEATHER L/S W10L 18', '187200/10L', '30001917', '10L', 120.0), ('T ALL WEATHER L/S A10L 18', '187204/10L', '30001922', '10L', 122.5), ('T ALL WEATHER L/S N10L 18', '187205/10L', '30001927', '10L', 122.5), ('T ALL WEATHER L/S D10L 18', '187209/10L', '30001941', '10L', 122.5), ('T ALL WEATHER L/S W4L 18', '187200/4L', '30001921', '4L', 57.5), ('T ALL WEATHER L/S A4L 18', '187204/4L', '30001926', '4L', 60.0), ('T ALL WEATHER L/S N4L 18', '187205/4L', '30001931', '4L', 60.0), ('T ALL WEATHER L/S D4L 18', '187209/4L', '30001944', '4L', 60.0), ('T ALL WEATHER MATT W15L 18', '187100/15L', '30001906', '15L', 145.0), ('T ALL WEATHER MATT A15L 18', '187104/15L', '30001910', '15L', 150.0), ('T ALL WEATHER MATT N15L 18', '187105/15L', '30001914', '15L', 150.0), ('T ALL WEATHER S/G W15L 18', '187400/15L', '30001950', '15L', 145.0), ('T ALL WEATHER S/G D15L 19', '187409/15L', '30001963', '15L', 150.0), ('T ALL WEATHER S/G A10L 19', '187404/10L', '30001954', '10L', 122.5), ('T ENDURE INT L/S W15L 18', '124200/15L', '30001368', '15L', 145.0), ('T ENDURE INT L/S W10L 18', '124200/10L', '30001367', '10L', 120.0), ('T ENDURE INT L/S W4L 18', '124200/4L', '30001371', '4L', 57.5), ('T ENDURE INT MATT W15L 18', '124100/15L', '30001356', '15L', 160.0), ('T ENDURE INT MATT W10L 18', '124100/10L', '30001355', '10L', 135.0), ('T ENDURE INT MATT W4L 18', '124100/4L', '30001359', '4L', 60.0), ('T PURE PERF L/S W15L 21', '279250/15L', '30008591', '15L', 145.0), ('T PURE PERF MATT W15L 21', '279150/15L', '30008588', '15L', 145.0), ('T PURE PERF CEILING W15L 21', '279050/15L', '30008581', '15L', 120.0), ('T Ceiling Premium W15L 22', '128000/15L', '30010919', '15L', 120.0), ('T PURE PERF WB ENAMEL GLOSS W10L 21', '279950/10L', '30008738', '10L', 122.0), ('T PURE PERF WB ENAMEL S/G W10L 21', '279850/10L', '30008596', '10L', 122.0), ('T PURE PERF WB ENAMEL GLOSS W4L 21', '279950/4L', '30008739', '4L', 65.0), ('T PURE PERF WB ENAMEL S/G W4L 21', '279850/4L', '30008737', '4L', 65.0), ('T WB ENAMEL GLOSS W10L 19', '121610/10L', '30001326', '10L', 125.0), ('T WB ENAMEL S/G W10L 19', '121410/10L', '30001294', '10L', 125.0), ('T WB ENAMEL GLOSS W4L 19', '121610/4L', '30001329', '4L', 65.0), ('T WB ENAMEL S/G W4L 19', '121410/4L', '30001297', '4L', 65.0), ('T ULTIMATE ENAMEL S/G W10L 19', '132810/10L', '30001427', '10L', 170.0), ('T ULTIMATE ENAMEL GLOSS W10L 19', '132910/10L', '30001441', '10L', 170.0), ('T ULTIMATE ENAMEL S/G W4L 19', '132810/4L', '30001429', '4L', 80.0), ('T ULTIMATE ENAMEL GLOSS W4L 19', '132910/4L', '30001443', '4L', 80.0), ('T TRADE EDGE UC W15L 16', '259500/15L', '30002265', '15L', 90.0), ('T ULTRA PREP W15L 09', '288500/15L', '30002664', '15L', 110.0), ('T TRADEX ULTRAPREP 15L', '274520/15L', '30002331', '15L', 105.0), ('T PURE PERF PREP W15L 21', '279550/15L', '30008595', '15L', 120.0), ('T TRADEX CEILING W15L 15', '274000/15L', '30002310', '15L', 100.0), ('T PRO INT L/S W15L 20', '278200/15L', '30002370', '15L', 120.0), ('T PRO EXT L/S W15L 20', '278710/15L', '30002387', '15L', 135.0), ('T PRO ENAMEL W/B GLOSS W10L20', '278600/10L', '30002381', '10L', 120.0), ('T PRO ENAMEL W/B S/G W10L 20', '278400/10L', '30002376', '10L', 120.0), ('T PRO CEILING W15L 20', '278000/15L', '30002364', '15L', 105.0), ('T 3IN1 W15L 15', '108100/15L', '30000957', '15L', 130.0), ('T 3IN1 W4L 15', '108100/4L', '30000960', '4L', 60.0), ('J PRO DECK OIL NAT 10L 17', '481200/10L', '30004332', '10L', 170.0), ('J PRO DECK OIL NAT 4L 17', '481200/4L', '30004334', '4L', 75.0), ('J PRO EXT CLEAR GLOSS 4L 17', '481121/4L', '30004331', '4L', 80.0), ('J PRO EXT CLEAR SATIN 4L 17', '481120/4L', '30004328', '4L', 80.0), ('T ARMAWALL A/SHIELD W15L 09', '310400/15L', '30003018', '15L', 150.0), ('T ARMAWALL PRIMER 15L 09', '315500/15L', '30003036', '15L', 135.0), ('T ARMAWALL SEALER BOND C10L', '315705/10L', '30003039', '10L', 135.0), ('T ARMAWALL SEALER BOND W10L', '315700/10L', '30003038', '10L', 135.0)]

    restored = 0
    for product_name, product_code, taubmans_sku, unit, price_ex_gst in products:
        execute("""
            INSERT INTO products
            (product_code, product_name, supplier, unit, price_ex_gst, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_code) DO UPDATE SET
                product_name = excluded.product_name,
                supplier = excluded.supplier,
                unit = excluded.unit,
                price_ex_gst = excluded.price_ex_gst,
                notes = excluded.notes
        """, (
            product_code,
            product_name,
            "Taubmans",
            unit,
            float(price_ex_gst),
            f"Taubmans SKU: {taubmans_sku} | Source: uploaded Premier Brushworks Taubmans price list"
        ))
        restored += 1

    return restored


def taubmans_product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products WHERE supplier = 'Taubmans'")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0





def restore_haymes_and_taubmans_product_lists():
    products = [('PB-H00001', 'Coverplus Interior L/S White', 'Haymes', '', 168.0, ''), ('PB-H00002', 'Elite Ceiling Toned White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00003', 'Elite Ceiling White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00004', 'Elite Interior Low Sheen White', 'Haymes', '', 118.0, ''), ('PB-H00005', 'Elite Interior Matt White, 15L', 'Haymes', '15L', 125.0, ''), ('PB-H00006', 'Elite Acrylic Sealer Undercoat', 'Haymes', '', 105.36, ''), ('PB-H00007', 'Elite Quick Dry Primer Undercoat', 'Haymes', '', 123.55, ''), ('PB-H00008', 'Expressions Low Sheen DKT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00009', 'Expressions Low Sheen EDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00010', 'Expressions Low Sheen UDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00011', 'Expressions Low Sheen White', 'Haymes', '', 107.48, ''), ('PB-H00012', 'Expressions Low Sheen White', 'Haymes', '', 145.0, ''), ('PB-H00013', 'Expressions Low Sheen White, 4L', 'Haymes', '4L', 67.26, ''), ('PB-H00014', 'Solashield Low Sheen DKT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00015', 'Solashield Low Sheen DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00016', 'Solashield Low Sheen DKT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00017', 'Solashield Low Sheen EDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00018', 'Solashield Low Sheen EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00019', 'Solashield Low Sheen EDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00020', 'Solashield Low Sheen UDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00021', 'Solashield Low Sheen UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00022', 'Solashield Low Sheen UDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00023', 'Solashield Low Sheen White, 10L', 'Haymes', '10L', 107.42, ''), ('PB-H00024', 'Solashield Low Sheen White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00025', 'Solashield Low Sheen White, 4L', 'Haymes', '4L', 67.4, ''), ('PB-H00026', 'R/Tex Roll On Coarse, 15L', 'Haymes', '15L', 175.0, ''), ('PB-H00027', 'Solashield Satin DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00028', 'Solashield Satin EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00029', 'Solashield Satin UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00030', 'Solashield Satin White, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00031', 'Solashield Satin White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00032', 'Ultra Premium Primer Sealer', 'Haymes', '', 167.46, ''), ('PB-H00033', 'Acrylic Sealer Undercoat', 'Haymes', '', 120.0, ''), ('PB-H00034', 'Ultratrim High Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00035', 'Ultratrim Semi Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00036', 'Woodcare Aqualac Floor Satin', 'Haymes', '', 250.44, ''), ('187200/15L', 'T ALL WEATHER L/S W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001918 | Source: uploaded Premier Brushworks Taubmans price list'), ('187204/15L', 'T ALL WEATHER L/S A15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001923 | Source: uploaded Premier Brushworks Taubmans price list'), ('187205/15L', 'T ALL WEATHER L/S N15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001928 | Source: uploaded Premier Brushworks Taubmans price list'), ('187209/15L', 'T ALL WEATHER L/S D15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001942 | Source: uploaded Premier Brushworks Taubmans price list'), ('187200/10L', 'T ALL WEATHER L/S W10L 18', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30001917 | Source: uploaded Premier Brushworks Taubmans price list'), ('187204/10L', 'T ALL WEATHER L/S A10L 18', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001922 | Source: uploaded Premier Brushworks Taubmans price list'), ('187205/10L', 'T ALL WEATHER L/S N10L 18', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001927 | Source: uploaded Premier Brushworks Taubmans price list'), ('187209/10L', 'T ALL WEATHER L/S D10L 18', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001941 | Source: uploaded Premier Brushworks Taubmans price list'), ('187200/4L', 'T ALL WEATHER L/S W4L 18', 'Taubmans', '4L', 57.5, 'Taubmans SKU: 30001921 | Source: uploaded Premier Brushworks Taubmans price list'), ('187204/4L', 'T ALL WEATHER L/S A4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001926 | Source: uploaded Premier Brushworks Taubmans price list'), ('187205/4L', 'T ALL WEATHER L/S N4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001931 | Source: uploaded Premier Brushworks Taubmans price list'), ('187209/4L', 'T ALL WEATHER L/S D4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001944 | Source: uploaded Premier Brushworks Taubmans price list'), ('187100/15L', 'T ALL WEATHER MATT W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001906 | Source: uploaded Premier Brushworks Taubmans price list'), ('187104/15L', 'T ALL WEATHER MATT A15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001910 | Source: uploaded Premier Brushworks Taubmans price list'), ('187105/15L', 'T ALL WEATHER MATT N15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001914 | Source: uploaded Premier Brushworks Taubmans price list'), ('187400/15L', 'T ALL WEATHER S/G W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001950 | Source: uploaded Premier Brushworks Taubmans price list'), ('187409/15L', 'T ALL WEATHER S/G D15L 19', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001963 | Source: uploaded Premier Brushworks Taubmans price list'), ('187404/10L', 'T ALL WEATHER S/G A10L 19', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001954 | Source: uploaded Premier Brushworks Taubmans price list'), ('124200/15L', 'T ENDURE INT L/S W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001368 | Source: uploaded Premier Brushworks Taubmans price list'), ('124200/10L', 'T ENDURE INT L/S W10L 18', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30001367 | Source: uploaded Premier Brushworks Taubmans price list'), ('124200/4L', 'T ENDURE INT L/S W4L 18', 'Taubmans', '4L', 57.5, 'Taubmans SKU: 30001371 | Source: uploaded Premier Brushworks Taubmans price list'), ('124100/15L', 'T ENDURE INT MATT W15L 18', 'Taubmans', '15L', 160.0, 'Taubmans SKU: 30001356 | Source: uploaded Premier Brushworks Taubmans price list'), ('124100/10L', 'T ENDURE INT MATT W10L 18', 'Taubmans', '10L', 135.0, 'Taubmans SKU: 30001355 | Source: uploaded Premier Brushworks Taubmans price list'), ('124100/4L', 'T ENDURE INT MATT W4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001359 | Source: uploaded Premier Brushworks Taubmans price list'), ('279250/15L', 'T PURE PERF L/S W15L 21', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30008591 | Source: uploaded Premier Brushworks Taubmans price list'), ('279150/15L', 'T PURE PERF MATT W15L 21', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30008588 | Source: uploaded Premier Brushworks Taubmans price list'), ('279050/15L', 'T PURE PERF CEILING W15L 21', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30008581 | Source: uploaded Premier Brushworks Taubmans price list'), ('128000/15L', 'T Ceiling Premium W15L 22', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30010919 | Source: uploaded Premier Brushworks Taubmans price list'), ('279950/10L', 'T PURE PERF WB ENAMEL GLOSS W10L 21', 'Taubmans', '10L', 122.0, 'Taubmans SKU: 30008738 | Source: uploaded Premier Brushworks Taubmans price list'), ('279850/10L', 'T PURE PERF WB ENAMEL S/G W10L 21', 'Taubmans', '10L', 122.0, 'Taubmans SKU: 30008596 | Source: uploaded Premier Brushworks Taubmans price list'), ('279950/4L', 'T PURE PERF WB ENAMEL GLOSS W4L 21', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30008739 | Source: uploaded Premier Brushworks Taubmans price list'), ('279850/4L', 'T PURE PERF WB ENAMEL S/G W4L 21', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30008737 | Source: uploaded Premier Brushworks Taubmans price list'), ('121610/10L', 'T WB ENAMEL GLOSS W10L 19', 'Taubmans', '10L', 125.0, 'Taubmans SKU: 30001326 | Source: uploaded Premier Brushworks Taubmans price list'), ('121410/10L', 'T WB ENAMEL S/G W10L 19', 'Taubmans', '10L', 125.0, 'Taubmans SKU: 30001294 | Source: uploaded Premier Brushworks Taubmans price list'), ('121610/4L', 'T WB ENAMEL GLOSS W4L 19', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30001329 | Source: uploaded Premier Brushworks Taubmans price list'), ('121410/4L', 'T WB ENAMEL S/G W4L 19', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30001297 | Source: uploaded Premier Brushworks Taubmans price list'), ('132810/10L', 'T ULTIMATE ENAMEL S/G W10L 19', 'Taubmans', '10L', 170.0, 'Taubmans SKU: 30001427 | Source: uploaded Premier Brushworks Taubmans price list'), ('132910/10L', 'T ULTIMATE ENAMEL GLOSS W10L 19', 'Taubmans', '10L', 170.0, 'Taubmans SKU: 30001441 | Source: uploaded Premier Brushworks Taubmans price list'), ('132810/4L', 'T ULTIMATE ENAMEL S/G W4L 19', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30001429 | Source: uploaded Premier Brushworks Taubmans price list'), ('132910/4L', 'T ULTIMATE ENAMEL GLOSS W4L 19', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30001443 | Source: uploaded Premier Brushworks Taubmans price list'), ('259500/15L', 'T TRADE EDGE UC W15L 16', 'Taubmans', '15L', 90.0, 'Taubmans SKU: 30002265 | Source: uploaded Premier Brushworks Taubmans price list'), ('288500/15L', 'T ULTRA PREP W15L 09', 'Taubmans', '15L', 110.0, 'Taubmans SKU: 30002664 | Source: uploaded Premier Brushworks Taubmans price list'), ('274520/15L', 'T TRADEX ULTRAPREP 15L', 'Taubmans', '15L', 105.0, 'Taubmans SKU: 30002331 | Source: uploaded Premier Brushworks Taubmans price list'), ('279550/15L', 'T PURE PERF PREP W15L 21', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30008595 | Source: uploaded Premier Brushworks Taubmans price list'), ('274000/15L', 'T TRADEX CEILING W15L 15', 'Taubmans', '15L', 100.0, 'Taubmans SKU: 30002310 | Source: uploaded Premier Brushworks Taubmans price list'), ('278200/15L', 'T PRO INT L/S W15L 20', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30002370 | Source: uploaded Premier Brushworks Taubmans price list'), ('278710/15L', 'T PRO EXT L/S W15L 20', 'Taubmans', '15L', 135.0, 'Taubmans SKU: 30002387 | Source: uploaded Premier Brushworks Taubmans price list'), ('278600/10L', 'T PRO ENAMEL W/B GLOSS W10L20', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30002381 | Source: uploaded Premier Brushworks Taubmans price list'), ('278400/10L', 'T PRO ENAMEL W/B S/G W10L 20', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30002376 | Source: uploaded Premier Brushworks Taubmans price list'), ('278000/15L', 'T PRO CEILING W15L 20', 'Taubmans', '15L', 105.0, 'Taubmans SKU: 30002364 | Source: uploaded Premier Brushworks Taubmans price list'), ('108100/15L', 'T 3IN1 W15L 15', 'Taubmans', '15L', 130.0, 'Taubmans SKU: 30000957 | Source: uploaded Premier Brushworks Taubmans price list'), ('108100/4L', 'T 3IN1 W4L 15', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30000960 | Source: uploaded Premier Brushworks Taubmans price list'), ('481200/10L', 'J PRO DECK OIL NAT 10L 17', 'Taubmans', '10L', 170.0, 'Taubmans SKU: 30004332 | Source: uploaded Premier Brushworks Taubmans price list'), ('481200/4L', 'J PRO DECK OIL NAT 4L 17', 'Taubmans', '4L', 75.0, 'Taubmans SKU: 30004334 | Source: uploaded Premier Brushworks Taubmans price list'), ('481121/4L', 'J PRO EXT CLEAR GLOSS 4L 17', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30004331 | Source: uploaded Premier Brushworks Taubmans price list'), ('481120/4L', 'J PRO EXT CLEAR SATIN 4L 17', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30004328 | Source: uploaded Premier Brushworks Taubmans price list'), ('310400/15L', 'T ARMAWALL A/SHIELD W15L 09', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30003018 | Source: uploaded Premier Brushworks Taubmans price list'), ('315500/15L', 'T ARMAWALL PRIMER 15L 09', 'Taubmans', '15L', 135.0, 'Taubmans SKU: 30003036 | Source: uploaded Premier Brushworks Taubmans price list'), ('315705/10L', 'T ARMAWALL SEALER BOND C10L', 'Taubmans', '10L', 135.0, 'Taubmans SKU: 30003039 | Source: uploaded Premier Brushworks Taubmans price list'), ('315700/10L', 'T ARMAWALL SEALER BOND W10L', 'Taubmans', '10L', 135.0, 'Taubmans SKU: 30003038 | Source: uploaded Premier Brushworks Taubmans price list')]

    restored = 0
    for product_code, product_name, supplier, unit, price_ex_gst, notes in products:
        execute("""
            INSERT INTO products
            (product_code, product_name, supplier, unit, price_ex_gst, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_code) DO UPDATE SET
                product_name = excluded.product_name,
                supplier = excluded.supplier,
                unit = excluded.unit,
                price_ex_gst = excluded.price_ex_gst,
                notes = excluded.notes
        """, (
            product_code,
            product_name,
            supplier,
            unit,
            float(price_ex_gst or 0),
            notes
        ))
        restored += 1

    return restored


def haymes_product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products WHERE supplier = 'Haymes'")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0


def combined_paint_product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products WHERE supplier IN ('Haymes', 'Taubmans')")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0


def restore_builders_clients_and_employees():
    """Historical embedded master data was removed for privacy and safety."""
    raise RuntimeError(
        "Embedded customer and employee data has been removed. "
        "Use the protected CSV import workflow instead."
    )



def builders_clients_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM builders_clients")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0


def employees_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM employees")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0




# =============================
# USER ACCOUNT DUPLICATE CLEANUP
# =============================
def normalise_username_value(username):
    return str(username or "").strip().lower()


def user_duplicate_summary():
    try:
        users = df_query("""
            SELECT u.id,
                   u.username,
                   u.role,
                   u.employee_id,
                   u.active,
                   COALESCE(e.name, '') AS employee_name,
                   u.notes
            FROM app_users u
            LEFT JOIN employees e ON e.id = u.employee_id
            ORDER BY LOWER(TRIM(u.username)), u.id
        """)
    except Exception:
        return pd.DataFrame()

    if users.empty:
        return users

    duplicate_ids = set()

    # Same username duplicates, ignoring case/spaces.
    username_groups = {}
    for _, row in users.iterrows():
        key = normalise_username_value(row["username"])
        if key:
            username_groups.setdefault(key, []).append(int(row["id"]))

    for ids in username_groups.values():
        if len(ids) > 1:
            duplicate_ids.update(ids)

    # Same linked employee duplicates.
    employee_groups = {}
    for _, row in users.iterrows():
        try:
            emp_id = int(row["employee_id"]) if row["employee_id"] not in [None, "", "None"] and pd.notna(row["employee_id"]) else None
        except Exception:
            emp_id = None
        if emp_id:
            employee_groups.setdefault(emp_id, []).append(int(row["id"]))

    for ids in employee_groups.values():
        if len(ids) > 1:
            duplicate_ids.update(ids)

    if not duplicate_ids:
        return pd.DataFrame()

    return users[users["id"].isin(duplicate_ids)].copy()


def clean_duplicate_user_accounts():
    """
    Deletes duplicate login rows.
    Keeps:
    - the currently logged-in user if they are in a duplicate group
    - otherwise an active admin where possible
    - otherwise an active account
    - otherwise the lowest id
    """
    users = df_query("""
        SELECT u.id,
               u.username,
               u.role,
               u.employee_id,
               u.active,
               COALESCE(e.name, '') AS employee_name,
               u.notes
        FROM app_users u
        LEFT JOIN employees e ON e.id = u.employee_id
        ORDER BY u.id
    """)

    if users.empty:
        return {"deleted": 0, "kept": 0, "skipped": 0}

    current_user = get_current_user() or {}
    current_user_id = int(current_user.get("id", -1))

    ids_to_delete = set()
    keep_ids = set()

    def choose_keep(group_df):
        # Keep current logged-in user if present.
        current_rows = group_df[group_df["id"].astype(int) == current_user_id]
        if not current_rows.empty:
            return int(current_rows.iloc[0]["id"])

        # Prefer active admin.
        active_admin = group_df[
            (group_df["role"].astype(str) == "admin") &
            (group_df["active"].fillna(0).astype(int) == 1)
        ]
        if not active_admin.empty:
            return int(active_admin.sort_values("id").iloc[0]["id"])

        # Prefer active account.
        active = group_df[group_df["active"].fillna(0).astype(int) == 1]
        if not active.empty:
            return int(active.sort_values("id").iloc[0]["id"])

        # Otherwise keep first row.
        return int(group_df.sort_values("id").iloc[0]["id"])

    # Duplicates by username.
    users["_username_key"] = users["username"].apply(normalise_username_value)
    for key, group in users.groupby("_username_key"):
        if key and len(group) > 1:
            keep_id = choose_keep(group)
            keep_ids.add(keep_id)
            for uid in group["id"].astype(int).tolist():
                if uid != keep_id:
                    ids_to_delete.add(uid)

    # Duplicates by linked employee.
    linked = users[users["employee_id"].notna()].copy()
    if not linked.empty:
        for emp_id, group in linked.groupby("employee_id"):
            if emp_id not in [None, "", "None"] and len(group) > 1:
                keep_id = choose_keep(group)
                keep_ids.add(keep_id)
                for uid in group["id"].astype(int).tolist():
                    if uid != keep_id:
                        ids_to_delete.add(uid)

    # Never delete current user.
    ids_to_delete.discard(current_user_id)

    # Never delete last active admin.
    admin_count_df = df_query("""
        SELECT COUNT(*) AS 'count'
        FROM app_users
        WHERE role = 'admin' AND active = 1
    """)
    active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0

    skipped = 0
    deleted = 0

    for uid in sorted(ids_to_delete):
        row_df = users[users["id"].astype(int) == int(uid)]
        if row_df.empty:
            continue

        row = row_df.iloc[0]
        is_active_admin = str(row["role"]) == "admin" and int(row["active"] or 0) == 1

        if is_active_admin and active_admin_count <= 1:
            skipped += 1
            continue

        try:
            execute("DELETE FROM app_users WHERE id = ?", (int(uid),))
            deleted += 1
            if is_active_admin:
                active_admin_count -= 1
        except Exception:
            # If deletion fails, safely disable it instead.
            try:
                execute("UPDATE app_users SET active = 0, notes = COALESCE(notes, '') || ' | duplicate disabled' WHERE id = ?", (int(uid),))
                skipped += 1
            except Exception:
                skipped += 1

    # Add unique indexes after cleanup so they cannot double up again.
    try:
        execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_username_lower_unique ON app_users (LOWER(TRIM(username)))")
    except Exception:
        pass

    try:
        execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_employee_unique ON app_users (employee_id) WHERE employee_id IS NOT NULL")
    except Exception:
        pass

    return {"deleted": deleted, "kept": len(keep_ids), "skipped": skipped}



# =============================
# USER LINK SAFETY
# =============================
def employee_linked_to_other_user(employee_id, selected_user_id):
    """
    Returns the other user account already linked to an employee, if any.
    Prevents app_users.employee_id unique constraint crashes.
    """
    if employee_id in [None, "", "None"]:
        return pd.DataFrame()

    try:
        return df_query("""
            SELECT id, username, role, active
            FROM app_users
            WHERE employee_id = ? AND id <> ?
            LIMIT 1
        """, (employee_id, selected_user_id))
    except Exception:
        return pd.DataFrame()


def safe_update_user_account(selected_user_id, username, role, employee_id, active, notes):
    """
    Safely updates app_users and prevents duplicate employee login links.
    Returns (success, message).
    """
    username = str(username or "").strip()

    if not username:
        return False, "Username cannot be blank."

    # Check username duplicate, ignoring case/spaces.
    existing_username = df_query("""
        SELECT id, username
        FROM app_users
        WHERE LOWER(TRIM(username)) = LOWER(TRIM(?)) AND id <> ?
        LIMIT 1
    """, (username, selected_user_id))

    if not existing_username.empty:
        return False, f"Username '{username}' is already used by another account."

    # Check employee duplicate link.
    other_link = employee_linked_to_other_user(employee_id, selected_user_id)
    if not other_link.empty:
        other = other_link.iloc[0]
        return False, (
            f"This employee is already linked to user account '{other['username']}'. "
            "Delete, disable, or unlink that duplicate account first, or choose 'No Employee Link'."
        )

    try:
        execute("""
            UPDATE app_users
            SET username = ?, role = ?, employee_id = ?, active = ?, notes = ?
            WHERE id = ?
        """, (username, role, employee_id, active, notes, selected_user_id))
        return True, "User updated."
    except Exception as e:
        message = str(e)
        if "idx_app_users_employee_unique" in message or "app_users_employee_id" in message or "duplicate key" in message:
            return False, (
                "That employee is already linked to another user account. "
                "Open User Access and use Clean Duplicate User Accounts, or select No Employee Link."
            )
        return False, f"User update failed: {message}"



# =============================
# BULK USER ACCOUNT DELETE
# =============================
# =============================
# BULK EMPLOYEE DELETE / DEACTIVATE
# =============================

# =============================
# LINKED USER / EMPLOYEE DELETE
# =============================
def employee_has_job_history(employee_id):
    """
    Employees with wage/timesheet history should not be fully deleted because
    deleting them can break job costing history. They are marked Inactive instead.
    """
    linked = []

    for table, column, label in [
        ("wage_entries", "employee_id", "wage records"),
        ("timesheet_entries", "employee_id", "timesheets"),
    ]:
        try:
            if has_related_records(table, column, employee_id):
                linked.append(label)
        except Exception:
            pass

    return linked


def delete_employee_and_linked_users(employee_id):
    """
    Employee delete button behaviour:
    - Deletes linked app user login account(s).
    - Deletes the employee record only if there is no wage/timesheet history.
    - If history exists, the employee is marked Inactive.
    - Protects current logged-in user and last active admin.
    """
    result = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    try:
        employee_id = int(employee_id)
    except Exception:
        result["skipped"] += 1
        result["messages"].append("Invalid employee id.")
        return result

    emp_df = df_query("SELECT id, name, status FROM employees WHERE id = ? LIMIT 1", (employee_id,))
    if emp_df.empty:
        result["skipped"] += 1
        result["messages"].append(f"Employee id {employee_id} not found.")
        return result

    employee_name = str(emp_df.iloc[0]["name"])

    current_user = get_current_user() or {}
    try:
        current_user_id = int(current_user.get("id", -1))
    except Exception:
        current_user_id = -1

    linked_users = df_query("""
        SELECT id, username, role, active
        FROM app_users
        WHERE employee_id = ?
        ORDER BY id
    """, (employee_id,))

    for _, user_row in linked_users.iterrows():
        user_id = int(user_row["id"])
        username = str(user_row["username"])
        role = str(user_row["role"])
        active = int(user_row["active"] or 0)

        if user_id == current_user_id:
            result["skipped"] += 1
            result["messages"].append(f"Skipped linked user {username}: cannot delete the account currently logged in.")
            continue

        if role == "admin" and active == 1:
            admin_count_df = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE role = 'admin' AND active = 1")
            active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0
            if active_admin_count <= 1:
                result["skipped"] += 1
                result["messages"].append(f"Skipped linked user {username}: cannot delete the last active admin account.")
                continue

        try:
            execute("DELETE FROM app_users WHERE id = ?", (user_id,))
            result["deleted_users"] += 1
            result["messages"].append(f"Deleted linked user login: {username}")
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not delete linked user {username}: {e}")

    # If a protected linked user remains, do not fully delete the employee.
    remaining_users = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE employee_id = ?", (employee_id,))
    remaining_user_count = int(remaining_users.iloc[0]["count"]) if not remaining_users.empty else 0

    if remaining_user_count > 0:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(f"Marked {employee_name} inactive because a protected linked user account remains.")
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate {employee_name}: {e}")
        return result

    history = employee_has_job_history(employee_id)

    if history:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(
                f"Deleted linked login(s), but marked {employee_name} inactive because they have: " + ", ".join(history)
            )
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate {employee_name}: {e}")
    else:
        try:
            execute("DELETE FROM employees WHERE id = ?", (employee_id,))
            result["deleted_employee"] += 1
            result["messages"].append(f"Deleted employee record: {employee_name}")
        except Exception as e:
            try:
                execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
                result["deactivated_employee"] += 1
                result["messages"].append(f"Could not fully delete {employee_name}, so marked inactive instead. Reason: {e}")
            except Exception:
                result["skipped"] += 1
                result["messages"].append(f"Could not delete or deactivate {employee_name}: {e}")

    return result


def delete_user_and_linked_employee(user_id):
    """
    User delete button behaviour:
    - Deletes the app user login account.
    - If linked to an employee, also deletes that employee if there is no wage/timesheet history.
    - If history exists, the employee is marked Inactive.
    - Protects current logged-in user and last active admin.
    """
    result = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    try:
        user_id = int(user_id)
    except Exception:
        result["skipped"] += 1
        result["messages"].append("Invalid user id.")
        return result

    user_df = df_query("""
        SELECT id, username, role, employee_id, active
        FROM app_users
        WHERE id = ?
        LIMIT 1
    """, (user_id,))

    if user_df.empty:
        result["skipped"] += 1
        result["messages"].append(f"User id {user_id} not found.")
        return result

    user_row = user_df.iloc[0]
    username = str(user_row["username"])
    role = str(user_row["role"])
    active = int(user_row["active"] or 0)

    try:
        employee_id = int(user_row["employee_id"]) if user_row["employee_id"] not in [None, "", "None"] and pd.notna(user_row["employee_id"]) else None
    except Exception:
        employee_id = None

    current_user = get_current_user() or {}
    try:
        current_user_id = int(current_user.get("id", -1))
    except Exception:
        current_user_id = -1

    if user_id == current_user_id:
        result["skipped"] += 1
        result["messages"].append(f"Skipped {username}: cannot delete the account currently logged in.")
        return result

    if role == "admin" and active == 1:
        admin_count_df = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE role = 'admin' AND active = 1")
        active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0
        if active_admin_count <= 1:
            result["skipped"] += 1
            result["messages"].append(f"Skipped {username}: cannot delete the last active admin account.")
            return result

    try:
        execute("DELETE FROM app_users WHERE id = ?", (user_id,))
        result["deleted_users"] += 1
        result["messages"].append(f"Deleted user login: {username}")
    except Exception as e:
        result["skipped"] += 1
        result["messages"].append(f"Could not delete user {username}: {e}")
        return result

    if not employee_id:
        return result

    emp_df = df_query("SELECT id, name, status FROM employees WHERE id = ? LIMIT 1", (employee_id,))
    if emp_df.empty:
        result["messages"].append("Linked employee record was not found.")
        return result

    employee_name = str(emp_df.iloc[0]["name"])

    # If other user accounts still link to this employee, do not fully delete employee.
    other_users = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE employee_id = ?", (employee_id,))
    other_user_count = int(other_users.iloc[0]["count"]) if not other_users.empty else 0

    if other_user_count > 0:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(f"Marked linked employee {employee_name} inactive because another login still references them.")
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate linked employee {employee_name}: {e}")
        return result

    history = employee_has_job_history(employee_id)

    if history:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(f"Marked linked employee {employee_name} inactive because they have: " + ", ".join(history))
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate linked employee {employee_name}: {e}")
    else:
        try:
            execute("DELETE FROM employees WHERE id = ?", (employee_id,))
            result["deleted_employee"] += 1
            result["messages"].append(f"Deleted linked employee record: {employee_name}")
        except Exception as e:
            try:
                execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
                result["deactivated_employee"] += 1
                result["messages"].append(f"Could not fully delete linked employee {employee_name}, so marked inactive instead. Reason: {e}")
            except Exception:
                result["skipped"] += 1
                result["messages"].append(f"Could not delete or deactivate linked employee {employee_name}: {e}")

    return result


def delete_or_deactivate_selected_employees(employee_ids):
    """
    Bulk employee delete:
    Deletes linked user login(s) too. If the employee has job history,
    the login is deleted and the employee is marked Inactive.
    """
    combined = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    if not employee_ids:
        combined["messages"].append("No employees selected.")
        return combined

    for emp_id in employee_ids:
        result = delete_employee_and_linked_users(emp_id)
        for key in ["deleted_users", "deleted_employee", "deactivated_employee", "skipped"]:
            combined[key] += result.get(key, 0)
        combined["messages"].extend(result.get("messages", []))

    return combined


def delete_selected_user_accounts(user_ids):
    """
    Bulk user delete:
    Deletes selected user login(s) and linked employee record(s) where safe.
    If linked employee has job history, employee is marked Inactive.
    """
    combined = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    if not user_ids:
        combined["messages"].append("No user accounts selected.")
        return combined

    for uid in user_ids:
        result = delete_user_and_linked_employee(uid)
        for key in ["deleted_users", "deleted_employee", "deactivated_employee", "skipped"]:
            combined[key] += result.get(key, 0)
        combined["messages"].extend(result.get("messages", []))

    return combined



# =============================
# JOB COSTS / FORECASTING + JOBHUB AI
# =============================
def jc_float(value, default=0.0):
    try:
        if value is None or value == "" or pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def jc_percent(numerator, denominator):
    denominator = jc_float(denominator)
    if denominator == 0:
        return 0.0
    return round((jc_float(numerator) / denominator) * 100, 2)


def jc_parse_date(value):
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()[:10]
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    return None


def jc_business_days(start_value, end_value):
    start = jc_parse_date(start_value)
    end = jc_parse_date(end_value)
    if not start or not end or end < start:
        return 0
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


def jc_add_business_days(start_date, days):
    current = start_date or date.today()
    added = 0
    days = int(max(days, 0))
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def jc_month_label(value):
    d = jc_parse_date(value)
    return d.strftime("%Y-%m") if d else "Unscheduled"


def job_cost_summary_dataframe():
    jobs = df_query("""
        SELECT j.id AS 'job_id',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               COALESCE(bc.name, '') AS 'Builder / Client',
               j.site_address AS 'Site Address',
               j.status AS 'Status',
               j.leading_hand AS 'Leading Hand',
               j.start_date AS 'Start Date',
               j.end_date AS 'End Date',
               COALESCE(j.contract_value, 0) AS 'Contract Value',
               j.notes AS 'Notes'
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        ORDER BY j.job_no
    """)

    if jobs.empty:
        return jobs

    materials = df_query("""
        SELECT m.job_id,
               COALESCE(SUM(COALESCE(m.qty_required, 0) * COALESCE(m.custom_unit_price, p.price_ex_gst, 0)), 0) AS 'Committed Material Cost',
               COALESCE(SUM(COALESCE(m.qty_received, 0) * COALESCE(m.custom_unit_price, p.price_ex_gst, 0)), 0) AS 'Actual Material Cost',
               COALESCE(SUM(COALESCE(m.qty_required, 0)), 0) AS 'Material Qty Required',
               COALESCE(SUM(COALESCE(m.qty_received, 0)), 0) AS 'Material Qty Received',
               COUNT(*) AS 'Material Lines'
        FROM material_entries m
        LEFT JOIN products p ON p.id = m.product_id
        GROUP BY m.job_id
    """)

    wages = df_query("""
        SELECT w.job_id,
               COALESCE(SUM(COALESCE(w.hours, 0)), 0) AS 'Wage Hours',
               COALESCE(SUM(
                   COALESCE(w.hours, 0) *
                   COALESCE(NULLIF(w.hourly_rate_snapshot, 0), e.rate_plus_10, e.base_hourly_rate, 0)
               ), 0) AS 'Actual Labour Cost',
               COUNT(*) AS 'Wage Lines'
        FROM wage_entries w
        LEFT JOIN employees e ON e.id = w.employee_id
        GROUP BY w.job_id
    """)

    timesheets = df_query("""
        SELECT job_id,
               COALESCE(SUM(COALESCE(total_hours, 0)), 0) AS 'Timesheet Hours',
               COUNT(*) AS 'Timesheet Lines'
        FROM timesheet_entries
        GROUP BY job_id
    """)

    estimates = df_query("""
        SELECT e.job_id,
               e.estimate_no AS 'Latest Estimate',
               e.revision AS 'Estimate Revision',
               COALESCE(e.labour_hours, 0) AS 'Estimated Labour Hours',
               COALESCE(e.labour_rate, 0) AS 'Estimated Labour Rate',
               COALESCE(e.material_allowance, 0) AS 'Estimated Materials',
               COALESCE(e.access_equipment_allowance, 0) AS 'Estimated Access / Equipment',
               COALESCE(e.subcontractor_allowance, 0) AS 'Estimated Subcontractor',
               COALESCE(e.sundries_allowance, 0) AS 'Estimated Sundries',
               COALESCE(e.total_ex_gst, 0) AS 'Estimate Total Ex GST',
               COALESCE(e.total_inc_gst, 0) AS 'Estimate Total Inc GST'
        FROM estimate_working_sheets e
        JOIN (
            SELECT job_id, MAX(id) AS max_id
            FROM estimate_working_sheets
            WHERE COALESCE(archived, 0) = 0
              AND COALESCE(status, 'Draft') NOT IN ('Lost', 'Superseded')
            GROUP BY job_id
        ) latest ON latest.max_id = e.id
    """)

    df = jobs.copy()
    for extra in [materials, wages, timesheets, estimates]:
        if extra is not None and not extra.empty:
            df = df.merge(extra, on="job_id", how="left")

    number_cols = [
        "Contract Value", "Committed Material Cost", "Actual Material Cost", "Material Qty Required", "Material Qty Received",
        "Material Lines", "Wage Hours", "Actual Labour Cost", "Wage Lines", "Timesheet Hours",
        "Timesheet Lines", "Estimated Labour Hours", "Estimated Labour Rate", "Estimated Materials",
        "Estimated Access / Equipment", "Estimated Subcontractor", "Estimated Sundries",
        "Estimate Total Ex GST", "Estimate Total Inc GST"
    ]

    for col in number_cols:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0)
    df["Estimated Labour Rate"] = PLANNING_LABOUR_RATE

    for col in ["Latest Estimate", "Estimate Revision"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    df["Actual Labour Hours"] = df["Wage Hours"]
    df["Total Actual Cost"] = df["Actual Material Cost"] + df["Actual Labour Cost"]
    df["Gross Profit"] = df["Contract Value"] - df["Total Actual Cost"]
    df["Gross Profit %"] = df.apply(lambda r: jc_percent(r["Gross Profit"], r["Contract Value"]), axis=1)
    df["Cost to Date %"] = df.apply(lambda r: jc_percent(r["Total Actual Cost"], r["Contract Value"]), axis=1)
    df["Remaining Labour Hours"] = (df["Estimated Labour Hours"] - df["Timesheet Hours"]).clip(lower=0)
    df["Working Days Scheduled"] = df.apply(lambda r: jc_business_days(r["Start Date"], r["End Date"]), axis=1)
    df["Forecast Month"] = df["Start Date"].apply(jc_month_label)
    return df


def job_costs_forecasting_page():
    st.header("Job Costs / Forecasting")
    st.caption("Job cost breakdowns, financial forecasting and labour/schedule forecasting.")

    df = job_cost_summary_dataframe()
    if df.empty:
        st.info("No jobs found yet.")
        return

    section = st.radio(
        "Section",
        ["Selected Job Breakdown", "Financial Forecast", "Scheduling Forecast", "Export"],
        horizontal=True,
        key="job_cost_forecast_section",
    )

    if section == "Selected Job Breakdown":
        job_options = {f"{r['Job No']} - {r['Job Name']}": int(r["job_id"]) for _, r in df.iterrows()}
        selected = st.selectbox("Select Job", list(job_options.keys()), key="job_cost_selected")
        row = df[df["job_id"].astype(int) == int(job_options[selected])].iloc[0]

        st.subheader(f"{row['Job No']} - {row['Job Name']}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Contract Value", f"${jc_float(row['Contract Value']):,.2f}")
        c2.metric("Actual Cost to Date", f"${jc_float(row['Total Actual Cost']):,.2f}")
        c3.metric("Gross Profit", f"${jc_float(row['Gross Profit']):,.2f}")
        c4.metric("Gross Profit %", f"{jc_float(row['Gross Profit %']):.2f}%")

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Material Cost", f"${jc_float(row['Actual Material Cost']):,.2f}")
        c6.metric("Labour Cost", f"${jc_float(row['Actual Labour Cost']):,.2f}")
        c7.metric("Timesheet Hours", f"{jc_float(row['Timesheet Hours']):.2f}")
        c8.metric("Remaining Est. Hours", f"{jc_float(row['Remaining Labour Hours']):.2f}")

        st.markdown("### Forecast Inputs")
        i1, i2, i3, i4 = st.columns(4)
        target_gp = i1.number_input("Target GP %", min_value=0.0, max_value=100.0, value=35.0, step=1.0)
        labour_cost_hour = i2.number_input(
            "Forecast Labour Cost / Hour",
            min_value=0.0,
            value=PLANNING_LABOUR_RATE,
            step=5.0,
            disabled=True,
            help="Forecast labour uses $60/hour. Actual labour cost remains based on recorded employee wage costs.",
        )
        crew_size = i3.number_input("Crew Size", min_value=1.0, value=3.0, step=1.0)
        hours_day = i4.number_input("Hours / Person / Day", min_value=1.0, value=8.0, step=0.5)

        target_cost = jc_float(row["Contract Value"]) * (1 - target_gp / 100)
        remaining_cost_budget = max(target_cost - jc_float(row["Total Actual Cost"]), 0)
        remaining_by_budget = remaining_cost_budget / labour_cost_hour if labour_cost_hour else 0
        remaining_hours = jc_float(row["Remaining Labour Hours"]) or remaining_by_budget
        daily_capacity = crew_size * hours_day
        days_required = int((remaining_hours + daily_capacity - 0.001) // daily_capacity) if daily_capacity else 0
        if daily_capacity and remaining_hours % daily_capacity:
            days_required += 1
        finish_date = jc_add_business_days(date.today(), days_required)

        forecast_cost = jc_float(row["Total Actual Cost"]) + remaining_hours * labour_cost_hour
        forecast_profit = jc_float(row["Contract Value"]) - forecast_cost
        forecast_gp = jc_percent(forecast_profit, row["Contract Value"])

        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Remaining Cost Budget", f"${remaining_cost_budget:,.2f}")
        f2.metric("Forecast Remaining Hours", f"{remaining_hours:,.2f}")
        f3.metric("Forecast Finish", str(finish_date))
        f4.metric("Forecast GP %", f"{forecast_gp:.2f}%")

        if forecast_gp < target_gp:
            st.warning("Forecast is below target. Check labour, materials, scope changes and variations.")
        else:
            pb_success("Forecast is at or above target based on these inputs.")

        detail_cols = [
            "Job No", "Job Name", "Builder / Client", "Status", "Leading Hand", "Start Date", "End Date",
            "Contract Value", "Actual Material Cost", "Actual Labour Cost", "Total Actual Cost",
            "Gross Profit", "Gross Profit %", "Estimate Total Ex GST", "Estimated Labour Hours",
            "Timesheet Hours", "Remaining Labour Hours", "Working Days Scheduled"
        ]
        st.dataframe(pd.DataFrame([row[detail_cols]]), width="stretch", hide_index=True)

    elif section == "Financial Forecast":
        st.subheader("Financial Forecast by Job")
        statuses = ["All"] + sorted([str(x) for x in df["Status"].fillna("").unique() if str(x).strip()])
        selected_status = st.selectbox("Status Filter", statuses)
        filtered = df.copy()
        if selected_status != "All":
            filtered = filtered[filtered["Status"].astype(str) == selected_status]

        total_contract = jc_float(filtered["Contract Value"].sum()) if not filtered.empty else 0
        total_cost = jc_float(filtered["Total Actual Cost"].sum()) if not filtered.empty else 0
        total_profit = total_contract - total_cost
        total_gp = jc_percent(total_profit, total_contract)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Contract Value", f"${total_contract:,.2f}")
        c2.metric("Cost to Date", f"${total_cost:,.2f}")
        c3.metric("Gross Profit", f"${total_profit:,.2f}")
        c4.metric("Gross Profit %", f"{total_gp:.2f}%")

        show_cols = [
            "Job No", "Job Name", "Builder / Client", "Status", "Start Date", "End Date",
            "Contract Value", "Total Actual Cost", "Gross Profit", "Gross Profit %",
            "Actual Material Cost", "Actual Labour Cost", "Timesheet Hours", "Estimate Total Ex GST"
        ]
        st.dataframe(filtered[[c for c in show_cols if c in filtered.columns]], width="stretch", hide_index=True)

        monthly = filtered.groupby("Forecast Month", dropna=False).agg({
            "Contract Value": "sum",
            "Total Actual Cost": "sum",
            "Gross Profit": "sum",
            "Timesheet Hours": "sum",
        }).reset_index()
        if not monthly.empty:
            monthly["Gross Profit %"] = monthly.apply(lambda r: jc_percent(r["Gross Profit"], r["Contract Value"]), axis=1)
            st.markdown("### Forecast by Month")
            st.dataframe(monthly, width="stretch", hide_index=True)

    elif section == "Scheduling Forecast":
        st.subheader("Scheduling / Labour Forecast")
        hours_day = st.number_input("Default Hours / Person / Day", min_value=1.0, value=8.0, step=0.5)
        sched = df.copy()
        sched["Budget Labour Hours"] = sched["Estimated Labour Hours"]
        sched["Budget Labour Hours"] = sched.apply(
            lambda r: jc_float(r["Budget Labour Hours"]) if jc_float(r["Budget Labour Hours"]) > 0 else jc_float(r["Contract Value"]) / 120,
            axis=1,
        )
        sched["Remaining Hours"] = (sched["Budget Labour Hours"] - sched["Timesheet Hours"]).clip(lower=0)
        sched["Remaining Painter Days"] = (sched["Remaining Hours"] / hours_day).round(2)
        sched["Required Painters"] = sched.apply(
            lambda r: round(jc_float(r["Budget Labour Hours"]) / (jc_float(r["Working Days Scheduled"]) * hours_day), 2)
            if jc_float(r["Working Days Scheduled"]) > 0 else 0,
            axis=1,
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("Remaining Labour Hours", f"{jc_float(sched['Remaining Hours'].sum()):,.2f}")
        c2.metric("Remaining Painter Days", f"{jc_float(sched['Remaining Painter Days'].sum()):,.2f}")
        c3.metric("Jobs in Forecast", len(sched))

        cols = [
            "Job No", "Job Name", "Status", "Leading Hand", "Start Date", "End Date",
            "Working Days Scheduled", "Budget Labour Hours", "Timesheet Hours",
            "Remaining Hours", "Required Painters", "Remaining Painter Days"
        ]
        st.dataframe(sched[[c for c in cols if c in sched.columns]], width="stretch", hide_index=True)

    else:
        st.subheader("Export Job Cost / Forecast Data")
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.drop(columns=["job_id"], errors="ignore").to_excel(writer, index=False, sheet_name="Job Forecast")
            monthly = df.groupby("Forecast Month", dropna=False).agg({
                "Contract Value": "sum",
                "Total Actual Cost": "sum",
                "Gross Profit": "sum",
                "Timesheet Hours": "sum",
            }).reset_index()
            if not monthly.empty:
                monthly["Gross Profit %"] = monthly.apply(lambda r: jc_percent(r["Gross Profit"], r["Contract Value"]), axis=1)
            monthly.to_excel(writer, index=False, sheet_name="Monthly Forecast")
            for ws in writer.book.worksheets:
                for column_cells in ws.columns:
                    max_len = 0
                    col_letter = column_cells[0].column_letter
                    for cell in column_cells:
                        value = "" if cell.value is None else str(cell.value)
                        max_len = max(max_len, len(value))
                    ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 45)
        output.seek(0)
        st.download_button(
            "Download Job Cost / Forecast Excel",
            data=output.getvalue(),
            file_name="PB_JobHub_Job_Cost_Forecast.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def jobhub_ai_api_key():
    try:
        if "OPENAI_API_KEY" in st.secrets:
            return st.secrets["OPENAI_API_KEY"]
    except Exception:
        pass
    return os.environ.get("OPENAI_API_KEY", "")


def jobhub_ai_model():
    try:
        if "OPENAI_MODEL" in st.secrets:
            return st.secrets["OPENAI_MODEL"]
    except Exception:
        pass
    return os.environ.get("OPENAI_MODEL", "gpt-5.5")


def jobhub_ai_context(selected_job_id=None):
    df = job_cost_summary_dataframe()
    lines = []

    if selected_job_id and not df.empty:
        selected = df[df["job_id"].astype(int) == int(selected_job_id)]
        if not selected.empty:
            r = selected.iloc[0]
            lines.append("SELECTED JOB SUMMARY")
            for col in [
                "Job No", "Job Name", "Builder / Client", "Status", "Leading Hand", "Start Date", "End Date",
                "Contract Value", "Actual Material Cost", "Actual Labour Cost", "Total Actual Cost",
                "Gross Profit", "Gross Profit %", "Timesheet Hours", "Estimated Labour Hours", "Remaining Labour Hours"
            ]:
                if col in selected.columns:
                    lines.append(f"{col}: {r.get(col, '')}")

            materials = df_query("""
                SELECT COALESCE(NULLIF(m.custom_product_code, ''), p.product_code, '') AS 'Product Code',
                       COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS 'Product Name',
                       m.qty_required AS 'Qty Required',
                       m.qty_received AS 'Qty Received',
                       COALESCE(m.custom_unit_price, p.price_ex_gst, 0) AS 'Unit Price',
                       ROUND(CAST((COALESCE(m.qty_required, 0) * COALESCE(m.custom_unit_price, p.price_ex_gst, 0)) AS numeric), 2) AS 'Line Cost',
                       m.notes AS 'Notes'
                FROM material_entries m
                LEFT JOIN products p ON p.id = m.product_id
                WHERE m.job_id = ?
                ORDER BY m.id DESC
                LIMIT 50
            """, (selected_job_id,))
            if not materials.empty:
                if not ai_personal_data_enabled():
                    materials = materials.drop(columns=["Notes"], errors="ignore")
                lines.append("\nMATERIALS")
                lines.append(materials.to_csv(index=False))

            timesheets = df_query("""
                SELECT t.employee_id AS 'Employee ID',
                       e.name AS 'Employee',
                       t.work_date AS 'Date',
                       t.total_hours AS 'Hours',
                       t.work_type AS 'Work Type',
                       t.status AS 'Status',
                       t.notes AS 'Notes'
                FROM timesheet_entries t
                LEFT JOIN employees e ON e.id = t.employee_id
                WHERE t.job_id = ?
                ORDER BY t.work_date DESC
                LIMIT 50
            """, (selected_job_id,))
            if not timesheets.empty:
                if not ai_personal_data_enabled():
                    timesheets["Employee"] = timesheets["Employee ID"].map(
                        lambda value: f"Staff #{int(value)}"
                    )
                    timesheets = timesheets.drop(
                        columns=["Employee ID", "Notes"],
                        errors="ignore",
                    )
                lines.append("\nTIMESHEETS")
                lines.append(timesheets.to_csv(index=False))
    else:
        if not df.empty:
            overview_cols = [
                "Job No", "Job Name", "Status", "Start Date", "End Date", "Contract Value",
                "Total Actual Cost", "Gross Profit", "Gross Profit %", "Timesheet Hours"
            ]
            lines.append("ALL JOBS OVERVIEW")
            lines.append(df[[c for c in overview_cols if c in df.columns]].head(60).to_csv(index=False))

    return "\n".join(lines)[:18000]







# =============================
# APP BUILDER AI
# =============================
def app_builder_read_file(path, max_chars=12000):
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return ""
        text = p.read_text(encoding="utf-8", errors="ignore")
        if len(text) > max_chars:
            return text[:max_chars] + f"\n\n...[trimmed after {max_chars} characters]..."
        return text
    except Exception as e:
        return f"Could not read {path}: {e}"


def app_builder_file_tree():
    allowed = []
    try:
        root = Path(".")
        for p in root.rglob("*"):
            if p.is_file():
                name = str(p).replace("\\", "/")
                if "__pycache__" in name or ".git" in name or "pb_jobhub.db" in name or "secrets.toml" in name:
                    continue
                if name.endswith((".py", ".txt", ".toml", ".sql")):
                    allowed.append(name)
    except Exception:
        allowed = ["pb_jobhub_app.py", "requirements.txt", "SUPABASE_SCHEMA_MANUAL_BACKUP.sql"]
    return sorted(allowed)[:80]


def app_builder_relevant_code_snippets(question, max_snippets=8, chars_per_snippet=1800):
    """
    Pulls relevant sections from pb_jobhub_app.py without sending the full app every time.
    """
    source = app_builder_read_file("pb_jobhub_app.py", max_chars=400000)
    if not source:
        return ""

    terms = []
    for raw in re.findall(r"[A-Za-z_]{4,}", str(question).lower()):
        if raw not in ["this", "that", "with", "from", "your", "have", "will", "make", "need", "want", "please"]:
            terms.append(raw)

    priority_terms = [
        "streamlit", "supabase", "postgres", "connect", "df_query", "execute", "job", "employee",
        "timesheet", "estimate", "material", "product", "user", "login", "forecast", "ai", "openai"
    ]
    terms = list(dict.fromkeys(terms + priority_terms))

    snippets = []
    lines = source.splitlines()
    lower_lines = [l.lower() for l in lines]

    matched_indexes = []
    for i, line in enumerate(lower_lines):
        if any(t in line for t in terms):
            matched_indexes.append(i)

    # group nearby line matches
    used = set()
    for idx in matched_indexes:
        if len(snippets) >= max_snippets:
            break
        start = max(idx - 20, 0)
        end = min(idx + 60, len(lines))
        key = (start // 40, end // 40)
        if key in used:
            continue
        used.add(key)
        snippet = "\n".join(lines[start:end])
        if len(snippet) > chars_per_snippet:
            snippet = snippet[:chars_per_snippet] + "\n...[snippet trimmed]..."
        snippets.append(f"--- pb_jobhub_app.py lines approx {start+1}-{end} ---\n{snippet}")

    return "\n\n".join(snippets)


def app_builder_notes_context(limit=20):
    try:
        notes = df_query("""
            SELECT topic AS 'Topic', note AS 'Note', source AS 'Source', created_at AS 'Created'
            FROM app_builder_notes
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        if notes.empty:
            return ""
        return notes.to_csv(index=False)
    except Exception:
        return ""


def save_app_builder_note(topic, note, source="Manual / AI"):
    execute("""
        INSERT INTO app_builder_notes (topic, note, source, created_at)
        VALUES (?, ?, ?, ?)
    """, (topic, note, source, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))







# =============================
# APP BUILDER SELF-EDIT HELPERS
# =============================
SELF_EDIT_ALLOWED_FILES = {
    "pb_jobhub_app.py",
    "requirements.txt",
    "SUPABASE_SCHEMA_MANUAL_BACKUP.sql",
    ".streamlit/config.toml",
}


def self_edit_enabled():
    return (
        is_admin()
        and str(os.getenv("JOBHUB_ENABLE_SELF_EDIT", "")).strip().casefold()
        in {"1", "true", "yes", "on"}
    )


def self_edit_safe_path(target_file):
    target_file = str(target_file or "").strip().replace("\\", "/")
    if target_file not in SELF_EDIT_ALLOWED_FILES:
        return None, f"File not allowed for self-edit: {target_file}"

    p = Path(target_file)
    if ".." in p.parts or p.is_absolute():
        return None, "Unsafe file path."

    return p, None


def self_edit_extract_json(text):
    """
    Extracts a JSON array from AI output.
    Expected format:
    [
      {
        "target_file": "pb_jobhub_app.py",
        "find": "exact old text",
        "replace": "new text",
        "reason": "why"
      }
    ]
    """
    raw = str(text or "").strip()

    # Remove markdown fences if present.
    raw = re.sub(r"^```(?:json)?", "", raw.strip(), flags=re.I).strip()
    raw = re.sub(r"```$", "", raw.strip()).strip()

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "replacements" in data:
            data = data["replacements"]
        return data if isinstance(data, list) else []
    except Exception:
        pass

    # Try to find the first JSON array in text.
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(raw[start:end+1])
            return data if isinstance(data, list) else []
        except Exception:
            pass

    return []


def self_edit_validate_replacements(replacements):
    issues = []
    if not replacements:
        issues.append("No replacement JSON found.")
        return issues

    for i, item in enumerate(replacements, start=1):
        if not isinstance(item, dict):
            issues.append(f"Replacement {i} is not an object.")
            continue

        target = item.get("target_file", "")
        find = item.get("find", "")
        replace = item.get("replace", "")

        path, error = self_edit_safe_path(target)
        if error:
            issues.append(f"Replacement {i}: {error}")

        if not find:
            issues.append(f"Replacement {i}: find text is empty.")

        if replace is None:
            issues.append(f"Replacement {i}: replace text is missing.")

        if path and path.exists():
            try:
                file_text = path.read_text(encoding="utf-8", errors="ignore")
                if find and find not in file_text:
                    issues.append(f"Replacement {i}: find text was not found in {target}.")
            except Exception as e:
                issues.append(f"Replacement {i}: could not read {target}: {e}")
        elif path:
            issues.append(f"Replacement {i}: target file does not exist: {target}")

    return issues


def self_edit_apply_replacements(replacements):
    """
    Applies exact find/replace patches.
    Creates backups first.
    If pb_jobhub_app.py compile fails, restores the backup.
    """
    result = {
        "applied": 0,
        "backups": [],
        "messages": [],
        "success": False,
    }

    issues = self_edit_validate_replacements(replacements)
    if issues:
        result["messages"].extend(issues)
        return result

    backup_root = Path(tempfile.gettempdir()) / "pb_jobhub_self_edit_backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    backup_map = {}

    def restore_every_touched_file():
        restored = []
        for original, backup in backup_map.items():
            try:
                shutil.copy2(backup, original)
                restored.append(original)
            except Exception as restore_error:
                result["messages"].append(
                    f"Could not restore {original}: {restore_error}"
                )
        if restored:
            result["messages"].append(
                "Restored all touched files: " + ", ".join(sorted(restored))
            )

    try:
        for item in replacements:
            target_file = item["target_file"]
            find = item["find"]
            replace = item["replace"]

            path, error = self_edit_safe_path(target_file)
            if error:
                raise RuntimeError(error)

            path = path.resolve()
            if str(path) not in backup_map:
                safe_backup_name = re.sub(r"[^A-Za-z0-9._-]", "_", str(path))
                backup_path = backup_root / f"{safe_backup_name}.{stamp}.bak"
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup_path)
                result["backups"].append(str(backup_path))
                backup_map[str(path)] = str(backup_path)

            current = path.read_text(encoding="utf-8", errors="ignore")
            updated = current.replace(find, replace, 1)
            path.write_text(updated, encoding="utf-8")
            result["applied"] += 1
            result["messages"].append(f"Applied replacement to {target_file}: {item.get('reason', 'No reason provided')}")

        # Compile check and rollback for Python app file.
        if "pb_jobhub_app.py" in [str(item.get("target_file")) for item in replacements]:
            try:
                py_compile.compile("pb_jobhub_app.py", doraise=True)
                result["messages"].append("Python compile check passed after self-edit.")
            except Exception as compile_error:
                restore_every_touched_file()
                result["messages"].append(
                    f"Compile failed. Every touched file was rolled back. Error: {compile_error}"
                )
                return result

        result["success"] = True
        return result

    except Exception as e:
        restore_every_touched_file()
        result["messages"].append(f"Self-edit failed: {e}")
        return result


def save_app_code_change(title, request, ai_response, patch_json, target_files, status, result_message=""):
    execute("""
        INSERT INTO app_code_changes
        (title, request, ai_response, patch_json, target_files, status, created_at, applied_at, result_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title,
        request,
        ai_response,
        patch_json,
        target_files,
        status,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "Applied" else "",
        result_message,
    ))


def app_builder_self_edit_prompt(user_request):
    current_code = app_builder_relevant_code_snippets(user_request, max_snippets=12, chars_per_snippet=2500)
    file_tree = "\n".join(app_builder_file_tree())

    return f"""
You are App Builder AI for Premier Brushworks JobHub.

The user wants you to alter the app code. You must return ONLY valid JSON. No markdown. No explanation outside JSON.

Return a JSON array of exact text replacements:
[
  {{
    "target_file": "pb_jobhub_app.py",
    "find": "exact existing text to find",
    "replace": "replacement text",
    "reason": "short reason"
  }}
]

Rules:
- Only target these files: pb_jobhub_app.py, requirements.txt, SUPABASE_SCHEMA_MANUAL_BACKUP.sql, .streamlit/config.toml
- Use exact find text from the code context.
- Keep changes small and safe.
- If the request needs a large rebuild, return one small safe first step.
- Do not include secrets.
- Do not include markdown fences.
- Do not invent code locations that are not in context.

FILE TREE:
{file_tree}

RELEVANT CODE:
{current_code}

USER REQUEST:
{user_request}
"""


def app_builder_self_edit_section():
    if not self_edit_enabled():
        pb_error(
            "Runtime source editing is disabled in production. "
            "Use App Builder AI to prepare a patch, then review, test and deploy it through version control."
        )
        return
    st.subheader("Controlled Self-Edit")
    st.warning(
        "This lets App Builder AI apply exact code replacements to the running app files. "
        "On Streamlit Cloud, file changes may not permanently survive a redeploy unless you download the changed file and upload it to GitHub."
    )

    st.caption(
        "Safety: only exact text replacements are allowed, only approved files can be changed, "
        "a backup is created, and pb_jobhub_app.py is compile-checked after changes."
    )

    request = st.text_area(
        "What code change should the AI make?",
        height=140,
        placeholder="Example: Add a dashboard card showing jobs with missing timesheets this week.",
        key="self_edit_request",
    )

    if st.checkbox("Show relevant code context", value=False, key="self_edit_show_context"):
        st.code(app_builder_relevant_code_snippets(request or "jobhub app"), language="python")

    if st.button("Generate Self-Edit Patch", key="generate_self_edit_patch"):
        if not request.strip():
            pb_error("Enter a code change request first.")
        else:
            prompt = app_builder_self_edit_prompt(request)
            with st.spinner("Generating safe code replacement JSON..."):
                answer, error = jobhub_ai_answer(prompt, "")

            if error:
                pb_error(error)
            else:
                st.session_state["self_edit_ai_response"] = answer
                st.session_state["self_edit_request"] = request
                pb_success("Patch proposal generated.")

    ai_response = st.session_state.get("self_edit_ai_response", "")
    stored_request = st.session_state.get("self_edit_request", request)

    if ai_response:
        st.markdown("### Proposed Patch JSON")
        st.code(ai_response, language="json")

        replacements = self_edit_extract_json(ai_response)
        issues = self_edit_validate_replacements(replacements)

        if issues:
            pb_error("Patch is not ready to apply:")
            for issue in issues:
                st.write(f"- {issue}")
        else:
            pb_success(f"Patch validated. {len(replacements)} replacement(s) ready.")
            preview_rows = []
            for i, item in enumerate(replacements, start=1):
                preview_rows.append({
                    "No": i,
                    "Target File": item.get("target_file", ""),
                    "Find Length": len(str(item.get("find", ""))),
                    "Replace Length": len(str(item.get("replace", ""))),
                    "Reason": item.get("reason", ""),
                })
            st.dataframe(pd.DataFrame(preview_rows), width="stretch", hide_index=True)

            confirm = st.text_input(
                "To apply this AI code change to the running app, type: APPLY CODE CHANGE",
                key="self_edit_confirm",
            )

            if st.button("Apply AI Code Change", key="apply_self_edit_patch"):
                if confirm.strip().upper() != "APPLY CODE CHANGE":
                    pb_error("Type APPLY CODE CHANGE exactly before applying.")
                else:
                    result = self_edit_apply_replacements(replacements)
                    status = "Applied" if result["success"] else "Failed"
                    save_app_code_change(
                        title=stored_request[:100],
                        request=stored_request,
                        ai_response=ai_response,
                        patch_json=json.dumps(replacements, indent=2),
                        target_files=", ".join(sorted(set(str(x.get("target_file", "")) for x in replacements))),
                        status=status,
                        result_message="\n".join(result["messages"]),
                    )

                    if result["success"]:
                        pb_success(f"Applied {result['applied']} code replacement(s).")
                        st.info("Download the changed file below and upload it to GitHub so the change persists after redeploy.")
                    else:
                        pb_error("Patch was not applied or was rolled back.")

                    with st.expander("Self-edit result details", expanded=True):
                        for msg in result["messages"]:
                            st.write(msg)

    st.markdown("### Download Current App Files")
    for file_name in ["pb_jobhub_app.py", "requirements.txt", "SUPABASE_SCHEMA_MANUAL_BACKUP.sql"]:
        p, error = self_edit_safe_path(file_name)
        if p and p.exists():
            data = p.read_text(encoding="utf-8", errors="ignore").encode("utf-8")
            st.download_button(
                f"Download {file_name}",
                data=data,
                file_name=file_name,
                mime="text/plain",
                key=f"download_{file_name}",
            )

    st.markdown("### Code Change History")
    try:
        changes = df_query("""
            SELECT id AS 'ID',
                   title AS 'Title',
                   target_files AS 'Target Files',
                   status AS 'Status',
                   created_at AS 'Created',
                   result_message AS 'Result'
            FROM app_code_changes
            ORDER BY id DESC
            LIMIT 50
        """)
        if changes.empty:
            st.info("No code changes saved yet.")
        else:
            st.dataframe(changes, width="stretch", hide_index=True)
    except Exception:
        st.info("Code change history table will be available after the app initializes the database.")



# =============================
# FREE LOCAL AI / OLLAMA OVERRIDES
# =============================
def ai_secret(name, default=""):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name, default)


def ai_provider():
    """
    AI provider rules:
    - AI_PROVIDER=openai: use OpenAI online/cloud.
    - AI_PROVIDER=ollama: use local Ollama only.
    - AI_PROVIDER=auto or blank:
        * if OPENAI_API_KEY exists, use OpenAI
        * if hosted on Render and no OpenAI key, switch AI off
        * if running locally and no OpenAI key, use Ollama
    - AI_PROVIDER=none/off/disabled: switch AI off
    """
    provider = str(ai_secret("AI_PROVIDER", "auto")).strip().lower()

    if provider in ["none", "off", "disabled", "disable", "false", "0", "no", "no_ai", "no-ai"]:
        return "none"

    if provider not in ["ollama", "openai", "auto"]:
        provider = "auto"

    has_openai_key = bool(str(jobhub_ai_api_key() or "").strip())
    is_render = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"))

    if provider == "openai":
        return "openai"

    if provider == "ollama":
        return "ollama"

    # auto mode
    if has_openai_key:
        return "openai"

    if is_render:
        return "none"

    return "ollama"


def ai_disabled_message():
    return (
        "External AI is switched off unless it is explicitly enabled. "
        "After approving your privacy policy, add AI_PROVIDER=openai, OPENAI_API_KEY and "
        "JOBHUB_ALLOW_EXTERNAL_AI=true in the hosting environment. "
        "For free Ollama AI, run JobHub locally on the same computer as Ollama."
    )


def ollama_base_url():
    return str(ai_secret("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")


def ollama_model():
    return str(ai_secret("OLLAMA_MODEL", "llama3.2:3b")).strip() or "llama3.2:3b"


def ollama_timeout():
    try:
        return int(ai_secret("OLLAMA_TIMEOUT", "120"))
    except Exception:
        return 120


def openai_enabled():
    return bool(str(jobhub_ai_api_key() or "").strip())


def external_ai_enabled():
    return str(ai_secret("JOBHUB_ALLOW_EXTERNAL_AI", "false")).strip().casefold() in {
        "1", "true", "yes", "on",
    }


def ai_personal_data_enabled():
    return str(ai_secret("JOBHUB_AI_INCLUDE_PERSONAL_DATA", "false")).strip().casefold() in {
        "1", "true", "yes", "on",
    }


def ollama_status():
    try:
        response = requests.get(f"{ollama_base_url()}/api/tags", timeout=5)
        if response.status_code == 200:
            return True, f"Ollama connected at {ollama_base_url()} using model {ollama_model()}."
        return False, f"Ollama responded with status {response.status_code}. Check Ollama is running."
    except Exception as e:
        return False, f"Ollama not reachable at {ollama_base_url()}. Start Ollama on this computer. Details: {e}"


def ai_backend_ready():
    provider = ai_provider()

    if provider == "none":
        return False, ai_disabled_message()

    if provider == "openai":
        if openai_enabled() and external_ai_enabled():
            return True, f"Using OpenAI online model {jobhub_ai_model()}."
        if not external_ai_enabled():
            return False, (
                "External AI is disabled. Set JOBHUB_ALLOW_EXTERNAL_AI=true only after "
                "approving the organisation's AI data policy."
            )
        return False, "AI_PROVIDER is openai but OPENAI_API_KEY is missing."

    if provider == "ollama":
        return ollama_status()

    return False, ai_disabled_message()


def ollama_generate(prompt, system="", context="", model=None, timeout=None):
    if ai_provider() == "none":
        return None, ai_disabled_message()

    if ai_provider() == "openai":
        return None, "Ollama is not used in OpenAI mode. Use the JobHub AI Assistant or App Builder AI with OpenAI."

    model = model or ollama_model()
    timeout = timeout or ollama_timeout()

    full_prompt = ""
    if system:
        full_prompt += "SYSTEM:\n" + str(system).strip() + "\n\n"
    if context:
        full_prompt += "CONTEXT:\n" + str(context).strip() + "\n\n"
    full_prompt += "USER:\n" + str(prompt).strip()

    payload = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
    }

    try:
        response = requests.post(
            f"{ollama_base_url()}/api/generate",
            json=payload,
            timeout=timeout,
        )
        if response.status_code >= 400:
            return None, f"Ollama error {response.status_code}: {response.text[:1000]}"

        data = response.json()
        return data.get("response", "").strip(), None
    except Exception as e:
        return None, f"Ollama request failed: {e}"


def openai_responses_answer(prompt, context_text="", include_web=False, require_web=False, system_text=""):
    if not external_ai_enabled():
        return None, (
            "External AI is disabled by policy. An administrator must explicitly set "
            "JOBHUB_ALLOW_EXTERNAL_AI=true before JobHub can send data to OpenAI."
        )
    api_key = jobhub_ai_api_key()
    if not api_key:
        return None, "OPENAI_API_KEY is missing."

    payload = {
        "model": jobhub_ai_model(),
        "input": (
            (system_text or "You are a helpful assistant for Premier Brushworks JobHub.") +
            "\n\nCONTEXT:\n" + str(context_text or "") +
            "\n\nUSER REQUEST:\n" + str(prompt)
        ),
    }

    if include_web:
        payload["tools"] = [{"type": "web_search"}]
        payload["tool_choice"] = "required" if require_web else "auto"

    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=90,
        )
        if response.status_code >= 400:
            return None, f"OpenAI API error {response.status_code}: {response.text[:1000]}"

        data = response.json()
        record_audit_event(
            "external_ai_request",
            "ai_request",
            "",
            {
                "model": jobhub_ai_model(),
                "context_characters": len(str(context_text or "")),
                "web_search": bool(include_web),
            },
        )
        if data.get("output_text"):
            return data["output_text"], None

        parts = []
        for item in data.get("output", []) or []:
            for content in item.get("content", []) or []:
                if isinstance(content, dict) and content.get("text"):
                    parts.append(str(content["text"]))

        return "\n".join(parts) if parts else json.dumps(data)[:3000], None
    except Exception as e:
        return None, f"OpenAI request failed: {e}"


def jobhub_ai_answer(question, context_text):
    system = (
        "You are JobHub AI for Premier Brushworks, a painting and decorating business. "
        "Use only the JobHub context provided. Give practical, direct advice for quoting, job costs, scheduling, "
        "materials, staffing, risks and next actions. If data is missing, say what is missing. Do not invent details."
    )

    provider = ai_provider()
    if provider == "none":
        return None, ai_disabled_message()

    if provider == "openai":
        return openai_responses_answer(question, context_text, include_web=False, require_web=False, system_text=system)

    return ollama_generate(question, system=system, context=context_text)


def app_builder_ai_call(question, include_web=False, require_web=False, selected_mode="Code Helper"):
    file_tree = "\n".join(app_builder_file_tree())
    reqs = app_builder_read_file("requirements.txt", max_chars=6000)
    schema = app_builder_read_file("SUPABASE_SCHEMA_MANUAL_BACKUP.sql", max_chars=12000)
    snippets = app_builder_relevant_code_snippets(question)
    saved_notes = app_builder_notes_context()

    system_prompt = f"""
You are App Builder AI inside Premier Brushworks JobHub.
You help improve and maintain this Streamlit + Supabase business app.

Rules:
- Be practical and direct.
- Help design features, find likely bugs, improve speed, improve database structure, and plan safe changes.
- If asked to change the app, provide a clear build plan and exact code/pseudocode sections.
- Do not pretend you have already changed GitHub or deployed the app.
- Do not expose or ask for secrets.
- If internet/web content is provided in context, use it and mention source URLs.
- If something is risky, say so and suggest the safest next step.
- This AI learns by saving notes in app_builder_notes. It does not retrain model weights.
Mode: {selected_mode}
"""

    context = f"""
APP FILE TREE:
{file_tree}

REQUIREMENTS:
{reqs}

DATABASE SCHEMA EXCERPT:
{schema}

RELEVANT CURRENT APP CODE SNIPPETS:
{snippets}

SAVED APP BUILDER LEARNINGS:
{saved_notes}
"""

    provider = ai_provider()
    if provider == "none":
        return None, ai_disabled_message()

    if provider == "openai":
        return openai_responses_answer(
            question,
            context,
            include_web=include_web,
            require_web=require_web,
            system_text=system_prompt,
        )

    if include_web:
        context += (
            "\n\nNOTE: Local Ollama mode does not have paid live web_search. "
            "Use the Internet Learning section with specific URLs to fetch pages for free and save notes."
        )

    return ollama_generate(question, system=system_prompt, context=context, timeout=ollama_timeout())


def fetch_web_page_text(url, max_chars=18000):
    """
    Free URL fetcher for internet learning.
    The user provides URLs. JobHub fetches the page and local Ollama summarises it.
    """
    current_url = str(url or "").strip()
    if not current_url:
        return "", "URL is blank."

    try:
        raw_content = b""
        response_encoding = "utf-8"
        for redirect_count in range(4):
            valid, validation_error = validate_public_http_url(current_url)
            if not valid:
                return "", validation_error

            with requests.get(
                current_url,
                timeout=(5, 20),
                headers={"User-Agent": "PremierBrushworksJobHubLearningBot/2.0"},
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location", "")
                    if not location:
                        return "", "The URL redirected without a destination."
                    current_url = urljoin(current_url, location)
                    if redirect_count >= 3:
                        return "", "The URL exceeded the redirect safety limit."
                    continue
                if response.status_code >= 400:
                    return "", f"Could not fetch URL. Status {response.status_code}"

                content_type = str(response.headers.get("Content-Type", "")).casefold()
                if not any(
                    allowed in content_type
                    for allowed in ("text/", "application/json", "application/xml", "application/xhtml")
                ):
                    return "", "Only text, HTML, JSON and XML pages can be learned from."
                content_length = int(response.headers.get("Content-Length", "0") or 0)
                if content_length > 1_000_000:
                    return "", "The web page exceeds the 1 MB safety limit."

                chunks = []
                bytes_read = 0
                for chunk in response.iter_content(chunk_size=16_384):
                    if not chunk:
                        continue
                    bytes_read += len(chunk)
                    if bytes_read > 1_000_000:
                        return "", "The web page exceeds the 1 MB safety limit."
                    chunks.append(chunk)
                raw_content = b"".join(chunks)
                response_encoding = response.encoding or "utf-8"
                break
        else:
            return "", "The URL could not be fetched safely."

        text = raw_content.decode(response_encoding, errors="replace")
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[trimmed]..."

        record_audit_event(
            "external_url_fetched",
            "learning_source",
            current_url,
            {"bytes": len(raw_content)},
        )
        return text, None
    except Exception as e:
        return "", f"Fetch failed: {e}"


def save_learning_source(topic, url, summary="", active=1):
    execute("""
        INSERT INTO app_learning_sources
        (topic, url, active, last_checked, last_summary, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        topic,
        url,
        int(active),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S") if summary else "",
        summary,
        "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))


def summarise_url_into_learning(topic, url):
    page_text, error = fetch_web_page_text(url)
    if error:
        return None, error

    prompt = (
        "Summarise this web page into practical JobHub learning notes for Premier Brushworks. "
        "Focus on what should be saved for future app building, quoting, cost forecasting, Streamlit, Supabase, "
        "Ollama/local AI, safety, or business operations. "
        "Return concise notes and include the source URL.\n\n"
        f"TOPIC: {topic}\nSOURCE URL: {url}\nPAGE TEXT:\n{page_text}"
    )

    answer, ai_error = app_builder_ai_call(
        question=prompt,
        include_web=False,
        require_web=False,
        selected_mode="Internet Learning Summariser",
    )
    if ai_error:
        return None, ai_error

    save_app_builder_note(topic, answer, source=f"URL: {url}")
    save_learning_source(topic, url, summary=answer, active=1)
    return answer, None


def free_local_ai_setup_page():
    st.header("Free Local AI Setup")
    st.caption("Use OpenAI online on Render, or Ollama for free local AI when running JobHub on your own computer.")

    status_ok, status_message = ai_backend_ready()

    c1, c2 = st.columns(2)
    c1.metric("AI Provider", ai_provider())
    c2.metric("OpenAI Model", jobhub_ai_model() if ai_provider() == "openai" else ollama_model())

    if status_ok:
        pb_success(status_message)
    else:
        st.warning(status_message)

    st.markdown("### Recommended Streamlit Secrets")
    st.code(
        'AI_PROVIDER = "ollama"\n'
        'OLLAMA_BASE_URL = "http://localhost:11434"\n'
        'OLLAMA_MODEL = "llama3.2:3b"\n'
        'OLLAMA_TIMEOUT = "120"\n\n'
        '# Optional external AI, only after privacy approval:\n'
        '# JOBHUB_ALLOW_EXTERNAL_AI = "true"\n'
        '# JOBHUB_AI_INCLUDE_PERSONAL_DATA = "false"\n'
        '# OPENAI_API_KEY = "sk-..."\n'
        '# OPENAI_MODEL = "gpt-5.5"\n',
        language="toml",
    )

    st.markdown("### Test Local AI")
    test_prompt = st.text_input("Test prompt", value="Say hello and confirm you are connected to JobHub.")
    if st.button("Test Ollama Local AI", key="test_ollama_ai"):
        answer, error = ollama_generate(test_prompt, system="You are a local AI test assistant.")
        if error:
            pb_error(error)
        else:
            pb_success("Local AI responded.")
            st.write(answer)

    st.markdown("### What free learning means")
    st.info(
        "The model learns by saving useful notes into JobHub's database. "
        "It does not retrain the AI model weights. Saved notes are reused as context in future AI answers."
    )


def app_builder_ai_page():
    st.header("App Builder AI")
    st.caption("Build, improve and learn for JobHub using free local Ollama AI by default.")

    status_ok, status_message = ai_backend_ready()
    if status_ok:
        pb_success(status_message)
    else:
        st.warning(status_message)
        st.info("Open the Free Local AI Setup tab for install and connection steps.")

    app_builder_sections = [
        "Build / Fix the App",
        "Internet Learning",
        "Saved Learnings",
        "Free Local AI Setup",
    ]
    if self_edit_enabled():
        app_builder_sections.insert(1, "Self-Edit Code")
    section = st.radio(
        "Section",
        app_builder_sections,
        horizontal=True,
        key="app_builder_section",
    )

    if section == "Build / Fix the App":
        st.subheader("Build / Fix the App")
        mode = st.selectbox(
            "Mode",
            ["Code Helper", "Bug Fixer", "Feature Planner", "Speed Optimiser", "Database / Supabase Helper", "Streamlit UI Helper"],
            key="app_builder_mode",
        )

        include_web = False
        require_web = False
        app_builder_external_consent = True

        if ai_provider() == "openai" or (ai_provider() == "auto" and openai_enabled()):
            include_web = st.checkbox("Allow OpenAI live internet research", value=True, key="app_builder_include_web")
            require_web = st.checkbox("Force OpenAI web search for this request", value=False, key="app_builder_require_web")
            app_builder_external_consent = st.checkbox(
                "I confirm the displayed code context may be sent to the configured external AI provider",
                value=False,
                key="app_builder_external_consent",
            )
        else:
            st.info("Free local Ollama mode is active. For internet learning, use the Internet Learning tab with URLs.")

        quick = st.selectbox(
            "Quick request",
            [
                "Custom",
                "Review this app and suggest the next 5 improvements",
                "Help me make the app faster",
                "Help me add a new feature safely",
                "Review saved learning notes and suggest the best next JobHub upgrade",
                "Tell me what code files need changing for this feature",
            ],
            key="app_builder_quick",
        )
        default_question = "" if quick == "Custom" else quick

        question = st.text_area(
            "What do you want to build or fix?",
            value=default_question,
            height=150,
            placeholder="Example: Add a daily dashboard showing jobs starting this week, overdue invoices, missing timesheets and jobs at margin risk.",
            key="app_builder_question",
        )

        if st.checkbox("Show app code context being sent", value=False, key="app_builder_show_context"):
            st.markdown("### File tree")
            st.code("\n".join(app_builder_file_tree()))
            st.markdown("### Relevant snippets")
            st.code(app_builder_relevant_code_snippets(question or "jobhub app"))

        if st.button("Ask App Builder AI", key="ask_app_builder_ai"):
            if not question.strip():
                pb_error("Enter a build/fix request first.")
            elif not app_builder_external_consent:
                pb_error("Review the code context and confirm external AI data sharing first.")
            else:
                with st.spinner("App Builder AI is reviewing JobHub..."):
                    answer, error = app_builder_ai_call(
                        question=question,
                        include_web=include_web,
                        require_web=require_web,
                        selected_mode=mode,
                    )

                if error:
                    pb_error(error)
                else:
                    st.markdown("### App Builder AI")
                    st.write(answer)

                    with st.expander("Save this as a learning note"):
                        note_topic = st.text_input("Topic", value=question[:80], key="save_ai_learning_topic")
                        note_text = st.text_area("Note to save", value=answer[:4000], height=200, key="save_ai_learning_text")
                        if st.button("Save Learning Note", key="save_ai_learning_button"):
                            save_app_builder_note(note_topic, note_text, source="App Builder AI")
                            pb_success("Learning note saved.")

    elif section == "Self-Edit Code":
        app_builder_self_edit_section()

    elif section == "Internet Learning":
        st.subheader("Free Internet Learning by URL")
        st.caption("Paste useful URLs. JobHub fetches the page, local AI summarises it, and the learning is saved for future use.")

        with st.form("url_learning_form"):
            topic = st.text_input(
                "Learning topic",
                value="Streamlit / Supabase / JobHub app improvement",
            )
            urls_text = st.text_area(
                "URLs to learn from, one per line",
                height=140,
                placeholder="https://docs.streamlit.io/...\nhttps://docs.ollama.com/...",
            )
            submitted = st.form_submit_button("Fetch URLs, Summarise and Save Learning")

        if submitted:
            urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
            if not urls:
                pb_error("Paste at least one URL.")
            else:
                for url in urls:
                    st.markdown(f"### Learning from: {url}")
                    with st.spinner(f"Fetching and summarising {url}..."):
                        summary, error = summarise_url_into_learning(topic, url)
                    if error:
                        pb_error(error)
                    else:
                        pb_success("Saved learning note.")
                        st.write(summary)

        st.markdown("### Saved Learning Sources")
        sources = df_query("""
            SELECT id AS 'ID',
                   topic AS 'Topic',
                   url AS 'URL',
                   active AS 'Active',
                   last_checked AS 'Last Checked',
                   last_summary AS 'Last Summary'
            FROM app_learning_sources
            ORDER BY id DESC
            LIMIT 100
        """)
        if sources.empty:
            st.info("No learning sources saved yet.")
        else:
            st.dataframe(sources[["ID", "Topic", "URL", "Active", "Last Checked"]], width="stretch", hide_index=True)

            if st.button("Refresh All Active Learning Sources", key="refresh_learning_sources"):
                active_sources = sources[sources["Active"].astype(int) == 1]
                if active_sources.empty:
                    st.info("No active sources to refresh.")
                else:
                    for _, row in active_sources.iterrows():
                        st.markdown(f"Refreshing: {row['URL']}")
                        summary, error = summarise_url_into_learning(row["Topic"], row["URL"])
                        if error:
                            pb_error(error)
                        else:
                            execute(
                                "UPDATE app_learning_sources SET last_checked = ?, last_summary = ? WHERE id = ?",
                                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), summary, int(row["ID"]))
                            )
                            pb_success("Refreshed and saved.")

    elif section == "Saved Learnings":
        st.subheader("Saved Learnings")
        notes = df_query("""
            SELECT id AS 'ID',
                   topic AS 'Topic',
                   source AS 'Source',
                   created_at AS 'Created',
                   note AS 'Note'
            FROM app_builder_notes
            ORDER BY id DESC
        """)

        if notes.empty:
            st.info("No saved learnings yet.")
        else:
            st.dataframe(notes[["ID", "Topic", "Source", "Created"]], width="stretch", hide_index=True)

            note_options = {f"{row['Topic']} | {row['Source']} | ID {row['ID']}": int(row["ID"]) for _, row in notes.iterrows()}
            selected = st.selectbox("Open learning note", list(note_options.keys()), key="open_learning_note")
            selected_id = note_options[selected]
            row = notes[notes["ID"].astype(int) == selected_id].iloc[0]
            st.markdown(f"### {row['Topic']}")
            st.caption(f"{row['Source']} • {row['Created']}")
            st.write(row["Note"])

            col1, col2 = st.columns(2)
            if col1.button("Delete This Learning Note", key="delete_learning_note"):
                execute("DELETE FROM app_builder_notes WHERE id = ?", (selected_id,))
                pb_success("Learning note deleted.")
                refresh()

        with st.expander("Add manual learning note"):
            with st.form("manual_learning_note_form"):
                topic = st.text_input("Topic")
                source = st.text_input("Source", value="Manual")
                note = st.text_area("Note", height=180)
                submitted = st.form_submit_button("Save Manual Learning")
                if submitted:
                    if not topic.strip() or not note.strip():
                        pb_error("Topic and note are required.")
                    else:
                        save_app_builder_note(topic, note, source=source)
                        pb_success("Learning note saved.")
                        refresh()

    else:
        free_local_ai_setup_page()


def jobhub_ai_assistant_page():
    st.header("JobHub AI Assistant")
    st.caption("Ask an AI assistant about your JobHub data, job costs, quotes, scheduling and risks.")

    status_ok, status_message = ai_backend_ready()
    if status_ok:
        pb_success(status_message)
    else:
        st.warning(status_message)
        st.info("For free mode, install Ollama and use App Builder AI > Free Local AI Setup.")
        return

    job_options = get_job_options()
    mode = st.radio("Context", ["All Jobs Overview", "Selected Job"], horizontal=True, key="ai_context_mode")
    selected_job_id = None

    if mode == "Selected Job":
        if not job_options:
            st.info("Create a job first.")
            return
        selected_job = st.selectbox("Select Job", list(job_options.keys()), key="ai_selected_job")
        selected_job_id = job_options[selected_job]

    quick = st.selectbox(
        "Quick Question",
        [
            "Custom",
            "Which jobs are at risk of running over budget?",
            "What should I check before quoting this job?",
            "How many painters do I need to finish this job on time?",
            "What materials or timesheets look unusual?",
            "Give me a director-level summary for this week.",
        ],
        key="ai_quick_question",
    )
    default_question = "" if quick == "Custom" else quick

    question = st.text_area(
        "Ask JobHub AI",
        value=default_question,
        height=120,
        placeholder="Example: Review this job and tell me the margin risk, labour pressure and next actions.",
        key="ai_question",
    )

    context_text = jobhub_ai_context(selected_job_id)
    learning_context = app_builder_notes_context(limit=20)
    if learning_context:
        context_text += "\n\nSAVED JOBHUB LEARNINGS:\n" + learning_context

    if st.checkbox("Show data being sent to AI", value=False, key="ai_show_context"):
        st.text_area("Context Preview", value=context_text, height=300)

    external_consent = True
    if ai_provider() == "openai":
        external_consent = st.checkbox(
            "I confirm this reviewed context may be sent to the configured external AI provider",
            value=False,
            key="ai_external_data_consent",
        )

    if st.button("Ask JobHub AI", key="ask_jobhub_ai"):
        if not question.strip():
            pb_error("Enter a question first.")
        elif not external_consent:
            pb_error("Review the context and confirm external AI data sharing first.")
        else:
            with st.spinner("JobHub AI is reviewing your data..."):
                answer, error = jobhub_ai_answer(question, context_text)
            if error:
                pb_error(error)
            else:
                st.markdown("### Answer")
                st.write(answer)



# =============================
# PB CONTROL CENTRE
# =============================
def pb_float(value, default=0.0):
    try:
        if value is None or value == "" or pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def pb_date(value):
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()[:10]
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    return None


def pb_percent(numerator, denominator):
    denominator = pb_float(denominator)
    if denominator == 0:
        return 0.0
    return round((pb_float(numerator) / denominator) * 100, 2)


def pb_business_days(start_value, end_value):
    start = pb_date(start_value)
    end = pb_date(end_value)
    if not start or not end or end < start:
        return 0
    total = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            total += 1
        current += timedelta(days=1)
    return total


def pb_next_variation_no(job_id):
    df = df_query(
        "SELECT variation_no FROM job_variations WHERE job_id = ?",
        (job_id,),
    )
    return next_scoped_number(
        df["variation_no"].tolist() if not df.empty else [],
        "VAR",
    )


def pb_next_claim_no(job_id):
    df = df_query(
        "SELECT claim_no FROM invoice_claims WHERE job_id = ?",
        (job_id,),
    )
    return next_scoped_number(
        df["claim_no"].tolist() if not df.empty else [],
        "CLAIM",
    )


def pb_job_cost_frame():
    jobs = df_query("""
        SELECT j.id AS job_id,
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               COALESCE(bc.name, '') AS 'Builder / Client',
               j.site_address AS 'Site Address',
               j.status AS 'Status',
               j.leading_hand AS 'Leading Hand',
               j.start_date AS 'Start Date',
               j.end_date AS 'End Date',
               COALESCE(j.contract_value, 0) AS 'Contract Value',
               j.notes AS 'Notes'
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        ORDER BY j.job_no
    """)
    if jobs.empty:
        return jobs

    materials = df_query("""
        SELECT m.job_id,
               COALESCE(SUM(COALESCE(m.qty_required, 0) * COALESCE(m.custom_unit_price, p.price_ex_gst, 0)), 0) AS 'Committed Material Cost',
               COALESCE(SUM(COALESCE(m.qty_received, 0) * COALESCE(m.custom_unit_price, p.price_ex_gst, 0)), 0) AS 'Material Cost',
               COALESCE(SUM(COALESCE(m.qty_required, 0)), 0) AS 'Material Qty Required',
               COALESCE(SUM(COALESCE(m.qty_received, 0)), 0) AS 'Material Qty Received',
               COUNT(*) AS 'Material Lines'
        FROM material_entries m
        LEFT JOIN products p ON p.id = m.product_id
        GROUP BY m.job_id
    """)

    wages = df_query("""
        SELECT w.job_id,
               COALESCE(SUM(COALESCE(w.hours, 0)), 0) AS 'Wage Hours',
               COALESCE(SUM(
                   COALESCE(w.hours, 0) *
                   COALESCE(NULLIF(w.hourly_rate_snapshot, 0), e.rate_plus_10, e.base_hourly_rate, 0)
               ), 0) AS 'Labour Cost'
        FROM wage_entries w
        LEFT JOIN employees e ON e.id = w.employee_id
        GROUP BY w.job_id
    """)

    timesheets = df_query("""
        SELECT job_id,
               COALESCE(SUM(COALESCE(total_hours, 0)), 0) AS 'Timesheet Hours',
               COUNT(*) AS 'Timesheet Lines'
        FROM timesheet_entries
        WHERE COALESCE(status, 'Submitted') <> 'Rejected'
        GROUP BY job_id
    """)

    budgets = df_query("""
        SELECT job_id,
               COALESCE(quoted_labour_hours, 0) AS 'Budget Labour Hours',
               COALESCE(quoted_labour_cost, 0) AS 'Budget Labour Cost',
               COALESCE(quoted_materials, 0) AS 'Budget Materials',
               COALESCE(quoted_access_equipment, 0) AS 'Budget Access',
               COALESCE(quoted_subcontractors, 0) AS 'Budget Subcontractors',
               COALESCE(quoted_sundries, 0) AS 'Budget Sundries',
               COALESCE(target_gp_percent, 35) AS 'Target GP %',
               locked_at AS 'Budget Locked'
        FROM job_budgets
    """)

    variations = df_query("""
        SELECT job_id,
               COALESCE(SUM(CASE WHEN status IN ('Approved', 'Sent') THEN COALESCE(amount_ex_gst, 0) ELSE 0 END), 0) AS 'Variation Value',
               COALESCE(SUM(CASE WHEN status = 'Approved' THEN COALESCE(amount_ex_gst, 0) ELSE 0 END), 0) AS 'Approved Variation Value',
               COUNT(*) AS 'Variation Count'
        FROM job_variations
        GROUP BY job_id
    """)

    claims = df_query("""
        SELECT job_id,
               COALESCE(SUM(
                   CASE
                       WHEN status NOT IN ('Draft', 'Void', 'Cancelled')
                       THEN COALESCE(amount_ex_gst, 0)
                       ELSE 0
                   END
               ), 0) AS 'Claimed Amount',
               COALESCE(SUM(CASE WHEN status = 'Paid' THEN COALESCE(amount_ex_gst, 0) ELSE 0 END), 0) AS 'Paid Amount',
               COUNT(*) AS 'Claim Count'
        FROM invoice_claims
        GROUP BY job_id
    """)

    df = jobs.copy()
    for extra in [materials, wages, timesheets, budgets, variations, claims]:
        if extra is not None and not extra.empty:
            df = df.merge(extra, on="job_id", how="left")

    numeric_cols = [
        "Contract Value", "Committed Material Cost", "Material Cost", "Material Qty Required", "Material Qty Received", "Material Lines",
        "Wage Hours", "Labour Cost", "Timesheet Hours", "Timesheet Lines", "Budget Labour Hours",
        "Budget Labour Cost", "Budget Materials", "Budget Access", "Budget Subcontractors", "Budget Sundries",
        "Target GP %", "Variation Value", "Approved Variation Value", "Variation Count", "Claimed Amount",
        "Paid Amount", "Claim Count"
    ]
    for col in numeric_cols:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0)

    for col in ["Budget Locked"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    df["Adjusted Contract Value"] = df["Contract Value"] + df["Approved Variation Value"]
    df["Total Budget Cost"] = df["Budget Labour Cost"] + df["Budget Materials"] + df["Budget Access"] + df["Budget Subcontractors"] + df["Budget Sundries"]
    df["Total Actual Cost"] = df["Material Cost"] + df["Labour Cost"]
    df["Gross Profit"] = df["Adjusted Contract Value"] - df["Total Actual Cost"]
    df["Gross Profit %"] = df.apply(lambda r: pb_percent(r["Gross Profit"], r["Adjusted Contract Value"]), axis=1)
    df["Cost to Date %"] = df.apply(lambda r: pb_percent(r["Total Actual Cost"], r["Adjusted Contract Value"]), axis=1)
    df["Remaining Budget"] = (df["Adjusted Contract Value"] - df["Total Actual Cost"]).clip(lower=0)
    df["Budget Variance"] = df["Total Budget Cost"] - df["Total Actual Cost"]
    df["Remaining Labour Hours"] = (df["Budget Labour Hours"] - df["Timesheet Hours"]).clip(lower=0)
    df["Working Days"] = df.apply(lambda r: pb_business_days(r["Start Date"], r["End Date"]), axis=1)
    df["Unclaimed Amount"] = (df["Adjusted Contract Value"] - df["Claimed Amount"]).clip(lower=0)
    df["Unpaid Claimed"] = (df["Claimed Amount"] - df["Paid Amount"]).clip(lower=0)

    def health(row):
        today = date.today()
        issues = []
        gp = pb_float(row["Gross Profit %"])
        cost_pct = pb_float(row["Cost to Date %"])
        target_gp = pb_float(row["Target GP %"], 35)
        end = pb_date(row["End Date"])

        if pb_float(row["Adjusted Contract Value"]) <= 0:
            issues.append("No contract value")
        if row["Budget Locked"] in [None, ""]:
            issues.append("Budget not locked")
        if gp < target_gp:
            issues.append("GP below target")
        if cost_pct > 85 and str(row["Status"]).lower() not in ["complete", "completed", "closed", "archived"]:
            issues.append("Cost high")
        if end and end < today and str(row["Status"]).lower() not in ["complete", "completed", "closed", "archived"]:
            issues.append("Past end date")
        if pb_float(row["Material Qty Required"]) > 0 and pb_float(row["Material Qty Received"]) < pb_float(row["Material Qty Required"]):
            issues.append("Materials short")

        if len(issues) >= 2:
            return "Red", "; ".join(issues)
        if len(issues) == 1:
            return "Orange", "; ".join(issues)
        return "Green", "On track"

    health_data = df.apply(health, axis=1)
    df["Health"] = [x[0] for x in health_data]
    df["Health Notes"] = [x[1] for x in health_data]
    return df


def pb_control_daily_dashboard(df):
    st.subheader("Daily Dashboard")

    today = date.today()
    week_end = today + timedelta(days=7)

    active = df[~df["Status"].astype(str).str.lower().isin(["complete", "completed", "closed", "archived"])]
    red = df[df["Health"] == "Red"]
    orange = df[df["Health"] == "Orange"]

    pending_timesheets = df_query("""
        SELECT COUNT(*) AS c
        FROM timesheet_entries
        WHERE COALESCE(status, 'Submitted') = 'Submitted'
    """)
    pending_count = int(pending_timesheets.iloc[0]["c"]) if not pending_timesheets.empty else 0

    overdue_claims = df_query("""
        SELECT COUNT(*) AS c,
               COALESCE(SUM(COALESCE(amount_ex_gst, 0)), 0) AS total
        FROM invoice_claims
        WHERE status <> 'Paid'
          AND due_date IS NOT NULL
          AND due_date <> ''
          AND due_date < ?
    """, (str(today),))
    overdue_count = int(overdue_claims.iloc[0]["c"]) if not overdue_claims.empty else 0
    overdue_total = pb_float(overdue_claims.iloc[0]["total"]) if not overdue_claims.empty else 0

    cols = st.columns(6)
    cols[0].metric("Active Jobs", len(active))
    cols[1].metric("Red Jobs", len(red))
    cols[2].metric("Orange Jobs", len(orange))
    cols[3].metric("Timesheets Pending", pending_count)
    cols[4].metric("Overdue Claims", overdue_count)
    cols[5].metric("Overdue $", f"${overdue_total:,.0f}")

    st.markdown("### Jobs Needing Attention")
    risk_cols = ["Job No", "Job Name", "Status", "Health", "Health Notes", "Adjusted Contract Value", "Total Actual Cost", "Gross Profit %", "End Date"]
    risks = df[df["Health"].isin(["Red", "Orange"])][risk_cols]
    if risks.empty:
        pb_success("No red or orange jobs found.")
    else:
        st.dataframe(risks, width="stretch", hide_index=True)

    st.markdown("### Jobs Starting / Finishing This Week")
    week_rows = []
    for _, row in df.iterrows():
        start = pb_date(row["Start Date"])
        end = pb_date(row["End Date"])
        if (start and today <= start <= week_end) or (end and today <= end <= week_end):
            week_rows.append(row)
    if week_rows:
        week_df = pd.DataFrame(week_rows)
        st.dataframe(week_df[["Job No", "Job Name", "Status", "Leading Hand", "Start Date", "End Date", "Health"]], width="stretch", hide_index=True)
    else:
        st.info("No jobs starting or finishing in the next 7 days.")


def pb_control_job_health(df):
    st.subheader("Job Health Score")
    st.caption("Green = on track, Orange = needs attention, Red = margin/schedule/data risk.")

    status_filter = st.selectbox("Status Filter", ["All"] + sorted([str(x) for x in df["Status"].fillna("").unique() if str(x).strip()]), key="health_status_filter")
    filtered = df.copy()
    if status_filter != "All":
        filtered = filtered[filtered["Status"].astype(str) == status_filter]

    health_filter = st.multiselect("Health Filter", ["Green", "Orange", "Red"], default=["Green", "Orange", "Red"], key="health_filter")
    filtered = filtered[filtered["Health"].isin(health_filter)]

    cols = ["Job No", "Job Name", "Builder / Client", "Status", "Health", "Health Notes", "Adjusted Contract Value", "Total Actual Cost", "Gross Profit %", "Cost to Date %", "Remaining Labour Hours", "End Date"]
    st.dataframe(filtered[cols], width="stretch", hide_index=True)


def pb_control_budget_lock(df):
    st.subheader("Job Budget Lock-In")
    st.caption("Lock in accepted quote budgets so actual labour/materials can be compared against the allowed budget.")

    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first.")
        return

    selected_job = st.selectbox("Job", list(job_options.keys()), key="budget_lock_job")
    job_id = job_options[selected_job]

    existing = df_query("SELECT * FROM job_budgets WHERE job_id = ?", (job_id,))
    current = existing.iloc[0].to_dict() if not existing.empty else {}

    with st.form("job_budget_form"):
        c1, c2, c3 = st.columns(3)
        quoted_labour_hours = c1.number_input("Quoted Labour Hours", min_value=0.0, value=pb_float(current.get("quoted_labour_hours", 0)), step=1.0)
        quoted_labour_cost = c2.number_input("Quoted Labour Cost", min_value=0.0, value=pb_float(current.get("quoted_labour_cost", 0)), step=100.0)
        quoted_materials = c3.number_input("Quoted Materials", min_value=0.0, value=pb_float(current.get("quoted_materials", 0)), step=100.0)

        c4, c5, c6 = st.columns(3)
        quoted_access = c4.number_input("Access / Equipment Allowance", min_value=0.0, value=pb_float(current.get("quoted_access_equipment", 0)), step=100.0)
        quoted_subbies = c5.number_input("Subcontractor Allowance", min_value=0.0, value=pb_float(current.get("quoted_subcontractors", 0)), step=100.0)
        quoted_sundries = c6.number_input("Sundries / Consumables", min_value=0.0, value=pb_float(current.get("quoted_sundries", 0)), step=50.0)

        target_gp = st.number_input("Target GP %", min_value=0.0, max_value=100.0, value=pb_float(current.get("target_gp_percent", 35), 35), step=1.0)
        notes = st.text_area("Budget Notes", value=str(current.get("notes", "") or ""))
        submitted = st.form_submit_button("Save / Lock Job Budget")

    if submitted:
        if existing.empty:
            execute("""
                INSERT INTO job_budgets
                (job_id, quoted_labour_hours, quoted_labour_cost, quoted_materials, quoted_access_equipment,
                 quoted_subcontractors, quoted_sundries, target_gp_percent, locked_at, locked_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, quoted_labour_hours, quoted_labour_cost, quoted_materials, quoted_access, quoted_subbies, quoted_sundries, target_gp, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), current_username(), notes))
        else:
            execute("""
                UPDATE job_budgets
                SET quoted_labour_hours = ?, quoted_labour_cost = ?, quoted_materials = ?, quoted_access_equipment = ?,
                    quoted_subcontractors = ?, quoted_sundries = ?, target_gp_percent = ?, locked_at = ?, locked_by = ?, notes = ?
                WHERE job_id = ?
            """, (quoted_labour_hours, quoted_labour_cost, quoted_materials, quoted_access, quoted_subbies, quoted_sundries, target_gp, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), current_username(), notes, job_id))
        pb_success("Job budget saved.")
        refresh()

    budget_df = df_query("""
        SELECT j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               b.quoted_labour_hours AS 'Labour Hours',
               b.quoted_labour_cost AS 'Labour Cost',
               b.quoted_materials AS 'Materials',
               b.quoted_access_equipment AS 'Access',
               b.quoted_subcontractors AS 'Subcontractors',
               b.quoted_sundries AS 'Sundries',
               b.target_gp_percent AS 'Target GP %',
               b.locked_at AS 'Locked At',
               b.locked_by AS 'Locked By'
        FROM job_budgets b
        JOIN jobs j ON j.id = b.job_id
        ORDER BY j.job_no
    """)
    st.markdown("### Locked Budgets")
    st.dataframe(budget_df, width="stretch", hide_index=True)


def pb_control_variations():
    st.subheader("Variations Register")
    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first.")
        return

    with st.expander("Add Variation", expanded=True):
        selected_job = st.selectbox("Job", list(job_options.keys()), key="variation_job")
        job_id = job_options[selected_job]
        with st.form("variation_form"):
            c1, c2, c3 = st.columns(3)
            variation_no = c1.text_input("Variation No", value=pb_next_variation_no(job_id))
            amount = c2.number_input("Amount Ex GST", min_value=0.0, step=100.0)
            status = c3.selectbox("Status", ["Draft", "Sent", "Approved", "Rejected"])
            description = st.text_area("Description")
            reason = st.text_area("Reason")
            c4, c5, c6 = st.columns(3)
            sent_date = c4.text_input("Sent Date", value=str(date.today()) if status in ["Sent", "Approved"] else "")
            approved_date = c5.text_input("Approved Date", value=str(date.today()) if status == "Approved" else "")
            approved_by = c6.text_input("Approved By")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Variation")
        if submitted:
            execute("""
                INSERT INTO job_variations
                (job_id, variation_no, description, reason, amount_ex_gst, status, sent_date, approved_date, approved_by, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, variation_no, description, reason, amount, status, sent_date, approved_date, approved_by, notes, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            pb_success("Variation saved.")
            refresh()

    variations = df_query("""
        SELECT v.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               v.variation_no AS 'Variation',
               v.description AS 'Description',
               v.amount_ex_gst AS 'Amount Ex GST',
               v.status AS 'Status',
               v.sent_date AS 'Sent',
               v.approved_date AS 'Approved',
               v.approved_by AS 'Approved By'
        FROM job_variations v
        JOIN jobs j ON j.id = v.job_id
        ORDER BY v.id DESC
    """)
    st.dataframe(variations, width="stretch", hide_index=True)


def pb_control_invoice_claims():
    st.subheader("Invoice / Claim Tracker")
    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first.")
        return

    with st.expander("Add Invoice / Claim", expanded=True):
        selected_job = st.selectbox("Job", list(job_options.keys()), key="claim_job")
        job_id = job_options[selected_job]
        with st.form("claim_form"):
            c1, c2, c3 = st.columns(3)
            claim_no = c1.text_input("Claim / Invoice No", value=pb_next_claim_no(job_id))
            amount = c2.number_input("Amount Ex GST", min_value=0.0, step=100.0)
            status = c3.selectbox("Status", ["Draft", "Sent", "Approved", "Paid", "Overdue", "Void"])
            description = st.text_area("Description")
            c4, c5, c6 = st.columns(3)
            invoice_date = c4.text_input("Invoice Date", value=str(date.today()))
            due_date = c5.text_input("Due Date")
            paid_date = c6.text_input("Paid Date")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Claim")
        if submitted:
            execute("""
                INSERT INTO invoice_claims
                (job_id, claim_no, description, amount_ex_gst, invoice_date, due_date, paid_date, status, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, claim_no, description, amount, invoice_date, due_date, paid_date, status, notes, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            pb_success("Invoice / claim saved.")
            refresh()

    claims = df_query("""
        SELECT c.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               c.claim_no AS 'Claim',
               c.description AS 'Description',
               c.amount_ex_gst AS 'Amount Ex GST',
               c.invoice_date AS 'Invoice Date',
               c.due_date AS 'Due Date',
               c.paid_date AS 'Paid Date',
               c.status AS 'Status'
        FROM invoice_claims c
        JOIN jobs j ON j.id = c.job_id
        ORDER BY c.id DESC
    """)
    st.dataframe(claims, width="stretch", hide_index=True)


def pb_control_staff_schedule():
    st.subheader("Staff Scheduling Board")
    job_options = get_job_options()
    employee_options = get_employee_options(active_only=True)
    if not job_options or not employee_options:
        st.info("Create jobs and active employees first.")
        return

    with st.expander("Add Staff Schedule Entry", expanded=True):
        with st.form("staff_schedule_form"):
            c1, c2 = st.columns(2)
            selected_job = c1.selectbox("Job", list(job_options.keys()), key="schedule_job")
            selected_employee = c2.selectbox("Employee", list(employee_options.keys()), key="schedule_employee")
            c3, c4, c5 = st.columns(3)
            schedule_date = c3.text_input("Date", value=str(date.today()))
            start_time = c4.text_input("Start Time", value="07:00")
            finish_time = c5.text_input("Finish Time", value="15:00")
            site_role = st.selectbox("Site Role", ["Painter", "Leading Hand", "Supervisor", "Apprentice", "Subcontractor", "Other"])
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Schedule Entry")
        if submitted:
            execute("""
                INSERT INTO staff_schedule
                (job_id, employee_id, schedule_date, start_time, finish_time, site_role, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_options[selected_job], employee_options[selected_employee], schedule_date, start_time, finish_time, site_role, notes, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            pb_success("Schedule entry saved.")
            refresh()

    c1, c2 = st.columns(2)
    start_filter = c1.text_input("From Date", value=str(date.today()))
    end_filter = c2.text_input("To Date", value=str(date.today() + timedelta(days=7)))
    schedule = df_query("""
        SELECT s.id AS 'ID',
               s.schedule_date AS 'Date',
               e.name AS 'Employee',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               s.start_time AS 'Start',
               s.finish_time AS 'Finish',
               s.site_role AS 'Role',
               s.notes AS 'Notes'
        FROM staff_schedule s
        JOIN jobs j ON j.id = s.job_id
        JOIN employees e ON e.id = s.employee_id
        WHERE s.schedule_date >= ? AND s.schedule_date <= ?
        ORDER BY s.schedule_date, e.name
    """, (start_filter, end_filter))
    st.dataframe(schedule, width="stretch", hide_index=True)



def pb_control_timesheet_approval():
    st.subheader("Timesheet Approval")
    st.caption(
        "Filter timesheets by status, tick multiple lines, then approve, reject, "
        "mark paid or delete them together."
    )

    timesheets = df_query("""
        SELECT t.id,
               t.work_date AS 'Date',
               e.name AS 'Employee',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               t.start_time AS 'Start',
               t.finish_time AS 'Finish',
               t.break_minutes AS 'Break',
               t.total_hours AS 'Hours',
               t.work_type AS 'Work Type',
               COALESCE(t.status, 'Submitted') AS 'Status',
               t.notes AS 'Notes'
        FROM timesheet_entries t
        JOIN jobs j ON j.id = t.job_id
        JOIN employees e ON e.id = t.employee_id
        ORDER BY t.work_date DESC, t.id DESC
        LIMIT 500
    """)

    if timesheets.empty:
        st.info("No timesheets have been submitted.")
        return

    selected_status = timesheet_status_filter(
        "Show Timesheets With Status",
        "control_timesheet_status_filter",
        default="Submitted",
    )
    filtered = filter_timesheets_by_status(timesheets, selected_status)
    st.caption(
        f"Showing {len(filtered)} of {len(timesheets)} timesheet(s): {selected_status}."
    )
    render_timesheet_bulk_actions(
        filtered,
        key_prefix=f"control_timesheets_{selected_status.lower().replace(' ', '_')}",
        empty_message=f"No {selected_status.lower()} timesheets found.",
    )

def pb_control_ai_job_review(df):
    st.subheader("AI Job Review")
    st.caption("Uses your JobHub AI/local Ollama setup to review margin, labour, material and schedule risk.")

    job_options = {f"{r['Job No']} - {r['Job Name']}": int(r["job_id"]) for _, r in df.iterrows()}
    if not job_options:
        st.info("Create a job first.")
        return

    selected = st.selectbox("Select Job", list(job_options.keys()), key="control_ai_review_job")
    job_id = job_options[selected]
    row = df[df["job_id"].astype(int) == int(job_id)].iloc[0]

    context = "\n".join([f"{col}: {row[col]}" for col in df.columns if col != "job_id"])
    prompt = (
        "Review this painting job for Premier Brushworks. "
        "Give a practical job risk review with: margin risk, labour risk, materials risk, schedule risk, "
        "missing information, and the next 5 actions for Nick/Bryce.\n\n"
        + context
    )

    if st.checkbox("Show AI context", value=False, key="show_control_ai_context"):
        st.text_area("Context", value=context, height=300)

    if st.button("Review This Job With AI"):
        with st.spinner("AI reviewing job..."):
            answer, error = jobhub_ai_answer(prompt, context)
        if error:
            pb_error(error)
        else:
            st.markdown("### AI Review")
            st.write(answer)


def control_centre_page():
    st.header("Premier Brushworks Control Centre")
    st.caption("Daily dashboard, job lookup, job health, budget lock-in, variations, claims, scheduling, timesheet approval and AI job review.")

    df = pb_job_cost_frame()
    if df.empty:
        st.info("Create your first job to start using the Control Centre.")
        return

    section = st.radio(
        "Control Centre Section",
        [
            "Daily Dashboard",
            "Job Health Score",
            "Job Budget Lock-In",
            "Variations Register",
            "Invoice / Claim Tracker",
            "Staff Scheduling Board",
            "Timesheet Approval",
            "Job Lookup / Links",
            "AI Job Review",
            "Export Control Centre"
        ],
        horizontal=False,
        key="control_centre_section"
    )

    if section == "Daily Dashboard":
        pb_control_daily_dashboard(df)
    elif section == "Job Health Score":
        pb_control_job_health(df)
    elif section == "Job Budget Lock-In":
        pb_control_budget_lock(df)
    elif section == "Variations Register":
        pb_control_variations()
    elif section == "Invoice / Claim Tracker":
        pb_control_invoice_claims()
    elif section == "Staff Scheduling Board":
        pb_control_staff_schedule()
    elif section == "Timesheet Approval":
        pb_control_timesheet_approval()
    elif section == "Job Lookup / Links":
        job_lookup_links_page()
    elif section == "AI Job Review":
        pb_control_ai_job_review(df)
    else:
        st.subheader("Export Control Centre")
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.drop(columns=["job_id"], errors="ignore").to_excel(writer, index=False, sheet_name="Job Health")
            df_query("""
                SELECT v.*, j.job_no, j.job_name
                FROM job_variations v
                JOIN jobs j ON j.id = v.job_id
                ORDER BY v.id DESC
            """).to_excel(writer, index=False, sheet_name="Variations")
            df_query("""
                SELECT c.*, j.job_no, j.job_name
                FROM invoice_claims c
                JOIN jobs j ON j.id = c.job_id
                ORDER BY c.id DESC
            """).to_excel(writer, index=False, sheet_name="Claims")
            df_query("""
                SELECT s.*, j.job_no, j.job_name, e.name AS employee
                FROM staff_schedule s
                JOIN jobs j ON j.id = s.job_id
                JOIN employees e ON e.id = s.employee_id
                ORDER BY s.schedule_date DESC
            """).to_excel(writer, index=False, sheet_name="Staff Schedule")
            for ws in writer.book.worksheets:
                for column_cells in ws.columns:
                    max_len = 0
                    col_letter = column_cells[0].column_letter
                    for cell in column_cells:
                        value = "" if cell.value is None else str(cell.value)
                        max_len = max(max_len, len(value))
                    ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 45)
        output.seek(0)
        st.download_button(
            "Download Control Centre Excel",
            data=output.getvalue(),
            file_name="PB_JobHub_Control_Centre.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def current_username():
    user = get_current_user() or {}
    return str(user.get("username", "unknown"))




# =============================
# LINKED JOB LOOKUP / DRILL-DOWN
# =============================
def safe_df_query(sql, params=()):
    try:
        return df_query(sql, params)
    except Exception:
        return pd.DataFrame()


JOB_STATUS_OPTIONS = [
    "Not Started", "Quoted", "Booked", "Active", "On Hold",
    "Completed", "Invoiced", "Paid", "Archived",
]


def render_selectable_job_details(job_details, job_id):
    """Let a user select the displayed job row and update all job details."""
    event = st.dataframe(
        job_details,
        width="stretch",
        hide_index=True,
        key=f"selectable_job_details_{int(job_id)}",
        on_select="rerun",
        selection_mode="single-row",
    )
    selected_rows = list(getattr(getattr(event, "selection", None), "rows", []) or [])
    if not selected_rows:
        st.caption("Select the job row to edit its details.")
        return

    row = job_details.iloc[selected_rows[0]]
    current_status = str(row.get("Status", "") or "Not Started")
    status_options = list(JOB_STATUS_OPTIONS)
    if current_status not in status_options:
        status_options.append(current_status)

    builder_options = get_builder_options()
    builder_names = [""] + list(builder_options.keys())
    current_builder = str(row.get("Builder / Client", "") or "")
    if current_builder and current_builder not in builder_names:
        builder_names.append(current_builder)
    employee_options = get_employee_options(active_only=True)
    employee_names = [""] + list(employee_options.keys())
    current_leading_hand = str(row.get("Leading Hand", "") or "")
    if current_leading_hand and current_leading_hand not in employee_names:
        employee_names.append(current_leading_hand)

    with st.form(f"quick_job_details_form_{int(job_id)}"):
        st.markdown("#### Edit selected job")
        c1, c2 = st.columns(2)
        new_job_no = c1.text_input("Job Number", value=str(row.get("Job No", "") or ""))
        new_job_name = c2.text_input("Job Name", value=str(row.get("Job Name", "") or ""))
        new_builder = st.selectbox(
            "Builder / Client",
            builder_names,
            index=builder_names.index(current_builder) if current_builder in builder_names else 0,
        )
        new_site_address = st.text_input("Site Address", value=str(row.get("Site Address", "") or ""))

        c3, c4, c5 = st.columns(3)
        new_status = c3.selectbox(
            "Status",
            status_options,
            index=status_options.index(current_status),
        )
        new_leading_hand = c4.selectbox(
            "Leading Hand",
            employee_names,
            index=employee_names.index(current_leading_hand) if current_leading_hand in employee_names else 0,
        )
        new_contract_value = c5.number_input(
            "Contract Value Ex GST",
            min_value=0.0,
            step=100.0,
            value=float(row.get("Contract Value Ex GST", 0) or 0),
        )

        c6, c7 = st.columns(2)
        new_start_date = c6.date_input(
            "Start Date",
            value=pb_date(row.get("Start Date")),
            format="DD/MM/YYYY",
        )
        new_end_date = c7.date_input(
            "End Date",
            value=pb_date(row.get("End Date")),
            format="DD/MM/YYYY",
        )

        st.markdown("##### Builder contact details")
        b1, b2 = st.columns(2)
        new_contact = b1.text_input("Contact", value=str(row.get("Contact", "") or ""))
        new_phone = b2.text_input("Phone", value=str(row.get("Phone", "") or ""))
        b3, b4 = st.columns(2)
        new_email = b3.text_input("Email", value=str(row.get("Email", "") or ""))
        new_terms = b4.text_input("Terms", value=str(row.get("Terms", "") or ""))
        new_notes = st.text_area("Notes", value=str(row.get("Notes", "") or ""))
        save_details = st.form_submit_button("Save job details", type="primary", use_container_width=True)

    if save_details:
        if not new_job_no.strip() or not new_job_name.strip():
            pb_error("Job Number and Job Name are required.")
            return
        if new_start_date and new_end_date and new_end_date < new_start_date:
            pb_error("End Date cannot be before Start Date.")
            return
        builder_id = builder_options.get(new_builder) if new_builder else None
        try:
            execute(
                """
                UPDATE jobs
                SET job_no = ?, job_name = ?, builder_client_id = ?, site_address = ?,
                    status = ?, leading_hand = ?, start_date = ?, end_date = ?,
                    contract_value = ?, notes = ?,
                    row_version = COALESCE(row_version, 1) + 1
                WHERE id = ?
                """,
                (
                    new_job_no.strip(), new_job_name.strip(), builder_id, new_site_address,
                    new_status, new_leading_hand,
                    new_start_date.isoformat() if new_start_date else "",
                    new_end_date.isoformat() if new_end_date else "",
                    new_contract_value, new_notes, int(job_id),
                ),
            )
            if builder_id:
                execute(
                    """
                    UPDATE builders_clients
                    SET contact_name = ?, phone = ?, email = ?, terms = ?
                    WHERE id = ?
                    """,
                    (new_contact, new_phone, new_email, new_terms, int(builder_id)),
                )
            pb_success(f"Updated all details for job {new_job_no.strip()}.")
            pb_rerun()
        except Exception:
            pb_error("The job could not be updated. Check that the Job Number is unique and try again.")


def go_to_linked_job_view(job_id=None, builder_id=None, mode=None):
    if job_id is not None:
        st.session_state["linked_view_selected_job_id"] = int(job_id)
    if builder_id is not None:
        st.session_state["linked_view_selected_builder_id"] = int(builder_id)
    if mode:
        st.session_state["linked_view_mode"] = mode

    # Defer navigation until the next rerun. The router applies both the
    # main menu and Control Centre section before their widgets are created.
    st.session_state["go_to_menu"] = "Job Lookup / Links"
    pb_rerun()


def job_lookup_dataframe(include_archived=True):
    where_clause = "" if include_archived else "WHERE COALESCE(j.status, '') != 'Archived'"
    return df_query(f"""
        SELECT j.id AS job_id,
               j.job_no AS "Job No",
               j.job_name AS "Job Name",
               COALESCE(bc.id, 0) AS builder_id,
               COALESCE(bc.name, '') AS "Builder / Client",
               COALESCE(bc.contact_name, '') AS "Contact",
               COALESCE(bc.phone, '') AS "Phone",
               COALESCE(bc.email, '') AS "Email",
               COALESCE(j.site_address, '') AS "Site Address",
               COALESCE(j.status, '') AS "Status",
               COALESCE(j.leading_hand, '') AS "Leading Hand",
               COALESCE(j.start_date, '') AS "Start Date",
               COALESCE(j.end_date, '') AS "End Date",
               COALESCE(j.contract_value, 0) AS "Contract Value"
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        {where_clause}
        ORDER BY j.job_no
    """)


def make_job_label(row):
    return (
        f"{row['Job No']} - {row['Job Name']} | "
        f"{row['Builder / Client']} | {row['Site Address']} | {row['Status']}"
    )


def select_job_from_dataframe(jobs_df, label, key, default_job_id=None):
    if jobs_df.empty:
        st.info("No matching jobs found.")
        return None

    job_map = {make_job_label(row): int(row["job_id"]) for _, row in jobs_df.iterrows()}
    labels = list(job_map.keys())

    default_index = 0
    if default_job_id is not None:
        for i, item in enumerate(labels):
            if int(job_map[item]) == int(default_job_id):
                default_index = i
                break

    selected = st.selectbox(label, labels, index=default_index, key=key)
    return job_map[selected]


def display_job_table_with_open_button(jobs_df, table_label, key_prefix):
    if jobs_df.empty:
        st.info("No matching jobs found.")
        return None

    visible_df = jobs_df.drop(columns=["job_id", "builder_id"], errors="ignore")
    st.dataframe(visible_df, width="stretch", hide_index=True)

    selected_job_id = select_job_from_dataframe(
        jobs_df,
        f"Open one of these jobs - {table_label}",
        key=f"{key_prefix}_job_select"
    )

    if st.button("Open selected job and all linked info", key=f"{key_prefix}_open_job"):
        go_to_linked_job_view(job_id=selected_job_id, mode="Open Job")

    return selected_job_id


def render_job_linked_info(job_id, expanded=True):
    job_id = int(job_id)

    job_details = safe_df_query("""
        SELECT j.job_no AS "Job No",
               j.job_name AS "Job Name",
               COALESCE(bc.name, '') AS "Builder / Client",
               COALESCE(bc.contact_name, '') AS "Contact",
               COALESCE(bc.phone, '') AS "Phone",
               COALESCE(bc.email, '') AS "Email",
               COALESCE(bc.terms, '') AS "Terms",
               COALESCE(j.site_address, '') AS "Site Address",
               COALESCE(j.status, '') AS "Status",
               COALESCE(j.leading_hand, '') AS "Leading Hand",
               COALESCE(j.start_date, '') AS "Start Date",
               COALESCE(j.end_date, '') AS "End Date",
               COALESCE(j.contract_value, 0) AS "Contract Value Ex GST",
               COALESCE(j.notes, '') AS "Notes"
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.id = ?
    """, (job_id,))

    if job_details.empty:
        st.warning("Selected job could not be found.")
        return

    job_no = str(job_details.iloc[0]["Job No"])
    job_name = str(job_details.iloc[0]["Job Name"])
    st.markdown(f"## {job_no} - {job_name}")
    material_details = safe_df_query("""
    
        SELECT m.id AS "ID",
               COALESCE(NULLIF(m.custom_product_code, ''), p.product_code, '') AS "Product Code",
               COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS "Product Name",
               COALESCE(NULLIF(m.supplier, ''), NULLIF(m.custom_supplier, ''), p.supplier, '') AS "Supplier",
               COALESCE(NULLIF(m.custom_unit, ''), p.unit, '') AS "Unit",
               COALESCE(m.custom_unit_price, p.price_ex_gst, 0) AS "Unit Price Ex GST",
               COALESCE(NULLIF(m.custom_colour, ''), '') AS "Colour / Finish",
               m.qty_required AS "Qty Required",
               m.qty_received AS "Qty Received",
               ROUND(CAST((COALESCE(m.custom_unit_price, p.price_ex_gst, 0) * COALESCE(m.qty_required, 0)) AS numeric), 2) AS "Total Cost Ex GST",
               m.date_ordered AS "Date Ordered",
               m.supplier AS "Supplier Override",
               m.notes AS "Notes"
        FROM material_entries m
        LEFT JOIN products p ON p.id = m.product_id
        WHERE m.job_id = ?
        ORDER BY m.id DESC
    """, (job_id,))

    imported_materials = safe_df_query("""
        SELECT id AS "ID",
               product AS "Product",
               colour AS "Colour",
               qty_required AS "Qty Required",
               qty_loaded AS "Qty Loaded",
               source_file AS "Source File",
               imported_at AS "Imported At",
               notes AS "Notes"
        FROM imported_material_entries
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    wage_details = safe_df_query("""
        SELECT w.id AS "ID",
               e.name AS "Employee",
               w.work_date AS "Date",
               w.hours AS "Hours",
               e.base_hourly_rate AS "Base Rate",
               e.rate_plus_10 AS "Rate + 10%",
               COALESCE(w.hours, 0) *
               COALESCE(NULLIF(w.hourly_rate_snapshot, 0), e.rate_plus_10, e.base_hourly_rate, 0)
                   AS "Total Wage Cost",
               w.notes AS "Notes"
        FROM wage_entries w
        JOIN employees e ON e.id = w.employee_id
        WHERE w.job_id = ?
        ORDER BY w.work_date DESC, w.id DESC
    """, (job_id,))

    timesheet_details = safe_df_query("""
        SELECT t.id AS "ID",
               t.work_date AS "Date",
               e.name AS "Employee",
               t.start_time AS "Start",
               t.finish_time AS "Finish",
               t.break_minutes AS "Break Minutes",
               t.total_hours AS "Hours",
               t.work_type AS "Work Type",
               COALESCE(t.status, 'Submitted') AS "Status",
               t.notes AS "Notes"
        FROM timesheet_entries t
        JOIN employees e ON e.id = t.employee_id
        WHERE t.job_id = ?
        ORDER BY t.work_date DESC, t.id DESC
    """, (job_id,))

    estimate_summary = safe_df_query("""
        SELECT estimate_no AS "Estimate No",
               revision AS "Revision",
               estimate_date AS "Date",
               status AS "Status",
               labour_hours AS "Labour Hours",
               labour_rate AS "Labour Rate",
               material_allowance AS "Material Allowance",
               access_equipment_allowance AS "Access / Equipment",
               subcontractor_allowance AS "Subcontractor",
               sundries_allowance AS "Sundries",
               margin_percent AS "Margin %",
               contingency_percent AS "Contingency %",
               total_ex_gst AS "Total Ex GST",
               gst_amount AS "GST",
               total_inc_gst AS "Total Inc GST",
               notes AS "Notes"
        FROM estimate_working_sheets
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    estimate_lines = safe_df_query("""
        SELECT e.estimate_no AS "Estimate No",
               l.section AS "Section",
               l.item_description AS "Description",
               l.qty AS "Qty",
               l.unit AS "Unit",
               COALESCE(l.estimated_labour_hours, 0) AS "Estimated Labour Hours",
               COALESCE(l.material_allowance, 0) AS "Material Allowance",
               l.substrate AS "Substrate",
               l.work_location AS "Location",
               l.coating_system AS "Coating System",
               l.colour_finish AS "Colour / Finish",
               l.unit_rate AS "Unit Rate",
               l.line_total AS "Line Total",
               l.source_pack AS "Source Pack",
               l.notes AS "Notes"
        FROM estimate_line_items l
        JOIN estimate_working_sheets e ON e.id = l.estimate_id
        WHERE e.job_id = ?
        ORDER BY e.id DESC, l.id ASC
    """, (job_id,))

    equipment_master = safe_df_query("""
        SELECT id AS "ID",
               equipment_item AS "Equipment Item",
               category AS "Category",
               serial_no AS "Serial No",
               date_out AS "Date Out",
               date_in AS "Date In",
               condition_out AS "Condition Out",
               condition_in AS "Condition In",
               assigned_to AS "Assigned To",
               notes AS "Notes"
        FROM equipment_entries
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    equipment_detail = safe_df_query("""
        SELECT r.id AS "ID",
               i.category AS "Category",
               i.item_name AS "Item",
               r.qty_required AS "Qty Required",
               r.qty_taken AS "Qty Taken",
               r.qty_returned AS "Qty Returned",
               CASE WHEN r.is_required = 1 THEN 'Yes' ELSE '' END AS "Required",
               CASE WHEN r.is_packed = 1 THEN 'Yes' ELSE '' END AS "Packed",
               CASE WHEN r.is_returned = 1 THEN 'Yes' ELSE '' END AS "Returned",
               r.date_out AS "Date Out",
               r.date_in AS "Date In",
               r.taken_by AS "Taken By",
               r.returned_by AS "Returned By",
               r.condition_out AS "Condition Out",
               r.condition_in AS "Condition In",
               r.notes AS "Notes"
        FROM equipment_checklist_records r
        JOIN equipment_checklist_items i ON i.id = r.checklist_item_id
        WHERE r.job_id = ?
        ORDER BY i.category, i.item_name
    """, (job_id,))

    budget_df = safe_df_query("""
        SELECT quoted_labour_hours AS "Quoted Labour Hours",
               quoted_labour_cost AS "Quoted Labour Cost",
               quoted_materials AS "Quoted Materials",
               quoted_access_equipment AS "Access / Equipment",
               quoted_subcontractors AS "Subcontractors",
               quoted_sundries AS "Sundries",
               target_gp_percent AS "Target GP %",
               locked_at AS "Locked At",
               locked_by AS "Locked By",
               notes AS "Notes"
        FROM job_budgets
        WHERE job_id = ?
    """, (job_id,))

    variations_df = safe_df_query("""
        SELECT variation_no AS "Variation No",
               description AS "Description",
               reason AS "Reason",
               amount_ex_gst AS "Amount Ex GST",
               status AS "Status",
               sent_date AS "Sent Date",
               approved_date AS "Approved Date",
               approved_by AS "Approved By",
               notes AS "Notes"
        FROM job_variations
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    claims_df = safe_df_query("""
        SELECT claim_no AS "Claim / Invoice No",
               description AS "Description",
               amount_ex_gst AS "Amount Ex GST",
               invoice_date AS "Invoice Date",
               due_date AS "Due Date",
               paid_date AS "Paid Date",
               status AS "Status",
               notes AS "Notes"
        FROM invoice_claims
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    schedule_df = safe_df_query("""
        SELECT s.schedule_date AS "Date",
               e.name AS "Employee",
               s.start_time AS "Start",
               s.finish_time AS "Finish",
               s.site_role AS "Role",
               s.notes AS "Notes"
        FROM staff_schedule s
        JOIN employees e ON e.id = s.employee_id
        WHERE s.job_id = ?
        ORDER BY s.schedule_date DESC, e.name
    """, (job_id,))

    photos_meta = safe_df_query("""
        SELECT id AS "Photo ID",
               photo_name AS "Photo Name",
               category AS "Category",
               caption AS "Caption",
               uploaded_by AS "Uploaded By",
               uploaded_at AS "Uploaded At",
               notes AS "Notes"
        FROM job_photos
        WHERE job_id = ?
        ORDER BY uploaded_at DESC, id DESC
    """, (job_id,))

    photos_full = safe_df_query("""
        SELECT id, photo_name, photo_type, photo_data, category, caption, uploaded_by, uploaded_at, notes
        FROM job_photos
        WHERE job_id = ?
        ORDER BY uploaded_at DESC, id DESC
    """, (job_id,))

    material_total = float(material_details["Total Cost Ex GST"].fillna(0).sum()) if not material_details.empty else 0.0
    wage_total = float(wage_details["Total Wage Cost"].fillna(0).sum()) if not wage_details.empty else 0.0
    timesheet_hours = float(timesheet_details["Hours"].fillna(0).sum()) if not timesheet_details.empty else 0.0
    contract_value = float(job_details.iloc[0]["Contract Value Ex GST"] or 0)
    approved_variations = float(variations_df[variations_df["Status"].astype(str).str.lower() == "approved"]["Amount Ex GST"].fillna(0).sum()) if not variations_df.empty else 0.0
    adjusted_contract = contract_value + approved_variations
    gross_position = adjusted_contract - material_total - wage_total

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Contract Ex GST", f"${contract_value:,.2f}")
    m2.metric("Approved Variations", f"${approved_variations:,.2f}")
    m3.metric("Materials", f"${material_total:,.2f}")
    m4.metric("Wages", f"${wage_total:,.2f}")
    m5.metric("Basic Position", f"${gross_position:,.2f}")

    tab_summary, tab_costs, tab_materials, tab_wages, tab_equipment, tab_control, tab_photos = st.tabs([
        "Summary",
        "Costs & Estimates",
        "Materials",
        "Wages & Timesheets",
        "Equipment",
        "Control / Claims",
        "Photos",
    ])

    with tab_summary:
        st.markdown("### Job Details")
        render_selectable_job_details(job_details, job_id)


        st.markdown("### Staff Schedule")
        if schedule_df.empty:
            st.info("No staff schedule entries saved for this job.")
        else:
            st.dataframe(schedule_df, width="stretch", hide_index=True)

    with tab_costs:
        c1, c2, c3 = st.columns(3)
        c1.metric("Timesheet Hours", f"{timesheet_hours:.2f}")
        c2.metric("Adjusted Contract", f"${adjusted_contract:,.2f}")
        c3.metric("Materials + Wages", f"${(material_total + wage_total):,.2f}")

        st.markdown("### Budget Lock-In")
        if budget_df.empty:
            st.info("No budget lock-in saved for this job.")
        else:
            st.dataframe(budget_df, width="stretch", hide_index=True)

        st.markdown("### Estimate Summary")
        if estimate_summary.empty:
            st.info("No estimate working sheets saved for this job.")
        else:
            st.dataframe(estimate_summary, width="stretch", hide_index=True)

        st.markdown("### Estimate Line Items")
        if estimate_lines.empty:
            st.info("No estimate line items saved for this job.")
        else:
            st.dataframe(estimate_lines, width="stretch", hide_index=True)

    with tab_materials:
        st.markdown("### Material Costs")
        if material_details.empty:
            st.info("No material cost entries saved for this job.")
        else:
            st.dataframe(material_details, width="stretch", hide_index=True)

        st.markdown("### Imported PDF Checklist Paint & Materials")
        if imported_materials.empty:
            st.info("No imported checklist material lines saved for this job.")
        else:
            st.dataframe(imported_materials, width="stretch", hide_index=True)

    with tab_wages:
        st.markdown("### Wage Entries")
        if wage_details.empty:
            st.info("No wage entries saved for this job.")
        else:
            st.dataframe(wage_details, width="stretch", hide_index=True)

        st.markdown("### Timesheets")
        if timesheet_details.empty:
            st.info("No timesheets saved for this job.")
        else:
            st.dataframe(
                timesheet_details,
                width="stretch",
                hide_index=True,
                key="job_dashboard_timesheet_details",
            )

    with tab_equipment:
        st.markdown("### Equipment Master Entries")
        if equipment_master.empty:
            st.info("No equipment master entries saved for this job.")
        else:
            st.dataframe(equipment_master, width="stretch", hide_index=True)

        st.markdown("### Equipment Checklist Detail")
        if equipment_detail.empty:
            st.info("No equipment checklist detail saved for this job.")
        else:
            st.dataframe(equipment_detail, width="stretch", hide_index=True)
 
    with tab_control:
        st.markdown("### Variations")

        if variations_df.empty:
            st.info("No variations saved for this job.")
        else:
            st.dataframe(variations_df, width="stretch", hide_index=True)

        st.markdown("### Claims / Invoices")

        if claims_df.empty:
            st.info("No claims or invoices saved for this job.")
        else:
            st.dataframe(claims_df, width="stretch", hide_index=True)

        st.divider()

        st.markdown("### Generate Job PDFs")

        if st.button(
            "Generate Day Labour Sheet",
            key=f"generate_day_labour_pdf_{job_id}",
        ):
            try:
                pdf_path = generate_day_labour_sheet_pdf(job_id)
                pb_success("Day Labour Sheet generated and attached to this job.")
                with open(pdf_path, "rb") as f:
                    st.download_button(
                        "Download Day Labour Sheet",
                        data=f,
                        file_name=os.path.basename(pdf_path),
                        mime="application/pdf",
                        key=f"download_day_labour_pdf_{job_id}",
                    )
            except Exception as e:
                pb_error(f"Could not generate Day Labour Sheet: {e}")

        pdf_col1, pdf_col2, pdf_col3 = st.columns(3)

        with pdf_col1:
            if st.button("Generate Paint & Materials Order PDF", key=f"generate_paint_order_{job_id}"):
                try:
                    pdf_path = generate_paint_order_pdf(job_id)
                    create_management_notifications(
                        "paint_order_form_generated",
                        "Paint order form generated",
                        f"{current_username()} generated the Paint & Materials Order PDF for this job.",
                        job_id=job_id,
                        entity_type="job_document",
                        entity_id=os.path.basename(pdf_path),
                    )
                    pb_success("Paint & Materials Order PDF generated and Nick/Bryce were notified.")

                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            "Download Paint & Materials Order PDF",
                            data=f,
                            file_name=os.path.basename(pdf_path),
                            mime="application/pdf",
                            key=f"download_paint_order_{job_id}",
                        )

                except Exception as e:
                    pb_error(f"Could not generate paint order PDF: {e}")

        with pdf_col2:
            if st.button("Generate Equipment Checklist PDF", key=f"generate_equipment_pdf_{job_id}"):
                try:
                    pdf_path = generate_equipment_checklist_pdf(job_id)
                    pb_success("Equipment Checklist PDF generated and attached to this job.")

                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            "Download Equipment Checklist PDF",
                            data=f,
                            file_name=os.path.basename(pdf_path),
                            mime="application/pdf",
                            key=f"download_equipment_pdf_{job_id}",
                        )

                except Exception as e:
                    pb_error(f"Could not generate equipment checklist PDF: {e}")

        with pdf_col3:
            st.caption("Variation form")
            variation_result_key = f"linked_variation_result_{job_id}"
            with st.form(f"linked_variation_form_generator_{job_id}"):
                variation_description = st.text_area("Variation Description", key=f"linked_variation_description_{job_id}")
                variation_reason = st.text_area("Reason / Details", key=f"linked_variation_reason_{job_id}")
                variation_notes = st.text_area("Notes", key=f"linked_variation_notes_{job_id}")
                generate_variation = st.form_submit_button("Generate Variation Form")

                if generate_variation:
                    try:
                        requested_by = current_username()
                        pdf_path, variation_no = generate_variation_form_pdf(
                            job_id,
                            requested_by=requested_by,
                            description=variation_description,
                            reason=variation_reason,
                            notes=variation_notes,
                        )
                        st.session_state[variation_result_key] = {
                            "pdf_path": pdf_path,
                            "variation_no": variation_no,
                        }
                    except Exception as e:
                        pb_error(f"Could not generate variation form PDF: {e}")

            if variation_result_key in st.session_state:
                variation_result = st.session_state[variation_result_key]
                pdf_path = variation_result["pdf_path"]
                variation_no = variation_result["variation_no"]
                pb_success(f"Variation Form {variation_no} generated and attached to this job.")

                with open(pdf_path, "rb") as f:
                    st.download_button(
                        "Download Variation Form PDF",
                        data=f,
                        file_name=os.path.basename(pdf_path),
                        mime="application/pdf",
                        key=f"download_variation_pdf_{job_id}_{variation_no}",
                    )

        st.divider()

        st.markdown("### Job Documents")

        documents_df = df_query("""
            SELECT id,
                   document_type AS 'Document Type',
                   file_name AS 'File Name',
                   file_path,
                   created_at AS 'Created At',
                   notes AS 'Notes',
                   COALESCE(mime_type, 'application/octet-stream') AS 'Mime Type'
            FROM job_documents
            WHERE job_id = ?
            ORDER BY id DESC
        """, (job_id,))

        if documents_df.empty:
            st.info("No documents attached to this job yet.")
        else:
            for _, doc in documents_df.iterrows():
                st.write(f"**{doc['Document Type']}** - {doc['File Name']}")
                st.caption(f"Created: {doc['Created At']}")

                file_path = str(doc["file_path"])

                try:
                    trusted_path = resolve_trusted_storage_file(file_path)
                except ValueError:
                    trusted_path = None
                if trusted_path and trusted_path.exists():
                    with open(trusted_path, "rb") as f:
                        st.download_button(
                            label=f"Download {doc['File Name']}",
                            data=f,
                            file_name=doc["File Name"],
                            mime=str(doc.get("Mime Type") or "application/octet-stream"),
                            key=f"download_job_doc_{doc['id']}",
                        )
                else:
                    st.warning("File path not found on disk.")
    
    with tab_photos:
        st.markdown("### Photo Register")
        if photos_meta.empty:
            st.info("No photos saved for this job.")
        else:
            st.dataframe(photos_meta, width="stretch", hide_index=True)

            with st.expander("View Photo Gallery"):
                for _, photo_row in photos_full.iterrows():
                    title_parts = [
                        str(photo_row["category"] or ""),
                        str(photo_row["caption"] or photo_row["photo_name"] or ""),
                    ]
                    st.markdown("#### " + " - ".join([p for p in title_parts if p]))
                    try:
                        st.image(photo_data_to_bytes(photo_row["photo_data"]), width="stretch")
                    except Exception:
                        st.warning("Could not display photo.")
                    st.caption(f"Uploaded: {photo_row['uploaded_at']} by {photo_row['uploaded_by']}")



def job_lookup_links_page():
    st.header("Job Lookup / Links")
    st.caption(
        "Filter by job status, then select a job number, job name, builder/client, "
        "address or leading hand and open all linked information."
    )

    filter_col1, filter_col2 = st.columns(2)
    include_archived = filter_col1.checkbox(
        "Include archived jobs",
        value=True,
        key="linked_include_archived",
    )
    jobs_df = job_lookup_dataframe(include_archived=include_archived)

    if jobs_df.empty:
        st.info("No jobs found.")
        return

    actual_statuses = [
        status
        for status in jobs_df["Status"].fillna("").astype(str).str.strip().unique().tolist()
        if status
    ]
    preferred_status_order = [
        "Not Started",
        "Quoted",
        "Booked",
        "On Hold",
        "Completed",
        "Invoiced",
        "Paid",
        "Archived",
    ]

    status_options = ["All Statuses"]
    for status in preferred_status_order[:3]:
        if status in actual_statuses:
            status_options.append(status)

    if any(status in actual_statuses for status in ("Started", "Active")):
        status_options.append("Started / Active")

    for status in preferred_status_order[3:]:
        if status in actual_statuses:
            status_options.append(status)

    known_statuses = set(preferred_status_order) | {"Started", "Active"}
    status_options.extend(
        sorted(status for status in actual_statuses if status not in known_statuses)
    )

    selected_job_status = filter_col2.selectbox(
        "Job Status",
        status_options,
        key="linked_job_status_filter",
    )

    if selected_job_status == "Started / Active":
        jobs_df = jobs_df[
            jobs_df["Status"].fillna("").astype(str).isin(["Started", "Active"])
        ].reset_index(drop=True)
    elif selected_job_status != "All Statuses":
        jobs_df = jobs_df[
            jobs_df["Status"].fillna("").astype(str) == selected_job_status
        ].reset_index(drop=True)

    st.caption(
        f"{len(jobs_df)} job(s) shown"
        + (
            f" with status {selected_job_status}."
            if selected_job_status != "All Statuses"
            else " across all statuses."
        )
    )

    if jobs_df.empty:
        st.info(f"No jobs found with status {selected_job_status}.")
        return

    mode_options = ["Open Job", "Jobs by Builder / Client", "Jobs by Leading Hand", "Search Anything"]
    requested_mode = st.session_state.get("linked_view_mode", "Open Job")
    mode_index = mode_options.index(requested_mode) if requested_mode in mode_options else 0
    mode = st.radio(
        "Lookup Mode",
        mode_options,
        index=mode_index,
        horizontal=True,
        key="linked_view_mode_radio",
    )
    st.session_state["linked_view_mode"] = mode

    selected_job_id = None

    if mode == "Open Job":
        default_job_id = st.session_state.get("linked_view_selected_job_id")
        selected_job_id = select_job_from_dataframe(
            jobs_df,
            "Select job number / job name / builder / address",
            key="linked_open_job_select",
            default_job_id=default_job_id,
        )

    elif mode == "Jobs by Builder / Client":
        builders_df = df_query("""
            SELECT id, name
            FROM builders_clients
            ORDER BY name
        """)
        if builders_df.empty:
            st.info("No builders or clients saved yet.")
            return

        builder_map = {str(row["name"]): int(row["id"]) for _, row in builders_df.iterrows()}
        builder_names = list(builder_map.keys())
        default_builder_id = st.session_state.get("linked_view_selected_builder_id")
        builder_index = 0
        if default_builder_id is not None:
            for i, name in enumerate(builder_names):
                if int(builder_map[name]) == int(default_builder_id):
                    builder_index = i
                    break

        selected_builder = st.selectbox(
            "Select builder/client",
            builder_names,
            index=builder_index,
            key="linked_builder_select",
        )
        builder_id = builder_map[selected_builder]
        st.session_state["linked_view_selected_builder_id"] = int(builder_id)

        builder_jobs = jobs_df[
            jobs_df["builder_id"].astype(int) == int(builder_id)
        ]
        st.markdown(f"### Jobs for {selected_builder}")
        selected_job_id = display_job_table_with_open_button(
            builder_jobs,
            selected_builder,
            "linked_builder_jobs",
        )

    elif mode == "Jobs by Leading Hand":
        leading_hands = sorted([
            value
            for value in jobs_df["Leading Hand"].dropna().astype(str).unique().tolist()
            if value.strip()
        ])
        if not leading_hands:
            st.info("No leading hands are saved against the jobs matching this status filter.")
            return

        selected_leading_hand = st.selectbox(
            "Select leading hand",
            leading_hands,
            key="linked_leading_hand",
        )
        lh_jobs = jobs_df[
            jobs_df["Leading Hand"].astype(str) == selected_leading_hand
        ]
        st.markdown(f"### Jobs for {selected_leading_hand}")
        selected_job_id = display_job_table_with_open_button(
            lh_jobs,
            selected_leading_hand,
            "linked_lh_jobs",
        )

    else:
        search_text = st.text_input(
            "Search job number, job name, builder/client, address, status or leading hand",
            key="linked_any_search",
        )
        filtered_jobs = jobs_df.copy()
        if search_text.strip():
            haystack = (
                filtered_jobs["Job No"].astype(str) + " " +
                filtered_jobs["Job Name"].astype(str) + " " +
                filtered_jobs["Builder / Client"].astype(str) + " " +
                filtered_jobs["Site Address"].astype(str) + " " +
                filtered_jobs["Status"].astype(str) + " " +
                filtered_jobs["Leading Hand"].astype(str)
            ).str.lower()
            filtered_jobs = filtered_jobs[
                haystack.str.contains(
                    search_text.strip().lower(),
                    na=False,
                    regex=False,
                )
            ]
        st.markdown("### Search Results")
        selected_job_id = display_job_table_with_open_button(
            filtered_jobs,
            "search results",
            "linked_search_jobs",
        )

    if selected_job_id:
        st.divider()
        st.session_state["linked_view_selected_job_id"] = int(selected_job_id)
        render_job_linked_info(selected_job_id)

# =============================
# JOB FOLDERS - MAIN JOB FILE VIEW
# =============================
def job_folders_page():
    st.header("Job Folders")
    st.caption("Open one job and access the full linked job file from one place: summary, plans/specs, colours/materials, timesheets, equipment, photos, documents, variations, forms and financials.")

    include_archived = st.checkbox("Include archived jobs", value=True, key="job_folder_include_archived")
    jobs_df = job_lookup_dataframe(include_archived=include_archived)

    if jobs_df.empty:
        st.info("No jobs found. Create a job first from Jobs > Add Job.")
        return

    quick_search = st.text_input(
        "Search job number, job name, builder/client, address, status or leading hand",
        key="job_folder_quick_search",
        placeholder="Start typing to filter jobs...",
    )

    filtered_jobs = jobs_df.copy()
    if quick_search.strip():
        haystack = (
            filtered_jobs["Job No"].astype(str) + " " +
            filtered_jobs["Job Name"].astype(str) + " " +
            filtered_jobs["Builder / Client"].astype(str) + " " +
            filtered_jobs["Site Address"].astype(str) + " " +
            filtered_jobs["Status"].astype(str) + " " +
            filtered_jobs["Leading Hand"].astype(str)
        ).str.lower()
        filtered_jobs = filtered_jobs[haystack.str.contains(quick_search.strip().lower(), na=False)]

    selected_job_id = select_job_from_dataframe(
        filtered_jobs,
        "Open Job Folder",
        key="job_folder_selected_job",
        default_job_id=st.session_state.get("linked_view_selected_job_id"),
    )

    if not selected_job_id:
        return

    st.session_state["linked_view_selected_job_id"] = int(selected_job_id)

    folder_action_cols = st.columns(4)
    if folder_action_cols[0].button("Go to Job Register", key="folder_go_job_register"):
        st.session_state["go_to_menu"] = "Jobs"
        pb_rerun()
    if folder_action_cols[1].button("Go to Materials", key="folder_go_materials"):
        st.session_state["go_to_menu"] = "Material Costs"
        pb_rerun()
    if folder_action_cols[2].button("Go to Timesheets", key="folder_go_timesheets"):
        st.session_state["go_to_menu"] = "Timesheets"
        pb_rerun()
    if folder_action_cols[3].button("Go to Photos", key="folder_go_photos"):
        st.session_state["go_to_menu"] = "Job Photos"
        pb_rerun()

    st.divider()
    render_job_linked_info(selected_job_id)


# =============================
# ENTERPRISE OPERATIONS CONTEXT
# =============================
def jobhub_enterprise_context():
    """Expose controlled existing JobHub services to the modular operations hub."""
    return {
        "connect": connect,
        "df_query": df_query,
        "execute": execute,
        "execute_many": execute_many,
        "record_audit_event": record_audit_event,
        "create_management_notifications": create_management_notifications,
        "get_current_user": get_current_user,
        "save_job_photo": save_job_photo,
        "pb_success": pb_success,
        "pb_error": pb_error,
        "pb_rerun": pb_rerun,
        "DATA_DIR": DATA_DIR,
        "JOB_FILES_DIR": JOB_FILES_DIR,
        "USE_POSTGRES": USE_POSTGRES,
    }


# =============================
# START APP
# =============================
@st.cache_resource(show_spinner=False)
def initialise_jobhub_runtime(database_url, data_dir):
    """Run idempotent schema and seed work once per server process.

    Previously this work ran after every button click and page change.  Caching
    it removes repeated migration/database overhead while still rerunning after
    each deployment or server restart.
    """
    init_db()
    apply_schema_migrations()
    ensure_enterprise_schema(connect)
    ensure_v2_schema(connect)
    ensure_v4_schema(connect)
    init_linked_schema()
    seed_data()
    seed_app_users()
    return True


try:
    initialise_jobhub_runtime(DATABASE_URL, DATA_DIR)
except Exception as exc:
    pb_error("JobHub could not complete its database startup checks.")
    st.code(str(exc))
    st.info(
        "Check DATABASE_URL, the Render persistent disk, and the latest deployment logs. "
        "No data-cleanup action was attempted."
    )
    st.stop()

require_login()

# Keep linked modules talking on every JobHub rerun. Only records explicitly
# linked to their source are updated; manual/fixed scheduler dates are preserved.
try:
    moved_schedule_rows = sync_linked_job_dates()
    sync_all_linked_progress(jobhub_enterprise_context())
    if moved_schedule_rows:
        pb_success(
            f"{moved_schedule_rows} linked schedule assignment(s) moved automatically "
            "after a job date changed."
        )
except Exception as exc:
    pb_error("JobHub could not refresh one or more linked records.")
    st.caption(str(exc))

# Lightweight restart-safe daily database export.  The module checks for an
# existing backup before doing any work, so normal page reruns remain fast.
try:
    ensure_daily_backup(jobhub_enterprise_context())
except Exception as exc:
    # Backup problems must never prevent staff from using JobHub.  They remain
    # visible in Operations Hub > System / Backups for administrator follow-up.
    pass

pb_sidebar_header()
pb_page_header(
    "Premier Brushworks JobHub",
    "Jobs, builders, clients, employees, scheduling, estimating and site operations in one place.",
    "Commercial painting operations",
)
logout_button()
render_sidebar_notifications()

role = current_role()

if role == "employee":
    main_menu_options = ["Field Mode", "Employee Portal"]
    management_menu_map = {}
    estimating_menu_map = {}
    site_operations_menu_map = {}
    ai_menu_map = {}
elif role == "manager":
    main_menu_options = [
        "Dashboard",
        "Control Centre",
        "Operations Hub",
        "Jobs",
        "Job Folders",
        "Estimating",
        "Site Operations",
        "Reports",
        "Management",
        "AI Assistant",
    ]
    management_menu_map = {
        "Builders & Clients": "Builders & Clients",
        "Employees": "Employees",
        "Products": "Products",
        "Equipment": "Equipment",
    }
    estimating_menu_map = {
        "Import / Create Job Pack": "Import Take-off Job Pack",
        "Estimate Working Sheet": "Estimate Working Sheet",
        "Job Progress Tracker": "Job Progress Tracker",
        "Estimating Rate Library": "Estimating Rate Library",
        "Job Costs / Forecasting": "Job Costs / Forecasting",
    }
    site_operations_menu_map = {
        "Staff Scheduler": "Staff Scheduler",
        "Painting Intelligence": "Painting Intelligence",
        "Material Costs": "Material Costs",
        "Wages": "Wages",
        "Timesheets": "Timesheets",
        "Job Photos": "Job Photos",
    }
    ai_menu_map = {
        "JobHub AI Assistant": "JobHub AI Assistant",
        "App Builder AI": "App Builder AI",
    }
else:
    main_menu_options = [
        "Dashboard",
        "Control Centre",
        "Operations Hub",
        "Jobs",
        "Job Folders",
        "Estimating",
        "Site Operations",
        "Reports",
        "Management",
        "AI Assistant",
    ]
    management_menu_map = {
        "User Accounts": "User Access",
        "Builders & Clients": "Builders & Clients",
        "Employees": "Employees",
        "Products": "Products",
        "Equipment": "Equipment",
    }
    estimating_menu_map = {
        "Import / Create Job Pack": "Import Take-off Job Pack",
        "Estimate Working Sheet": "Estimate Working Sheet",
        "Job Progress Tracker": "Job Progress Tracker",
        "Estimating Rate Library": "Estimating Rate Library",
        "Job Costs / Forecasting": "Job Costs / Forecasting",
    }
    site_operations_menu_map = {
        "Staff Scheduler": "Staff Scheduler",
        "Painting Intelligence": "Painting Intelligence",
        "Material Costs": "Material Costs",
        "Wages": "Wages",
        "Timesheets": "Timesheets",
        "Job Photos": "Job Photos",
    }
    ai_menu_map = {
        "JobHub AI Assistant": "JobHub AI Assistant",
        "App Builder AI": "App Builder AI",
    }

reports_menu_map = {"Reports / Export": "Reports / Export"}

sidebar_reset_target = "Dashboard" if "Dashboard" in main_menu_options else main_menu_options[0]
if st.sidebar.button(
    "↥ Reset menu / return to start",
    key="sidebar_reset_navigation",
    width="stretch",
):
    for navigation_key in (
        "main_menu",
        "management_menu",
        "estimating_menu",
        "site_operations_menu",
        "ai_menu",
        "control_centre_section",
        "go_to_menu",
        "_pb_sidebar_navigation_signature",
    ):
        st.session_state.pop(navigation_key, None)
    st.session_state["go_to_menu"] = sidebar_reset_target
    pb_rerun()

hidden_route_options = (
    list(management_menu_map.values()) +
    list(estimating_menu_map.values()) +
    list(site_operations_menu_map.values()) +
    list(ai_menu_map.values()) +
    list(reports_menu_map.values()) +
    ["Job Lookup / Links"]
)
allowed_menu = main_menu_options + hidden_route_options

requested_menu = st.session_state.pop("go_to_menu", None)
if requested_menu in main_menu_options:
    st.session_state["main_menu"] = requested_menu
elif requested_menu in hidden_route_options:
    if requested_menu == "Job Lookup / Links":
        st.session_state["main_menu"] = "Control Centre"
        st.session_state["control_centre_section"] = "Job Lookup / Links"
    elif requested_menu in management_menu_map.values():
        st.session_state["main_menu"] = "Management"
        for label, target in management_menu_map.items():
            if target == requested_menu:
                st.session_state["management_menu"] = label
                break
    elif requested_menu in estimating_menu_map.values():
        st.session_state["main_menu"] = "Estimating"
        for label, target in estimating_menu_map.items():
            if target == requested_menu:
                st.session_state["estimating_menu"] = label
                break
    elif requested_menu in site_operations_menu_map.values():
        st.session_state["main_menu"] = "Site Operations"
        for label, target in site_operations_menu_map.items():
            if target == requested_menu:
                st.session_state["site_operations_menu"] = label
                break
    elif requested_menu in ai_menu_map.values():
        st.session_state["main_menu"] = "AI Assistant"
        for label, target in ai_menu_map.items():
            if target == requested_menu:
                st.session_state["ai_menu"] = label
                break
    elif requested_menu == "Reports / Export":
        st.session_state["main_menu"] = "Reports"

if st.session_state.get("main_menu") not in main_menu_options:
    st.session_state["main_menu"] = main_menu_options[0]

main_menu_choice = st.sidebar.radio("Menu", main_menu_options, key="main_menu")

if main_menu_choice == "Management":
    st.sidebar.markdown("### Management")
    management_labels = list(management_menu_map.keys())
    if st.session_state.get("management_menu") not in management_labels:
        st.session_state["management_menu"] = management_labels[0] if management_labels else ""
    # PB_JOBHUB_SIDEBAR_MENU_FIX: show every option instead of using a clipped dropdown.
    selected_management_label = st.sidebar.radio(
        "Management Section",
        management_labels,
        key="management_menu",
        label_visibility="collapsed",
    )
    menu = management_menu_map.get(selected_management_label, selected_management_label)
elif main_menu_choice == "Estimating":
    st.sidebar.markdown("### Estimating")
    estimating_labels = list(estimating_menu_map.keys())
    if st.session_state.get("estimating_menu") not in estimating_labels:
        st.session_state["estimating_menu"] = estimating_labels[0] if estimating_labels else ""
    # PB_JOBHUB_SIDEBAR_MENU_FIX: show every option instead of using a clipped dropdown.
    selected_estimating_label = st.sidebar.radio(
        "Estimating Section",
        estimating_labels,
        key="estimating_menu",
        label_visibility="collapsed",
    )
    menu = estimating_menu_map.get(selected_estimating_label, selected_estimating_label)
elif main_menu_choice == "Site Operations":
    st.sidebar.markdown("### Site Operations")
    site_labels = list(site_operations_menu_map.keys())
    if st.session_state.get("site_operations_menu") not in site_labels:
        st.session_state["site_operations_menu"] = site_labels[0] if site_labels else ""
    # PB_JOBHUB_SIDEBAR_MENU_FIX: show every option instead of using a clipped dropdown.
    selected_site_label = st.sidebar.radio(
        "Site Section",
        site_labels,
        key="site_operations_menu",
        label_visibility="collapsed",
    )
    menu = site_operations_menu_map.get(selected_site_label, selected_site_label)
elif main_menu_choice == "AI Assistant":
    st.sidebar.markdown("### AI Assistant")
    ai_labels = list(ai_menu_map.keys())
    if st.session_state.get("ai_menu") not in ai_labels:
        st.session_state["ai_menu"] = ai_labels[0] if ai_labels else ""
    # PB_JOBHUB_SIDEBAR_MENU_FIX: show every option instead of using a clipped dropdown.
    selected_ai_label = st.sidebar.radio(
        "AI Section",
        ai_labels,
        key="ai_menu",
        label_visibility="collapsed",
    )
    menu = ai_menu_map.get(selected_ai_label, selected_ai_label)
elif main_menu_choice == "Reports":
    menu = "Reports / Export"
else:
    menu = main_menu_choice

navigation_signature = f"{main_menu_choice}|{menu}"
if st.session_state.get("_pb_sidebar_navigation_signature") != navigation_signature:
    st.session_state["_pb_sidebar_navigation_signature"] = navigation_signature
    pb_scroll_sidebar_to_top()


# =============================
# EMPLOYEE PORTAL / USER ACCESS
# =============================

# PB_JOBHUB_PRODUCT_PRICING_IMPORT_V1
PRODUCT_PRICING_REQUIRED_COLUMNS = [
    "Product Code",
    "Product Name",
    "Supplier",
    "Unit",
    "Price Ex GST",
]

PRODUCT_PRICING_COLUMN_ALIASES = {
    "product_code": {"product_code", "product code", "code", "material", "l&s", "l_s"},
    "product_name": {"product_name", "product name", "description", "material description"},
    "supplier": {"supplier", "brand", "manufacturer"},
    "unit": {"unit", "size", "pack size", "pack_size"},
    "price_ex_gst": {"price_ex_gst", "price ex gst", "price", "amount", "amount ex gst"},
    "notes": {"notes", "note", "source notes", "source"},
}


def _normalised_product_column_name(value):
    return re.sub(r"[^a-z0-9&]+", " ", str(value or "").strip().casefold()).strip()


def _product_import_float(value):
    text = str(value or "").replace("$", "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except Exception as exc:
        raise ValueError(f"Invalid price value: {value}") from exc


def normalise_product_pricing_dataframe(source_df):
    if source_df is None or source_df.empty:
        raise ValueError("The product pricing file is empty.")

    work = source_df.copy()
    original_columns = list(work.columns)
    normalised_columns = {_normalised_product_column_name(column): column for column in original_columns}

    resolved = {}
    for target, aliases in PRODUCT_PRICING_COLUMN_ALIASES.items():
        for alias in aliases:
            key = _normalised_product_column_name(alias)
            if key in normalised_columns:
                resolved[target] = normalised_columns[key]
                break

    missing = []
    for target, display_name in [
        ("product_code", "Product Code"),
        ("product_name", "Product Name"),
        ("supplier", "Supplier"),
        ("unit", "Unit"),
        ("price_ex_gst", "Price Ex GST"),
    ]:
        if target not in resolved:
            missing.append(display_name)
    if missing:
        raise ValueError("Missing required column(s): " + ", ".join(missing))

    records_by_code = {}
    for _, source_row in work.iterrows():
        product_code = str(source_row.get(resolved["product_code"], "") or "").strip()
        product_name = str(source_row.get(resolved["product_name"], "") or "").strip()
        supplier = str(source_row.get(resolved["supplier"], "") or "").strip()
        unit = str(source_row.get(resolved["unit"], "") or "").strip()
        notes_column = resolved.get("notes")
        notes = str(source_row.get(notes_column, "") or "").strip() if notes_column else ""

        if not product_code and not product_name:
            continue
        if not product_code:
            raise ValueError("Every product row must have a Product Code.")
        if not product_name:
            raise ValueError(f"Product {product_code} is missing Product Name.")
        if not supplier:
            raise ValueError(f"Product {product_code} is missing Supplier.")
        if not unit:
            raise ValueError(f"Product {product_code} is missing Unit.")

        price = _product_import_float(source_row.get(resolved["price_ex_gst"], ""))
        records_by_code[product_code.casefold()] = (
            product_code,
            product_name,
            supplier,
            unit,
            price,
            notes,
        )

    rows = list(records_by_code.values())
    if not rows:
        raise ValueError("No valid product records were found.")
    if len(rows) > MAX_CSV_IMPORT_ROWS:
        raise ValueError(f"The product pricing file exceeds the {MAX_CSV_IMPORT_ROWS:,}-row import limit.")
    return rows


def import_product_pricing_dataframe(source_df, source_file_name=""):
    """Import pricing with one existing-code lookup and batched writes."""
    rows = normalise_product_pricing_dataframe(source_df)
    conn = connect()
    inserted = 0
    updated = 0
    try:
        cur = conn.cursor()
        cur.execute("SELECT product_code FROM products")
        existing_codes = {
            str(row[0] or "").strip().casefold(): str(row[0] or "").strip()
            for row in cur.fetchall()
            if str(row[0] or "").strip()
        }

        update_rows = []
        insert_rows = []
        for product_code, product_name, supplier, unit, price_ex_gst, notes in rows:
            existing_code = existing_codes.get(product_code.casefold())
            if existing_code:
                update_rows.append((
                    product_name,
                    supplier,
                    unit,
                    price_ex_gst,
                    notes,
                    existing_code,
                ))
            else:
                insert_rows.append((
                    product_code,
                    product_name,
                    supplier,
                    unit,
                    price_ex_gst,
                    notes,
                ))
                existing_codes[product_code.casefold()] = product_code

        if update_rows:
            cur.executemany("""
                UPDATE products
                SET product_name = ?, supplier = ?, unit = ?, price_ex_gst = ?, notes = ?
                WHERE product_code = ?
            """, update_rows)
            updated = len(update_rows)

        if insert_rows:
            cur.executemany("""
                INSERT INTO products
                (product_code, product_name, supplier, unit, price_ex_gst, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """, insert_rows)
            inserted = len(insert_rows)

        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

    record_audit_event(
        "product_pricing_csv_imported",
        "products",
        "",
        {
            "file_name": safe_file_name(source_file_name or "product_pricing.csv"),
            "inserted": inserted,
            "updated": updated,
            "total": len(rows),
        },
    )
    return {"inserted": inserted, "updated": updated, "total": len(rows)}


if menu == "Field Mode":
    render_field_mode(jobhub_enterprise_context())

elif menu == "Employee Portal":
    employee_portal()

elif menu == "Operations Hub":
    render_operations_hub(jobhub_enterprise_context())

elif menu == "Painting Intelligence":
    render_painting_intelligence(jobhub_enterprise_context())

elif menu == "App Builder AI":
    app_builder_ai_page()


elif menu == "User Access":
    user_access_page()


# =============================
# DASHBOARD
# =============================
elif menu == "Control Centre":
    control_centre_page()


elif menu == "Job Lookup / Links":
    job_lookup_links_page()


elif menu == "Job Folders":
    job_folders_page()


elif menu == "Dashboard":
    dashboard_counts = df_query("""
        SELECT
            (SELECT COUNT(*) FROM jobs) AS jobs_count,
            (SELECT COUNT(*) FROM builders_clients) AS contacts_count,
            (SELECT COUNT(*) FROM employees) AS employees_count,
            (SELECT COUNT(*) FROM products) AS products_count
    """)
    counts = dashboard_counts.iloc[0] if not dashboard_counts.empty else {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Jobs", int(counts.get("jobs_count", 0) or 0))
    c2.metric("Builders / Clients", int(counts.get("contacts_count", 0) or 0))
    c3.metric("Employees", int(counts.get("employees_count", 0) or 0))
    c4.metric("Products", int(counts.get("products_count", 0) or 0))

    st.subheader("Open Jobs")
    active = df_query("""
        SELECT j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               bc.name AS 'Builder / Client',
               j.site_address AS 'Site Address',
               j.status AS 'Status',
               j.leading_hand AS 'Leading Hand',
               j.start_date AS 'Start Date'
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.status NOT IN ('Completed', 'Paid', 'Archived')
        ORDER BY j.job_no
    """)
    st.dataframe(active, width="stretch", hide_index=True)


# =============================
# JOBS - ADD / EDIT / REMOVE
# =============================
elif menu == "Jobs":
    st.header("Job Register")
    builder_options = get_builder_options()
    material_supplier_options = get_product_supplier_options()

    tab_add, tab_edit, tab_remove, tab_archived, tab_search, tab_list = st.tabs(
        ["Add Job", "Edit Job", "Remove / Archive", "Archived Jobs", "Search by Builder", "Job Register"]
    )

    with tab_add:
        st.subheader("Add New Job")
        with st.form("add_job_form"):
            col1, col2 = st.columns(2)
            job_no = col1.text_input("Job Number", next_job_no())
            job_name = col2.text_input("Job Name")

            builder_label = st.selectbox("Builder / Client", [""] + list(builder_options.keys()))
            site_address = st.text_input("Site Address")

            col3, col4, col5 = st.columns(3)
            status = col3.selectbox("Status", ["Not Started", "Quoted", "Booked", "Active", "On Hold", "Completed", "Invoiced", "Paid", "Archived"])
            employee_options_add_job = get_employee_options(active_only=True)
            leading_hand = col4.selectbox("Leading Hand", [""] + list(employee_options_add_job.keys()))
            contract_value = col5.number_input("Contract Value Ex GST", min_value=0.0, step=100.0)

            col6, col7 = st.columns(2)
            start_date_value = col6.date_input("Start Date", value=None)
            end_date_value = col7.date_input("End Date", value=None)
            start_date = start_date_value.isoformat() if start_date_value else ""
            end_date = end_date_value.isoformat() if end_date_value else ""

            st.markdown("#### Material supplier / brand allocation")
            restrict_material_products = st.checkbox(
                "Only show products from the approved suppliers/brands for this job",
                value=False,
                key="add_job_restrict_material_products",
            )
            allowed_material_suppliers = st.multiselect(
                "Approved suppliers / brands",
                material_supplier_options,
                key="add_job_allowed_material_suppliers",
                help="Example: select Haymes so staff only see Haymes products when requesting materials for this job.",
            )

            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Job")

            if submitted and job_no:
                builder_id = builder_options.get(builder_label) if builder_label else None
                if start_date_value and end_date_value and end_date_value < start_date_value:
                    pb_error("End Date cannot be before Start Date.")
                elif restrict_material_products and not allowed_material_suppliers:
                    pb_error("Select at least one approved supplier/brand before restricting this job's products.")
                else:
                    try:
                        execute("""
                            INSERT INTO jobs
                            (job_no, job_name, builder_client_id, site_address, status, leading_hand,
                             start_date, end_date, contract_value, notes,
                             restrict_material_products, allowed_material_suppliers)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            job_no.strip(), job_name, builder_id, site_address, status, leading_hand,
                            start_date, end_date, contract_value, notes,
                            1 if restrict_material_products else 0,
                            serialise_material_supplier_list(allowed_material_suppliers),
                        ))
                        record_audit_event(
                            "job_created",
                            "job",
                            job_no.strip(),
                            {
                                "restrict_material_products": bool(restrict_material_products),
                                "allowed_material_suppliers": allowed_material_suppliers,
                            },
                        )
                        pb_success(f"Saved job {job_no}")
                        refresh()
                    except Exception:
                        pb_error("That job number already exists or the job could not be saved. Use Edit Existing Job for updates.")

    with tab_edit:
        st.subheader("Edit Existing Job")
        jobs_df = df_query("""
            SELECT j.*, COALESCE(bc.name, '') AS builder_name
            FROM jobs j
            LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
            ORDER BY j.job_no
        """)
        if jobs_df.empty:
            st.info("No jobs yet.")
        else:
            job_map = {f"{row['job_no']} - {row['job_name']}": int(row["id"]) for _, row in jobs_df.iterrows()}
            selected_job = st.selectbox("Select Job to Edit", list(job_map.keys()))
            selected_id = job_map[selected_job]
            current = jobs_df[jobs_df["id"] == selected_id].iloc[0]

            builder_names = [""] + list(builder_options.keys())
            current_builder = str(current["builder_name"] or "")
            builder_index = builder_names.index(current_builder) if current_builder in builder_names else 0
            current_allowed_suppliers = parse_material_supplier_list(current.get("allowed_material_suppliers", ""))
            edit_supplier_options = list(material_supplier_options)
            for supplier_name in current_allowed_suppliers:
                if supplier_name not in edit_supplier_options:
                    edit_supplier_options.append(supplier_name)

            with st.form("edit_job_form"):
                col1, col2 = st.columns(2)
                edit_job_no = col1.text_input("Job Number", value=str(current["job_no"] or ""))
                edit_job_name = col2.text_input("Job Name", value=str(current["job_name"] or ""))

                edit_builder_label = st.selectbox("Builder / Client", builder_names, index=builder_index)
                edit_site_address = st.text_input("Site Address", value=str(current["site_address"] or ""))

                statuses = ["Not Started", "Quoted", "Booked", "Active", "On Hold", "Completed", "Invoiced", "Paid", "Archived"]
                current_status = str(current["status"] or "Not Started")
                status_index = statuses.index(current_status) if current_status in statuses else 0

                col3, col4, col5 = st.columns(3)
                edit_status = col3.selectbox("Status", statuses, index=status_index)
                employee_options_edit_job = get_employee_options(active_only=True)
                employee_names_edit_job = [""] + list(employee_options_edit_job.keys())
                current_leading_hand = str(current["leading_hand"] or "")
                leading_hand_index = employee_names_edit_job.index(current_leading_hand) if current_leading_hand in employee_names_edit_job else 0
                edit_leading_hand = col4.selectbox("Leading Hand", employee_names_edit_job, index=leading_hand_index)
                edit_contract_value = col5.number_input("Contract Value Ex GST", min_value=0.0, step=100.0, value=float(current["contract_value"] or 0))

                col6, col7 = st.columns(2)
                edit_start_date_value = col6.date_input(
                    "Start Date",
                    value=pb_date(current["start_date"]),
                    format="DD/MM/YYYY",
                )
                edit_end_date_value = col7.date_input(
                    "End Date",
                    value=pb_date(current["end_date"]),
                    format="DD/MM/YYYY",
                )
                edit_start_date = edit_start_date_value.isoformat() if edit_start_date_value else ""
                edit_end_date = edit_end_date_value.isoformat() if edit_end_date_value else ""

                st.markdown("#### Material supplier / brand allocation")
                edit_restrict_material_products = st.checkbox(
                    "Only show products from the approved suppliers/brands for this job",
                    value=bool(int(current.get("restrict_material_products", 0) or 0)),
                    key=f"edit_job_restrict_material_products_{selected_id}",
                )
                edit_allowed_material_suppliers = st.multiselect(
                    "Approved suppliers / brands",
                    edit_supplier_options,
                    default=current_allowed_suppliers,
                    key=f"edit_job_allowed_material_suppliers_{selected_id}",
                    help="Staff see only these brands by default. They can use the override with a required reason.",
                )

                edit_notes = st.text_area("Notes", value=str(current["notes"] or ""))
                submitted = st.form_submit_button("Update Job")

                if submitted:
                    edit_builder_id = builder_options.get(edit_builder_label) if edit_builder_label else None
                    current_version = int(current["row_version"] or 1)
                    if edit_start_date_value and edit_end_date_value and edit_end_date_value < edit_start_date_value:
                        pb_error("End Date cannot be before Start Date.")
                    elif edit_restrict_material_products and not edit_allowed_material_suppliers:
                        pb_error("Select at least one approved supplier/brand before restricting this job's products.")
                    else:
                        updated_rows = execute_with_rowcount("""
                            UPDATE jobs
                            SET job_no = ?, job_name = ?, builder_client_id = ?, site_address = ?, status = ?,
                                leading_hand = ?, start_date = ?, end_date = ?, contract_value = ?, notes = ?,
                                restrict_material_products = ?, allowed_material_suppliers = ?,
                                row_version = COALESCE(row_version, 1) + 1
                            WHERE id = ? AND COALESCE(row_version, 1) = ?
                        """, (
                            edit_job_no, edit_job_name, edit_builder_id, edit_site_address, edit_status,
                            edit_leading_hand, edit_start_date, edit_end_date, edit_contract_value, edit_notes,
                            1 if edit_restrict_material_products else 0,
                            serialise_material_supplier_list(edit_allowed_material_suppliers),
                            selected_id, current_version,
                        ))
                        if updated_rows == 0:
                            pb_error(
                                "This job changed after you opened the form. Reload it and review the latest values "
                                "before saving again."
                            )
                        else:
                            record_audit_event(
                                "job_updated",
                                "job",
                                selected_id,
                                {
                                    "previous_row_version": current_version,
                                    "restrict_material_products": bool(edit_restrict_material_products),
                                    "allowed_material_suppliers": edit_allowed_material_suppliers,
                                },
                            )
                            pb_success(f"Updated job {edit_job_no}")
                            refresh()

    with tab_remove:
        st.subheader("Remove or Archive Job")
        st.warning("If a job has wages, materials or equipment saved against it, archive it instead of deleting it.")
        jobs_df = df_query("SELECT id, job_no, job_name FROM jobs ORDER BY job_no")
        if jobs_df.empty:
            st.info("No jobs yet.")
        else:
            job_map = {f"{row['job_no']} - {row['job_name']}": int(row["id"]) for _, row in jobs_df.iterrows()}
            selected_job = st.selectbox("Select Job", list(job_map.keys()), key="remove_job_select")
            selected_id = job_map[selected_job]

            col1, col2 = st.columns(2)
            if col1.button("Archive Job"):
                user = get_current_user() or {}
                execute("""
                    UPDATE jobs
                    SET status = 'Archived', archived_at = ?, archived_by = ?,
                        row_version = COALESCE(row_version, 1) + 1
                    WHERE id = ?
                """, (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    str(user.get("username") or ""),
                    selected_id,
                ))
                record_audit_event("job_archived", "job", selected_id)
                pb_success("Job archived.")
                refresh()

            if col2.button("Delete Job"):
                linked = any(value > 0 for value in linked_job_counts(selected_id).values())
                if linked:
                    user = get_current_user() or {}
                    execute("""
                        UPDATE jobs
                        SET status = 'Archived', archived_at = ?, archived_by = ?,
                            row_version = COALESCE(row_version, 1) + 1
                        WHERE id = ?
                    """, (
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        str(user.get("username") or ""),
                        selected_id,
                    ))
                    record_audit_event("job_archived", "job", selected_id)
                    st.info("This job has linked records, so it was archived instead of deleted.")
                else:
                    execute("DELETE FROM jobs WHERE id = ?", (selected_id,))
                    record_audit_event("empty_job_deleted", "job", selected_id)
                    pb_success("Job deleted.")
                refresh()

    with tab_archived:
        st.subheader("Archived Jobs")

        archived_df = df_query("""
            SELECT j.*, COALESCE(bc.name, '') AS builder_name
            FROM jobs j
            LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
            WHERE j.status = 'Archived'
            ORDER BY j.job_no
        """)

        if archived_df.empty:
            st.info("No archived jobs found.")
        else:
            archived_view = archived_df[[
                "job_no", "job_name", "builder_name", "site_address",
                "leading_hand", "start_date", "end_date", "contract_value", "notes"
            ]].rename(columns={
                "job_no": "Job No",
                "job_name": "Job Name",
                "builder_name": "Builder / Client",
                "site_address": "Site Address",
                "leading_hand": "Leading Hand",
                "start_date": "Start Date",
                "end_date": "End Date",
                "contract_value": "Contract Value",
                "notes": "Notes",
            })

            st.markdown("### View Archived Jobs")
            st.dataframe(archived_view, width="stretch", hide_index=True)

            archived_map = {
                f"{row['job_no']} - {row['job_name']}": int(row["id"])
                for _, row in archived_df.iterrows()
            }

            selected_archived_job = st.selectbox(
                "Select Archived Job",
                list(archived_map.keys()),
                key="archived_job_select"
            )
            selected_archived_id = archived_map[selected_archived_job]
            current = archived_df[archived_df["id"] == selected_archived_id].iloc[0]

            counts = linked_job_counts(selected_archived_id)

            st.markdown("### Linked Data Saved Against This Archived Job")
            count_df = pd.DataFrame([
                ["Materials", counts.get("material_entries", 0)],
                ["Wages", counts.get("wage_entries", 0)],
                ["Old Equipment Entries", counts.get("equipment_entries", 0)],
                ["Equipment Checklist Lines", counts.get("equipment_checklist_records", 0)],
                ["Imported Checklist Materials", counts.get("imported_material_entries", 0)],
            ], columns=["Linked Data", "Record Count"])
            st.dataframe(count_df, width="stretch", hide_index=True)

            st.markdown("### Edit Archived Job")
            builder_options_archived = get_builder_options()
            builder_names_archived = [""] + list(builder_options_archived.keys())
            current_builder = str(current["builder_name"] or "")
            builder_index = builder_names_archived.index(current_builder) if current_builder in builder_names_archived else 0

            with st.form("edit_archived_job_form"):
                col1, col2 = st.columns(2)
                edit_job_no = col1.text_input("Job Number", value=str(current["job_no"] or ""), key="arch_job_no")
                edit_job_name = col2.text_input("Job Name", value=str(current["job_name"] or ""), key="arch_job_name")

                edit_builder_label = st.selectbox(
                    "Builder / Client",
                    builder_names_archived,
                    index=builder_index,
                    key="arch_builder"
                )
                edit_site_address = st.text_input("Site Address", value=str(current["site_address"] or ""), key="arch_site_address")

                employee_options_archived_job = get_employee_options(active_only=True)
                employee_names_archived_job = [""] + list(employee_options_archived_job.keys())
                current_leading_hand = str(current["leading_hand"] or "")
                leading_hand_index = employee_names_archived_job.index(current_leading_hand) if current_leading_hand in employee_names_archived_job else 0

                col3, col4, col5 = st.columns(3)
                edit_status = col3.selectbox("Status", ["Archived", "Not Started", "Quoted", "Booked", "Active", "On Hold", "Completed", "Invoiced", "Paid"], index=0, key="arch_status")
                edit_leading_hand = col4.selectbox("Leading Hand", employee_names_archived_job, index=leading_hand_index, key="arch_leading_hand")
                edit_contract_value = col5.number_input(
                    "Contract Value Ex GST",
                    min_value=0.0,
                    step=100.0,
                    value=float(current["contract_value"] or 0),
                    key="arch_contract_value"
                )

                col6, col7 = st.columns(2)
                edit_start_date_value = col6.date_input(
                    "Start Date",
                    value=pb_date(current["start_date"]),
                    format="DD/MM/YYYY",
                    key="arch_start_date",
                )
                edit_end_date_value = col7.date_input(
                    "End Date",
                    value=pb_date(current["end_date"]),
                    format="DD/MM/YYYY",
                    key="arch_end_date",
                )
                edit_start_date = edit_start_date_value.isoformat() if edit_start_date_value else ""
                edit_end_date = edit_end_date_value.isoformat() if edit_end_date_value else ""

                edit_notes = st.text_area("Notes", value=str(current["notes"] or ""), key="arch_notes")
                update_archived = st.form_submit_button("Update Archived Job")

                if update_archived:
                    edit_builder_id = builder_options_archived.get(edit_builder_label) if edit_builder_label else None
                    current_version = int(current["row_version"] or 1)
                    updated_rows = execute_with_rowcount("""
                        UPDATE jobs
                        SET job_no = ?, job_name = ?, builder_client_id = ?, site_address = ?, status = ?,
                            leading_hand = ?, start_date = ?, end_date = ?, contract_value = ?, notes = ?,
                            row_version = COALESCE(row_version, 1) + 1
                        WHERE id = ? AND COALESCE(row_version, 1) = ?
                    """, (
                        edit_job_no, edit_job_name, edit_builder_id, edit_site_address, edit_status,
                        edit_leading_hand, edit_start_date, edit_end_date, edit_contract_value, edit_notes,
                        selected_archived_id, current_version,
                    ))

                    if updated_rows == 0:
                        pb_error(
                            "This job changed after you opened the form. Reload it and review the latest values "
                            "before saving again."
                        )
                    else:
                        record_audit_event(
                            "archived_job_updated",
                            "job",
                            selected_archived_id,
                            {"previous_row_version": current_version},
                        )
                        if edit_status != "Archived":
                            pb_success(f"Updated and restored job {edit_job_no}.")
                        else:
                            pb_success(f"Updated archived job {edit_job_no}.")
                        refresh()

            st.markdown("### Restore or Permanently Delete")
            col_restore, col_delete = st.columns(2)

            if col_restore.button("Restore Archived Job to Active"):
                execute("""
                    UPDATE jobs
                    SET status = 'Active', archived_at = '', archived_by = '',
                        row_version = COALESCE(row_version, 1) + 1
                    WHERE id = ?
                """, (selected_archived_id,))
                record_audit_event("job_restored", "job", selected_archived_id)
                pb_success("Job restored to Active.")
                refresh()

            with col_delete:
                st.warning("Permanent delete removes the archived job and all linked materials, wages, equipment and imported checklist data.")
                confirm_delete = st.checkbox(
                    "I understand this will permanently delete this archived job and all linked data.",
                    key="confirm_delete_archived_job"
                )

                if st.button("Permanently Delete Archived Job"):
                    if not confirm_delete:
                        pb_error("Tick the confirmation box before permanently deleting.")
                    else:
                        permanently_delete_job_and_linked_data(selected_archived_id)
                        pb_success("Archived job and linked data permanently deleted.")
                        refresh()


    with tab_search:
        st.subheader("Search Job Numbers by Builder / Client")
        selected_builder = st.selectbox("Select Builder / Client", [""] + list(builder_options.keys()), key="job_search_builder")
        if selected_builder:
            search_df = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       j.status AS 'Status',
                       j.site_address AS 'Site Address'
                FROM jobs j
                JOIN builders_clients bc ON bc.id = j.builder_client_id
                WHERE bc.name = ?
                ORDER BY j.job_no
            """, (selected_builder,))
            st.dataframe(search_df, width="stretch", hide_index=True)

            if st.button("Open this builder/client in Job Lookup", key="open_search_builder_linked_view"):
                go_to_linked_job_view(builder_id=builder_options[selected_builder], mode="Jobs by Builder / Client")

    with tab_list:
        st.subheader("Full Job Register")
        include_archived = st.checkbox("Show archived jobs in register", value=True)
        where_clause = "" if include_archived else "WHERE j.status != 'Archived'"

        job_df = df_query(f"""
            SELECT j.job_no AS 'Job No',
                   j.job_name AS 'Job Name',
                   bc.name AS 'Builder / Client',
                   bc.contact_name AS 'Contact',
                   bc.phone AS 'Phone',
                   bc.email AS 'Email',
                   bc.terms AS 'Terms',
                   j.site_address AS 'Site Address',
                   j.status AS 'Status',
                   j.leading_hand AS 'Leading Hand',
                   j.start_date AS 'Start Date',
                   j.end_date AS 'End Date',
                   j.contract_value AS 'Contract Value',
                   j.notes AS 'Notes'
            FROM jobs j
            LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
            {where_clause}
            ORDER BY j.job_no
        """)
        st.dataframe(job_df, width="stretch", hide_index=True)

        st.markdown("### Open Linked Job Info")
        st.caption("Select a job here to open the full linked file: job details, builder, materials, wages, timesheets, equipment, photos, estimates, variations and claims.")
        open_jobs_df = job_lookup_dataframe(include_archived=include_archived)
        selected_open_job_id = select_job_from_dataframe(
            open_jobs_df,
            "Select job number / name / builder / address to open",
            key="job_register_open_linked_select",
            default_job_id=st.session_state.get("linked_view_selected_job_id")
        )
        if selected_open_job_id and st.button("Open selected job and all linked info", key="job_register_open_linked_button"):
            go_to_linked_job_view(job_id=selected_open_job_id, mode="Open Job")


# =============================
# ESTIMATE WORKING SHEET / TAKE-OFF IMPORT
# =============================
elif menu == "Import Take-off Job Pack":
    takeoff_job_pack_import_page()


elif menu == "Estimating Rate Library":
    estimating_rate_library_page()


elif menu == "Estimate Working Sheet":
    estimate_working_sheet_page()


elif menu == "Job Progress Tracker":
    render_progress_tracker(jobhub_enterprise_context())


# =============================
# BUILDERS / CLIENTS - ADD / EDIT / REMOVE
# =============================
elif menu == "Job Costs / Forecasting":
    job_costs_forecasting_page()


elif menu == "JobHub AI Assistant":
    jobhub_ai_assistant_page()


elif menu == "Builders & Clients":
    st.header("Builders & Clients")

    tab_add, tab_edit, tab_remove, tab_merge, tab_list = st.tabs(["Add", "Edit", "Remove", "Merge", "List"])

    with tab_add:
        st.subheader("Add Builder / Client")
        with st.form("add_builder_form"):
            col1, col2 = st.columns(2)
            typ = col1.text_input("Type", "Builder")
            name = col2.text_input("Company / Client Name")
            contact = st.text_input("Contact Name")
            col3, col4 = st.columns(2)
            phone = col3.text_input("Phone / Mobile")
            email = col4.text_input("Email")
            address = st.text_input("Address")
            col5, col6, col7 = st.columns(3)
            qbcc = col5.text_input("QBCC")
            abn = col6.text_input("ABN")
            terms = col7.text_input("Payment Terms")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Builder / Client")

            if submitted and name:
                normalised_name = name.strip().casefold()
                duplicate = df_query(
                    "SELECT id FROM builders_clients WHERE LOWER(TRIM(name)) = ? LIMIT 1",
                    (normalised_name,),
                )
                if not duplicate.empty:
                    pb_error("A builder/client with that name already exists. Edit or merge the existing record.")
                else:
                    try:
                        execute("""
                            INSERT INTO builders_clients
                            (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes, normalised_name)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (typ, name.strip(), contact, phone, email, address, qbcc, abn, terms, notes, normalised_name))
                        record_audit_event("builder_client_created", "builder_client", name.strip())
                        pb_success(f"Saved {name}")
                        refresh()
                    except Exception:
                        pb_error("The builder/client could not be saved. Check for an existing duplicate.")

    with tab_edit:
        st.subheader("Edit Builder / Client")
        builders_df = df_query("SELECT * FROM builders_clients ORDER BY name")
        if builders_df.empty:
            st.info("No builders or clients yet.")
        else:
            builder_map = {row["name"]: int(row["id"]) for _, row in builders_df.iterrows()}
            selected_builder = st.selectbox("Select Builder / Client to Edit", list(builder_map.keys()))
            selected_id = builder_map[selected_builder]
            current = builders_df[builders_df["id"] == selected_id].iloc[0]

            with st.form("edit_builder_form"):
                col1, col2 = st.columns(2)
                typ = col1.text_input("Type", value=str(current["type"] or ""))
                name = col2.text_input("Company / Client Name", value=str(current["name"] or ""))
                contact = st.text_input("Contact Name", value=str(current["contact_name"] or ""))
                col3, col4 = st.columns(2)
                phone = col3.text_input("Phone / Mobile", value=str(current["phone"] or ""))
                email = col4.text_input("Email", value=str(current["email"] or ""))
                address = st.text_input("Address", value=str(current["address"] or ""))
                col5, col6, col7 = st.columns(3)
                qbcc = col5.text_input("QBCC", value=str(current["qbcc"] or ""))
                abn = col6.text_input("ABN", value=str(current["abn"] or ""))
                terms = col7.text_input("Payment Terms", value=str(current["terms"] or ""))
                notes = st.text_area("Notes", value=str(current["notes"] or ""))
                submitted = st.form_submit_button("Update Builder / Client")

                if submitted:
                    execute("""
                        UPDATE builders_clients
                        SET type = ?, name = ?, contact_name = ?, phone = ?, email = ?, address = ?,
                            qbcc = ?, abn = ?, terms = ?, notes = ?, normalised_name = LOWER(TRIM(?))
                        WHERE id = ?
                    """, (
                        typ, name, contact, phone, email, address, qbcc, abn, terms,
                        notes, name, selected_id,
                    ))
                    record_audit_event("builder_client_updated", "builder_client", selected_id)
                    pb_success(f"Updated {name}")
                    refresh()

    with tab_remove:
        st.subheader("Remove Builder / Client")
        st.warning("If this builder/client has jobs linked, they cannot be deleted until the jobs are changed or archived.")
        builders_df = df_query("SELECT id, name FROM builders_clients ORDER BY name")
        if builders_df.empty:
            st.info("No builders or clients yet.")
        else:
            builder_map = {row["name"]: int(row["id"]) for _, row in builders_df.iterrows()}
            selected_builder = st.selectbox("Select Builder / Client to Remove", list(builder_map.keys()), key="remove_builder_select")
            selected_id = builder_map[selected_builder]

            linked_jobs = df_query("SELECT COUNT(*) AS c FROM jobs WHERE builder_client_id = ?", (selected_id,))
            job_count = int(linked_jobs.iloc[0]["c"])
            st.write(f"Linked jobs: {job_count}")

            if st.button("Delete Builder / Client"):
                if job_count > 0:
                    pb_error("Cannot delete this builder/client because jobs are linked to them. Edit those jobs first or leave the builder in the database.")
                else:
                    execute("DELETE FROM builders_clients WHERE id = ?", (selected_id,))
                    pb_success("Builder/client deleted.")
                    refresh()

    with tab_merge:
        st.subheader("Merge Duplicate Contacts")
        st.caption(
            "Keep one primary record, transfer every linked job from the selected duplicates, "
            "review the final details, then remove only those duplicate records."
        )

        merge_contacts_df = df_query("""
            SELECT id, type, name, contact_name, phone, email, address,
                   qbcc, abn, terms, notes
            FROM builders_clients
            ORDER BY name, id
        """)

        if len(merge_contacts_df.index) < 2:
            st.info("At least two builder/client records are required before contacts can be merged.")
        else:
            merge_contacts_df["id"] = merge_contacts_df["id"].astype(int)
            contact_rows = {
                int(row["id"]): row
                for _, row in merge_contacts_df.iterrows()
            }

            def merge_contact_label(contact_id):
                row = contact_rows[int(contact_id)]
                details = [clean_contact_merge_value(row.get("phone")), clean_contact_merge_value(row.get("email"))]
                details = [item for item in details if item]
                suffix = f" — {' / '.join(details)}" if details else ""
                return f'{clean_contact_merge_value(row.get("name"))}{suffix} (ID {int(contact_id)})'

            all_contact_ids = list(contact_rows.keys())
            primary_merge_id = st.selectbox(
                "Primary contact to keep",
                all_contact_ids,
                format_func=merge_contact_label,
                key="builder_contact_merge_primary",
            )
            duplicate_options = [contact_id for contact_id in all_contact_ids if contact_id != int(primary_merge_id)]
            duplicate_merge_ids = st.multiselect(
                "Duplicate contacts to merge into the primary contact",
                duplicate_options,
                format_func=merge_contact_label,
                key="builder_contact_merge_duplicates",
            )

            if duplicate_merge_ids:
                selected_merge_ids = [int(primary_merge_id)] + [int(x) for x in duplicate_merge_ids]
                selected_merge_df = merge_contacts_df[merge_contacts_df["id"].isin(selected_merge_ids)].copy()

                linked_job_counts = df_query(
                    f"""
                    SELECT builder_client_id, COUNT(*) AS linked_jobs
                    FROM jobs
                    WHERE builder_client_id IN ({', '.join(['?'] * len(selected_merge_ids))})
                    GROUP BY builder_client_id
                    """,
                    tuple(selected_merge_ids),
                )
                count_map = {
                    int(row["builder_client_id"]): int(row["linked_jobs"])
                    for _, row in linked_job_counts.iterrows()
                } if not linked_job_counts.empty else {}
                selected_merge_df["Linked Jobs"] = selected_merge_df["id"].map(count_map).fillna(0).astype(int)
                selected_merge_df["Role"] = selected_merge_df["id"].apply(
                    lambda record_id: "PRIMARY — KEEP" if int(record_id) == int(primary_merge_id) else "DUPLICATE — REMOVE"
                )

                st.markdown("#### Records selected")
                st.dataframe(
                    selected_merge_df[[
                        "Role", "name", "contact_name", "phone", "email", "address", "Linked Jobs"
                    ]].rename(columns={
                        "name": "Company / Client",
                        "contact_name": "Contact",
                        "phone": "Phone",
                        "email": "Email",
                        "address": "Address",
                    }),
                    width="stretch",
                    hide_index=True,
                )

                merge_defaults = builder_client_merge_defaults(selected_merge_df, int(primary_merge_id))
                jobs_to_move = int(selected_merge_df.loc[
                    selected_merge_df["id"] != int(primary_merge_id), "Linked Jobs"
                ].sum())

                st.info(
                    f"This merge will keep 1 contact, remove {len(duplicate_merge_ids)} duplicate "
                    f"record(s), and transfer {jobs_to_move} linked job(s) to the primary contact."
                )

                with st.form("builder_contact_merge_review_form"):
                    st.markdown("#### Review the final contact")
                    c1, c2 = st.columns(2)
                    merged_type = c1.text_input("Type", value=merge_defaults["type"])
                    merged_name = c2.text_input("Company / Client Name", value=merge_defaults["name"])
                    merged_contact = st.text_input("Contact Name", value=merge_defaults["contact_name"])
                    c3, c4 = st.columns(2)
                    merged_phone = c3.text_input("Phone / Mobile", value=merge_defaults["phone"])
                    merged_email = c4.text_input("Email", value=merge_defaults["email"])
                    merged_address = st.text_input("Address", value=merge_defaults["address"])
                    c5, c6, c7 = st.columns(3)
                    merged_qbcc = c5.text_input("QBCC", value=merge_defaults["qbcc"])
                    merged_abn = c6.text_input("ABN", value=merge_defaults["abn"])
                    merged_terms = c7.text_input("Payment Terms", value=merge_defaults["terms"])
                    merged_notes = st.text_area("Notes", value=merge_defaults["notes"], height=140)
                    merge_confirmed = st.checkbox(
                        "I have reviewed the primary contact and understand the selected duplicate records will be deleted."
                    )
                    merge_submitted = st.form_submit_button("Merge contacts safely", type="primary")

                    if merge_submitted:
                        if not merge_confirmed:
                            pb_error("Tick the confirmation box before merging contacts.")
                        else:
                            try:
                                merge_result = merge_builder_client_records(
                                    int(primary_merge_id),
                                    [int(x) for x in duplicate_merge_ids],
                                    {
                                        "type": merged_type,
                                        "name": merged_name,
                                        "contact_name": merged_contact,
                                        "phone": merged_phone,
                                        "email": merged_email,
                                        "address": merged_address,
                                        "qbcc": merged_qbcc,
                                        "abn": merged_abn,
                                        "terms": merged_terms,
                                        "notes": merged_notes,
                                    },
                                )
                                pb_success(
                                    f'Merged into "{merge_result["primary_name"]}". '
                                    f'{merge_result["duplicates_removed"]} duplicate record(s) removed and '
                                    f'{merge_result["jobs_moved"]} linked job(s) transferred.'
                                )
                                refresh()
                            except Exception as exc:
                                pb_error(f"Contacts were not merged: {exc}")
            else:
                st.info("Choose one or more duplicate contacts to preview and merge.")

    with tab_list:
        st.subheader("Builder & Client List")
        df = df_query("""
            SELECT type AS 'Type',
                   name AS 'Company / Client',
                   contact_name AS 'Contact',
                   phone AS 'Phone',
                   email AS 'Email',
                   address AS 'Address',
                   qbcc AS 'QBCC',
                   abn AS 'ABN',
                   terms AS 'Terms',
                   notes AS 'Notes'
            FROM builders_clients
            ORDER BY name
        """)
        st.dataframe(df, width="stretch", hide_index=True)

        st.markdown("### View Jobs for a Builder / Client")
        builder_lookup = df_query("SELECT id, name FROM builders_clients ORDER BY name")
        if builder_lookup.empty:
            st.info("No builders or clients saved yet.")
        else:
            builder_map = {str(row["name"]): int(row["id"]) for _, row in builder_lookup.iterrows()}
            selected_builder_lookup = st.selectbox(
                "Select builder/client to view linked jobs",
                list(builder_map.keys()),
                key="builder_list_linked_jobs_select"
            )
            selected_builder_id = builder_map[selected_builder_lookup]

            linked_jobs_df = job_lookup_dataframe(include_archived=True)
            linked_jobs_df = linked_jobs_df[linked_jobs_df["builder_id"].astype(int) == int(selected_builder_id)]

            if linked_jobs_df.empty:
                st.info("No jobs linked to this builder/client.")
            else:
                st.dataframe(linked_jobs_df.drop(columns=["job_id", "builder_id"], errors="ignore"), width="stretch", hide_index=True)
                selected_builder_job_id = select_job_from_dataframe(
                    linked_jobs_df,
                    "Select one of this builder/client's jobs",
                    key="builder_list_job_to_open_select"
                )
                col_open_builder, col_open_job = st.columns(2)
                if col_open_builder.button("Open builder/client in Job Lookup", key="builder_list_open_builder_lookup"):
                    go_to_linked_job_view(builder_id=selected_builder_id, mode="Jobs by Builder / Client")
                if col_open_job.button("Open selected job and all linked info", key="builder_list_open_job_lookup"):
                    go_to_linked_job_view(job_id=selected_builder_job_id, mode="Open Job")


# =============================
# EMPLOYEES - ADD / EDIT / REMOVE
# =============================
elif menu == "Employees":
    st.header("Employees")

    tab_add, tab_edit, tab_remove, tab_list = st.tabs(["Add", "Edit", "Remove / Deactivate", "List"])

    with tab_add:
        st.subheader("Add Employee")
        with st.form("add_employee_form"):
            col1, col2 = st.columns(2)
            name = col1.text_input("Employee Name")
            role = col2.text_input("Role")
            phone = st.text_input("Phone")
            col3, col4 = st.columns(2)
            base_rate = col3.number_input("Base Hourly Rate", min_value=0.0, step=1.0)
            rate_plus = col4.number_input("Rate + 10%", min_value=0.0, step=1.0, value=0.0)
            status = st.selectbox("Status", ["Active", "Inactive"])
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Employee")

            if submitted and name:
                if rate_plus == 0 and base_rate > 0:
                    rate_plus = round(base_rate * 1.10, 2)
                try:
                    execute("""
                        INSERT INTO employees
                        (name, role, phone, base_hourly_rate, rate_plus_10, status, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (name.strip(), role, phone, base_rate, rate_plus, status, notes))
                    record_audit_event("employee_created", "employee", name.strip())
                    pb_success(f"Saved {name}")
                    refresh()
                except Exception:
                    pb_error("An employee with that name already exists. Use Edit Employee for updates.")

    with tab_edit:
        st.subheader("Edit Employee")
        employees_df = df_query("SELECT * FROM employees ORDER BY name")
        if employees_df.empty:
            st.info("No employees yet.")
        else:
            employee_map = {row["name"]: int(row["id"]) for _, row in employees_df.iterrows()}
            selected_employee = st.selectbox("Select Employee to Edit", list(employee_map.keys()))
            selected_id = employee_map[selected_employee]
            current = employees_df[employees_df["id"] == selected_id].iloc[0]

            with st.form("edit_employee_form"):
                col1, col2 = st.columns(2)
                name = col1.text_input("Employee Name", value=str(current["name"] or ""))
                role = col2.text_input("Role", value=str(current["role"] or ""))
                phone = st.text_input("Phone", value=str(current["phone"] or ""))

                col3, col4 = st.columns(2)
                base_rate = col3.number_input("Base Hourly Rate", min_value=0.0, step=1.0, value=float(current["base_hourly_rate"] or 0))
                rate_plus = col4.number_input("Rate + 10%", min_value=0.0, step=1.0, value=float(current["rate_plus_10"] or 0))

                statuses = ["Active", "Inactive"]
                current_status = str(current["status"] or "Active")
                status_index = statuses.index(current_status) if current_status in statuses else 0
                status = st.selectbox("Status", statuses, index=status_index)

                notes = st.text_area("Notes", value=str(current["notes"] or ""))
                submitted = st.form_submit_button("Update Employee")

                if submitted:
                    if rate_plus == 0 and base_rate > 0:
                        rate_plus = round(base_rate * 1.10, 2)
                    execute("""
                        UPDATE employees
                        SET name = ?, role = ?, phone = ?, base_hourly_rate = ?, rate_plus_10 = ?, status = ?, notes = ?
                        WHERE id = ?
                    """, (name, role, phone, base_rate, rate_plus, status, notes, selected_id))
                    pb_success(f"Updated {name}")
                    refresh()

    with tab_remove:
        st.subheader("Remove or Deactivate Employee")
        st.warning("If the employee has wage records, timesheets, or a linked user login, the app will mark them Inactive instead of deleting their history.")
        employees_df = df_query("SELECT id, name FROM employees ORDER BY name")
        if employees_df.empty:
            st.info("No employees yet.")
        else:
            employee_map = {row["name"]: int(row["id"]) for _, row in employees_df.iterrows()}
            selected_employee = st.selectbox("Select Employee", list(employee_map.keys()), key="remove_employee_select")
            selected_id = employee_map[selected_employee]

            col1, col2 = st.columns(2)
            if col1.button("Deactivate Employee"):
                execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (selected_id,))
                # If this employee has a login, disable that login as well.
                if has_related_records("app_users", "employee_id", selected_id):
                    execute("UPDATE app_users SET active = 0 WHERE employee_id = ?", (selected_id,))
                pb_success("Employee marked Inactive.")
                refresh()

            if col2.button("Delete Employee"):
                result = delete_employee_and_linked_users(selected_id)

                if result["deleted_users"]:
                    pb_success(f"Deleted {result['deleted_users']} linked user login account(s).")

                if result["deleted_employee"]:
                    pb_success(f"Deleted {result['deleted_employee']} employee record(s).")

                if result["deactivated_employee"]:
                    st.info(f"Marked {result['deactivated_employee']} employee(s) as Inactive because they had job history or protected linked records.")

                if result["skipped"]:
                    st.warning(f"Skipped {result['skipped']} item(s).")

                with st.expander("Employee delete details"):
                    for msg in result["messages"]:
                        st.write(msg)

                refresh()

    with tab_list:
        st.subheader("Employee List")

        show_inactive_workers = st.checkbox(
            "Show inactive workers",
            value=False,
            key="show_inactive_workers_employee_list"
        )

        if show_inactive_workers:
            df = df_query("""
                SELECT id AS 'ID',
                       name AS 'Employee',
                       role AS 'Role',
                       phone AS 'Phone',
                       base_hourly_rate AS 'Base Rate',
                       rate_plus_10 AS 'Rate + 10%',
                       status AS 'Status',
                       notes AS 'Notes'
                FROM employees
                ORDER BY status, name
            """)
        else:
            df = df_query("""
                SELECT id AS 'ID',
                       name AS 'Employee',
                       role AS 'Role',
                       phone AS 'Phone',
                       base_hourly_rate AS 'Base Rate',
                       rate_plus_10 AS 'Rate + 10%',
                       status AS 'Status',
                       notes AS 'Notes'
                FROM employees
                WHERE status = 'Active'
                ORDER BY name
            """)

        if df.empty:
            if show_inactive_workers:
                st.info("No employees found.")
            else:
                st.info("No active employees found. Tick 'Show inactive workers' to view inactive records.")
        else:
            st.dataframe(df, width="stretch", hide_index=True)

            st.markdown("### Remove Multiple Employees")
            st.warning(
                "This deletes the selected employee and linked user login account where safe. "
                "If an employee has wages or timesheets, the linked login will be deleted and the employee will be marked Inactive instead."
            )

            employee_delete_options = {
                f"{row['Employee']} | {row['Role'] or 'No Role'} | {row['Status']} | ID {row['ID']}": int(row["ID"])
                for _, row in df.iterrows()
            }

            selected_employee_labels = st.multiselect(
                "Select employees to delete or deactivate",
                list(employee_delete_options.keys()),
                key="bulk_employee_delete_multiselect"
            )

            selected_employee_ids = [employee_delete_options[label] for label in selected_employee_labels]

            if selected_employee_ids:
                selected_preview = df[df["ID"].astype(int).isin(selected_employee_ids)]
                st.markdown("Selected employees:")
                st.dataframe(selected_preview, width="stretch", hide_index=True)

            employee_bulk_confirm = st.text_input(
                "To delete/deactivate the selected employees, type: DELETE EMPLOYEES",
                key="bulk_employee_delete_confirm"
            )

            if st.button("Delete / Deactivate Selected Employees", key="bulk_employee_delete_button"):
                if not selected_employee_ids:
                    pb_error("Select at least one employee first.")
                elif employee_bulk_confirm.strip().upper() != "DELETE EMPLOYEES":
                    pb_error("Type DELETE EMPLOYEES exactly before continuing.")
                else:
                    result = delete_or_deactivate_selected_employees(selected_employee_ids)

                    if result["deleted_users"]:
                        pb_success(f"Deleted {result['deleted_users']} linked user login account(s).")

                    if result["deleted_employee"]:
                        pb_success(f"Deleted {result['deleted_employee']} employee record(s).")

                    if result["deactivated_employee"]:
                        st.info(f"Marked {result['deactivated_employee']} employee(s) as Inactive because they had job history or protected linked records.")

                    if result["skipped"]:
                        st.warning(f"Skipped {result['skipped']} item(s).")

                    with st.expander("Employee delete/deactivate details"):
                        for msg in result["messages"]:
                            st.write(msg)

                    refresh()


# =============================
# PRODUCTS
# =============================
elif menu == "Products":
    st.header("Products")
    st.caption("Maintain the master product list used by Material Costs and material ordering.")

    product_summary = df_query("""
        SELECT COUNT(*) AS product_count,
               COUNT(DISTINCT COALESCE(NULLIF(TRIM(supplier), ''), 'Unspecified')) AS supplier_count
        FROM products
    """)
    if not product_summary.empty:
        metric_a, metric_b = st.columns(2)
        metric_a.metric("Saved products", int(product_summary.iloc[0]["product_count"] or 0))
        metric_b.metric("Suppliers / brands", int(product_summary.iloc[0]["supplier_count"] or 0))

    with st.expander("Import Product Pricing CSV", expanded=True):
        st.caption(
            "Upload a CSV containing Product Code, Product Name, Supplier, Unit, Price Ex GST and optional Notes. "
            "Matching product codes are updated; new product codes are added."
        )
        product_pricing_upload = st.file_uploader(
            "Choose product-pricing CSV",
            type=["csv"],
            key="product_pricing_csv_upload",
        )

        if product_pricing_upload is not None:
            try:
                if uploaded_file_size(product_pricing_upload) > MAX_CSV_UPLOAD_BYTES:
                    raise ValueError("CSV is larger than the 5 MB upload limit.")
                product_pricing_upload.seek(0)
                product_preview_df = pd.read_csv(product_pricing_upload).fillna("")
                if len(product_preview_df) > MAX_CSV_IMPORT_ROWS:
                    raise ValueError(f"CSV contains more than {MAX_CSV_IMPORT_ROWS:,} rows.")

                normalised_preview = normalise_product_pricing_dataframe(product_preview_df)
                preview_display = pd.DataFrame(
                    normalised_preview,
                    columns=["Product Code", "Product Name", "Supplier", "Unit", "Price Ex GST", "Notes"],
                )
                st.dataframe(preview_display.head(30), width="stretch", hide_index=True)
                st.caption(
                    f"{len(preview_display):,} unique product(s) detected. "
                    "Duplicate codes within the CSV use the last matching row."
                )

                product_import_confirm = st.checkbox(
                    "I have reviewed the product codes and prices.",
                    key="product_pricing_import_reviewed",
                )
                if st.button(
                    "Import / Update Product Pricing",
                    type="primary",
                    key="product_pricing_import_button",
                ):
                    if not product_import_confirm:
                        pb_error("Review the pricing and tick the confirmation box before importing.")
                    else:
                        result = import_product_pricing_dataframe(
                            product_preview_df,
                            source_file_name=product_pricing_upload.name,
                        )
                        pb_success(
                            f"Product pricing imported: {result['inserted']} added, "
                            f"{result['updated']} updated, {result['total']} processed."
                        )
                        refresh()
            except Exception as exc:
                pb_error(f"Could not read or import this product-pricing CSV: {exc}")

        current_products_export = df_query("""
            SELECT product_code AS 'Product Code',
                   product_name AS 'Product Name',
                   supplier AS 'Supplier',
                   unit AS 'Unit',
                   price_ex_gst AS 'Price Ex GST',
                   notes AS 'Notes'
            FROM products
            ORDER BY supplier, product_code
        """)
        if not current_products_export.empty:
            st.download_button(
                "Export Current Product List",
                data=current_products_export.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"PB_JobHub_Product_List_{date.today().isoformat()}.csv",
                mime="text/csv",
                key="products_export_current_csv",
            )

    with st.expander("Add / Update One Product", expanded=False):
        with st.form("product_form"):
            col1, col2 = st.columns(2)
            code = col1.text_input("Product Code")
            product_name = col2.text_input("Product Name")
            col3, col4, col5 = st.columns(3)
            supplier = col3.text_input("Supplier")
            unit = col4.text_input("Unit")
            price = col5.number_input("Price Ex GST", min_value=0.0, step=1.0)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Product")

            if submitted and code:
                execute("""
                    INSERT INTO products
                    (product_code, product_name, supplier, unit, price_ex_gst, notes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(product_code) DO UPDATE SET
                        product_name = excluded.product_name,
                        supplier = excluded.supplier,
                        unit = excluded.unit,
                        price_ex_gst = excluded.price_ex_gst,
                        notes = excluded.notes
                """, (code, product_name, supplier, unit, price, notes))
                record_audit_event("product_upserted", "product", code)
                pb_success(f"Saved product {code}")
                refresh()

    product_search = st.text_input(
        "Search products",
        placeholder="Product code, description or supplier",
        key="products_search_text",
    ).strip().lower()

    df = df_query("""
        SELECT product_code AS 'Product Code',
               product_name AS 'Product Name',
               supplier AS 'Supplier',
               unit AS 'Unit',
               price_ex_gst AS 'Price Ex GST',
               notes AS 'Notes'
        FROM products
        ORDER BY supplier, product_code
    """)

    if product_search and not df.empty:
        haystack = (
            df["Product Code"].fillna("").astype(str) + " " +
            df["Product Name"].fillna("").astype(str) + " " +
            df["Supplier"].fillna("").astype(str)
        ).str.lower()
        df = df[haystack.str.contains(product_search, na=False, regex=False)]

    st.caption(f"Showing {len(df):,} product(s).")
    st.dataframe(df, width="stretch", hide_index=True)


# =============================
# MATERIAL COSTS
# =============================
elif menu == "Staff Scheduler":
    render_jobhub_staff_scheduler(get_current_user() or {})


elif menu == "Material Costs":
    st.header("Material Costs")
    st.caption("Use saved products from the database, or add one-off materials that are not added to the master product list.")

    job_options = get_job_options()

    if not job_options:
        st.info("Create a job first.")
    else:
        with st.expander("Material Order History & Deliveries", expanded=True):
            st.caption("Review purchase orders already created and mark complete orders as delivered.")
            order_history = df_query("""
                SELECT po.id AS "ID",
                       po.po_no AS "PO Number",
                       j.job_no AS "Job No",
                       j.job_name AS "Job Name",
                       po.supplier AS "Supplier",
                       po.status AS "Status",
                       po.order_date AS "Order Date",
                       po.expected_date AS "Expected Date",
                       po.requested_by AS "Requested By",
                       po.subtotal_ex_gst AS "Subtotal Ex GST",
                       po.total_inc_gst AS "Total Inc GST",
                       po.notes AS "Notes"
                FROM purchase_orders po
                JOIN jobs j ON j.id = po.job_id
                ORDER BY po.id DESC
            """)
            if order_history.empty:
                st.info("No material purchase orders have been created yet.")
            else:
                f1, f2 = st.columns(2)
                job_filter_options = ["All jobs"] + sorted(order_history["Job No"].dropna().astype(str).unique().tolist())
                status_filter_options = ["All statuses"] + sorted(order_history["Status"].dropna().astype(str).unique().tolist())
                order_job_filter = f1.selectbox("Show job", job_filter_options, key="material_order_history_job")
                order_status_filter = f2.selectbox("Show status", status_filter_options, key="material_order_history_status")
                filtered_orders = order_history.copy()
                if order_job_filter != "All jobs":
                    filtered_orders = filtered_orders[filtered_orders["Job No"].astype(str) == order_job_filter]
                if order_status_filter != "All statuses":
                    filtered_orders = filtered_orders[filtered_orders["Status"].astype(str) == order_status_filter]

                st.dataframe(
                    filtered_orders.drop(columns=["ID"]),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Subtotal Ex GST": st.column_config.NumberColumn(format="$%.2f"),
                        "Total Inc GST": st.column_config.NumberColumn(format="$%.2f"),
                    },
                )
                if filtered_orders.empty:
                    st.info("No material orders match those filters.")
                else:
                    order_labels = {
                        (
                            f"{row['PO Number']} · {row['Job No']} {row['Job Name']} · "
                            f"{row['Supplier']} · {row['Status']}"
                        ): int(row["ID"])
                        for _, row in filtered_orders.iterrows()
                    }
                    selected_order_label = st.selectbox(
                        "Review material order",
                        list(order_labels),
                        key="material_order_history_select",
                    )
                    selected_order_id = order_labels[selected_order_label]
                    selected_order = order_history[order_history["ID"] == selected_order_id].iloc[0]
                    order_lines = df_query("""
                        SELECT product_code AS "Product Code",
                               description AS "Product / Material",
                               colour AS "Colour",
                               qty AS "Ordered Qty",
                               received_qty AS "Received Qty",
                               unit AS "Unit",
                               unit_price_ex_gst AS "Unit Price Ex GST",
                               line_total_ex_gst AS "Line Total Ex GST",
                               notes AS "Notes"
                        FROM purchase_order_lines
                        WHERE purchase_order_id = ?
                        ORDER BY id
                    """, (selected_order_id,))
                    st.markdown(f"#### {selected_order['PO Number']} · {selected_order['Supplier']}")
                    st.dataframe(
                        order_lines,
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "Unit Price Ex GST": st.column_config.NumberColumn(format="$%.2f"),
                            "Line Total Ex GST": st.column_config.NumberColumn(format="$%.2f"),
                        },
                    )
                    delivered = str(selected_order["Status"] or "").strip().lower() in {"received", "closed", "delivered"}
                    if delivered:
                        pb_success("This material order is marked as delivered.")
                    elif st.button(
                        "Mark Entire Order as Delivered",
                        key=f"material_order_delivered_{selected_order_id}",
                        type="primary",
                        use_container_width=True,
                    ):
                        execute(
                            "UPDATE purchase_order_lines SET received_qty = qty WHERE purchase_order_id = ?",
                            (selected_order_id,),
                        )
                        execute(
                            """
                            UPDATE material_entries
                            SET qty_received = qty_required
                            WHERE id IN (
                                SELECT material_entry_id
                                FROM purchase_order_lines
                                WHERE purchase_order_id = ?
                                  AND material_entry_id IS NOT NULL
                            )
                            """,
                            (selected_order_id,),
                        )
                        execute(
                            "UPDATE purchase_orders SET status = ?, updated_at = ? WHERE id = ?",
                            ("Received", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), selected_order_id),
                        )
                        record_audit_event(
                            "purchase_order_marked_delivered",
                            "purchase_order",
                            selected_order_id,
                            {
                                "po_number": str(selected_order["PO Number"]),
                                "job_no": str(selected_order["Job No"]),
                                "marked_by": current_username(),
                            },
                        )
                        create_management_notifications(
                            "purchase_order_delivered",
                            f"Material order {selected_order['PO Number']} delivered",
                            (
                                f"{current_username()} marked {selected_order['PO Number']} for "
                                f"{selected_order['Job No']} {selected_order['Job Name']} as delivered."
                            ),
                            entity_type="purchase_order",
                            entity_id=selected_order_id,
                        )
                        pb_success("Material order marked as delivered and received quantities updated.")
                        refresh()

        with st.expander("Add Material Entry", expanded=True):
            job_label = st.selectbox("Job", list(job_options.keys()), key="material_job_select")
            selected_material_job_id = job_options[job_label]
            material_policy = get_job_material_policy(selected_material_job_id)
            material_admin_override = False
            material_admin_override_reason = ""
            if material_policy["restricted"]:
                supplier_text = ", ".join(material_policy["suppliers"]) or "No suppliers selected"
                st.info(f"This job is restricted to: {supplier_text}.")
                material_admin_override = st.checkbox(
                    "Override this job's product restriction",
                    key=f"material_admin_override_{selected_material_job_id}",
                )
                if material_admin_override:
                    material_admin_override_reason = st.text_input(
                        "Override reason",
                        key=f"material_admin_override_reason_{selected_material_job_id}",
                    )

            allowed_suppliers = None
            if material_policy["restricted"] and not material_admin_override:
                allowed_suppliers = material_policy["suppliers"]

            product_code_options = get_product_options(allowed_suppliers)
            product_name_options = get_product_name_options(allowed_suppliers)

            entry_type_options = []
            if product_code_options:
                entry_type_options.append("Saved Product")
            if not material_policy["restricted"] or material_admin_override:
                entry_type_options.append("One-off / Not Listed")

            if not entry_type_options:
                st.warning("No saved products match this job's approved supplier list. Enable override or edit the job allocation.")
                entry_type = None
            else:
                entry_type = st.radio(
                    "Material entry type",
                    entry_type_options,
                    horizontal=True,
                    key="material_entry_type",
                )

            product_id = None
            matched_code = ""
            matched_name = ""
            matched_supplier = ""
            matched_unit = ""
            matched_price = 0.0
            matched_notes = ""

            if entry_type == "Saved Product":
                product_search_type = st.radio(
                    "Select product by",
                    ["Product Code", "Product Name"],
                    horizontal=True,
                    key="material_product_search_type",
                )

                if product_search_type == "Product Code":
                    selected_product = st.selectbox(
                        "Product Code",
                        list(product_code_options.keys()),
                        key="material_product_code_select",
                    )
                    product_id = product_code_options[selected_product]
                else:
                    selected_product = st.selectbox(
                        "Product Name",
                        list(product_name_options.keys()),
                        key="material_product_name_select",
                    )
                    product_id = product_name_options[selected_product]

                product = df_query("""
                    SELECT id, product_code, product_name, supplier, unit, price_ex_gst, notes
                    FROM products
                    WHERE id = ?
                """, (product_id,))

                if not product.empty:
                    product_row = product.iloc[0]
                    matched_code = str(product_row["product_code"] or "")
                    matched_name = str(product_row["product_name"] or "")
                    matched_supplier = str(product_row["supplier"] or "")
                    matched_unit = str(product_row["unit"] or "")
                    matched_price = float(product_row["price_ex_gst"] or 0)
                    matched_notes = str(product_row["notes"] or "")

                    pb_success(f"Selected product matches: {matched_code} — {matched_name}")

                    match_cols = st.columns(5)
                    match_cols[0].metric("Code", matched_code)
                    match_cols[1].metric("Product", matched_name[:28] + ("..." if len(matched_name) > 28 else ""))
                    match_cols[2].metric("Supplier", matched_supplier[:18] + ("..." if len(matched_supplier) > 18 else ""))
                    match_cols[3].metric("Unit", matched_unit)
                    match_cols[4].metric("Unit Ex GST", f"${matched_price:,.2f}")

                    with st.expander("View full matched product details"):
                        st.write({
                            "Product Code": matched_code,
                            "Product Name": matched_name,
                            "Supplier": matched_supplier,
                            "Unit": matched_unit,
                            "Price Ex GST": f"${matched_price:,.2f}",
                            "Notes": matched_notes,
                        })

            with st.form("material_form"):
                st.markdown("#### Save Material Entry")

                custom_product_code = ""
                custom_product_name = ""
                custom_supplier = ""
                custom_unit = ""
                custom_unit_price = None
                custom_colour = ""

                if entry_type == "One-off / Not Listed":
                    st.caption("This will be saved to this material cost entry only. It will not be added to the master product database.")

                    c1, c2 = st.columns(2)
                    custom_product_code = c1.text_input("Product Code / Ref", value="CUSTOM")
                    custom_product_name = c2.text_input("Product / Material Name")

                    c3, c4, c5 = st.columns(3)
                    custom_supplier = c3.text_input("Supplier")
                    custom_unit = c4.text_input("Unit", value="each")
                    custom_unit_price = c5.number_input("Unit Price Ex GST", min_value=0.0, step=1.0)

                    custom_colour = st.text_input("Colour / Finish")
                    display_product_name = custom_product_name
                    display_unit_price = custom_unit_price or 0
                    default_supplier = custom_supplier

                else:
                    st.caption(f"This entry will be saved against **{job_label}** using **{matched_code} — {matched_name}**.")
                    display_product_name = matched_name
                    display_unit_price = matched_price
                    default_supplier = matched_supplier

                col1, col2, col3 = st.columns(3)
                qty_required = col1.number_input("Qty Required", min_value=0.0, step=1.0)
                qty_received = col2.number_input("Qty Received", min_value=0.0, step=1.0)
                date_ordered = col3.text_input("Date Ordered", value=str(date.today()))

                estimated_total = float(qty_required or 0) * float(display_unit_price or 0)
                st.info(f"Estimated material cost ex GST: ${estimated_total:,.2f}")

                supplier = st.text_input("Supplier Override", value=default_supplier)
                notes = st.text_area("Notes")
                submitted = st.form_submit_button("Save Material Entry")

                if submitted:
                    if entry_type is None:
                        pb_error("No product is available under this job's current supplier allocation.")
                    elif material_admin_override and not material_admin_override_reason.strip():
                        pb_error("Enter an override reason before saving a product outside the approved job supplier list.")
                    elif entry_type == "Saved Product" and not product_id:
                        pb_error("Select a saved product first.")
                    elif entry_type == "One-off / Not Listed" and not custom_product_name.strip():
                        pb_error("Enter a product/material name.")
                    else:
                        override_note = ""
                        if material_admin_override:
                            override_note = f" Product filter override: {material_admin_override_reason.strip()}."
                        execute("""
                            INSERT INTO material_entries
                            (
                                job_id,
                                product_id,
                                qty_required,
                                qty_received,
                                date_ordered,
                                supplier,
                                notes,
                                custom_product_code,
                                custom_product_name,
                                custom_supplier,
                                custom_unit,
                                custom_unit_price,
                                custom_colour
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            selected_material_job_id,
                            product_id,
                            qty_required,
                            qty_received,
                            date_ordered,
                            supplier,
                            f"{notes}{override_note}".strip(),
                            custom_product_code,
                            custom_product_name,
                            custom_supplier,
                            custom_unit,
                            custom_unit_price,
                            custom_colour,
                        ))

                        if material_admin_override:
                            record_audit_event(
                                "job_material_filter_overridden",
                                "job",
                                selected_material_job_id,
                                {
                                    "reason": material_admin_override_reason.strip(),
                                    "product": display_product_name,
                                    "supplier": supplier,
                                },
                            )
                        create_management_notifications(
                            "paint_order_requested",
                            "Paint/material entry added",
                            (
                                f"{current_username()} added {float(qty_required or 0):g} {matched_unit or custom_unit or 'unit(s)'} "
                                f"of {display_product_name or 'material'} to {job_label}. "
                                f"Supplier: {supplier or 'Unspecified'}."
                                + (f" Override reason: {material_admin_override_reason.strip()}." if material_admin_override else "")
                            ),
                            job_id=selected_material_job_id,
                            entity_type="material_request",
                            entity_id="",
                        )
                        pb_success("Material entry saved. Nick and Bryce were notified.")
                        refresh()

    df = df_query("""
        SELECT m.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               COALESCE(NULLIF(m.custom_product_code, ''), p.product_code, '') AS 'Product Code',
               COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS 'Product Name',
               COALESCE(NULLIF(m.supplier, ''), NULLIF(m.custom_supplier, ''), p.supplier, '') AS 'Supplier',
               COALESCE(NULLIF(m.custom_unit, ''), p.unit, '') AS 'Unit',
               COALESCE(m.custom_unit_price, p.price_ex_gst, 0) AS 'Unit Price',
               COALESCE(NULLIF(m.custom_colour, ''), '') AS 'Colour / Finish',
               m.qty_required AS 'Qty Required',
               m.qty_received AS 'Qty Received',
               ROUND(CAST((COALESCE(m.custom_unit_price, p.price_ex_gst, 0) * COALESCE(m.qty_required, 0)) AS numeric), 2) AS 'Total Cost',
               m.date_ordered AS 'Date Ordered',
               m.notes AS 'Notes'
        FROM material_entries m
        JOIN jobs j ON j.id = m.job_id
        LEFT JOIN products p ON p.id = m.product_id
        ORDER BY m.id DESC
    """)
    st.markdown("### Material Cost Entries")
    st.caption("Click any line to select it, then edit or delete it below.")
    material_table_event = st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="selectable_material_cost_entries",
    )

    selected_material_rows = []
    material_selection = getattr(material_table_event, "selection", None)
    if material_selection is None and isinstance(material_table_event, dict):
        material_selection = material_table_event.get("selection")
    if material_selection is not None:
        selected_material_rows = getattr(material_selection, "rows", None)
        if selected_material_rows is None and isinstance(material_selection, dict):
            selected_material_rows = material_selection.get("rows", [])

    if df.empty:
        st.info("No material cost entries saved.")
    elif not selected_material_rows:
        st.info("Select a material line in the table to open its Edit and Delete controls.")
    else:
        selected_material_index = int(selected_material_rows[0])
        if 0 <= selected_material_index < len(df):
            selected_material = df.iloc[selected_material_index]
            selected_material_id = int(selected_material["ID"])
            st.markdown(
                f"#### Selected: {selected_material['Product Code']} — "
                f"{selected_material['Product Name']}"
            )
            st.caption(
                f"{selected_material['Job No']} · {selected_material['Job Name']} · "
                f"Material entry ID {selected_material_id}"
            )

            edit_tab, delete_tab = st.tabs(["Edit selected line", "Delete selected line"])
            with edit_tab:
                with st.form(f"edit_material_entry_{selected_material_id}"):
                    e1, e2, e3 = st.columns(3)
                    edit_qty_required = e1.number_input(
                        "Qty Required",
                        min_value=0.0,
                        value=float(selected_material["Qty Required"] or 0),
                        step=1.0,
                    )
                    edit_qty_received = e2.number_input(
                        "Qty Received",
                        min_value=0.0,
                        value=float(selected_material["Qty Received"] or 0),
                        step=1.0,
                    )
                    edit_date_ordered = e3.text_input(
                        "Date Ordered",
                        value=str(selected_material["Date Ordered"] or ""),
                    )
                    e4, e5 = st.columns(2)
                    edit_supplier = e4.text_input(
                        "Supplier",
                        value=str(selected_material["Supplier"] or ""),
                    )
                    edit_colour = e5.text_input(
                        "Colour / Finish",
                        value=str(selected_material["Colour / Finish"] or ""),
                    )
                    edit_notes = st.text_area(
                        "Notes",
                        value=str(selected_material["Notes"] or ""),
                    )
                    if st.form_submit_button("Save Changes", type="primary"):
                        execute("""
                            UPDATE material_entries
                            SET qty_required = ?,
                                qty_received = ?,
                                date_ordered = ?,
                                supplier = ?,
                                custom_colour = ?,
                                notes = ?
                            WHERE id = ?
                        """, (
                            edit_qty_required,
                            edit_qty_received,
                            edit_date_ordered,
                            edit_supplier,
                            edit_colour,
                            edit_notes,
                            selected_material_id,
                        ))
                        record_audit_event(
                            "material_entry_updated",
                            "material_entry",
                            selected_material_id,
                            {"job_no": str(selected_material["Job No"])},
                        )
                        pb_success("Material line updated.")
                        refresh()

            with delete_tab:
                st.warning(
                    "This permanently deletes the selected material cost line. "
                    "It does not delete the product from the product list."
                )
                delete_selected_material_confirm = st.checkbox(
                    "Yes, delete this selected material line",
                    key=f"confirm_delete_material_{selected_material_id}",
                )
                if st.button(
                    "Delete Selected Line",
                    key=f"delete_material_{selected_material_id}",
                    type="primary",
                ):
                    if not delete_selected_material_confirm:
                        pb_error("Tick the confirmation box before deleting.")
                    else:
                        execute(
                            "DELETE FROM material_entries WHERE id = ?",
                            (selected_material_id,),
                        )
                        record_audit_event(
                            "material_entry_deleted",
                            "material_entry",
                            selected_material_id,
                            {
                                "job_no": str(selected_material["Job No"]),
                                "product": str(selected_material["Product Name"]),
                            },
                        )
                        pb_success("Material line deleted.")
                        refresh()
    imported_df = df_query("""
        SELECT im.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               im.product AS 'Product',
               im.colour AS 'Colour',
               im.qty_required AS 'Qty Required',
               im.qty_loaded AS 'Qty Loaded',
               im.source_file AS 'Source File',
               im.imported_at AS 'Imported At',
               im.notes AS 'Notes'
        FROM imported_material_entries im
        JOIN jobs j ON j.id = im.job_id
        ORDER BY im.id DESC
    """)

    st.markdown("### Imported PDF Checklist Material Lines")
    if imported_df.empty:
        st.info("No imported PDF material lines saved.")
    else:
        st.dataframe(imported_df, width="stretch", hide_index=True)

        st.markdown("### Delete Imported PDF Material Lines")
        st.caption("Use this for wrongly imported PDF checklist material lines.")

        imported_options = {
            f"ID {row['ID']} | {row['Job No']} - {row['Job Name']} | {row['Product']} | Colour {row['Colour']} | Qty {row['Qty Required']}": int(row["ID"])
            for _, row in imported_df.iterrows()
        }

        selected_imported_labels = st.multiselect(
            "Select imported PDF material lines to delete",
            list(imported_options.keys()),
            key="delete_imported_material_entries_select"
        )
        selected_imported_ids = [imported_options[label] for label in selected_imported_labels]

        delete_imported_confirm = st.text_input(
            "To delete selected imported PDF material lines, type: DELETE IMPORTED MATERIALS",
            key="delete_imported_material_entries_confirm"
        )

        if st.button("Delete Selected Imported PDF Material Lines", key="delete_imported_material_entries_button"):
            if not selected_imported_ids:
                pb_error("Select at least one imported PDF material line first.")
            elif delete_imported_confirm.strip().upper() != "DELETE IMPORTED MATERIALS":
                pb_error("Type DELETE IMPORTED MATERIALS exactly before deleting imported material lines.")
            else:
                for imported_id in selected_imported_ids:
                    execute("DELETE FROM imported_material_entries WHERE id = ?", (int(imported_id),))
                pb_success(f"Deleted {len(selected_imported_ids)} imported PDF material line{'s' if len(selected_imported_ids) != 1 else ''}.")
                refresh()


# =============================
# WAGES
# =============================
elif menu == "Wages":
    st.header("Wages")

    job_options = get_job_options()
    employee_options = get_employee_options(active_only=True)

    if not job_options or not employee_options:
        st.info("Create jobs and active employees first.")
    else:
        with st.expander("Add Wage Entry", expanded=True):
            with st.form("wage_form"):
                job_label = st.selectbox("Job", list(job_options.keys()))
                employee_name = st.selectbox("Employee", list(employee_options.keys()))
                employee_id = employee_options[employee_name]

                employee = df_query("SELECT base_hourly_rate, rate_plus_10 FROM employees WHERE id = ?", (employee_id,))
                if not employee.empty:
                    st.info(
                        f"Base Rate: ${float(employee.iloc[0]['base_hourly_rate'] or 0):.2f} | "
                        f"Rate + 10%: ${float(employee.iloc[0]['rate_plus_10'] or 0):.2f}"
                    )

                col1, col2 = st.columns(2)
                work_date = col1.text_input("Date", value=str(date.today()))
                hours = col2.number_input("Hours", min_value=0.0, step=0.5)
                notes = st.text_area("Notes")
                submitted = st.form_submit_button("Save Wage Entry")

                if submitted:
                    execute("""
                        INSERT INTO wage_entries
                        (job_id, employee_id, work_date, hours, notes)
                        VALUES (?, ?, ?, ?, ?)
                    """, (job_options[job_label], employee_id, work_date, hours, notes))
                    pb_success("Wage entry saved.")
                    refresh()

    df = df_query("""
        SELECT w.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               e.name AS 'Employee',
               w.work_date AS 'Date',
               w.hours AS 'Hours',
               e.base_hourly_rate AS 'Base Rate',
               e.rate_plus_10 AS 'Rate + 10%',
               COALESCE(w.hours, 0) *
               COALESCE(NULLIF(w.hourly_rate_snapshot, 0), e.rate_plus_10, e.base_hourly_rate, 0)
                   AS 'Total Wage Cost',
               w.notes AS 'Notes'
        FROM wage_entries w
        JOIN jobs j ON j.id = w.job_id
        JOIN employees e ON e.id = w.employee_id
        ORDER BY w.work_date DESC, w.id DESC
    """)
    st.dataframe(df, width="stretch", hide_index=True)

    st.markdown("### Delete Wage Entries")
    st.caption("Use this for wrong duplicate or accidental wage entries. This deletes wage rows only; it does not delete any timesheet record.")
    if df.empty:
        st.info("No wage entries to delete.")
    else:
        wage_options = {
            f"ID {row['ID']} | {row['Date']} | {row['Employee']} | {row['Job No']} - {row['Job Name']} | {row['Hours']} hrs | ${float(row['Total Wage Cost'] or 0):,.2f}": int(row["ID"])
            for _, row in df.iterrows()
        }
        selected_wage_labels = st.multiselect(
            "Select wage entries to delete",
            list(wage_options.keys()),
            key="delete_wage_entries_select"
        )
        selected_wage_ids = [wage_options[label] for label in selected_wage_labels]

        delete_wages_confirm = st.text_input(
            "To delete the selected wage entries, type: DELETE WAGES",
            key="delete_wage_entries_confirm"
        )

        if st.button("Delete Selected Wage Entries", key="delete_wage_entries_button"):
            if not selected_wage_ids:
                pb_error("Select at least one wage entry first.")
            elif delete_wages_confirm.strip().upper() != "DELETE WAGES":
                pb_error("Type DELETE WAGES exactly before deleting wage entries.")
            else:
                for wage_id in selected_wage_ids:
                    execute("DELETE FROM wage_entries WHERE id = ?", (int(wage_id),))
                pb_success(f"Deleted {len(selected_wage_ids)} wage entr{'y' if len(selected_wage_ids) == 1 else 'ies'}.")
                refresh()


# =============================
# EQUIPMENT CHECKLIST
# =============================
elif menu == "Timesheets":
    timesheets_page(employee_restricted=False)


elif menu == "Equipment":
    st.header("Equipment")

    job_options = get_job_options()

    tab_import, tab_checklist, tab_master, tab_saved, tab_items = st.tabs(
        ["Import Filled PDF Checklist", "Job Equipment Checklist", "Job Equipment Master List", "All Saved Equipment", "Manage Checklist Items"]
    )

    with tab_import:
        st.subheader("Import Filled Master Site Checklist PDF")
        st.caption("Upload the completed fillable PDF checklist and assign it to the correct job. Imported quantities will save to that selected job only.")

        if not job_options:
            st.info("Create a job first, then import the checklist.")
        else:
            uploaded_checklist = st.file_uploader("Upload completed Master Site Checklist PDF", type=["pdf"])

            if uploaded_checklist is not None:
                try:
                    job_info, import_equipment_df, import_materials_df = parse_master_checklist_pdf(uploaded_checklist)

                    st.markdown("### Details found in PDF")
                    preview_details = pd.DataFrame([job_info])
                    st.dataframe(preview_details, width="stretch", hide_index=True)

                    suggested_job = None
                    if job_info.get("job_number"):
                        for label in job_options:
                            if label.startswith(job_info["job_number"]):
                                suggested_job = label
                                break
                    if suggested_job is None and job_info.get("job_name"):
                        for label in job_options:
                            if job_info["job_name"].lower() in label.lower():
                                suggested_job = label
                                break

                    job_labels = list(job_options.keys())
                    default_index = job_labels.index(suggested_job) if suggested_job in job_labels else 0

                    selected_import_job = st.selectbox(
                        "Import this checklist against job",
                        job_labels,
                        index=default_index,
                        key="pdf_import_job_select"
                    )

                    update_job = st.checkbox("Update job details from the PDF where provided", value=True)
                    replace_materials = st.checkbox("Replace existing imported PDF material lines for this job", value=True)

                    st.markdown("### Equipment / Consumables found")
                    if import_equipment_df.empty:
                        st.info("No equipment or consumable quantities found in the PDF.")
                    else:
                        st.dataframe(import_equipment_df, width="stretch", hide_index=True)

                    st.markdown("### Paint & Materials Register found")
                    if import_materials_df.empty:
                        st.info("No paint/material register lines found in the PDF.")
                    else:
                        st.dataframe(import_materials_df, width="stretch", hide_index=True)

                    if st.button("Import Checklist Into Selected Job"):
                        equipment_count, material_count = import_master_checklist_to_job(
                            job_id=job_options[selected_import_job],
                            job_info=job_info,
                            equipment_df=import_equipment_df,
                            materials_df=import_materials_df,
                            source_file=uploaded_checklist.name,
                            update_job=update_job,
                            replace_imported_materials=replace_materials,
                        )

                        pb_success(
                            f"Imported checklist into {selected_import_job}. "
                            f"Equipment/consumable lines saved: {equipment_count}. "
                            f"Paint/material lines saved: {material_count}."
                        )
                        st.info("You can now view this under Job Equipment Master List and Reports / Export > Job Pack by Job.")
                        refresh()

                except Exception as e:
                    pb_error(f"Could not import this PDF checklist: {e}")


    with tab_checklist:
        st.subheader("Fill Out Equipment Checklist")
        if not job_options:
            st.info("Create a job first.")
        else:
            selected_job_label = st.selectbox("Select Job", list(job_options.keys()), key="equipment_job")
            selected_job_id = job_options[selected_job_label]

            items_df = df_query("""
                SELECT id, category, item_name, default_qty, notes
                FROM equipment_checklist_items
                ORDER BY category, item_name
            """)

            existing_df = df_query("""
                SELECT *
                FROM equipment_checklist_records
                WHERE job_id = ?
            """, (selected_job_id,))

            existing_by_item = {}
            if not existing_df.empty:
                existing_by_item = {int(row["checklist_item_id"]): row for _, row in existing_df.iterrows()}

            st.caption("This checklist saves directly against the selected job. The Job Equipment Master List totals everything for that same job.")

            with st.form("equipment_checklist_form"):
                save_rows = []

                categories = list(items_df["category"].dropna().unique())

                for category in categories:
                    st.markdown(f"### {category}")
                    category_items = items_df[items_df["category"] == category]

                    for _, item in category_items.iterrows():
                        item_id = int(item["id"])
                        existing = existing_by_item.get(item_id)

                        item_name = str(item["item_name"])
                        default_qty = float(item["default_qty"] or 0)

                        req_default = bool(existing["is_required"]) if existing is not None else False
                        packed_default = bool(existing["is_packed"]) if existing is not None else False
                        returned_default = bool(existing["is_returned"]) if existing is not None else False
                        qty_req_default = float(existing["qty_required"] or default_qty) if existing is not None else default_qty
                        qty_taken_default = float(existing["qty_taken"] or 0) if existing is not None else 0.0
                        qty_returned_default = float(existing["qty_returned"] or 0) if existing is not None else 0.0

                        cols = st.columns([3, 1, 1, 1, 1, 1])
                        required = cols[0].checkbox(item_name, value=req_default, key=f"required_{selected_job_id}_{item_id}")
                        qty_required = cols[1].number_input("Req", min_value=0.0, value=qty_req_default, step=1.0, key=f"qty_required_{selected_job_id}_{item_id}")
                        qty_taken = cols[2].number_input("Out", min_value=0.0, value=qty_taken_default, step=1.0, key=f"qty_taken_{selected_job_id}_{item_id}")
                        qty_returned = cols[3].number_input("Back", min_value=0.0, value=qty_returned_default, step=1.0, key=f"qty_returned_{selected_job_id}_{item_id}")
                        packed = cols[4].checkbox("Packed", value=packed_default, key=f"packed_{selected_job_id}_{item_id}")
                        returned = cols[5].checkbox("Returned", value=returned_default, key=f"returned_{selected_job_id}_{item_id}")

                        save_rows.append({
                            "job_id": selected_job_id,
                            "item_id": item_id,
                            "qty_required": qty_required,
                            "qty_taken": qty_taken,
                            "qty_returned": qty_returned,
                            "is_required": 1 if required else 0,
                            "is_packed": 1 if packed else 0,
                            "is_returned": 1 if returned else 0,
                        })

                st.markdown("### Sign Out / Return Details")
                col_a, col_b, col_c, col_d = st.columns(4)
                date_out = col_a.text_input("Date Out", value=str(date.today()))
                date_in = col_b.text_input("Date In")
                taken_by = col_c.text_input("Taken By")
                returned_by = col_d.text_input("Returned By")

                col_e, col_f = st.columns(2)
                condition_out = col_e.text_input("Condition Out")
                condition_in = col_f.text_input("Condition In")
                notes = st.text_area("Notes")

                submitted = st.form_submit_button("Save Equipment Checklist to Job")

                if submitted:
                    for row in save_rows:
                        should_save = (
                            row["is_required"] == 1
                            or row["is_packed"] == 1
                            or row["is_returned"] == 1
                            or row["qty_taken"] > 0
                            or row["qty_returned"] > 0
                        )

                        existing = df_query("""
                            SELECT id FROM equipment_checklist_records
                            WHERE job_id = ? AND checklist_item_id = ?
                            ORDER BY id ASC
                        """, (row["job_id"], row["item_id"]))

                        if should_save:
                            if existing.empty:
                                execute("""
                                    INSERT INTO equipment_checklist_records
                                    (job_id, checklist_item_id, qty_required, qty_taken, qty_returned,
                                     is_required, is_packed, is_returned, date_out, date_in, taken_by, returned_by,
                                     condition_out, condition_in, notes)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    row["job_id"], row["item_id"], row["qty_required"], row["qty_taken"], row["qty_returned"],
                                    row["is_required"], row["is_packed"], row["is_returned"], date_out, date_in, taken_by, returned_by,
                                    condition_out, condition_in, notes
                                ))
                            else:
                                keep_id = int(existing.iloc[0]["id"])
                                execute("""
                                    UPDATE equipment_checklist_records
                                    SET qty_required = ?, qty_taken = ?, qty_returned = ?,
                                        is_required = ?, is_packed = ?, is_returned = ?,
                                        date_out = ?, date_in = ?, taken_by = ?, returned_by = ?,
                                        condition_out = ?, condition_in = ?, notes = ?
                                    WHERE id = ?
                                """, (
                                    row["qty_required"], row["qty_taken"], row["qty_returned"],
                                    row["is_required"], row["is_packed"], row["is_returned"],
                                    date_out, date_in, taken_by, returned_by,
                                    condition_out, condition_in, notes, keep_id
                                ))

                                # Remove duplicates if an older database allowed them
                                for dup_id in list(existing["id"])[1:]:
                                    execute("DELETE FROM equipment_checklist_records WHERE id = ?", (int(dup_id),))
                        else:
                            if not existing.empty:
                                for old_id in list(existing["id"]):
                                    execute("DELETE FROM equipment_checklist_records WHERE id = ?", (int(old_id),))

                    pb_success("Equipment checklist saved to the selected job.")
                    refresh()

    with tab_master:
        st.subheader("Job Equipment Master List")
        if not job_options:
            st.info("Create a job first.")
        else:
            selected_job_label = st.selectbox("Select Job for Master List", list(job_options.keys()), key="equipment_master_job")
            selected_job_id = job_options[selected_job_label]

            master_df = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       i.category AS 'Category',
                       i.item_name AS 'Equipment Item',
                       COALESCE(SUM(r.qty_required), 0) AS 'Total Required',
                       COALESCE(SUM(r.qty_taken), 0) AS 'Total Taken',
                       COALESCE(SUM(r.qty_returned), 0) AS 'Total Returned',
                       COALESCE(SUM(r.qty_taken - r.qty_returned), 0) AS 'Still Out',
                       COALESCE(MAX(r.date_out), '') AS 'Last Date Out',
                       COALESCE(MAX(r.date_in), '') AS 'Last Date In',
                       COALESCE(MAX(r.taken_by), '') AS 'Taken By',
                       COALESCE(MAX(r.returned_by), '') AS 'Returned By',
                       COALESCE(MAX(r.notes), '') AS 'Notes'
                FROM equipment_checklist_items i
                CROSS JOIN jobs j
                LEFT JOIN equipment_checklist_records r
                    ON r.checklist_item_id = i.id
                   AND r.job_id = j.id
                WHERE j.id = ?
                GROUP BY j.job_no, j.job_name, i.category, i.item_name
                ORDER BY i.category, i.item_name
            """, (selected_job_id,))

            if master_df.empty:
                st.info("No equipment checklist has been saved for this job yet.")
            else:
                st.dataframe(master_df, width="stretch", hide_index=True)

                total_taken = float(master_df["Total Taken"].fillna(0).sum())
                total_returned = float(master_df["Total Returned"].fillna(0).sum())
                still_out = float(master_df["Still Out"].fillna(0).sum())

                c1, c2, c3 = st.columns(3)
                c1.metric("Total Items Taken", total_taken)
                c2.metric("Total Items Returned", total_returned)
                c3.metric("Total Still Out", still_out)

                st.download_button(
                    "Download this Job Equipment Master List CSV",
                    data=master_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"equipment_master_list_{selected_job_label.split(' - ')[0]}.csv",
                    mime="text/csv",
                )

    with tab_saved:
        st.subheader("All Saved Equipment Checklist Records")
        all_df = df_query("""
            SELECT r.id AS 'Record ID',
                   j.job_no AS 'Job No',
                   j.job_name AS 'Job Name',
                   i.category AS 'Category',
                   i.item_name AS 'Equipment Item',
                   r.qty_required AS 'Qty Required',
                   r.qty_taken AS 'Qty Taken',
                   r.qty_returned AS 'Qty Returned',
                   CASE WHEN r.is_required = 1 THEN 'Yes' ELSE '' END AS 'Required',
                   CASE WHEN r.is_packed = 1 THEN 'Yes' ELSE '' END AS 'Packed',
                   CASE WHEN r.is_returned = 1 THEN 'Yes' ELSE '' END AS 'Returned',
                   r.date_out AS 'Date Out',
                   r.date_in AS 'Date In',
                   r.taken_by AS 'Taken By',
                   r.returned_by AS 'Returned By',
                   r.condition_out AS 'Condition Out',
                   r.condition_in AS 'Condition In',
                   r.notes AS 'Notes'
            FROM equipment_checklist_records r
            JOIN jobs j ON j.id = r.job_id
            JOIN equipment_checklist_items i ON i.id = r.checklist_item_id
            ORDER BY j.job_no, i.category, i.item_name
        """)
        if all_df.empty:
            st.info("No saved equipment records yet.")
        else:
            st.dataframe(all_df.drop(columns=["Record ID"]), width="stretch", hide_index=True)

            with st.expander("Delete Saved Equipment Line"):
                delete_map = {
                    f"{row['Job No']} - {row['Equipment Item']}": int(row["Record ID"])
                    for _, row in all_df.iterrows()
                }
                selected = st.selectbox("Select line to delete", list(delete_map.keys()))
                if st.button("Delete Selected Equipment Line"):
                    execute("DELETE FROM equipment_checklist_records WHERE id = ?", (delete_map[selected],))
                    pb_success("Equipment line deleted.")
                    refresh()

    with tab_items:
        st.subheader("Manage Checklist Items")
        with st.form("add_equipment_item_form"):
            col1, col2, col3 = st.columns(3)
            category = col1.text_input("Category")
            item_name = col2.text_input("Equipment Item")
            default_qty = col3.number_input("Default Qty", min_value=0.0, step=1.0, value=0.0)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Checklist Item")

            if submitted and item_name:
                execute("""
                    INSERT INTO equipment_checklist_items
                    (category, item_name, default_qty, notes)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(item_name) DO UPDATE SET
                        category = excluded.category,
                        default_qty = excluded.default_qty,
                        notes = excluded.notes
                """, (category, item_name, default_qty, notes))
                record_audit_event("equipment_item_upserted", "equipment_item", item_name)
                pb_success(f"Saved checklist item: {item_name}")
                refresh()

        items_df = df_query("""
            SELECT id,
                   category AS 'Category',
                   item_name AS 'Equipment Item',
                   default_qty AS 'Default Qty',
                   notes AS 'Notes'
            FROM equipment_checklist_items
            ORDER BY category, item_name
        """)
        st.dataframe(items_df.drop(columns=["id"]) if not items_df.empty else items_df, width="stretch", hide_index=True)


# =============================
# REPORTS
# =============================
elif menu == "Job Photos":
    job_photos_page(employee_restricted=False)


elif menu == "Reports / Export":
    st.header("Reports / Export")

    tab_job_pack, tab_reports = st.tabs(["Job Pack by Job", "General Reports"])

    with tab_job_pack:
        st.subheader("Produce Full Job Pack")

        job_options = get_job_options()

        if not job_options:
            st.info("No jobs found. Create a job first.")
        else:
            selected_job_label = st.selectbox(
                "Select Job Number / Job Name",
                list(job_options.keys()),
                key="job_pack_selector"
            )
            selected_job_id = job_options[selected_job_label]

            job_details = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       bc.name AS 'Builder / Client',
                       bc.contact_name AS 'Contact',
                       bc.phone AS 'Phone',
                       bc.email AS 'Email',
                       bc.terms AS 'Terms',
                       bc.qbcc AS 'Builder QBCC',
                       bc.abn AS 'Builder ABN',
                       j.site_address AS 'Site Address',
                       j.status AS 'Status',
                       j.leading_hand AS 'Leading Hand',
                       j.start_date AS 'Start Date',
                       j.end_date AS 'End Date',
                       j.contract_value AS 'Contract Value',
                       j.notes AS 'Notes'
                FROM jobs j
                LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
                WHERE j.id = ?
            """, (selected_job_id,))

            material_details = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       COALESCE(NULLIF(m.custom_product_code, ''), p.product_code, '') AS 'Product Code',
                       COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS 'Product Name',
                       COALESCE(NULLIF(m.supplier, ''), NULLIF(m.custom_supplier, ''), p.supplier, '') AS 'Supplier',
                       COALESCE(NULLIF(m.custom_unit, ''), p.unit, '') AS 'Unit',
                       COALESCE(m.custom_unit_price, p.price_ex_gst, 0) AS 'Unit Price Ex GST',
                       COALESCE(NULLIF(m.custom_colour, ''), '') AS 'Colour / Finish',
                       m.qty_required AS 'Qty Required',
                       m.qty_received AS 'Qty Received',
                       ROUND(CAST((COALESCE(m.custom_unit_price, p.price_ex_gst, 0) * COALESCE(m.qty_required, 0)) AS numeric), 2) AS 'Total Cost Ex GST',
                       m.date_ordered AS 'Date Ordered',
                       m.supplier AS 'Supplier Override',
                       m.notes AS 'Notes'
                FROM material_entries m
                JOIN jobs j ON j.id = m.job_id
                LEFT JOIN products p ON p.id = m.product_id
                WHERE j.id = ?
                ORDER BY m.id ASC
            """, (selected_job_id,))

            estimate_summary = df_query("""
                SELECT e.estimate_no AS 'Estimate No',
                       e.revision AS 'Revision',
                       e.estimate_date AS 'Date',
                       e.status AS 'Status',
                       e.labour_hours AS 'Labour Hours',
                       e.labour_rate AS 'Labour Rate',
                       e.material_allowance AS 'Material Allowance',
                       e.access_equipment_allowance AS 'Access / Equipment',
                       e.subcontractor_allowance AS 'Subcontractor',
                       e.sundries_allowance AS 'Sundries',
                       e.margin_percent AS 'Margin %',
                       e.contingency_percent AS 'Contingency %',
                       e.total_ex_gst AS 'Total Ex GST',
                       e.gst_amount AS 'GST',
                       e.total_inc_gst AS 'Total Inc GST',
                       e.notes AS 'Notes'
                FROM estimate_working_sheets e
                WHERE e.job_id = ?
                ORDER BY e.id DESC
            """, (selected_job_id,))

            estimate_lines = df_query("""
                SELECT e.estimate_no AS 'Estimate No',
                       l.section AS 'Section',
                       l.item_description AS 'Description',
                       l.qty AS 'Qty',
                       l.unit AS 'Unit',
                       COALESCE(l.estimated_labour_hours, 0) AS 'Estimated Labour Hours',
                       COALESCE(l.material_allowance, 0) AS 'Material Allowance',
                       l.substrate AS 'Substrate',
                       l.work_location AS 'Location',
                       l.coating_system AS 'Coating System',
                       l.colour_finish AS 'Colour / Finish',
                       l.unit_rate AS 'Unit Rate',
                       l.line_total AS 'Line Total',
                       l.source_pack AS 'Source Pack',
                       l.notes AS 'Notes'
                FROM estimate_line_items l
                JOIN estimate_working_sheets e ON e.id = l.estimate_id
                WHERE e.job_id = ?
                ORDER BY e.id DESC, l.id ASC
            """, (selected_job_id,))

            timesheet_details = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       e.name AS 'Employee',
                       t.work_date AS 'Date',
                       t.start_time AS 'Start',
                       t.finish_time AS 'Finish',
                       t.break_minutes AS 'Break Minutes',
                       t.total_hours AS 'Hours',
                       t.work_type AS 'Work Type',
                       t.status AS 'Status',
                       t.notes AS 'Notes'
                FROM timesheet_entries t
                JOIN jobs j ON j.id = t.job_id
                JOIN employees e ON e.id = t.employee_id
                WHERE j.id = ?
                ORDER BY t.work_date ASC, e.name ASC
            """, (selected_job_id,))

            wage_details = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       e.name AS 'Employee',
                       w.work_date AS 'Date',
                       w.hours AS 'Hours',
                       e.base_hourly_rate AS 'Base Rate',
                       e.rate_plus_10 AS 'Rate + 10%',
                       COALESCE(w.hours, 0) *
                       COALESCE(NULLIF(w.hourly_rate_snapshot, 0), e.rate_plus_10, e.base_hourly_rate, 0)
                           AS 'Total Wage Cost',
                       w.notes AS 'Notes'
                FROM wage_entries w
                JOIN jobs j ON j.id = w.job_id
                JOIN employees e ON e.id = w.employee_id
                WHERE j.id = ?
                ORDER BY w.work_date ASC, e.name ASC
            """, (selected_job_id,))

            timesheet_details = df_query("""
                SELECT j.job_no AS "Job No",
                       j.job_name AS "Job Name",
                       e.name AS "Employee",
                       t.work_date AS "Date",
                       t.start_time AS "Start",
                       t.finish_time AS "Finish",
                       t.break_minutes AS "Break Minutes",
                       t.total_hours AS "Hours",
                       t.work_type AS "Work Type",
                       t.status AS "Status",
                       t.submitted_by AS "Submitted By",
                       t.submitted_at AS "Submitted At",
                       t.notes AS "Notes"
                FROM timesheet_entries t
                JOIN jobs j ON j.id = t.job_id
                JOIN employees e ON e.id = t.employee_id
                WHERE j.id = ?
                ORDER BY t.work_date ASC, e.name ASC
            """, (selected_job_id,))

            equipment_master = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       i.category AS 'Category',
                       i.item_name AS 'Equipment Item',
                       COALESCE(SUM(r.qty_required), 0) AS 'Total Required',
                       COALESCE(SUM(r.qty_taken), 0) AS 'Total Taken',
                       COALESCE(SUM(r.qty_returned), 0) AS 'Total Returned',
                       COALESCE(SUM(r.qty_taken - r.qty_returned), 0) AS 'Still Out',
                       COALESCE(MAX(r.date_out), '') AS 'Last Date Out',
                       COALESCE(MAX(r.date_in), '') AS 'Last Date In',
                       COALESCE(MAX(r.taken_by), '') AS 'Taken By',
                       COALESCE(MAX(r.returned_by), '') AS 'Returned By',
                       COALESCE(MAX(r.condition_out), '') AS 'Condition Out',
                       COALESCE(MAX(r.condition_in), '') AS 'Condition In',
                       COALESCE(MAX(r.notes), '') AS 'Notes'
                FROM equipment_checklist_items i
                CROSS JOIN jobs j
                LEFT JOIN equipment_checklist_records r
                    ON r.checklist_item_id = i.id
                   AND r.job_id = j.id
                WHERE j.id = ?
                GROUP BY j.job_no, j.job_name, i.category, i.item_name
                ORDER BY i.category, i.item_name
            """, (selected_job_id,))

            equipment_detail = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       i.category AS 'Category',
                       i.item_name AS 'Equipment Item',
                       r.qty_required AS 'Qty Required',
                       r.qty_taken AS 'Qty Taken',
                       r.qty_returned AS 'Qty Returned',
                       CASE WHEN r.is_required = 1 THEN 'Yes' ELSE '' END AS 'Required',
                       CASE WHEN r.is_packed = 1 THEN 'Yes' ELSE '' END AS 'Packed',
                       CASE WHEN r.is_returned = 1 THEN 'Yes' ELSE '' END AS 'Returned',
                       r.date_out AS 'Date Out',
                       r.date_in AS 'Date In',
                       r.taken_by AS 'Taken By',
                       r.returned_by AS 'Returned By',
                       r.condition_out AS 'Condition Out',
                       r.condition_in AS 'Condition In',
                       r.notes AS 'Notes'
                FROM equipment_checklist_records r
                JOIN jobs j ON j.id = r.job_id
                JOIN equipment_checklist_items i ON i.id = r.checklist_item_id
                WHERE j.id = ?
                ORDER BY i.category, i.item_name
            """, (selected_job_id,))

            imported_materials = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       im.product AS 'Product',
                       im.colour AS 'Colour',
                       im.qty_required AS 'Qty Required',
                       im.qty_loaded AS 'Qty Loaded',
                       im.source_file AS 'Source File',
                       im.imported_at AS 'Imported At',
                       im.notes AS 'Notes'
                FROM imported_material_entries im
                JOIN jobs j ON j.id = im.job_id
                WHERE j.id = ?
                ORDER BY im.id ASC
            """, (selected_job_id,))

            job_photos_meta = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       jp.id AS 'Photo ID',
                       jp.photo_name AS 'Photo Name',
                       jp.category AS 'Category',
                       jp.caption AS 'Caption',
                       jp.uploaded_by AS 'Uploaded By',
                       jp.uploaded_at AS 'Uploaded At',
                       jp.notes AS 'Notes'
                FROM job_photos jp
                JOIN jobs j ON j.id = jp.job_id
                WHERE j.id = ?
                ORDER BY jp.uploaded_at DESC, jp.id DESC
            """, (selected_job_id,))

            job_photos_full = df_query("""
                SELECT id, photo_name, photo_type, photo_data, category, caption, uploaded_by, uploaded_at, notes
                FROM job_photos
                WHERE job_id = ?
                ORDER BY uploaded_at DESC, id DESC
            """, (selected_job_id,))

            material_total = float(material_details["Total Cost Ex GST"].fillna(0).sum()) if not material_details.empty else 0.0
            wage_total = float(wage_details["Total Wage Cost"].fillna(0).sum()) if not wage_details.empty else 0.0
            equipment_still_out = float(equipment_master["Still Out"].fillna(0).sum()) if not equipment_master.empty else 0.0

            col1, col2, col3 = st.columns(3)
            col1.metric("Material Cost Ex GST", f"${material_total:,.2f}")
            col2.metric("Wage Cost", f"${wage_total:,.2f}")
            col3.metric("Equipment Still Out", f"{equipment_still_out:g}")

            st.markdown("### Job Details")
            st.dataframe(job_details, width="stretch", hide_index=True)

            st.markdown("### Estimate Working Sheets for this Job")
            if estimate_summary.empty:
                st.info("No estimate working sheets saved for this job.")
            else:
                st.dataframe(estimate_summary, width="stretch", hide_index=True)

            st.markdown("### Estimate Line Items for this Job")
            if estimate_lines.empty:
                st.info("No estimate line items saved for this job.")
            else:
                st.dataframe(estimate_lines, width="stretch", hide_index=True)

            st.markdown("### Timesheets for this Job")
            if timesheet_details.empty:
                st.info("No timesheets saved for this job.")
            else:
                st.metric(
                    "Total Timesheet Hours",
                    f"{float(timesheet_details['Hours'].fillna(0).sum()):.2f}",
                )
                st.dataframe(
                    timesheet_details,
                    width="stretch",
                    hide_index=True,
                    key="all_data_timesheet_details",
                )

            st.markdown("### Material Costs for this Job")
            if material_details.empty:
                st.info("No material cost entries saved for this job.")
            else:
                st.dataframe(material_details, width="stretch", hide_index=True)

            st.markdown("### Imported Checklist Paint & Materials for this Job")
            if imported_materials.empty:
                st.info("No imported checklist paint/material lines saved for this job.")
            else:
                st.dataframe(imported_materials, width="stretch", hide_index=True)

            st.markdown("### Wages for this Job")
            if wage_details.empty:
                st.info("No wage entries saved for this job.")
            else:
                st.dataframe(wage_details, width="stretch", hide_index=True)

            st.markdown("### Equipment Master List for this Job")
            if equipment_master.empty:
                st.info("No equipment checklist entries saved for this job.")
            else:
                st.dataframe(equipment_master, width="stretch", hide_index=True)

            st.markdown("### Equipment Checklist Detail for this Job")
            if equipment_detail.empty:
                st.info("No equipment checklist detail saved for this job.")
            else:
                st.dataframe(equipment_detail, width="stretch", hide_index=True)

            st.markdown("### Job Photos for this Job")
            if job_photos_meta.empty:
                st.info("No photos saved for this job.")
            else:
                st.dataframe(job_photos_meta, width="stretch", hide_index=True)

                with st.expander("View Photo Gallery"):
                    for _, photo_row in job_photos_full.iterrows():
                        title_parts = [
                            str(photo_row["category"] or ""),
                            str(photo_row["caption"] or photo_row["photo_name"] or ""),
                        ]
                        st.markdown("#### " + " - ".join([p for p in title_parts if p]))
                        try:
                            st.image(photo_data_to_bytes(photo_row["photo_data"]), width="stretch")
                        except Exception:
                            st.warning("Could not display photo.")
                        st.caption(f"Uploaded: {photo_row['uploaded_at']} by {photo_row['uploaded_by']}")

            # Create a full Excel job pack with one sheet per document/report
            output = BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                job_details.to_excel(writer, index=False, sheet_name="Job Details")
                material_details.to_excel(writer, index=False, sheet_name="Materials")
                imported_materials.to_excel(writer, index=False, sheet_name="Imported Materials")
                job_photos_meta.to_excel(writer, index=False, sheet_name="Job Photos")
                timesheet_details.to_excel(writer, index=False, sheet_name="Timesheets")
                wage_details.to_excel(writer, index=False, sheet_name="Wages")
                equipment_master.to_excel(writer, index=False, sheet_name="Equipment Master")
                equipment_detail.to_excel(writer, index=False, sheet_name="Equipment Detail")

                summary_df = pd.DataFrame([
                    ["Estimate Total Ex GST", float(estimate_summary["Total Ex GST"].fillna(0).sum()) if not estimate_summary.empty else 0],
                    ["Estimate Total Inc GST", float(estimate_summary["Total Inc GST"].fillna(0).sum()) if not estimate_summary.empty else 0],
                    ["Timesheet Hours", float(timesheet_details["Hours"].fillna(0).sum()) if not timesheet_details.empty else 0],
                    ["Material Cost Ex GST", material_total],
                    ["Wage Cost", wage_total],
                    ["Equipment Still Out", equipment_still_out],
                ], columns=["Summary Item", "Value"])
                summary_df.to_excel(writer, index=False, sheet_name="Summary")

                # Basic column width clean-up
                for ws in writer.book.worksheets:
                    for column_cells in ws.columns:
                        max_len = 0
                        col_letter = column_cells[0].column_letter
                        for cell in column_cells:
                            value = "" if cell.value is None else str(cell.value)
                            max_len = max(max_len, len(value))
                        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 45)

            output.seek(0)

            clean_job_no = "job_pack"
            if not job_details.empty:
                clean_job_no = str(job_details.iloc[0]["Job No"]).replace("/", "-").replace("\\", "-")

            st.download_button(
                label="Download Full Job Pack Excel",
                data=output.getvalue(),
                file_name=f"{clean_job_no}_Job_Pack.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            # Individual CSV downloads
            st.markdown("### Individual Downloads")
            d1, d2, d3, d4, d5 = st.columns(5)
            d1.download_button(
                "Materials CSV",
                data=material_details.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_materials.csv",
                mime="text/csv",
            )
            d2.download_button(
                "Wages CSV",
                data=wage_details.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_wages.csv",
                mime="text/csv",
            )
            d3.download_button(
                "Equipment CSV",
                data=equipment_master.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_equipment_master.csv",
                mime="text/csv",
            )
            d4.download_button(
                "Job Details CSV",
                data=job_details.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_job_details.csv",
                mime="text/csv",
            )
            d5.download_button(
                "Imported Materials CSV",
                data=imported_materials.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_imported_materials.csv",
                mime="text/csv",
            )
            st.download_button(
                "Job Photos Register CSV",
                data=job_photos_meta.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"{clean_job_no}_job_photos.csv",
                mime="text/csv",
            )

    with tab_reports:
        st.subheader("General Reports")

        reports = {
            "Estimate Working Sheets": """
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       e.estimate_no AS 'Estimate No',
                       e.revision AS 'Revision',
                       e.estimate_date AS 'Date',
                       e.status AS 'Status',
                       e.total_ex_gst AS 'Total Ex GST',
                       e.gst_amount AS 'GST',
                       e.total_inc_gst AS 'Total Inc GST',
                       e.notes AS 'Notes'
                FROM estimate_working_sheets e
                JOIN jobs j ON j.id = e.job_id
                ORDER BY j.job_no, e.id DESC
            """,
            "Estimate Line Items": """
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       e.estimate_no AS 'Estimate No',
                       l.section AS 'Section',
                       l.item_description AS 'Description',
                       l.qty AS 'Qty',
                       l.unit AS 'Unit',
                       COALESCE(l.estimated_labour_hours, 0) AS 'Estimated Labour Hours',
                       COALESCE(l.material_allowance, 0) AS 'Material Allowance',
                       l.substrate AS 'Substrate',
                       l.work_location AS 'Location',
                       l.coating_system AS 'Coating System',
                       l.colour_finish AS 'Colour / Finish',
                       l.unit_rate AS 'Unit Rate',
                       l.line_total AS 'Line Total',
                       l.source_pack AS 'Source Pack',
                       l.notes AS 'Notes'
                FROM estimate_line_items l
                JOIN estimate_working_sheets e ON e.id = l.estimate_id
                JOIN jobs j ON j.id = e.job_id
                ORDER BY j.job_no, e.id DESC, l.id ASC
            """,
            "Timesheets": """
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       e.name AS 'Employee',
                       t.work_date AS 'Date',
                       t.start_time AS 'Start',
                       t.finish_time AS 'Finish',
                       t.break_minutes AS 'Break Minutes',
                       t.total_hours AS 'Hours',
                       t.work_type AS 'Work Type',
                       t.status AS 'Status',
                       t.notes AS 'Notes'
                FROM timesheet_entries t
                JOIN jobs j ON j.id = t.job_id
                JOIN employees e ON e.id = t.employee_id
                ORDER BY t.work_date DESC, j.job_no, e.name
            """,
            "Archived Jobs": """
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       bc.name AS 'Builder / Client',
                       bc.contact_name AS 'Contact',
                       bc.phone AS 'Phone',
                       bc.email AS 'Email',
                       j.site_address AS 'Site Address',
                       j.status AS 'Status',
                       j.leading_hand AS 'Leading Hand',
                       j.start_date AS 'Start Date',
                       j.end_date AS 'End Date',
                       j.contract_value AS 'Contract Value',
                       j.notes AS 'Notes'
                FROM jobs j
                LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
                WHERE j.status = 'Archived'
                ORDER BY j.job_no
            """,
            "Job Register": """
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       bc.name AS 'Builder / Client',
                       bc.contact_name AS 'Contact',
                       bc.phone AS 'Phone',
                       bc.email AS 'Email',
                       j.site_address AS 'Site Address',
                       j.status AS 'Status',
                       j.leading_hand AS 'Leading Hand',
                       j.start_date AS 'Start Date',
                       j.end_date AS 'End Date',
                       j.contract_value AS 'Contract Value',
                       j.notes AS 'Notes'
                FROM jobs j
                LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
                ORDER BY j.job_no
            """,
            "Builders & Clients": "SELECT * FROM builders_clients ORDER BY name",
            "Employees": "SELECT * FROM employees ORDER BY name",
            "Products": "SELECT * FROM products ORDER BY product_code",
            "Material Costs": """
                SELECT j.job_no,
                       j.job_name,
                       COALESCE(NULLIF(m.custom_product_code, ''), p.product_code, '') AS product_code,
                       COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS product_name,
                       COALESCE(NULLIF(m.supplier, ''), NULLIF(m.custom_supplier, ''), p.supplier, '') AS supplier,
                       COALESCE(NULLIF(m.custom_unit, ''), p.unit, '') AS unit,
                       COALESCE(m.custom_unit_price, p.price_ex_gst, 0) AS price_ex_gst,
                       COALESCE(NULLIF(m.custom_colour, ''), '') AS colour_finish,
                       m.qty_required,
                       m.qty_received,
                       ROUND(CAST((COALESCE(m.custom_unit_price, p.price_ex_gst, 0) * COALESCE(m.qty_required, 0)) AS numeric), 2) AS total_cost,
                       m.date_ordered,
                       m.notes
                FROM material_entries m
                JOIN jobs j ON j.id = m.job_id
                LEFT JOIN products p ON p.id = m.product_id
                ORDER BY m.id DESC
            """,
            "Wages": """
                SELECT j.job_no,
                       j.job_name,
                       e.name AS employee,
                       w.work_date,
                       w.hours,
                       e.rate_plus_10,
                       COALESCE(w.hours, 0) *
                       COALESCE(NULLIF(w.hourly_rate_snapshot, 0), e.rate_plus_10, e.base_hourly_rate, 0)
                           AS total_cost,
                       w.notes
                FROM wage_entries w
                JOIN jobs j ON j.id = w.job_id
                JOIN employees e ON e.id = w.employee_id
                ORDER BY w.work_date DESC
            """,
            "Equipment Master List": """
                SELECT j.job_no,
                       j.job_name,
                       i.category,
                       i.item_name,
                       COALESCE(SUM(r.qty_required), 0) AS total_required,
                       COALESCE(SUM(r.qty_taken), 0) AS total_taken,
                       COALESCE(SUM(r.qty_returned), 0) AS total_returned,
                       COALESCE(SUM(r.qty_taken - r.qty_returned), 0) AS still_out,
                       COALESCE(MAX(r.date_out), '') AS last_date_out,
                       COALESCE(MAX(r.date_in), '') AS last_date_in,
                       COALESCE(MAX(r.taken_by), '') AS taken_by,
                       COALESCE(MAX(r.returned_by), '') AS returned_by,
                       COALESCE(MAX(r.notes), '') AS notes
                FROM jobs j
                CROSS JOIN equipment_checklist_items i
                LEFT JOIN equipment_checklist_records r
                    ON r.job_id = j.id
                   AND r.checklist_item_id = i.id
                GROUP BY j.job_no, j.job_name, i.category, i.item_name
                ORDER BY j.job_no, i.category, i.item_name
            """,
            "Imported Checklist Materials": """
                SELECT j.job_no,
                       j.job_name,
                       im.product,
                       im.colour,
                       im.qty_required,
                       im.qty_loaded,
                       im.source_file,
                       im.imported_at,
                       im.notes
                FROM imported_material_entries im
                JOIN jobs j ON j.id = im.job_id
                ORDER BY j.job_no, im.id
            """,
        }

        report_name = st.selectbox("Select report", list(reports.keys()))
        report_df = df_query(reports[report_name])
        st.dataframe(report_df, width="stretch", hide_index=True)

        st.download_button(
            f"Download {report_name} CSV",
            data=report_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{report_name.replace(' ', '_').lower()}.csv",
            mime="text/csv",
        )


# PB_JOBHUB_FULL_AUDIT_CLEANUP_20260727

# PB_JOBHUB_PERFORMANCE_FEEDBACK_V2_20260727
