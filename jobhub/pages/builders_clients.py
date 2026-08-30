"""Builders and clients page with lazy section rendering."""
from __future__ import annotations

from ..runtime import *


BUILDER_SECTIONS = ["Add", "Edit", "Remove", "List"]


def _render_add_builder():
    pb_section_heading("Add builder / client", "Create a company or client record for jobs and contacts.")
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
        submitted = st.form_submit_button("Save Builder / Client", type="primary")

    if submitted and name:
        execute("""
            INSERT INTO builders_clients
            (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                type = excluded.type,
                contact_name = excluded.contact_name,
                phone = excluded.phone,
                email = excluded.email,
                address = excluded.address,
                qbcc = excluded.qbcc,
                abn = excluded.abn,
                terms = excluded.terms,
                notes = excluded.notes
        """, (typ, name, contact, phone, email, address, qbcc, abn, terms, notes))
        st.success(f"Saved {name}")
        refresh()


def _render_edit_builder():
    pb_section_heading("Edit builder / client", "Update one existing company or client record.")
    builders_df = df_query("SELECT * FROM builders_clients ORDER BY name")
    if builders_df.empty:
        pb_empty_state("No builders or clients yet", "Add one first, then it can be edited here.")
        return

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
        submitted = st.form_submit_button("Update Builder / Client", type="primary")

    if submitted:
        execute("""
            UPDATE builders_clients
            SET type = ?, name = ?, contact_name = ?, phone = ?, email = ?, address = ?,
                qbcc = ?, abn = ?, terms = ?, notes = ?
            WHERE id = ?
        """, (typ, name, contact, phone, email, address, qbcc, abn, terms, notes, selected_id))
        st.success(f"Updated {name}")
        refresh()


def _render_remove_builder():
    pb_section_heading("Remove builder / client", "Deletion is blocked while jobs are linked.")
    st.warning("If this builder/client has jobs linked, they cannot be deleted until the jobs are changed or archived.")
    builders_df = df_query("SELECT id, name FROM builders_clients ORDER BY name")
    if builders_df.empty:
        pb_empty_state("No builders or clients yet", "There is nothing to remove.")
        return

    builder_map = {row["name"]: int(row["id"]) for _, row in builders_df.iterrows()}
    selected_builder = st.selectbox("Select Builder / Client to Remove", list(builder_map.keys()), key="remove_builder_select")
    selected_id = builder_map[selected_builder]
    linked_jobs = df_query("SELECT COUNT(*) AS c FROM jobs WHERE builder_client_id = ?", (selected_id,))
    job_count = int(linked_jobs.iloc[0]["c"] or 0)
    st.metric("Linked jobs", job_count)

    if st.button("Delete Builder / Client", type="primary"):
        if job_count > 0:
            st.error("Cannot delete this builder/client because jobs are linked to them. Edit those jobs first or leave the record in JobHub.")
        else:
            execute("DELETE FROM builders_clients WHERE id = ?", (selected_id,))
            st.success("Builder/client deleted.")
            refresh()


def _render_builder_list():
    pb_section_heading("Builder & client list", "Review contact details and open linked jobs.")
    df = df_query("""
        SELECT id AS 'ID',
               type AS 'Type',
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
    if df.empty:
        pb_empty_state("No builders or clients yet", "Add a record to start building the customer directory.")
        return
    st.dataframe(df.drop(columns=["ID"], errors="ignore"), width="stretch", hide_index=True)

    pb_section_heading("View linked jobs", "Choose a builder/client to see the jobs associated with them.")
    builder_map = {str(row["Company / Client"]): int(row["ID"]) for _, row in df.iterrows()}
    selected_builder = st.selectbox(
        "Select builder/client to view linked jobs",
        list(builder_map.keys()),
        key="builder_list_linked_jobs_select",
    )
    selected_builder_id = builder_map[selected_builder]

    linked_jobs_df = job_lookup_dataframe(include_archived=True)
    if not linked_jobs_df.empty:
        linked_jobs_df = linked_jobs_df[linked_jobs_df["builder_id"].astype(int) == int(selected_builder_id)]

    if linked_jobs_df.empty:
        pb_empty_state("No linked jobs", "No jobs are currently associated with this builder/client.")
        if st.button("Open builder/client in Job Lookup", key="builder_list_open_builder_lookup"):
            go_to_linked_job_view(builder_id=selected_builder_id, mode="Jobs by Builder / Client")
        return

    st.dataframe(linked_jobs_df.drop(columns=["job_id", "builder_id"], errors="ignore"), width="stretch", hide_index=True)
    selected_builder_job_id = select_job_from_dataframe(
        linked_jobs_df,
        "Select one of this builder/client's jobs",
        key="builder_list_job_to_open_select",
    )
    col_open_builder, col_open_job = st.columns(2)
    if col_open_builder.button("Open builder/client in Job Lookup", key="builder_list_open_builder_lookup", width="stretch"):
        go_to_linked_job_view(builder_id=selected_builder_id, mode="Jobs by Builder / Client")
    if col_open_job.button("Open selected job and all linked info", key="builder_list_open_job_lookup", width="stretch"):
        go_to_linked_job_view(job_id=selected_builder_job_id, mode="Open Job")


def render_builders_clients():
    pb_page_header(
        "Builders & Clients",
        "Maintain the customer and builder directory used across jobs, contacts and reporting.",
    )
    section = st.radio(
        "Builder/client section",
        BUILDER_SECTIONS,
        horizontal=True,
        key="builders_clients_section",
        label_visibility="collapsed",
    )

    if section == "Edit":
        _render_edit_builder()
    elif section == "Remove":
        _render_remove_builder()
    elif section == "List":
        _render_builder_list()
    else:
        _render_add_builder()


# =============================
# EMPLOYEES - ADD / EDIT / REMOVE
# =============================
