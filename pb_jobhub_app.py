Warning: truncated output (original token count: 170534)
Total output lines: 16281

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

PB_JOBHUB_BUILD = "2026.07.28-linked-progress-smart-scheduler-v1"
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
    return conn


@st.cache_resource
def init_db():
    conn = connect()
    cur = conn.cursor()

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
            cur.execute("DELETE FROM wage_entries WHERE timesheet_id = ?", (int(timesheet_…70534 tokens truncated…")
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
        st.dataframe(job_details, width="stretch", hide_index=True)


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
            st.dataframe(timesheet_details, width="stretch", hide_index=True)

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
                edit_start_date = col6.text_input("Start Date", value=str(current["start_date"] or ""))
                edit_end_date = col7.text_input("End Date", value=str(current["end_date"] or ""))

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
                    if edit_restrict_material_products and not edit_allowed_material_suppliers:
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
                edit_start_date = col6.text_input("Start Date", value=str(current["start_date"] or ""), key="arch_start_date")
                edit_end_date = col7.text_input("End Date", value=str(current["end_date"] or ""), key="arch_end_date")

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
    st.dataframe(df, width="stretch", hide_index=True)

    st.markdown("### Delete Material Cost Entries")
    st.caption("Use this for wrong, duplicate or accidental material cost entries. This deletes saved material cost rows only; it does not delete the product from the product list.")

    if df.empty:
        st.info("No material cost entries to delete.")
    else:
        material_options = {
            f"ID {row['ID']} | {row['Job No']} - {row['Job Name']} | {row['Product Code']} | {row['Product Name']} | Qty {row['Qty Required']} | ${float(row['Total Cost'] or 0):,.2f}": int(row["ID"])
            for _, row in df.iterrows()
        }

        selected_material_labels = st.multiselect(
            "Select material cost entries to delete",
            list(material_options.keys()),
            key="delete_material_entries_select"
        )
        selected_material_ids = [material_options[label] for label in selected_material_labels]

        delete_materials_confirm = st.text_input(
            "To delete selected material cost entries, type: DELETE MATERIALS",
            key="delete_material_entries_confirm"
        )

        if st.button("Delete Selected Material Cost Entries", key="delete_material_entries_button"):
            if not selected_material_ids:
                pb_error("Select at least one material cost entry first.")
            elif delete_materials_confirm.strip().upper() != "DELETE MATERIALS":
                pb_error("Type DELETE MATERIALS exactly before deleting material entries.")
            else:
                for material_id in selected_material_ids:
                    execute("DELETE FROM material_entries WHERE id = ?", (int(material_id),))
                pb_success(f"Deleted {len(selected_material_ids)} material cost entr{'y' if len(selected_material_ids) == 1 else 'ies'}.")
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
                st.metric("Total Timesheet Hours", f"{float(timesheet_details['Hours'].fillna(0).sum()):.2f}")
                st.dataframe(timesheet_details, width="stretch", hide_index=True)

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

            st.markdown("### Timesheets for this Job")
            if timesheet_details.empty:
                st.info("No timesheets saved for this job.")
            else:
                st.metric("Total Timesheet Hours", f"{float(timesheet_details['Hours'].fillna(0).sum()):.2f}")
                st.dataframe(timesheet_details, width="stretch", hide_index=True)

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