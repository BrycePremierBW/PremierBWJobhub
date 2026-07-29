"""Reusable branded Streamlit user-interface helpers.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *


def pb_logo_data_uri():
    """Return the Premier Brushworks logo as a browser-safe data URI for CSS backgrounds."""
    possible_paths = [
        PB_LOGO_BACKGROUND_IMAGE,
        os.path.join(os.path.dirname(__file__), "PB_Logo_Main_PNG.png"),
    ]
    for logo_path in possible_paths:
        try:
            if os.path.exists(logo_path):
                with open(logo_path, "rb") as f:
                    encoded_logo = base64.b64encode(f.read()).decode("utf-8")
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
    """Apply a clean, consistent Premier Brushworks interface theme."""
    st.markdown(
        """
        <style>
        :root {
            --pb-bg: #f5f3ef;
            --pb-surface: #ffffff;
            --pb-soft: #faf9f7;
            --pb-text: #242321;
            --pb-muted: #706d68;
            --pb-border: #ded9d1;
            --pb-sidebar: #181817;
            --pb-sidebar-hover: #292826;
            --pb-accent: #9a8067;
            --pb-success: #47735b;
            --pb-warning: #a06f2f;
            --pb-danger: #9b4c48;
            --pb-radius: 12px;
            --pb-shadow: 0 6px 22px rgba(29, 27, 24, 0.06);
        }

        html, body, [class*="css"] {
            font-family: "Poppins", "Segoe UI", Arial, sans-serif;
        }

        .stApp, [data-testid="stAppViewContainer"] {
            background: var(--pb-bg) !important;
            color: var(--pb-text);
        }

        [data-testid="stHeader"] { background: transparent; }

        .block-container {
            max-width: 1560px;
            padding-top: 1.15rem;
            padding-bottom: 2.5rem;
        }

        section[data-testid="stSidebar"] {
            background: var(--pb-sidebar);
            border-right: 1px solid #34322f;
        }

        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] span,
        section[data-testid="stSidebar"] h1,
        section[data-testid="stSidebar"] h2,
        section[data-testid="stSidebar"] h3 {
            color: #f5f2ec !important;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] label {
            border-radius: 9px;
            padding: 0.38rem 0.5rem;
        }

        section[data-testid="stSidebar"] [role="radiogroup"] label:hover {
            background: var(--pb-sidebar-hover);
        }

        section[data-testid="stSidebar"] div[data-baseweb="select"] > div {
            background: #ffffff !important;
        }

        section[data-testid="stSidebar"] div[data-baseweb="select"] * {
            color: #151515 !important;
            -webkit-text-fill-color: #151515 !important;
        }

        h1 { font-size: 2rem !important; letter-spacing: -0.035em; }
        h2 { font-size: 1.42rem !important; letter-spacing: -0.02em; }
        h3 { font-size: 1.08rem !important; }

        div[data-testid="stMetric"],
        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: var(--pb-surface);
            border: 1px solid var(--pb-border);
            border-radius: var(--pb-radius);
            box-shadow: var(--pb-shadow);
        }

        div[data-testid="stMetric"] { padding: 1rem 1.05rem; }
        div[data-testid="stMetricLabel"] { color: var(--pb-muted); }

        .stButton > button, .stDownloadButton > button {
            min-height: 2.55rem;
            border-radius: 9px !important;
            border: 1px solid var(--pb-border) !important;
            background: var(--pb-surface) !important;
            color: var(--pb-text) !important;
            font-weight: 600 !important;
            box-shadow: none !important;
        }

        .stButton > button:hover, .stDownloadButton > button:hover {
            border-color: var(--pb-accent) !important;
            background: #f0ebe5 !important;
            transform: none !important;
        }

        button[kind="primary"] {
            background: var(--pb-sidebar) !important;
            color: white !important;
            border-color: var(--pb-sidebar) !important;
        }

        [data-baseweb="tab-list"] {
            gap: 0.3rem;
            border-bottom: 1px solid var(--pb-border);
        }

        [data-baseweb="tab"] {
            border-radius: 8px 8px 0 0;
            padding-left: 0.85rem;
            padding-right: 0.85rem;
        }

        [data-baseweb="input"] > div,
        [data-baseweb="select"] > div,
        textarea { border-radius: 9px !important; }

        [data-testid="stDataFrame"], [data-testid="stTable"] {
            border: 1px solid var(--pb-border);
            border-radius: 10px;
            overflow: hidden;
            background: var(--pb-surface);
        }

        .pb-page-hero {
            background: var(--pb-surface);
            border: 1px solid var(--pb-border);
            border-radius: 14px;
            padding: 1.05rem 1.2rem;
            margin: 0.25rem 0 1rem;
            box-shadow: var(--pb-shadow);
        }

        .pb-page-eyebrow {
            color: var(--pb-accent);
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            margin-bottom: 0.25rem;
        }

        .pb-page-title {
            color: var(--pb-text);
            font-size: 1.65rem;
            font-weight: 700;
            letter-spacing: -0.035em;
            line-height: 1.15;
        }

        .pb-page-subtitle {
            color: var(--pb-muted);
            margin-top: 0.35rem;
            line-height: 1.45;
        }

        .pb-card {
            background: var(--pb-surface);
            border: 1px solid var(--pb-border);
            border-radius: var(--pb-radius);
            padding: 1rem 1.05rem;
            min-height: 116px;
            margin-bottom: 0.7rem;
            box-shadow: var(--pb-shadow);
        }

        .pb-card-label {
            color: var(--pb-muted);
            font-size: 0.77rem;
            font-weight: 650;
            text-transform: uppercase;
            letter-spacing: 0.055em;
        }

        .pb-card-value {
            color: var(--pb-text);
            font-size: 1.65rem;
            font-weight: 750;
            line-height: 1.15;
            margin-top: 0.3rem;
        }

        .pb-card-subtitle {
            color: var(--pb-muted);
            font-size: 0.82rem;
            margin-top: 0.3rem;
        }

        .pb-card.green { border-left: 5px solid var(--pb-success); }
        .pb-card.orange { border-left: 5px solid var(--pb-warning); }
        .pb-card.red { border-left: 5px solid var(--pb-danger); }
        .pb-card.blue, .pb-card.taupe { border-left: 5px solid var(--pb-accent); }

        .pb-sidebar-logo {
            padding: 0.55rem 0.2rem 0.4rem;
            margin-bottom: 0.45rem;
        }

        .pb-logo-mark {
            display: inline-flex;
            width: 38px;
            height: 38px;
            align-items: center;
            justify-content: center;
            border-radius: 10px;
            background: #f5f2ec;
            color: #181817;
            font-weight: 800;
            margin-bottom: 0.6rem;
        }

        .pb-sidebar-title { color: #ffffff; font-size: 1rem; font-weight: 700; }
        .pb-sidebar-subtitle { color: #aaa69f; font-size: 0.76rem; margin-top: 0.15rem; }

        .pb-job-header {
            background: var(--pb-surface);
            border: 1px solid var(--pb-border);
            border-radius: 14px;
            padding: 1.1rem 1.2rem;
            box-shadow: var(--pb-shadow);
            margin: 0.4rem 0 1rem;
        }

        .pb-job-title { color: var(--pb-text); font-size: 1.5rem; font-weight: 700; }
        .pb-job-no { color: var(--pb-accent); font-size: 0.72rem; font-weight: 700; text-transform: uppercase; }
        .pb-job-meta { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.7rem; }
        .pb-chip, .pb-status {
            display: inline-flex;
            border: 1px solid var(--pb-border);
            border-radius: 999px;
            background: var(--pb-soft);
            color: var(--pb-muted);
            padding: 0.28rem 0.58rem;
            font-size: 0.75rem;
            font-weight: 600;
        }

        @media (max-width: 900px) {
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
            div[data-testid="stHorizontalBlock"] {
                flex-wrap: wrap !important;
                gap: 0.65rem !important;
            }
            div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
                flex: 1 1 100% !important;
                width: 100% !important;
                min-width: 0 !important;
            }
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
            [data-testid="stDataFrame"],
            [data-testid="stTable"],
            [data-testid="stDataEditor"],
            div[data-testid="stTabs"] [data-baseweb="tab-list"] {
                max-width: 100% !important;
                min-width: 0 !important;
                overflow-x: auto !important;
                -webkit-overflow-scrolling: touch !important;
            }
            div[data-testid="stTabs"] [role="tab"] { flex: 0 0 auto !important; }
            .stButton > button,
            .stDownloadButton > button {
                width: 100% !important;
                min-height: 44px !important;
                white-space: normal !important;
            }
            .pb-page-title { font-size: 1.4rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

def pb_sidebar_header():
    st.sidebar.markdown(
        """
        <div class="pb-sidebar-logo">
            <div class="pb-logo-mark">PB</div>
            <div class="pb-sidebar-title">Premier Brushworks JobHub</div>
            <div class="pb-sidebar-subtitle">Jobs, site operations and estimating</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.divider()

def pb_page_header(title, subtitle="", eyebrow="Premier Brushworks"):
    st.markdown(f"""
    <div class="pb-page-hero">
        <div class="pb-page-eyebrow">{pb_html(eyebrow)}</div>
        <div class="pb-page-title">{pb_html(title)}</div>
        <div class="pb-page-subtitle">{pb_html(subtitle)}</div>
    </div>
    """, unsafe_allow_html=True)

def pb_metric_card(label, value, subtitle="", tone="taupe"):
    st.markdown(f"""
    <div class="pb-card {pb_html(tone)}">
        <div class="pb-card-label">{pb_html(label)}</div>
        <div class="pb-card-value">{pb_html(value)}</div>
        <div class="pb-card-subtitle">{pb_html(subtitle)}</div>
    </div>
    """, unsafe_allow_html=True)

def pb_status_tone(status):
    status_text = str(status or "").strip().lower()
    if status_text in ["active", "booked", "approved", "paid", "complete", "completed"]:
        return "green"
    if status_text in ["quoted", "not started", "on hold", "draft", "sent", "invoiced"]:
        return "orange"
    if status_text in ["archived", "closed", "cancelled", "rejected"]:
        return "grey"
    return "taupe"

def pb_job_header(row):
    status = row.get("Status", "") if hasattr(row, "get") else ""
    tone = pb_status_tone(status)
    st.markdown(f"""
    <div class="pb-job-header">
        <div class="pb-job-no">Job Folder</div>
        <div class="pb-job-title">{pb_html(row.get('Job No', ''))} - {pb_html(row.get('Job Name', ''))}</div>
        <span class="pb-status {pb_html(tone)}">{pb_html(status or 'No Status')}</span>
        <div class="pb-job-meta">
            <span class="pb-chip">🏗️ {pb_html(row.get('Builder / Client', 'No builder/client'))}</span>
            <span class="pb-chip">📍 {pb_html(row.get('Site Address', 'No address'))}</span>
            <span class="pb-chip">👷 {pb_html(row.get('Leading Hand', 'No leading hand'))}</span>
            <span class="pb-chip">📅 {pb_html(row.get('Start Date', ''))} → {pb_html(row.get('End Date', ''))}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
