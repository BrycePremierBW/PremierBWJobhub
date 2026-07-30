Warning: truncated output (original token count: 179134)
Total output lines: 17207

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
from jobhub_v3.schema import ensure_xero_schema
from jobhub_v3.streamlit_xero import render_xero_settings
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
    # Let Streamlit keep the navigation open on desktop while collapsing it on
    # narrow/mobile viewports. Forcing it open makes the signed-in page appear
    # zoomed and leaves too little room for the main content on phones.
    initial_sidebar_state="auto",
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
            /* PB_JOBHUB_MOBILE_VIEWPORT_FIX
               Keep the signed-in app inside the phone viewport. */
            html,
            body,
            [data-testid="stAppViewContainer"],
            [data-testid="stMain"],
            .stApp {
                width: 100% !important;
                max-width: 100vw !important;
                min-width: 0 !important;
                overflow-x: hidden !important;
            }

            .block-container {
                width: 100% !important;
                max-width: 100% !important;
                min-width: 0 !important;
                padding: 0.75rem 0.65rem 2rem !important;
                box-sizing: border-box !important;
            }

            /* Desktop column groups become readable vertical sections. */
            div[data-testid="stHorizontalBlock"] {
                flex-wrap: wrap !important;
                gap: 0.65rem !important;
            }

            div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
                flex: 1 1 100% !important;
                width: 100% !important;
                min-width: 0 !important;
            }

            /* Do not let form controls, uploaders or custom cards set a wider
               intrinsic page width. */
            div[data-testid="stVerticalBlock"],
            div[data-testid="stVerticalBlockBorderWrapper"],
            div[data-testid="stForm"],
            div[data-testid="stForm"] > div,
            div[data-testid="stFileUploader"],
            div[data-baseweb="select"],
            .pb-page-hero,
            .pb-card {
                width: 100% !important;
                max-width: 100% !important;
                min-width: 0 !important;
                box-sizing: border-box !important;
            }

            .pb-page-hero {
                padding: 1rem !important;
                border-radius: 16px !important;
            }

            .pb-page-title { font-size: 26px; }
            .pb-card { min-height: auto; }

            /* Wide records remain usable by scrolling the record itself rather
               than zooming or widening the whole application. */
            [data-testid="stDataFrame"],
            [data-testid="stTable"],
            [data-testid="stDataEditor"] {
                width: 100% !important;
                max-width: 100% !important;
                min-width: 0 !important;
                overflow-x: auto !important;
                -webkit-overflow-scrolling: touch !important;
            }

            div[data-testid="stTabs"] {
                width: 100% !important;
                max-width: 100% !important;
                min-width: 0 !important;
                overflow: hidden !important;
            }

            div[data-testid="stTabs"] [data-baseweb="tab-list"] {
                overflow-x: auto !important;
                overflow-y: hidden !important;
                white-space: nowrap !important;
                scrollbar-width: thin !important;
                -webkit-overflow-scrolling: touch !important;
            }

            div[data-testid="stTabs"] [role="tab"] {
                flex: 0 0 auto !important;
            }

            .stButton > button,
            .stDownloadButton > button {
                width: 100% !important;
                min-height: 44px !important;
                white-space: normal !important;
            }
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
            )…149134 tokens truncated…cted_material['Job No']} · {selected_material['Job Name']} · "
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
