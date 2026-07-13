#!/usr/bin/env python3
"""Safely tidy the Premier Brushworks JobHub Streamlit interface."""

from __future__ import annotations

import argparse
import py_compile
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from textwrap import dedent


CLEAN_THEME = dedent(r'''
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
            .block-container { padding-left: 0.85rem; padding-right: 0.85rem; }
            .pb-page-title { font-size: 1.4rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


''')

CLEAN_SIDEBAR = dedent(r'''
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


''')

CLEAN_NAVIGATION = dedent(r'''
# Clean grouped navigation.
if role == "employee":
    nav_groups = {
        "My Work": {
            "Employee Portal": "Employee Portal",
        },
    }
else:
    nav_groups = {
        "Home": {
            "Dashboard": "Dashboard",
        },
        "Jobs": {
            "Job Folders": "Job Folders",
            "Job Register": "Jobs",
            "Planning & Claims": "Control Centre",
        },
        "Site & Team": {
            "Timesheets": "Timesheets",
            "Materials": "Material Costs",
            "Wages": "Wages",
            "Equipment": "Equipment",
            "Job Photos": "Job Photos",
            "PDF Import": "PDF Import Centre",
        },
        "Estimating": {
            "Estimate Worksheet": "Estimate Working Sheet",
            "Job Costs & Forecasting": "Job Costs / Forecasting",
            "Import Take-off / Model": "Import Take-off / Model File",
            "Progress & Billing": "Progress / Billing Model",
            "3D Model Viewer": "3D Model Viewer",
        },
        "Reports": {
            "Reports & Export": "Reports / Export",
        },
        "Administration": {
            "Builders & Clients": "Builders & Clients",
            "Employees": "Employees",
            "Products": "Products",
            "JobHub AI": "JobHub AI Assistant",
            "Developer Tools": "App Builder AI",
        },
    }

    if role != "manager":
        admin_pages = nav_groups["Administration"]
        nav_groups["Administration"] = {
            "User Accounts": "User Access",
            **admin_pages,
        }

route_aliases = {
    "Reports": "Reports / Export",
    "Management": "Builders & Clients",
    "Site Operations": "Timesheets",
    "Estimating": "Estimate Working Sheet",
    "AI Assistant": "JobHub AI Assistant",
    "Job Lookup / Links": "Control Centre",
    "Painting Take-off Generator": "Import Take-off / Model File",
}

route_lookup = {}
for group_name, pages in nav_groups.items():
    for page_label, route_name in pages.items():
        route_lookup[route_name] = (group_name, page_label)

requested_control_section = st.session_state.pop("go_to_control_centre_section", None)
requested_menu = st.session_state.pop("go_to_menu", None)

if requested_control_section:
    requested_menu = "Control Centre"
    st.session_state["control_centre_section"] = requested_control_section

requested_menu = route_aliases.get(requested_menu, requested_menu)
if requested_menu in route_lookup:
    requested_group, requested_page = route_lookup[requested_menu]
    st.session_state["pb_nav_group"] = requested_group
    st.session_state["pb_nav_page"] = requested_page

group_names = list(nav_groups.keys())
if st.session_state.get("pb_nav_group") not in group_names:
    st.session_state["pb_nav_group"] = group_names[0]

nav_icons = {
    "Home": "⌂",
    "Jobs": "▣",
    "Site & Team": "◷",
    "Estimating": "▤",
    "Reports": "▥",
    "Administration": "⚙",
    "My Work": "✓",
}

selected_group = st.sidebar.radio(
    "Navigation",
    group_names,
    key="pb_nav_group",
    format_func=lambda item: f"{nav_icons.get(item, '•')}  {item}",
)

page_map = nav_groups[selected_group]
page_labels = list(page_map.keys())
if st.session_state.get("pb_nav_page") not in page_labels:
    st.session_state["pb_nav_page"] = page_labels[0]

if len(page_labels) == 1:
    selected_page = page_labels[0]
    st.session_state["pb_nav_page"] = selected_page
else:
    selected_page = st.sidebar.selectbox(
        "Page",
        page_labels,
        key="pb_nav_page",
        label_visibility="collapsed",
    )

menu = page_map[selected_page]

''')

CLEAN_DASHBOARD = dedent(r'''
elif menu == "Dashboard":
    pb_page_header(
        "Dashboard",
        "The items needing attention now, followed by current jobs and upcoming work.",
        "Operations overview",
    )

    jobs_count = int(df_query("SELECT COUNT(*) AS c FROM jobs").iloc[0]["c"])
    active_jobs_count = int(df_query("""
        SELECT COUNT(*) AS c
        FROM jobs
        WHERE COALESCE(status, '') NOT IN ('Completed', 'Paid', 'Archived')
    """).iloc[0]["c"])

    try:
        pending_timesheets = int(df_query("""
            SELECT COUNT(*) AS c
            FROM timesheet_entries
            WHERE COALESCE(status, 'Submitted') = 'Submitted'
        """).iloc[0]["c"])
    except Exception:
        pending_timesheets = 0

    try:
        open_variations = int(df_query("""
            SELECT COUNT(*) AS c
            FROM job_variations
            WHERE COALESCE(status, 'Draft')
                  NOT IN ('Approved', 'Rejected', 'Invoiced')
        """).iloc[0]["c"])
    except Exception:
        open_variations = 0

    try:
        overdue_claims_df = df_query("""
            SELECT COUNT(*) AS c,
                   COALESCE(SUM(COALESCE(amount_ex_gst, 0)), 0) AS total
            FROM invoice_claims
            WHERE COALESCE(status, '') <> 'Paid'
              AND due_date IS NOT NULL
              AND due_date <> ''
              AND due_date < ?
        """, (str(date.today()),))
        overdue_claims = int(overdue_claims_df.iloc[0]["c"])
        overdue_value = float(overdue_claims_df.iloc[0]["total"] or 0)
    except Exception:
        overdue_claims = 0
        overdue_value = 0

    m1, m2, m3, m4 = st.columns(4)

    with m1:
        pb_metric_card("Active Jobs", active_jobs_count, f"{jobs_count} jobs in the register", "green")
        if st.button("Open job folders", key="tidy_dash_jobs", use_container_width=True):
            st.session_state["go_to_menu"] = "Job Folders"
            st.rerun()

    with m2:
        pb_metric_card(
            "Timesheets Pending",
            pending_timesheets,
            "Submitted and awaiting review",
            "orange" if pending_timesheets else "green",
        )
        if st.button("Review timesheets", key="tidy_dash_timesheets", use_container_width=True):
            st.session_state["go_to_menu"] = "Timesheets"
            st.rerun()

    with m3:
        pb_metric_card(
            "Open Variations",
            open_variations,
            "Not yet approved, rejected or invoiced",
            "orange" if open_variations else "green",
        )
        if st.button("Open variations", key="tidy_dash_variations", use_container_width=True):
            st.session_state["go_to_menu"] = "Control Centre"
            st.session_state["go_to_control_centre_section"] = "Variations Register"
            st.rerun()

    with m4:
        pb_metric_card(
            "Overdue Claims",
            overdue_claims,
            pb_money(overdue_value),
            "red" if overdue_claims else "green",
        )
        if st.button("Open claims", key="tidy_dash_claims", use_container_width=True):
            st.session_state["go_to_menu"] = "Control Centre"
            st.session_state["go_to_control_centre_section"] = "Invoice / Claim Tracker"
            st.rerun()

    st.markdown("#### Quick access")
    q1, q2, q3, q4 = st.columns(4)

    if q1.button("Add or edit a job", key="tidy_quick_jobs", use_container_width=True):
        st.session_state["go_to_menu"] = "Jobs"
        st.rerun()

    if q2.button("Staff scheduling", key="tidy_quick_schedule", use_container_width=True):
        st.session_state["go_to_menu"] = "Control Centre"
        st.session_state["go_to_control_centre_section"] = "Staff Scheduling Board"
        st.rerun()

    if q3.button("Materials & costs", key="tidy_quick_materials", use_container_width=True):
        st.session_state["go_to_menu"] = "Material Costs"
        st.rerun()

    if q4.button("Reports & export", key="tidy_quick_reports", use_container_width=True):
        st.session_state["go_to_menu"] = "Reports / Export"
        st.rerun()

    tab_open, tab_upcoming, tab_attention = st.tabs(
        ["Open Jobs", "Upcoming Work", "Attention"]
    )

    with tab_open:
        active = df_query("""
            SELECT j.job_no AS 'Job No',
                   j.job_name AS 'Job Name',
                   bc.name AS 'Builder / Client',
                   j.site_address AS 'Site Address',
                   j.status AS 'Status',
                   j.leading_hand AS 'Leading Hand',
                   j.start_date AS 'Start Date',
                   j.end_date AS 'End Date'
            FROM jobs j
            LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
            WHERE COALESCE(j.status, '') NOT IN ('Completed', 'Paid', 'Archived')
            ORDER BY j.job_no
        """)
        if active.empty:
            st.info("No open jobs found.")
        else:
            st.dataframe(active, use_container_width=True, hide_index=True)

    with tab_upcoming:
        upcoming = df_query("""
            SELECT j.job_no AS 'Job No',
                   j.job_name AS 'Job Name',
                   COALESCE(bc.name, '') AS 'Builder / Client',
                   j.start_date AS 'Start Date',
                   j.leading_hand AS 'Leading Hand',
                   j.status AS 'Status'
            FROM jobs j
            LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
            WHERE COALESCE(j.status, '') IN ('Not Started', 'Quoted', 'Booked')
            ORDER BY j.start_date, j.job_no
        """)
        if upcoming.empty:
            st.info("No upcoming work is currently recorded.")
        else:
            st.dataframe(upcoming, use_container_width=True, hide_index=True)

    with tab_attention:
        missing_details = df_query("""
            SELECT j.job_no AS 'Job No',
                   j.job_name AS 'Job Name',
                   j.status AS 'Status',
                   j.leading_hand AS 'Leading Hand',
                   j.start_date AS 'Start Date',
                   CASE
                       WHEN COALESCE(j.leading_hand, '') = '' THEN 'Leading hand missing'
                       WHEN COALESCE(j.start_date, '') = '' THEN 'Start date missing'
                       ELSE 'Review'
                   END AS 'Attention'
            FROM jobs j
            WHERE COALESCE(j.status, '') NOT IN ('Completed', 'Paid', 'Archived')
              AND (COALESCE(j.leading_hand, '') = '' OR COALESCE(j.start_date, '') = '')
            ORDER BY j.job_no
        """)

        a1, a2, a3 = st.columns(3)
        a1.metric("Missing job details", len(missing_details.index))
        a2.metric("Pending timesheets", pending_timesheets)
        a3.metric("Overdue claims", overdue_claims)

        if missing_details.empty:
            st.success("No open jobs are missing a leading hand or start date.")
        else:
            st.dataframe(missing_details, use_container_width=True, hide_index=True)

''')

CONTROL_SELECTOR = dedent(r'''
    section_options = [
        "Daily Dashboard",
        "Job Health Score",
        "Job Budget Lock-In",
        "Variations Register",
        "Invoice / Claim Tracker",
        "Staff Scheduling Board",
        "Timesheet Approval",
        "Job Lookup / Links",
        "AI Job Review",
        "Export Control Centre",
    ]

    if st.session_state.get("control_centre_section") not in section_options:
        st.session_state["control_centre_section"] = section_options[0]

    section = st.selectbox(
        "Choose planning, finance or review area",
        section_options,
        key="control_centre_section",
    )

''')


def replace_function(text: str, start_name: str, next_name: str, replacement: str):
    start = text.find(f"def {start_name}(")
    end = text.find(f"def {next_name}(", start + 1)
    if start < 0 or end < 0:
        return text, False
    return text[:start] + replacement + text[end:], True


def remove_global_header(text: str):
    pattern = re.compile(
        r'''pb_page_header\(\s*["']JobHub["']\s*,\s*'''
        r'''["']A central job management system.*?["']\s*,\s*'''
        r'''["']Internal System["']\s*\)\s*''',
        flags=re.DOTALL,
    )
    updated, count = pattern.subn("", text, count=1)
    return updated, bool(count)


def replace_navigation(text: str):
    start_marker = "# Restored JobHub navigation."
    end_marker = "# =============================\n# EMPLOYEE PORTAL / USER ACCESS"
    start = text.find(start_marker)
    end = text.find(end_marker, start + 1)
    if start < 0 or end < 0:
        return text, False
    return text[:start] + CLEAN_NAVIGATION + "\n" + text[end:], True


def replace_dashboard(text: str):
    start = text.find('elif menu == "Dashboard":')
    if start < 0:
        return text, False

    markers = [
        "# =============================\n# JOBS - ADD / EDIT / REMOVE",
        '\nelif menu == "Jobs":',
    ]
    ends = [text.find(marker, start + 1) for marker in markers]
    ends = [position for position in ends if position >= 0]
    if not ends:
        return text, False

    end = min(ends)
    return text[:start] + CLEAN_DASHBOARD + "\n" + text[end:], True


def replace_control_selector(text: str):
    function_start = text.find("def control_centre_page():")
    if function_start < 0:
        return text, False
    start = text.find("    section = st.radio(", function_start)
    end = text.find('    if section == "Daily Dashboard":', start + 1)
    if start < 0 or end < 0:
        return text, False
    return text[:start] + CONTROL_SELECTOR + text[end:], True


def apply_cleanup(source: str):
    text = source
    results = []

    text, changed = replace_function(text, "apply_pb_branding", "pb_sidebar_header", CLEAN_THEME)
    results.append(("Clean commercial theme", changed))

    text, changed = replace_function(text, "pb_sidebar_header", "pb_page_header", CLEAN_SIDEBAR)
    results.append(("Compact sidebar header", changed))

    text, changed = remove_global_header(text)
    results.append(("Remove duplicate global banner", changed))

    text, changed = replace_navigation(text)
    results.append(("Grouped navigation", changed))

    text, changed = replace_dashboard(text)
    results.append(("Four-priority dashboard", changed))

    text, changed = replace_control_selector(text)
    results.append(("Compact Control Centre selector", changed))

    return text, results


def main() -> int:
    parser = argparse.ArgumentParser(description="Tidy Premier Brushworks JobHub")
    parser.add_argument("app_file", nargs="?", default="pb_jobhub_app.py")
    args = parser.parse_args()

    target = Path(args.app_file).resolve()
    if not target.exists():
        print(f"ERROR: Could not find {target}")
        print("Place this file beside pb_jobhub_app.py and run it again.")
        return 1

    original = target.read_text(encoding="utf-8")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = target.with_name(f"{target.name}.{stamp}.before_tidy.bak")
    shutil.copy2(target, backup)

    updated, results = apply_cleanup(original)

    print("\nPremier Brushworks JobHub cleanup")
    print("=" * 42)
    for label, changed in results:
        print(f"- {label}: {'updated' if changed else 'marker not found / already updated'}")

    if updated == original:
        print("\nNo changes were made. The source may already be cleaned or differ from the expected version.")
        print(f"Backup: {backup}")
        return 0

    target.write_text(updated, encoding="utf-8")

    try:
        py_compile.compile(str(target), doraise=True)
    except Exception as exc:
        shutil.copy2(backup, target)
        print("\nERROR: Compile check failed. The original was restored automatically.")
        print(f"Reason: {exc}")
        print(f"Backup: {backup}")
        return 2

    print("\nSUCCESS: The cleaned app passed Python compile checking.")
    print(f"Updated: {target}")
    print(f"Backup:  {backup}")
    print("\nTest locally, then commit pb_jobhub_app.py to GitHub for Render to redeploy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
