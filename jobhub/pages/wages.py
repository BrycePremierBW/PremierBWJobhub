"""Wages page."""
from __future__ import annotations

from ..runtime import *


def render_wages():
    st.header("Wages")

    render_context_pdf_import_for_selected_job(
        context="wages",
        title="Import wage, payroll support or day labour PDFs",
        key_prefix="wages_pdf_import",
    )
    st.divider()

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

                period_type = st.radio(
                    "Entry Type",
                    ["Single Day", "Week Ending"],
                    horizontal=True,
                    key="wage_entry_period_type",
                )

                if period_type == "Single Day":
                    col1, col2 = st.columns(2)
                    work_day = col1.date_input("Date", value=date.today(), key="wage_single_date")
                    hours = col2.number_input("Hours", min_value=0.0, step=0.5, key="wage_single_hours")
                    work_date = str(work_day)
                    period_start = str(work_day)
                    period_end = str(work_day)
                else:
                    col1, col2, col3 = st.columns(3)
                    default_week_end = date.today()
                    default_week_start = default_week_end - timedelta(days=4)
                    from_date = col1.date_input("From Date", value=default_week_start, key="wage_week_from")
                    week_ending = col2.date_input("Week Ending", value=default_week_end, key="wage_week_ending")
                    hours = col3.number_input("Total Hours for This Job / Week", min_value=0.0, step=0.5, value=38.0, key="wage_week_hours")
                    work_date = str(from_date)
                    period_start = str(from_date)
                    period_end = str(week_ending)
                    st.caption("Use this when the employee was on the same job for the full week. It saves one wage entry instead of daily entries.")

                notes = st.text_area("Notes")
                submitted = st.form_submit_button("Save Wage Entry")

                if submitted:
                    if hours <= 0:
                        st.error("Hours must be greater than 0.")
                    elif period_type == "Week Ending" and period_end < period_start:
                        st.error("Week ending date must be after the from date.")
                    else:
                        execute("""
                            INSERT INTO wage_entries
                            (job_id, employee_id, work_date, hours, notes, period_type, period_start, period_end)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (job_options[job_label], employee_id, work_date, hours, notes, period_type, period_start, period_end))
                        st.success("Wage entry saved.")
                        refresh()

    df = df_query("""
        SELECT w.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               e.name AS 'Employee',
               COALESCE(NULLIF(w.period_type, ''), 'Single Day') AS 'Period',
               COALESCE(NULLIF(w.period_start, ''), w.work_date) AS 'From Date',
               COALESCE(NULLIF(w.period_end, ''), w.work_date) AS 'Week Ending / To Date',
               w.hours AS 'Hours',
               e.base_hourly_rate AS 'Base Rate',
               e.rate_plus_10 AS 'Rate + 10%',
               ROUND(w.hours * e.rate_plus_10, 2) AS 'Total Wage Cost',
               w.notes AS 'Notes'
        FROM wage_entries w
        JOIN jobs j ON j.id = w.job_id
        JOIN employees e ON e.id = w.employee_id
        ORDER BY COALESCE(NULLIF(w.period_start, ''), w.work_date) DESC, w.id DESC
    """)
    st.dataframe(df, width="stretch", hide_index=True)

    st.markdown("### Delete Wage Entries")
    st.caption("Use this for wrong duplicate or accidental wage entries. This deletes wage rows only; it does not delete any timesheet record.")
    if df.empty:
        st.info("No wage entries to delete.")
    else:
        wage_options = {
            f"ID {row['ID']} | {row['From Date']} to {row['Week Ending / To Date']} | {row['Employee']} | {row['Job No']} - {row['Job Name']} | {row['Hours']} hrs | ${float(row['Total Wage Cost'] or 0):,.2f}": int(row["ID"])
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
                st.error("Select at least one wage entry first.")
            elif delete_wages_confirm.strip().upper() != "DELETE WAGES":
                st.error("Type DELETE WAGES exactly before deleting wage entries.")
            else:
                for wage_id in selected_wage_ids:
                    execute("DELETE FROM wage_entries WHERE id = ?", (int(wage_id),))
                st.success(f"Deleted {len(selected_wage_ids)} wage entr{'y' if len(selected_wage_ids) == 1 else 'ies'}.")
                refresh()


# =============================
# EQUIPMENT CHECKLIST
# =============================
