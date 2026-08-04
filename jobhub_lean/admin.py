from __future__ import annotations

from datetime import datetime

import streamlit as st

from .auth import hash_password, is_admin
from .common import AppContext, _clean, _int, employee_options
from .compat import build_enterprise_context
from .ui import header, rerun_success, selected_row


def users_page(ctx: AppContext) -> None:
    if not is_admin():
        st.error("Administrator access required.")
        return
    header("User Access", "Accounts, roles and employee links.")
    frame = ctx.db.query(
        """
        SELECT u.id,u.username,COALESCE(u.role,'') AS role,COALESCE(e.name,'') AS employee,
               COALESCE(u.active,1) AS active,COALESCE(u.notes,'') AS notes
        FROM app_users u LEFT JOIN employees e ON e.id=u.employee_id ORDER BY u.username
        """
    )
    row = selected_row(frame, key="users_table")
    if row:
        st.session_state["lean_selected_user_id"] = _int(row.get("id"))
    selected_id = _int(st.session_state.get("lean_selected_user_id"))
    employees = employee_options(ctx, active_only=False)
    employee_map = {"No linked employee": 0, **employees}
    roles = ["employee", "manager", "admin"]
    with st.expander("Add user", expanded=frame.empty):
        with st.form("user_add"):
            username = st.text_input("Username")
            password = st.text_input("Temporary password", type="password")
            role = st.selectbox("Role", roles)
            employee_label = st.selectbox("Linked employee", list(employee_map))
            notes = st.text_area("Notes")
            add = st.form_submit_button("Create user", type="primary")
        if add:
            if not username.strip() or len(password) < 10:
                st.error("Enter a username and a password of at least 10 characters.")
            else:
                user_id = ctx.db.insert_id(
                    """
                    INSERT INTO app_users(username,password_hash,role,employee_id,active,must_change_password,notes,password_changed_at)
                    VALUES (?,?,?,?,1,1,?,?)
                    """,
                    (username.strip(), hash_password(password), role, employee_map.get(employee_label) or None, notes.strip(), datetime.now().isoformat(timespec="seconds")),
                )
                ctx.audit("create", "app_users", user_id, username.strip())
                rerun_success("User created.")
    if selected_id:
        detail = ctx.db.query("SELECT * FROM app_users WHERE id=?", (selected_id,))
        if not detail.empty:
            user = detail.iloc[0].to_dict()
            current_employee = next((label for label, value in employee_map.items() if value == _int(user.get("employee_id"))), "No linked employee")
            with st.expander("Edit selected user", expanded=True):
                with st.form(f"user_edit_{selected_id}"):
                    username = st.text_input("Username", value=_clean(user.get("username")))
                    current_role = _clean(user.get("role")) or "employee"
                    if current_role not in roles:
                        roles.append(current_role)
                    role = st.selectbox("Role", roles, index=roles.index(current_role))
                    employee_label = st.selectbox("Linked employee", list(employee_map), index=list(employee_map).index(current_employee))
                    active = st.checkbox("Active", value=bool(_int(user.get("active", 1))))
                    notes = st.text_area("Notes", value=_clean(user.get("notes")))
                    new_password = st.text_input("New password (leave blank to keep current)", type="password")
                    update = st.form_submit_button("Update user", type="primary")
                if update:
                    if new_password and len(new_password) < 10:
                        st.error("Use at least 10 characters for the new password.")
                    else:
                        ctx.db.execute(
                            "UPDATE app_users SET username=?,role=?,employee_id=?,active=?,notes=? WHERE id=?",
                            (username.strip(), role, employee_map.get(employee_label) or None, int(active), notes.strip(), selected_id),
                        )
                        if new_password:
                            ctx.db.execute(
                                "UPDATE app_users SET password_hash=?,must_change_password=0,password_changed_at=? WHERE id=?",
                                (hash_password(new_password), datetime.now().isoformat(timespec="seconds"), selected_id),
                            )
                        ctx.audit("update", "app_users", selected_id, username.strip())
                        rerun_success("User updated.")


def _run_linked_sync(ctx: AppContext) -> tuple[int, int]:
    moved = progress = 0
    try:
        from pb_jobhub_visual_scheduler import sync_linked_job_dates
        moved = int(sync_linked_job_dates() or 0)
    except Exception:
        moved = 0
    try:
        from jobhub_progress_tracker import sync_all_linked_progress
        progress = int(sync_all_linked_progress(build_enterprise_context(ctx)) or 0)
    except Exception:
        progress = 0
    return moved, progress


def system_page(ctx: AppContext) -> None:
    if not is_admin():
        st.error("Administrator access required.")
        return
    header("System", "Manual maintenance only. No hidden rerun heartbeat or full-app sync loop.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Database", "PostgreSQL" if ctx.db.postgres else "SQLite")
    c2.metric("Data folder", str(ctx.data_dir))
    c3.metric("Job files", str(ctx.job_files_dir))
    if st.button("Synchronise linked schedule and progress now", type="primary"):
        moved, progress = _run_linked_sync(ctx)
        ctx.audit("manual_sync", "system", None, f"schedule={moved}; progress={progress}")
        st.success(f"Sync complete. Schedule moves: {moved}; progress additions/updates: {progress}.")
    st.subheader("Recent audit events")
    audit = ctx.db.query(
        """
        SELECT created_at AS "Time",username AS "User",action AS "Action",
               entity_type AS "Type",entity_id AS "ID",details AS "Details"
        FROM audit_events ORDER BY id DESC LIMIT 250
        """
    )
    st.dataframe(audit, hide_index=True, use_container_width=True)


def external_page(ctx: AppContext, page: str) -> None:
    module_context = build_enterprise_context(ctx)
    try:
        if page == "Field Mode":
            from jobhub_enterprise import ensure_enterprise_schema, render_field_mode
            ensure_enterprise_schema(ctx.db.connect)
            render_field_mode(module_context)
        elif page == "Operations Hub":
            from jobhub_enterprise import ensure_enterprise_schema, render_operations_hub
            ensure_enterprise_schema(ctx.db.connect)
            render_operations_hub(module_context)
        elif page == "Staff Scheduler":
            from pb_jobhub_visual_scheduler import render_jobhub_staff_scheduler
            render_jobhub_staff_scheduler(ctx.user)
        elif page == "Job Progress":
            from jobhub_progress_tracker import render_progress_tracker
            render_progress_tracker(module_context)
        elif page == "Painting Intelligence":
            from jobhub_v4.streamlit_painting import render_painting_intelligence
            render_painting_intelligence(module_context)
        else:
            st.error(f"Unknown modular page: {page}")
    except Exception as exc:
        header(page, "This module could not be loaded.")
        st.error(str(exc))
        st.caption("The lean shell remains available; check the module and deployment dependencies.")
