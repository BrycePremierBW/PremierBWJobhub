"""Reusable branded Streamlit user-interface helpers.

The UI defaults to Premier Brushworks for the existing production tenant, while
allowing subscriber-specific company identity to be supplied through session
state. This keeps commercial branding concerns in one place instead of spreading
hard-coded company names throughout individual pages.
"""

from __future__ import annotations

from .runtime import *


DEFAULT_COMPANY_NAME = "Premier Brushworks"
DEFAULT_PRODUCT_NAME = "JobHub"
DEFAULT_PRODUCT_SUBTITLE = "Jobs, site operations and estimating"


def pb_html(value):
    return html.escape(str(value or ""))


def pb_money(value):
    try:
        return f"${float(value or 0):,.0f}"
    except Exception:
        return "$0"


def pb_company_initials(company_name):
    words = [part for part in re.split(r"\s+", str(company_name or "").strip()) if part]
    if not words:
        return "JH"
    if len(words) == 1:
        cleaned = "".join(ch for ch in words[0] if ch.isalnum())
        return (cleaned[:2] or "JH").upper()
    return f"{words[0][0]}{words[1][0]}".upper()


def pb_brand_context(session_state=None):
    """Return the active tenant-facing brand without requiring database access."""
    if session_state is None:
        try:
            session_state = st.session_state
        except Exception:
            session_state = {}
    try:
        get_value = session_state.get
    except Exception:
        session_state = {}
        get_value = session_state.get

    company_name = str(get_value("jobhub_company_name", "") or "").strip() or DEFAULT_COMPANY_NAME
    product_name = str(get_value("jobhub_product_name", "") or "").strip() or DEFAULT_PRODUCT_NAME
    subtitle = str(get_value("jobhub_company_subtitle", "") or "").strip() or DEFAULT_PRODUCT_SUBTITLE
    logo_data_uri = str(get_value("jobhub_company_logo_data_uri", "") or "").strip()
    return {
        "company_name": company_name,
        "product_name": product_name,
        "subtitle": subtitle,
        "logo_data_uri": logo_data_uri,
        "initials": pb_company_initials(company_name),
    }


def pb_logo_data_uri(company_name=None):
    """Return the active tenant logo, falling back to PB only for the PB tenant."""
    brand = pb_brand_context()
    if brand["logo_data_uri"]:
        return brand["logo_data_uri"]

    active_company = str(company_name or brand["company_name"] or "").strip()
    if active_company.casefold() != DEFAULT_COMPANY_NAME.casefold():
        return ""

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


def apply_pb_branding():
    """Apply the shared JobHub design system across desktop and mobile."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Nunito+Sans:opsz,wght@6..12,400;6..12,500;6..12,600;6..12,700;6..12,800&display=swap');

        :root {
            --pb-bg: #f9f6f1;
            --pb-surface: #fffdfa;
            --pb-soft: #fbf7f1;
            --pb-text: #2b2520;
            --pb-muted: #7a7166;
            --pb-border: #eadfd1;
            --pb-sidebar: #1c1a17;
            --pb-sidebar-hover: #2e2822;
            --pb-accent: #8a6b4b;
            --pb-success: #2e8b57;
            --pb-warning: #cf9d38;
            --pb-danger: #c05a4e;
            --pb-radius: 16px;
            --pb-shadow: 0 10px 26px rgba(49, 39, 28, 0.08);
        }

        html, body, [class*="css"] {
            font-family: 'Nunito Sans', 'Plus Jakarta Sans', 'Segoe UI', Arial, sans-serif;
        }

        h1, h2, h3, h4 {
            font-family: 'Plus Jakarta Sans', 'Nunito Sans', 'Segoe UI', sans-serif;
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
            border-radius: 999px !important;
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

        .stButton > button:focus-visible,
        .stDownloadButton > button:focus-visible,
        [data-baseweb="input"] input:focus-visible,
        textarea:focus-visible {
            outline: 3px solid rgba(138, 107, 75, 0.25) !important;
            outline-offset: 2px !important;
        }

        .stButton > button:disabled,
        .stDownloadButton > button:disabled {
            opacity: 0.55 !important;
            cursor: not-allowed !important;
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

        [data-testid="stFileUploader"] section {
            border-radius: 12px !important;
            border-color: var(--pb-border) !important;
            background: var(--pb-soft) !important;
        }

        div[data-testid="stForm"] {
            border-color: var(--pb-border) !important;
            border-radius: var(--pb-radius) !important;
            background: var(--pb-surface) !important;
        }

        div[data-testid="stAlert"] {
            border-radius: 12px !important;
        }

        details[data-testid="stExpander"] {
            border: 1px solid var(--pb-border) !important;
            border-radius: 12px !important;
            background: var(--pb-surface) !important;
        }

        [data-testid="stDataFrame"], [data-testid="stTable"] {
            border: 1px solid var(--pb-border);
            border-radius: 16px;
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

        .pb-section-heading {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 0.75rem;
            margin: 1.35rem 0 0.65rem;
        }

        .pb-section-title {
            color: var(--pb-text);
            font-size: 1.05rem;
            font-weight: 750;
        }

        .pb-section-subtitle {
            color: var(--pb-muted);
            font-size: 0.82rem;
            margin-top: 0.15rem;
        }

        .pb-empty-state {
            border: 1px dashed var(--pb-border);
            border-radius: 14px;
            background: var(--pb-soft);
            padding: 1rem 1.1rem;
            color: var(--pb-muted);
            margin: 0.4rem 0 0.8rem;
        }

        .pb-empty-state strong {
            color: var(--pb-text);
            display: block;
            margin-bottom: 0.2rem;
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
            width: 42px;
            height: 42px;
            align-items: center;
            justify-content: center;
            border-radius: 11px;
            background: #f5f2ec;
            color: #181817;
            font-weight: 800;
            margin-bottom: 0.6rem;
            overflow: hidden;
        }

        .pb-logo-mark img {
            width: 100%;
            height: 100%;
            object-fit: contain;
            background: white;
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
            .pb-section-heading { align-items: flex-start; flex-direction: column; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def pb_sidebar_header(company_name=None, subtitle=None, logo_data_uri=None):
    brand = pb_brand_context()
    company_name = str(company_name or brand["company_name"] or DEFAULT_COMPANY_NAME).strip()
    subtitle = str(subtitle or brand["subtitle"] or DEFAULT_PRODUCT_SUBTITLE).strip()
    logo_data_uri = str(logo_data_uri or brand["logo_data_uri"] or pb_logo_data_uri(company_name) or "").strip()
    if logo_data_uri:
        mark = f'<img src="{pb_html(logo_data_uri)}" alt="{pb_html(company_name)} logo">'
    else:
        mark = pb_html(pb_company_initials(company_name))

    st.sidebar.markdown(
        f"""
        <div class="pb-sidebar-logo">
            <div class="pb-logo-mark">{mark}</div>
            <div class="pb-sidebar-title">{pb_html(company_name)} {pb_html(brand['product_name'])}</div>
            <div class="pb-sidebar-subtitle">{pb_html(subtitle)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.divider()


def pb_page_header(title, subtitle="", eyebrow=None):
    if eyebrow is None:
        eyebrow = pb_brand_context()["company_name"]
    subtitle_html = f'<div class="pb-page-subtitle">{pb_html(subtitle)}</div>' if str(subtitle or "").strip() else ""
    st.markdown(f"""
    <div class="pb-page-hero">
        <div class="pb-page-eyebrow">{pb_html(eyebrow)}</div>
        <div class="pb-page-title">{pb_html(title)}</div>
        {subtitle_html}
    </div>
    """, unsafe_allow_html=True)


def pb_section_header(title, subtitle=""):
    subtitle_html = f'<div class="pb-section-subtitle">{pb_html(subtitle)}</div>' if str(subtitle or "").strip() else ""
    st.markdown(
        f"""
        <div class="pb-section-heading">
            <div>
                <div class="pb-section-title">{pb_html(title)}</div>
                {subtitle_html}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def pb_empty_state(title, message=""):
    message_html = pb_html(message) if str(message or "").strip() else ""
    st.markdown(
        f'<div class="pb-empty-state"><strong>{pb_html(title)}</strong>{message_html}</div>',
        unsafe_allow_html=True,
    )


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
