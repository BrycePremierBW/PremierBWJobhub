"""Dashboard page."""
from __future__ import annotations

from ..runtime import *


def _dashboard_counts():
    """Load core dashboard counters in two database round trips at most."""
    job_counts = df_query("""
        SELECT COUNT(*) AS jobs_count,
               SUM(CASE
                       WHEN COALESCE(status, '') NOT IN ('Completed', 'Paid', 'Archived')
                       THEN 1 ELSE 0
                   END) AS active_jobs_count
        FROM jobs
    """)
    jobs_count = int(job_counts.iloc[0]["jobs_count"] or 0)
    active_jobs_count = int(job_counts.iloc[0]["active_jobs_count"] or 0)

    pending_timesheets = 0
    open_variations = 0
    overdue_claims = 0
    overdue_value = 0.0
    try:
        operational = df_query("""
            SELECT
                (SELECT COUNT(*)
                   FROM timesheet_entries
                  WHERE COALESCE(status, 'Submitted') = 'Submitted') AS pending_timesheets,
                (SELECT COUNT(*)
                   FROM job_variations
                  WHERE COALESCE(status, 'Draft')
                        NOT IN ('Approved', 'Rejected', 'Invoiced')) AS open_variations,
                (SELECT COUNT(*)
                   FROM invoice_claims
                  WHERE COALESCE(status, '') <> 'Paid'
                    AND due_date IS NOT NULL
                    AND due_date <> ''
                    AND due_date < ?) AS overdue_claims,
                (SELECT COALESCE(SUM(COALESCE(amount_ex_gst, 0)), 0)
                   FROM invoice_claims
                  WHERE COALESCE(status, '') <> 'Paid'
                    AND due_date IS NOT NULL
                    AND due_date <> ''
                    AND due_date < ?) AS overdue_value
        """, (str(jobhub_today()), str(jobhub_today())))
        row = operational.iloc[0]
        pending_timesheets = int(row["pending_timesheets"] or 0)
        open_variations = int(row["open_variations"] or 0)
        overdue_claims = int(row["overdue_claims"] or 0)
        overdue_value = float(row["overdue_value"] or 0)
    except Exception:
        # Keep the dashboard usable during a partial/legacy schema upgrade.
        pass

    return {
        "jobs_count": jobs_count,
        "active_jobs_count": active_jobs_count,
        "pending_timesheets": pending_timesheets,
        "open_variations": open_variations,
        "overdue_claims": overdue_claims,
        "overdue_value": overdue_value,
    }


def _render_open_jobs():
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
        pb_empty_state("No open jobs", "Active and upcoming jobs will appear here once they are added.")
    else:
        st.dataframe(active, width="stretch", hide_index=True)


def _render_upcoming_work():
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
        pb_empty_state("No upcoming work", "Quoted, booked and not-started jobs will appear here.")
    else:
        st.dataframe(upcoming, width="stretch", hide_index=True)


def _render_attention(pending_timesheets, overdue_claims):
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
        st.dataframe(missing_details, width="stretch", hide_index=True)


def render_dashboard():
    pb_page_header(
        "Dashboard",
        "The items needing attention now, followed by current jobs and upcoming work.",
        "Operations overview",
    )

    counts = _dashboard_counts()
    jobs_count = counts["jobs_count"]
    active_jobs_count = counts["active_jobs_count"]
    pending_timesheets = counts["pending_timesheets"]
    open_variations = counts["open_variations"]
    overdue_claims = counts["overdue_claims"]
    overdue_value = counts["overdue_value"]

    m1, m2, m3, m4 = st.columns(4)

    with m1:
        pb_metric_card("Active Jobs", active_jobs_count, f"{jobs_count} jobs in the register", "green")
        if st.button("Open job folders", key="tidy_dash_jobs", width="stretch"):
            st.session_state["go_to_menu"] = "Job Folders"
            st.rerun()

    with m2:
        pb_metric_card(
            "Timesheets Pending",
            pending_timesheets,
            "Submitted and awaiting review",
            "orange" if pending_timesheets else "green",
        )
        if st.button("Review timesheets", key="tidy_dash_timesheets", width="stretch"):
            st.session_state["go_to_menu"] = "Timesheets"
            st.rerun()

    with m3:
        pb_metric_card(
            "Open Variations",
            open_variations,
            "Not yet approved, rejected or invoiced",
            "orange" if open_variations else "green",
        )
        if st.button("Open variations", key="tidy_dash_variations", width="stretch"):
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
        if st.button("Open claims", key="tidy_dash_claims", width="stretch"):
            st.session_state["go_to_menu"] = "Control Centre"
            st.session_state["go_to_control_centre_section"] = "Invoice / Claim Tracker"
            st.rerun()

    pb_section_heading("Quick access", "Common management actions without digging through menus.")
    q1, q2, q3, q4 = st.columns(4)

    if q1.button("Add or edit a job", key="tidy_quick_jobs", width="stretch"):
        st.session_state["go_to_menu"] = "Jobs"
        st.rerun()

    if q2.button("Staff scheduling", key="tidy_quick_schedule", width="stretch"):
        st.session_state["go_to_menu"] = "Control Centre"
        st.session_state["go_to_control_centre_section"] = "Staff Scheduling Board"
        st.rerun()

    if q3.button("Materials & costs", key="tidy_quick_materials", width="stretch"):
        st.session_state["go_to_menu"] = "Material Costs"
        st.rerun()

    if q4.button("Reports & export", key="tidy_quick_reports", width="stretch"):
        st.session_state["go_to_menu"] = "Reports / Export"
        st.rerun()

    pb_section_heading("Work overview", "Only the selected view is queried and rendered.")
    view = st.radio(
        "Work overview",
        ["Open Jobs", "Upcoming Work", "Attention"],
        horizontal=True,
        key="dashboard_work_overview",
        label_visibility="collapsed",
    )

    if view == "Upcoming Work":
        _render_upcoming_work()
    elif view == "Attention":
        _render_attention(pending_timesheets, overdue_claims)
    else:
        _render_open_jobs()


# =============================
# JOBS - ADD / EDIT / REMOVE
# =============================
