"""Employees page.

The page uses one selected section instead of eager Streamlit tabs so hidden
employee forms and list queries are not executed on every rerun.
"""
from __future__ import annotations

from ..runtime import *


EMPLOYEE_SECTIONS = ["Add", "Edit", "Remove / Deactivate", "List"]


def _render_add_employee():
    pb_section_heading("Add employee", "Create a worker record and their costing defaults.")
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
        submitted = st.form_submit_button("Save Employee", type="primary")

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


def _render_edit_employee():
    pb_section_heading("Edit employee", "Update one existing employee record.")
    employees_df = df_query("SELECT * FROM employees ORDER BY name")
    if employees_df.empty:
        pb_empty_state("No employees yet", "Add an employee first, then they can be edited here.")
        return

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
        base_rate = col3.number_input(
            "Base Hourly Rate", min_value=0.0, step=1.0,
            value=float(current["base_hourly_rate"] or 0),
        )
        rate_plus = col4.number_input(
            "Rate + 10%", min_value=0.0, step=1.0,
            value=float(current["rate_plus_10"] or 0),
        )

        statuses = ["Active", "Inactive"]
        current_status = str(current["status"] or "Active")
        status_index = statuses.index(current_status) if current_status in statuses else 0
        status = st.selectbox("Status", statuses, index=status_index)
        notes = st.text_area("Notes", value=str(current["notes"] or ""))
        submitted = st.form_submit_button("Update Employee", type="primary")

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


def _render_remove_employee():
    pb_section_heading("Remove or deactivate", "Preserve worker history while safely removing access.")
    st.warning(
        "If the employee has wage records, timesheets, or a linked user login, "
        "JobHub will mark them Inactive instead of deleting protected history."
    )
    employees_df = df_query("SELECT id, name FROM employees ORDER BY name")
    if employees_df.empty:
        pb_empty_state("No employees yet", "There are no employee records to remove or deactivate.")
        return

    employee_map = {row["name"]: int(row["id"]) for _, row in employees_df.iterrows()}
    selected_employee = st.selectbox("Select Employee", list(employee_map.keys()), key="remove_employee_select")
    selected_id = employee_map[selected_employee]

    col1, col2 = st.columns(2)
    if col1.button("Deactivate Employee", width="stretch"):
        execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (selected_id,))
        if has_related_records("app_users", "employee_id", selected_id):
            execute("UPDATE app_users SET active = 0 WHERE employee_id = ?", (selected_id,))
        st.success("Employee marked Inactive.")
        refresh()

    if col2.button("Delete Employee", width="stretch"):
        result = delete_employee_and_linked_users(selected_id)
        _render_delete_result(result)
        refresh()


def _render_delete_result(result):
    if result["deleted_users"]:
        st.success(f"Deleted {result['deleted_users']} linked user login account(s).")
    if result["deleted_employee"]:
        st.success(f"Deleted {result['deleted_employee']} employee record(s).")
    if result["deactivated_employee"]:
        st.info(
            f"Marked {result['deactivated_employee']} employee(s) as Inactive "
            "because they had job history or protected linked records."
        )
    if result["skipped"]:
        st.warning(f"Skipped {result['skipped']} item(s).")
    with st.expander("Employee delete details"):
        for msg in result["messages"]:
            st.write(msg)


def _employee_list(show_inactive_workers):
    if show_inactive_workers:
        return df_query("""
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
    return df_query("""
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


def _render_employee_list():
    pb_section_heading("Employee list", "Review active workers and perform controlled bulk cleanup.")
    show_inactive_workers = st.checkbox(
        "Show inactive workers",
        value=False,
        key="show_inactive_workers_employee_list",
    )
    df = _employee_list(show_inactive_workers)

    if df.empty:
        message = "No employees found." if show_inactive_workers else "No active employees found."
        detail = "" if show_inactive_workers else "Show inactive workers to review deactivated records."
        pb_empty_state(message, detail)
        return

    st.dataframe(df, width="stretch", hide_index=True)
    pb_section_heading("Remove multiple employees", "Protected history is retained automatically.")
    st.warning(
        "This deletes selected employees and linked user logins where safe. "
        "Workers with wages or timesheets are deactivated instead."
    )

    employee_delete_options = {
        f"{row['Employee']} | {row['Role'] or 'No Role'} | {row['Status']} | ID {row['ID']}": int(row["ID"])
        for _, row in df.iterrows()
    }
    selected_employee_labels = st.multiselect(
        "Select employees to delete or deactivate",
        list(employee_delete_options.keys()),
        key="bulk_employee_delete_multiselect",
    )
    selected_employee_ids = [employee_delete_options[label] for label in selected_employee_labels]

    if selected_employee_ids:
        selected_preview = df[df["ID"].astype(int).isin(selected_employee_ids)]
        st.caption("Selected employees")
        st.dataframe(selected_preview, width="stretch", hide_index=True)

    employee_bulk_confirm = st.text_input(
        "To delete/deactivate the selected employees, type: DELETE EMPLOYEES",
        key="bulk_employee_delete_confirm",
    )
    if st.button("Delete / Deactivate Selected Employees", key="bulk_employee_delete_button"):
        if not selected_employee_ids:
            st.error("Select at least one employee first.")
        elif employee_bulk_confirm.strip().upper() != "DELETE EMPLOYEES":
            st.error("Type DELETE EMPLOYEES exactly before continuing.")
        else:
            result = delete_or_deactivate_selected_employees(selected_employee_ids)
            _render_delete_result(result)
            refresh()


def render_employees():
    pb_page_header(
        "Employees",
        "Manage worker details, costing rates, status and protected employee history.",
    )
    section = st.radio(
        "Employee section",
        EMPLOYEE_SECTIONS,
        horizontal=True,
        key="employee_page_section",
        label_visibility="collapsed",
    )

    if section == "Edit":
        _render_edit_employee()
    elif section == "Remove / Deactivate":
        _render_remove_employee()
    elif section == "List":
        _render_employee_list()
    else:
        _render_add_employee()


# =============================
# PRODUCTS
# =============================
