"""Jobs page.

Expensive job-management sections are selected explicitly instead of rendered in
eager Streamlit tabs. This keeps normal job-page reruns focused on the section
the user is actually using.
"""
from __future__ import annotations

from ..runtime import *


JOB_SECTIONS = [
    "Add Job", "Edit Job", "Remove / Archive", "Archived Jobs",
    "Search by Builder", "Job Register",
]
JOB_STATUSES = [
    "Not Started", "Quoted", "Booked", "Active", "On Hold",
    "Completed", "Invoiced", "Paid", "Archived",
]


def job_date_value(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return pd.to_datetime(str(value).strip()[:10], errors="raise").date()
    except Exception:
        return None


def _render_add_job(builder_options):
    pb_section_heading("Add new job", "Create the core job record before adding stages, documents and labour.")
    with st.form("add_job_form"):
        col1, col2 = st.columns(2)
        job_no = col1.text_input("Job Number", next_job_no())
        job_name = col2.text_input("Job Name")
        builder_label = st.selectbox("Builder / Client", [""] + list(builder_options.keys()))
        site_address = st.text_input("Site Address")

        col3, col4, col5 = st.columns(3)
        status = col3.selectbox("Status", JOB_STATUSES)
        employee_options = get_employee_options(active_only=True)
        leading_hand = col4.selectbox("Leading Hand", [""] + list(employee_options.keys()))
        contract_value = col5.number_input("Contract Value Ex GST", min_value=0.0, step=100.0)

        col6, col7 = st.columns(2)
        start_date_value = col6.date_input("Start Date", value=None, format="DD/MM/YYYY")
        end_date_value = col7.date_input("End Date", value=None, format="DD/MM/YYYY")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save Job", type="primary")

    if not submitted:
        return
    if not str(job_no or "").strip():
        st.error("Enter a job number first.")
        return
    if start_date_value and end_date_value and end_date_value < start_date_value:
        st.error("End Date cannot be before Start Date.")
        return

    builder_id = builder_options.get(builder_label) if builder_label else None
    start_date = start_date_value.isoformat() if start_date_value else ""
    end_date = end_date_value.isoformat() if end_date_value else ""
    execute("""
        INSERT INTO jobs
        (job_no, job_name, builder_client_id, site_address, status, leading_hand, start_date, end_date, contract_value, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_no) DO UPDATE SET
            job_name = excluded.job_name,
            builder_client_id = excluded.builder_client_id,
            site_address = excluded.site_address,
            status = excluded.status,
            leading_hand = excluded.leading_hand,
            start_date = excluded.start_date,
            end_date = excluded.end_date,
            contract_value = excluded.contract_value,
            notes = excluded.notes
    "", (job_no, job_name, builder_id, site_address, status, leading_hand, start_date, end_date, contract_value, notes))
    st.success(f"Saved job {job_no}")
    refresh()


def _render_edit_job(builder_options):
    pb_section_heading("Edit existing job", "Change the main details for one job.")
    jobs_df = df_query("""
        SELECT j.*, COALESCE(bc.name, '') AS builder_name
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        ORDER BY j.job_no
    """)
    if jobs_df.empty:
        pb_empty_state("No jobs yet", "Add a job first, then it can be edited here.")
        return

    job_map = {f"{row['job_no']} - {row['job_name']}": int(row["id"]) for _, row in jobs_df.iterrows()}
    selected_job = st.selectbox("Select Job to Edit", list(job_map.keys()))
    selected_id = job_map[selected_job]
    current = jobs_df[jobs_df["id"] == selected_id].iloc[0]

    builder_names = [""] + list(builder_options.keys())
    current_builder = str(current["builder_name"] or "")
    builder_index = builder_names.index(current_builder) if current_builder in builder_names else 0
    current_status = str(current["status"] or "Not Started")
    status_index = JOB_STATUSES.index(current_status) if current_status in JOB_STATUSES else 0
    employee_options = get_employee_options(active_only=True)
    employee_names = [""] + list(employee_options.keys())
    current_leading_hand = str(current["leading_hand"] or "")
    leading_hand_index = employee_names.index(current_leading_hand) if current_leading_hand in employee_names else 0

    with st.form("edit_job_form"):
        col1, col2 = st.columns(2)
        edit_job_no = col1.text_input("Job Number", value=str(current["job_no"] or ""))
        edit_job_name = col2.text_input("Job Name", value=str(current["job_name"] or ""))
        edit_builder_label = st.selectbox("Builder / Client", builder_names, index=builder_index)
        edit_site_address = st.text_input("Site Address", value=str(current["site_address"] or ""))

        col3, col4, col5 = st.columns(3)
        edit_status = col3.selectbox("Status", JOB_STATUSES, index=status_index)
        edit_leading_hand = col4.selectbox("Leading Hand", employee_names, index=leading_hand_index)
        edit_contract_value = col5.number_input(
            "Contract Value Ex GST", min_value=0.0, step=100.0,
            value=float(current["contract_value"] or 0),
        )
        col6, col7 = st.columns(2)
        edit_start_date_value = col6.date_input(
            "Start Date", value=job_date_value(current["start_date"]), format="DD/MM/YYYY"
        )
        edit_end_date_value = col7.date_input(
            "End Date", value=job_date_value(current["end_date"]), format="DD/MM/YYYY"
        )
        edit_notes = st.text_area("Notes", value=str(current["notes"] or ""))
        submitted = st.form_submit_button("Update Job", type="primary")

    if not submitted:
        return
    if edit_start_date_value and edit_end_date_value and edit_end_date_value < edit_start_date_value:
        st.error("End Date cannot be before Start Date.")
        return
    edit_builder_id = builder_options.get(edit_builder_label) if edit_builder_label else None
    edit_start_date = edit_start_date_value.isoformat() if edit_start_date_value else ""
    edit_end_date = edit_end_date_value.isoformat() if edit_end_date_value else ""
    execute("""
        UPDATE jobs
        SET job_no = ?, job_name = ?, builder_client_id = ?, site_address = ?, status = ?,
            leading_hand = ?, start_date = ?, end_date = ?, contract_value = ?, notes = ?
        WHERE id = ?
    "", (
        edit_job_no, edit_job_name, edit_builder_id, edit_site_address, edit_status,
        edit_leading_hand, edit_start_date, edit_end_date, edit_contract_value, edit_notes, selected_id,
    ))
    st.success(f"Updated job {edit_job_no}")
    refresh()


def _render_remove_job():
    pb_section_heading("Remove or archive", "Archive jobs with linked history; delete only clean records.")
    st.warning("If a job has wages, materials or equipment saved against it, archive it instead of deleting it.")
    jobs_df = df_query("SELECT id, job_no, job_name FROM jobs ORDER BY job_no")
    if jobs_df.empty:
        pb_empty_state("No jobs yet", "There are no job records to remove or archive.")
        return

    job_map = {f"{row['job_no']} - {row['job_name']}": int(row["id"]) for _, row in jobs_df.iterrows()}
    selected_job = st.selectbox("Select Job", list(job_map.keys()), key="remove_job_select")
    selected_id = job_map[selected_job]
    col1, col2 = st.columns(2)

    if col1.button("Archive Job", width="stretch"):
        execute("UPDATE jobs SET status = 'Archived' WHERE id = ?", (selected_id,))
        st.success("Job archived.")
        refresh()

    if col2.button("Delete Job", width="stretch"):
        linked_counts = linked_job_counts(selected_id)
        if any(int(value or 0) > 0 for value in linked_counts.values()):
            execute("UPDATE jobs SET status = 'Archived' WHERE id = ?", (selected_id,))
            st.info("This job has linked records, so it was archived instead of deleted.")
        else:
            permanently_delete_job_and_linked_data(selected_id)
            st.success("Job deleted.")
        refresh()


def _render_archived_jobs():
    pb_section_heading("Archived jobs", "Review, restore, edit or permanently delete archived records.")
    archived_df = df_query("""
        SELECT j.*, COALESCE(bc.name, '') AS builder_name
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.status = 'Archived'
        ORDER BY j.job_no
    """)
    if archived_df.empty:
        pb_empty_state("No archived jobs", "Archived jobs will appear here.")
        return

    archived_view = archived_df[[
        "job_no", "job_name", "builder_name", "site_address", "leading_hand",
        "start_date", "end_date", "contract_value", "notes",
    ]].rename(columns={
        "job_no": "Job No", "job_name": "Job Name", "builder_name": "Builder / Client",
        "site_address": "Site Address", "leading_hand": "Leading Hand",
        "start_date": "Start Date", "end_date": "End Date",
        "contract_value": "Contract Value", "notes": "Notes",
    })
    st.dataframe(archived_view, width="stretch", hide_index=True)

    archived_map = {
        f"{row['job_no']} - {row['job_name']}": int(row["id"])
        for _, row in archived_df.iterrows()
    }
    selected_archived_job = st.selectbox("Select Archived Job", list(archived_map.keys()), key="archived_job_select")
    selected_archived_id = archived_map[selected_archived_job]
    current = archived_df[archived_df["id"] == selected_archived_id].iloc[0]
    counts = linked_job_counts(selected_archived_id)

    pb_section_heading("Linked data", "History that will be affected by a permanent delete.")
    count_df = pd.DataFrame([
        ["Materials", counts.get("material_entries", 0)],
        ["Wages", counts.get("wage_entries", 0)],
        ["Old Equipment Entries", counts.get("equipment_entries", 0)],
        ["Equipment Checklist Lines", counts.get("equipment_checklist_records", 0)],
        ["Imported Checklist Materials", counts.get("imported_material_entries", 0)],
    ], columns=["Linked Data", "Record Count"])
    st.dataframe(count_df, width="stretch", hide_index=True)

    builder_options = get_builder_options()
    builder_names = [""] + list(builder_options.keys())
    current_builder = str(current["builder_name"] or "")
    builder_index = builder_names.index(current_builder) if current_builder in builder_names else 0
    employee_options = get_employee_options(active_only=True)
    employee_names = [""] + list(employee_options.keys())
    current_leading_hand = str(current["leading_hand"] or "")
    leading_hand_index = employee_names.index(current_leading_hand) if current_leading_hand in employee_names else 0
    archived_statuses = ["Archived"] + [status for status in JOB_STATUSES if status != "Archived"]

    pb_section_heading("Edit archived job", "Update details or change the status to restore the job.")
    with st.form("edit_archived_job_form"):
        col1, col2 = st.columns(2)
        edit_job_no = col1.text_input("Job Number", value=str(current["job_no"] or ""), key="arch_job_no")
        edit_job_name = col2.text_input("Job Name", value=str(current["job_name"] or ""), key="arch_job_name")
        edit_builder_label = st.selectbox("Builder / Client", builder_names, index=builder_index, key="arch_builder")
        edit_site_address = st.text_input("Site Address", value=str(current["site_address"] or ""), key="arch_site_address")
        col3, col4, col5 = st.columns(3)
        edit_status = col3.selectbox("Status", archived_statuses, index=0, key="arch_status")
        edit_leading_hand = col4.selectbox("Leading Hand", employee_names, index=leading_hand_index, key="arch_leading_hand")
        edit_contract_value = col5.number_input(
            "Contract Value Ex GST", min_value=0.0, step=100.0,
            value=float(current["contract_value"] or 0), key="arch_contract_value",
        )
        col6, col7 = st.columns(2)
        edit_start_date_value = col6.date_input(
            "Start Date", value=job_date_value(current["start_date"]),
            format="DD/MM/YYYY", key="arch_start_date",
        )
        edit_end_date_value = col7.date_input(
            "End Date", value=job_date_value(current["end_date"]),
            format="DD/MM/YYYY", key="arch_end_date",
        )
        edit_notes = st.text_area("Notes", value=str(current["notes"] or ""), key="arch_notes")
        update_archived = st.form_submit_button("Update Archived Job", type="primary")

    if update_archived:
        if edit_start_date_value and edit_end_date_value and edit_end_date_value < edit_start_date_value:
            st.error("End Date cannot be before Start Date.")
        else:
            edit_builder_id = builder_options.get(edit_builder_label) if edit_builder_label else None
            edit_start_date = edit_start_date_value.isoformat() if edit_start_date_value else ""
            edit_end_date = edit_end_date_value.isoformat() if edit_end_date_value else ""
            execute("""
                UPDATE jobs
                SET job_no = ?, job_name = ?, builder_client_id = ?, site_address = ?, status = ?,
                    leading_hand = ?, start_date = ?, end_date = ?, contract_value = ?, notes = ?
                WHERE id = ?
            "", (
                edit_job_no, edit_job_name, edit_builder_id, edit_site_address, edit_status,
                edit_leading_hand, edit_start_date, edit_end_date, edit_contract_value,
                edit_notes, selected_archived_id,
            ))
            st.success(
                f"Updated and restored job {edit_job_no}." if edit_status != "Archived"
                else f"Updated archived job {edit_job_no}."
            )
            refresh()

    pb_section_heading("Restore or permanently delete", "Permanent deletion cannot be undone.")
    col_restore, col_delete = st.columns(2)
    if col_restore.button("Restore Archived Job to Active", width="stretch"):
        execute("UPDATE jobs SET status = 'Active' WHERE id = ?", (selected_archived_id,))
        st.success("Job restored to Active.")
        refresh()

    with col_delete:
        st.warning("Permanent delete removes the archived job and all linked materials, wages, equipment and imported checklist data.")
        confirm_delete = st.checkbox(
            "I understand this will permanently delete this archived job and all linked data.",
            key="confirm_delete_archived_job",
        )
        if st.button("Permanently Delete Archived Job", width="stretch"):
            if not confirm_delete:
                st.error("Tick the confirmation box before permanently deleting.")
            else:
                permanently_delete_job_and_linked_data(selected_archived_id)
                st.success("Archived job and linked data permanently deleted.")
                refresh()


def _render_search_by_builder(builder_options):
    pb_section_heading("Search by builder / client", "Find every job associated with one builder or client.")
    selected_builder = st.selectbox(
        "Select Builder / Client", [""] + list(builder_options.keys()), key="job_search_builder"
    )
    if not selected_builder:
        pb_empty_state("Choose a builder or client", "Their linked jobs will appear here.")
        return

    search_df = df_query("""
        SELECT j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               j.status AS 'Status',
               j.site_address AS 'Site Address'
        FROM jobs j
        JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE bc.name = ?
        ORDER BY j.job_no
    "", (selected_builder,))
    if search_df.empty:
        pb_empty_state("No linked jobs", "No jobs are currently associated with this builder or client.")
    else:
        st.dataframe(search_df, width="stretch", hide_index=True)

    if st.button("Open this builder/client in Job Lookup", key="open_search_builder_linked_view"):
        go_to_linked_job_view(builder_id=builder_options[selected_builder], mode="Jobs by Builder / Client")


def _render_job_register():
    pb_section_heading("Full job register", "Browse jobs and open the complete linked job file.")
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
    if job_df.empty:
        pb_empty_state("No jobs found", "Add a job or include archived jobs to populate the register.")
    else:
        st.dataframe(job_df, width="stretch", hide_index=True)

    pb_section_heading("Open linked job info", "Open job details, builder, labour, materials, documents and commercial records.")
    open_jobs_df = job_lookup_dataframe(include_archived=include_archived)
    selected_open_job_id = select_job_from_dataframe(
        open_jobs_df,
        "Select job number / name / builder / address to open",
        key="job_register_open_linked_select",
        default_job_id=st.session_state.get("linked_view_selected_job_id"),
    )
    if selected_open_job_id and st.button("Open selected job and all linked info", key="job_register_open_linked_button"):
        go_to_linked_job_view(job_id=selected_open_job_id, mode="Open Job")


def render_jobs():
    pb_page_header(
        "Job Register",
        "Create, maintain, archive and search the jobs that drive JobHub.",
    )
    section = st.radio(
        "Job register section",
        JOB_SECTIONS,
        horizontal=True,
        key="job_register_section",
        label_visibility="collapsed",
    )

    # Builder options are needed by four sections, but not by Remove/Archive or
    # the full register. Avoid the lookup on those paths.
    if section == "Remove / Archive":
        _render_remove_job()
    elif section == "Archived Jobs":
        _render_archived_jobs()
    elif section == "Job Register":
        _render_job_register()
    else:
        builder_options = get_builder_options()
        if section == "Edit Job":
            _render_edit_job(builder_options)
        elif section == "Search by Builder":
            _render_search_by_builder(builder_options)
        else:
            _render_add_job(builder_options)


# =============================
# ESTIMATE WORKING SHEET
# =============================
