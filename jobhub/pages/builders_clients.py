"""Builders Clients page."""
from __future__ import annotations

from ..runtime import *


def render_builders_clients():
    st.header("Builders & Clients")

    tab_add, tab_edit, tab_remove, tab_list = st.tabs(["Add", "Edit", "Remove", "List"])

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
                            qbcc = ?, abn = ?, terms = ?, notes = ?
                        WHERE id = ?
                    """, (typ, name, contact, phone, email, address, qbcc, abn, terms, notes, selected_id))
                    st.success(f"Updated {name}")
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
                    st.error("Cannot delete this builder/client because jobs are linked to them. Edit those jobs first or leave the builder in the database.")
                else:
                    execute("DELETE FROM builders_clients WHERE id = ?", (selected_id,))
                    st.success("Builder/client deleted.")
                    refresh()

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
