"""Employees page."""
from __future__ import annotations

from ..runtime import *


def render_employees():
    st.header("Employees")

    tab_add, tab_edit, tab_remove, tab_list = st.tabs(["Add", "Edit", "Remove / Deactivate", "List"])

    with tab_add:
        st.subheader("Add Employee")
        with st.form("add_employee_form"):
            col1, col2 = st.columns(2)
            name = col1.text_input("Employee Name")
            role = col2.text_input("Role")
            phone = st.text_input("Phone")
            col3, col4 = st.columns(2)
            base_rate = col3.number_input("Base Hourly Rate", min_value=0.0, step=1.0)
            rate_plus = col4.number_input("Rate + 10%", min_value=0.0, step=1.0, value=0.0)
            status = st.selectbox("Status", ["Active", "Inactive"])
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Save Employee")

            if submitted and name:
                if rate_plus == 0 and base_rate > 0:
                    rate_plus = round(base_rate * 1.10, 2)
                execute("""
                    INSERT INTO employees
                    (name, role, phone, base_hourly_rate, rate_plus_10, status, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        role = excluded.role,
                        phone = excluded.phone,
                        base_hourly_rate = excluded.base_hourly_rate,
                        rate_plus_10 = excluded.rate_plus_10,
                        status = excluded.status,
                        notes = excluded.notes
                """, (name, role, phone, base_rate, rate_plus, status, notes))
                st.success(f"Saved {name}")
                refresh()

    with tab_edit:
        st.subheader("Edit Employee")
        employees_df = df_query("SELECT * FROM employees ORDER BY name")
        if employees_df.empty:
            st.info("No employees yet.")
        else:
            employee_map = {row["name"]: int(row["id"]) for _, row in employees_df.iterrows()}
            selected_employee = st.selectbox("Select Employee to Edit", list(employee_map.keys()))
            selected_id = employee_map[selected_employee]
            current = employees_df[employees_df["id"] == selected_id].iloc[0]

            with st.form("edit_employee_form"):
                col1, col2 = st.columns(2)
                name = col1.text_input("Employee Name", value=str(current["name"] or ""))
                role = col2.text_input("Role", value=str(current["role"] or ""))
                phone = st.text_input("Phone", value=str(current["phone"] or ""))

                col3, col4 = st.columns(2)
                base_rate = col3.number_input("Base Hourly Rate", min_value=0.0, step=1.0, value=float(current["base_hourly_rate"] or 0))
                rate_plus = col4.number_input("Rate + 10%", min_value=0.0, step=1.0, value=float(current["rate_plus_10"] or 0))

                statuses = ["Active", "Inactive"]
                current_status = str(current["status"] or "Active")
                status_index = statuses.index(current_status) if current_status in statuses else 0
                status = st.selectbox("Status", statuses, index=status_index)

                notes = st.text_area("Notes", value=str(current["notes"] or ""))
                submitted = st.form_submit_button("Update Employee")

                if submitted:
                    if rate_plus == 0 and base_rate > 0:
                        rate_plus = round(base_rate * 1.10, 2)
                    execute("""
                        UPDATE employees
                        SET name = ?, role = ?, phone = ?, base_hourly_rate = ?, rate_plus_10 = ?, status = ?, notes = ?
                        WHERE id = ?
                    """, (name, role, phone, base_rate, rate_plus, status, notes, selected_id))
                    st.success(f"Updated {name}")
                    refresh()

    with tab_remove:
        st.subheader("Remove or Deactivate Employee")
        st.warning("If the employee has wage records, timesheets, or a linked user login, the app will mark them Inactive instead of deleting their history.")
        employees_df = df_query("SELECT id, name FROM employees ORDER BY name")
        if employees_df.empty:
            st.info("No employees yet.")
        else:
            employee_map = {row["name"]: int(row["id"]) for _, row in employees_df.iterrows()}
            selected_employee = st.selectbox("Select Employee", list(employee_map.keys()), key="remove_employee_select")
            selected_id = employee_map[selected_employee]

            col1, col2 = st.columns(2)
            if col1.button("Deactivate Employee"):
                execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (selected_id,))
                # If this employee has a login, disable that login as well.
                if has_related_records("app_users", "employee_id", selected_id):
                    execute("UPDATE app_users SET active = 0 WHERE employee_id = ?", (selected_id,))
                st.success("Employee marked Inactive.")
                refresh()

            if col2.button("Delete Employee"):
                result = delete_employee_and_linked_users(selected_id)

                if result["deleted_users"]:
                    st.success(f"Deleted {result['deleted_users']} linked user login account(s).")

                if result["deleted_employee"]:
                    st.success(f"Deleted {result['deleted_employee']} employee record(s).")

                if result["deactivated_employee"]:
                    st.info(f"Marked {result['deactivated_employee']} employee(s) as Inactive because they had job history or protected linked records.")

                if result["skipped"]:
                    st.warning(f"Skipped {result['skipped']} item(s).")

                with st.expander("Employee delete details"):
                    for msg in result["messages"]:
                        st.write(msg)

                refresh()

    with tab_list:
        st.subheader("Employee List")

        show_inactive_workers = st.checkbox(
            "Show inactive workers",
            value=False,
            key="show_inactive_workers_employee_list"
        )

        if show_inactive_workers:
            df = df_query("""
                SELECT id AS 'ID',
                       name AS 'Employee',
                       role AS 'Role',
                       phone AS 'Phone',
                       base_hourly_rate AS 'Base Rate',
                       rate_plus_10 AS 'Rate + 10%',
                       status AS 'Status',
                       notes AS 'Notes'
                FROM employees
                ORDER BY status, name
            """)
        else:
            df = df_query("""
                SELECT id AS 'ID',
                       name AS 'Employee',
                       role AS 'Role',
                       phone AS 'Phone',
                       base_hourly_rate AS 'Base Rate',
                       rate_plus_10 AS 'Rate + 10%',
                       status AS 'Status',
                       notes AS 'Notes'
                FROM employees
                WHERE status = 'Active'
                ORDER BY name
            """)

        if df.empty:
            if show_inactive_workers:
                st.info("No employees found.")
            else:
                st.info("No active employees found. Tick 'Show inactive workers' to view inactive records.")
        else:
            st.dataframe(df, width="stretch", hide_index=True)

            st.markdown("### Remove Multiple Employees")
            st.warning(
                "This deletes the selected employee and linked user login account where safe. "
                "If an employee has wages or timesheets, the linked login will be deleted and the employee will be marked Inactive instead."
            )

            employee_delete_options = {
                f"{row['Employee']} | {row['Role'] or 'No Role'} | {row['Status']} | ID {row['ID']}": int(row["ID"])
                for _, row in df.iterrows()
            }

            selected_employee_labels = st.multiselect(
                "Select employees to delete or deactivate",
                list(employee_delete_options.keys()),
                key="bulk_employee_delete_multiselect"
            )

            selected_employee_ids = [employee_delete_options[label] for label in selected_employee_labels]

            if selected_employee_ids:
                selected_preview = df[df["ID"].astype(int).isin(selected_employee_ids)]
                st.markdown("Selected employees:")
                st.dataframe(selected_preview, width="stretch", hide_index=True)

            employee_bulk_confirm = st.text_input(
                "To delete/deactivate the selected employees, type: DELETE EMPLOYEES",
                key="bulk_employee_delete_confirm"
            )

            if st.button("Delete / Deactivate Selected Employees", key="bulk_employee_delete_button"):
                if not selected_employee_ids:
                    st.error("Select at least one employee first.")
                elif employee_bulk_confirm.strip().upper() != "DELETE EMPLOYEES":
                    st.error("Type DELETE EMPLOYEES exactly before continuing.")
                else:
                    result = delete_or_deactivate_selected_employees(selected_employee_ids)

                    if result["deleted_users"]:
                        st.success(f"Deleted {result['deleted_users']} linked user login account(s).")

                    if result["deleted_employee"]:
                        st.success(f"Deleted {result['deleted_employee']} employee record(s).")

                    if result["deactivated_employee"]:
                        st.info(f"Marked {result['deactivated_employee']} employee(s) as Inactive because they had job history or protected linked records.")

                    if result["skipped"]:
                        st.warning(f"Skipped {result['skipped']} item(s).")

                    with st.expander("Employee delete/deactivate details"):
                        for msg in result["messages"]:
                            st.write(msg)

                    refresh()


# =============================
# PRODUCTS
# =============================
