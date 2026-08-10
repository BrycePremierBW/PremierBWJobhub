"""Control Centre dashboards, budgets, variations, claims and scheduling.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *
from jobhub_time import jobhub_today


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
    df = df_query("SELECT COUNT(*) AS c FROM job_variations WHERE job_id = ?", (job_id,))
    return f"VAR-{int(df.iloc[0]['c']) + 1:03d}" if not df.empty else "VAR-001"

def pb_next_claim_no(job_id):
    df = df_query("SELECT COUNT(*) AS c FROM invoice_claims WHERE job_id = ?", (job_id,))
    return f"CLAIM-{int(df.iloc[0]['c']) + 1:03d}" if not df.empty else "CLAIM-001"

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
               COALESCE(SUM(COALESCE(m.qty_required, 0) * COALESCE(m.custom_unit_price, p.price_ex_gst, 0)), 0) AS 'Material Cost',
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
               COALESCE(SUM(COALESCE(w.hours, 0) * COALESCE(e.rate_plus_10, e.base_hourly_rate, 0)), 0) AS 'Labour Cost'
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
               COALESCE(SUM(COALESCE(amount_ex_gst, 0)), 0) AS 'Claimed Amount',
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
        "Contract Value", "Material Cost", "Material Qty Required", "Material Qty Received", "Material Lines",
        "Wage Hours", "Labour Cost", "Timesheet Hours", "Timesheet Lines", "Budget Labour Hours",
        "Budget Labour Cost", "Budget Materials", "Budget Access", "Budget Subcontractors", "Budget Sundries",
        "Variation Value", "Approved Variation Value", "Variation Count", "Claimed Amount",
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
        today = jobhub_today()
        issues = []
        cost_pct = pb_float(row["Cost to Date %"])
        end = pb_date(row["End Date"])

        if pb_float(row["Adjusted Contract Value"]) <= 0:
            issues.append("No contract value")
        if row["Budget Locked"] in [None, ""]:
            issues.append("Budget not locked")
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

    today = jobhub_today()
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
        st.success("No red or orange jobs found.")
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
    st.caption("Green = on track, Orange = needs attention, Red = margin/schedule/data risk. The entire job line is coloured to match the health score.")

    status_filter = st.selectbox("Status Filter", ["All"] + sorted([str(x) for x in df["Status"].fillna("").unique() if str(x).strip()]), key="health_status_filter")
    filtered = df.copy()
    if status_filter != "All":
        filtered = filtered[filtered["Status"].astype(str) == status_filter]

    health_filter = st.multiselect("Health Filter", ["Green", "Orange", "Red"], default=["Green", "Orange", "Red"], key="health_filter")
    filtered = filtered[filtered["Health"].isin(health_filter)]

    cols = ["Job No", "Job Name", "Builder / Client", "Status", "Health", "Health Notes", "Adjusted Contract Value", "Total Actual Cost", "Gross Profit %", "Cost to Date %", "Remaining Labour Hours", "End Date"]
    health_view = filtered[cols].copy()

    def colour_health_row(row):
        health = str(row.get("Health", "")).lower()
        if health == "red":
            style = "background-color: #fee2e2; color: #111827; font-weight: 700;"
        elif health == "orange":
            style = "background-color: #ffedd5; color: #111827; font-weight: 600;"
        elif health == "green":
            style = "background-color: #dcfce7; color: #111827;"
        else:
            style = "background-color: #ffffff; color: #111827;"
        return [style for _ in row]

    st.dataframe(health_view.style.apply(colour_health_row, axis=1), width="stretch", hide_index=True)

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

        target_gp = 0.0
        st.caption("The $1,000 completed-work target already includes profit; no GP percentage is added.")
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
        st.success("Job budget saved.")
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

    render_context_pdf_import_for_selected_job(
        context="variations",
        title="Import variation, correspondence or scope PDFs",
        key_prefix="variations_pdf_import",
    )

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
            sent_date = c4.text_input("Sent Date", value=str(jobhub_today()) if status in ["Sent", "Approved"] else "")
            approved_date = c5.text_input("Approved Date", value=str(jobhub_today()) if status == "Approved" else "")
            approved_by = c6.text_input("Approved By")
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Variation")
        if submitted:
            execute("""
                INSERT INTO job_variations
                (job_id, variation_no, description, reason, amount_ex_gst, status, sent_date, approved_date, approved_by, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, variation_no, description, reason, amount, status, sent_date, approved_date, approved_by, notes, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            st.success("Variation saved.")
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

    render_context_pdf_import_for_selected_job(
        context="claims",
        title="Import progress claim, invoice, PO or sign-off PDFs",
        key_prefix="claims_pdf_import",
    )

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
            invoice_date = c4.text_input("Invoice Date", value=str(jobhub_today()))
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
            st.success("Invoice / claim saved.")
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
    st.caption("Schedule one day or one full week against a job. Use the grouped views to quickly see staff by job or jobs by staff.")

    job_options = get_job_options()
    employee_options = get_employee_options(active_only=True)
    if not job_options or not employee_options:
        st.info("Create jobs and active employees first.")
        return

    render_context_pdf_import_for_selected_job(
        context="site",
        title="Import roster, SWMS, day labour or site PDF",
        key_prefix="schedule_pdf_import",
    )

    with st.expander("Add Staff Schedule Entry", expanded=True):
        with st.form("staff_schedule_form"):
            selected_job = st.selectbox("Job", list(job_options.keys()), key="schedule_job")
            selected_employees = st.multiselect(
                "Staff Members",
                list(employee_options.keys()),
                default=[list(employee_options.keys())[0]] if employee_options else [],
                key="schedule_employees_multi",
            )

            period_type = st.radio(
                "Schedule Type",
                ["Single Day", "Week Ending"],
                horizontal=True,
                key="schedule_period_type",
            )

            if period_type == "Single Day":
                c1, c2, c3, c4 = st.columns(4)
                schedule_day = c1.date_input("Date", value=jobhub_today(), key="schedule_single_day")
                start_time = c2.text_input("Start Time", value="07:00", key="schedule_single_start")
                finish_time = c3.text_input("Finish Time", value="15:00", key="schedule_single_finish")
                planned_hours = c4.number_input("Planned Hours", min_value=0.0, step=0.25, value=8.0, key="schedule_single_hours")
                schedule_date = str(schedule_day)
                period_start = str(schedule_day)
                period_end = str(schedule_day)
            else:
                c1, c2, c3, c4 = st.columns(4)
                default_week_end = jobhub_today()
                default_week_start = default_week_end - timedelta(days=4)
                from_date = c1.date_input("From Date", value=default_week_start, key="schedule_week_from")
                week_ending = c2.date_input("Week Ending", value=default_week_end, key="schedule_week_ending")
                start_time = c3.text_input("Daily Start", value="07:00", key="schedule_week_start_time")
                finish_time = c4.text_input("Daily Finish", value="15:00", key="schedule_week_finish_time")
                planned_hours = st.number_input("Planned Hours Per Staff Member for This Job / Week", min_value=0.0, step=0.25, value=38.0, key="schedule_week_hours")
                schedule_date = str(from_date)
                period_start = str(from_date)
                period_end = str(week_ending)
                st.caption("Use this when the same staff are planned on the same job for the week. It creates one weekly schedule row per staff member.")

            site_role = st.selectbox("Site Role", ["Painter", "Leading Hand", "Supervisor", "Apprentice", "Subcontractor", "Other"], key="schedule_site_role")
            notes = st.text_area("Notes", key="schedule_notes")
            submitted = st.form_submit_button("Save Schedule Entry")

        if submitted:
            if not selected_employees:
                st.error("Select at least one staff member.")
            elif period_type == "Week Ending" and period_end < period_start:
                st.error("Week ending date must be after the from date.")
            else:
                saved_count = 0
                for employee_name in selected_employees:
                    execute("""
                        INSERT INTO staff_schedule
                        (job_id, employee_id, schedule_date, start_time, finish_time, site_role, notes, created_at,
                         period_type, period_start, period_end, planned_hours)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        job_options[selected_job],
                        employee_options[employee_name],
                        schedule_date,
                        start_time,
                        finish_time,
                        site_role,
                        notes,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        period_type,
                        period_start,
                        period_end,
                        planned_hours,
                    ))
                    saved_count += 1
                st.success(f"Saved {saved_count} schedule entr{'y' if saved_count == 1 else 'ies'} for {selected_job}.")
                refresh()

    c1, c2 = st.columns(2)
    start_filter = str(c1.date_input("From Date", value=jobhub_today(), key="schedule_filter_from"))
    end_filter = str(c2.date_input("To / Week Ending", value=jobhub_today() + timedelta(days=7), key="schedule_filter_to"))

    schedule = df_query("""
        SELECT s.id AS 'ID',
               COALESCE(NULLIF(s.period_type, ''), 'Single Day') AS 'Schedule Type',
               COALESCE(NULLIF(s.period_start, ''), s.schedule_date) AS 'From Date',
               COALESCE(NULLIF(s.period_end, ''), s.schedule_date) AS 'Week Ending / To Date',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               COALESCE(j.site_address, '') AS 'Site Address',
               e.name AS 'Staff Member',
               s.start_time AS 'Start',
               s.finish_time AS 'Finish',
               COALESCE(s.planned_hours, 0) AS 'Planned Hours',
               s.site_role AS 'Role',
               s.notes AS 'Notes'
        FROM staff_schedule s
        JOIN jobs j ON j.id = s.job_id
        JOIN employees e ON e.id = s.employee_id
        WHERE COALESCE(NULLIF(s.period_start, ''), s.schedule_date) <= ?
          AND COALESCE(NULLIF(s.period_end, ''), s.schedule_date) >= ?
        ORDER BY COALESCE(NULLIF(s.period_start, ''), s.schedule_date), j.job_no, e.name
    """, (end_filter, start_filter))

    if schedule.empty:
        st.info("No staff schedule entries found for this date range.")
        return

    schedule["Job"] = schedule["Job No"].astype(str) + " - " + schedule["Job Name"].astype(str)

    st.markdown("### Schedule by Job")
    by_job = schedule.groupby(["From Date", "Week Ending / To Date", "Job", "Site Address", "Role"], dropna=False).agg({
        "Staff Member": lambda s: ", ".join(sorted([str(x) for x in s if str(x).strip()])),
        "Planned Hours": "sum",
    }).reset_index().rename(columns={"Staff Member": "Staff"})
    st.dataframe(by_job, width="stretch", hide_index=True)

    st.markdown("### Schedule by Staff")
    by_staff = schedule.groupby(["Staff Member", "From Date", "Week Ending / To Date"], dropna=False).agg({
        "Job": lambda s: ", ".join(sorted(set([str(x) for x in s if str(x).strip()]))),
        "Planned Hours": "sum",
    }).reset_index().rename(columns={"Job": "Jobs"})
    st.dataframe(by_staff, width="stretch", hide_index=True)

    st.markdown("### Full Schedule Detail")
    detail_cols = [
        "Schedule Type", "From Date", "Week Ending / To Date", "Job No", "Job Name",
        "Staff Member", "Role", "Start", "Finish", "Planned Hours", "Site Address", "Notes"
    ]
    st.dataframe(schedule[detail_cols], width="stretch", hide_index=True)

def pb_control_timesheet_approval():
    st.subheader("Timesheet Approval")
    st.caption("Approve or reject submitted timesheets before they are treated as final.")

    pending = df_query("""
        SELECT t.id AS 'ID',
               COALESCE(NULLIF(t.period_type, ''), 'Single Day') AS 'Period',
               COALESCE(NULLIF(t.period_start, ''), t.work_date) AS 'From Date',
               COALESCE(NULLIF(t.period_end, ''), t.work_date) AS 'Week Ending / To Date',
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
        WHERE COALESCE(t.status, 'Submitted') = 'Submitted'
        ORDER BY COALESCE(NULLIF(t.period_start, ''), t.work_date) DESC, t.id DESC
    """)

    if pending.empty:
        st.success("No submitted timesheets waiting for approval.")
        return

    st.dataframe(pending, width="stretch", hide_index=True)
    options = {f"{row['From Date']} to {row['Week Ending / To Date']} | {row['Employee']} | {row['Job No']} | {row['Hours']} hrs | ID {row['ID']}": int(row["ID"]) for _, row in pending.iterrows()}
    selected = st.multiselect("Select timesheets", list(options.keys()), key="approve_timesheets_select")
    selected_ids = [options[x] for x in selected]

    c1, c2, c3 = st.columns(3)
    if c1.button("Approve Selected Timesheets"):
        for ts_id in selected_ids:
            execute("UPDATE timesheet_entries SET status = 'Approved' WHERE id = ?", (ts_id,))
        st.success(f"Approved {len(selected_ids)} timesheet(s).")
        refresh()
    if c2.button("Reject Selected Timesheets"):
        for ts_id in selected_ids:
            execute("UPDATE timesheet_entries SET status = 'Rejected' WHERE id = ?", (ts_id,))
        st.warning(f"Rejected {len(selected_ids)} timesheet(s).")
        refresh()
    if c3.button("Mark Selected As Paid/Processed"):
        for ts_id in selected_ids:
            execute("UPDATE timesheet_entries SET status = 'Processed' WHERE id = ?", (ts_id,))
        st.info(f"Marked {len(selected_ids)} timesheet(s) as processed.")
        refresh()

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
            st.error(error)
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
