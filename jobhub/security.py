"""Authentication, users, employee portal and safe linked-record deletion.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *


JOB_DIRECT_LINK_TABLES = [
    "material_entries",
    "wage_entries",
    "timesheet_entries",
    "equipment_entries",
    "equipment_checklist_records",
    "imported_material_entries",
    "job_photos",
    "job_documents",
    "job_budgets",
    "job_variations",
    "invoice_claims",
    "staff_schedule",
]

def linked_job_counts(job_id):
    counts = {}
    for table in JOB_DIRECT_LINK_TABLES + [
        "material_order_requests",
        "estimate_working_sheets",
        "painting_takeoff_packages",
        "painting_progress_sections",
        "building_model_surfaces",
        "drawing_progress_zones",
    ]:
        try:
            df = df_query(f"SELECT COUNT(*) AS c FROM {table} WHERE job_id = ?", (job_id,))
            counts[table] = int(df.iloc[0]["c"])
        except Exception:
            counts[table] = 0
    return counts

def delete_job_linked_records(cur, job_id=None):
    """Delete job-linked records in foreign-key-safe order."""
    if job_id is None:
        cur.execute("DELETE FROM drawing_progress_zones")
        cur.execute("DELETE FROM building_model_surfaces")
        cur.execute("DELETE FROM painting_progress_sections")
        cur.execute("DELETE FROM painting_takeoff_lines")
        cur.execute("DELETE FROM painting_takeoff_packages")
        cur.execute("DELETE FROM estimate_line_items")
        cur.execute("DELETE FROM estimate_working_sheets")
        cur.execute("DELETE FROM material_order_items")
        cur.execute("DELETE FROM material_order_requests")
        for table in JOB_DIRECT_LINK_TABLES:
            cur.execute(f"DELETE FROM {table}")
        return

    params = (job_id,)
    cur.execute("DELETE FROM drawing_progress_zones WHERE job_id = ?", params)
    cur.execute("DELETE FROM building_model_surfaces WHERE job_id = ?", params)
    cur.execute("DELETE FROM painting_progress_sections WHERE job_id = ?", params)
    cur.execute("""
        DELETE FROM painting_takeoff_lines
        WHERE package_id IN (
            SELECT id FROM painting_takeoff_packages WHERE job_id = ?
        )
    """, params)
    cur.execute("DELETE FROM painting_takeoff_packages WHERE job_id = ?", params)
    cur.execute("""
        DELETE FROM estimate_line_items
        WHERE estimate_id IN (
            SELECT id FROM estimate_working_sheets WHERE job_id = ?
        )
    """, params)
    cur.execute("DELETE FROM estimate_working_sheets WHERE job_id = ?", params)
    cur.execute("""
        DELETE FROM material_order_items
        WHERE request_id IN (SELECT id FROM material_order_requests WHERE job_id = ?)
    """, params)
    cur.execute("DELETE FROM material_order_requests WHERE job_id = ?", params)
    for table in JOB_DIRECT_LINK_TABLES:
        cur.execute(f"DELETE FROM {table} WHERE job_id = ?", params)

def permanently_delete_job_and_linked_data(job_id):
    conn = connect()
    try:
        cur = conn.cursor()
        delete_job_linked_records(cur, job_id)
        cur.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        cur.execute("""
            INSERT INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value
        """, ("starter_data_seeded", "yes"))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def check_password(password, password_hash):
    return hash_password(password) == password_hash

def username_from_employee_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())

def seed_app_users():
    conn = connect()
    cur = conn.cursor()

    def user_exists(username=None, employee_id=None):
        if username and employee_id:
            cur.execute("""
                SELECT id FROM app_users
                WHERE LOWER(TRIM(username)) = LOWER(TRIM(?)) OR employee_id = ?
                LIMIT 1
            """, (username, employee_id))
        elif username:
            cur.execute("""
                SELECT id FROM app_users
                WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
                LIMIT 1
            """, (username,))
        elif employee_id:
            cur.execute("""
                SELECT id FROM app_users
                WHERE employee_id = ?
                LIMIT 1
            """, (employee_id,))
        else:
            return True
        return cur.fetchone() is not None

    # Default admin account
    if not user_exists(username="admin"):
        cur.execute("""
            INSERT INTO app_users
            (username, password_hash, role, employee_id, active, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("admin", hash_password("admin123"), "admin", None, 1, "Default admin account - change password immediately"))

    # Default manager account
    if not user_exists(username="manager"):
        cur.execute("""
            INSERT INTO app_users
            (username, password_hash, role, employee_id, active, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("manager", hash_password("manager123"), "manager", None, 1, "Default manager account - change password immediately"))

    # Create basic employee logins for active employees if missing.
    # Username example: "bryce", "robpullin"
    # Default password: changeme123
    cur.execute("SELECT id, name FROM employees WHERE status = 'Active'")
    for employee_id, employee_name in cur.fetchall():
        username = username_from_employee_name(employee_name)
        if not username:
            continue

        # Do not create another account if either the username OR employee link already exists.
        if user_exists(username=username, employee_id=employee_id):
            continue

        cur.execute("""
            INSERT INTO app_users
            (username, password_hash, role, employee_id, active, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (username, hash_password("changeme123"), "employee", employee_id, 1, "Auto-created employee account"))

    conn.commit()
    conn.close()

def get_current_user():
    return st.session_state.get("user")

def current_role():
    user = get_current_user()
    if not user:
        return ""
    return user.get("role", "")

def is_admin():
    return current_role() == "admin"

def is_manager_or_admin():
    return current_role() in ["admin", "manager"]

def require_login():
    if "user" not in st.session_state:
        st.session_state["user"] = None

    if st.session_state["user"]:
        return True

    st.title("Premier Brushworks JobHub")
    st.subheader("Login")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")

        if submitted:
            user_df = df_query("""
                SELECT u.id, u.username, u.password_hash, u.role, u.employee_id, u.active,
                       e.name AS employee_name
                FROM app_users u
                LEFT JOIN employees e ON e.id = u.employee_id
                WHERE u.username = ?
            """, (username.strip(),))

            if user_df.empty:
                st.error("Invalid username or password.")
            else:
                row = user_df.iloc[0]
                if int(row["active"] or 0) != 1:
                    st.error("This user account is inactive.")
                elif not check_password(password, row["password_hash"]):
                    st.error("Invalid username or password.")
                else:
                    st.session_state["user"] = {
                        "id": int(row["id"]),
                        "username": str(row["username"]),
                        "role": str(row["role"]),
                        "employee_id": int(row["employee_id"]) if not pd.isna(row["employee_id"]) else None,
                        "employee_name": "" if pd.isna(row["employee_name"]) else str(row["employee_name"]),
                    }
                    st.success("Logged in.")
                    st.rerun()

    st.info("Default admin login: admin / admin123. Change this immediately in User Access.")
    st.stop()

def logout_button():
    user = get_current_user()
    if user:
        st.sidebar.write(f"Logged in as **{user['username']}**")
        st.sidebar.caption(f"Role: {user['role']}")
        if st.sidebar.button("Logout"):
            st.session_state["user"] = None
            st.rerun()

def employee_portal():
    user = get_current_user()
    employee_id = user.get("employee_id")
    employee_name = user.get("employee_name") or user.get("username")

    pb_page_header(
        "Employee Portal",
        "Restricted staff access for job details, equipment, forms, photos and timesheets. Financial information is hidden from employee logins.",
        "Site Mode"
    )

    if not employee_id:
        st.warning("This login is not linked to an employee record. Ask admin to link it in User Access.")
        return

    tab_jobs, tab_hours, tab_equipment, tab_forms, tab_photos, tab_password = st.tabs([
        "My Job Info",
        "Submit Timesheet",
        "View Equipment",
        "Generate Forms",
        "Upload Photos",
        "Change Password",
    ])

    job_options = get_job_options()

    with tab_jobs:
        st.subheader("Job Information")
        if not job_options:
            st.info("No jobs available.")
        else:
            selected_job = st.selectbox("Select Job", list(job_options.keys()), key="employee_job_info")
            selected_job_id = job_options[selected_job]

            job_df = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       bc.name AS 'Builder / Client',
                       bc.contact_name AS 'Contact',
                       bc.phone AS 'Phone',
                       bc.email AS 'Email',
                       j.site_address AS 'Site Address',
                       j.status AS 'Status',
                       j.leading_hand AS 'Leading Hand',
                       j.start_date AS 'Start Date',
                       j.end_date AS 'End Date',
                       j.notes AS 'Notes'
                FROM jobs j
                LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
                WHERE j.id = ?
            """, (selected_job_id,))
            st.dataframe(job_df, width="stretch", hide_index=True)

            st.markdown("### Job Schedule")
            employee_schedule_df = df_query("""
                SELECT COALESCE(NULLIF(s.period_type, ''), 'Single Day') AS 'Schedule Type',
                       COALESCE(NULLIF(s.period_start, ''), s.schedule_date) AS 'From Date',
                       COALESCE(NULLIF(s.period_end, ''), s.schedule_date) AS 'Week Ending / To Date',
                       s.start_time AS 'Start',
                       s.finish_time AS 'Finish',
                       COALESCE(s.planned_hours, 0) AS 'Planned Hours',
                       e.name AS 'Staff Member',
                       s.site_role AS 'Role',
                       s.notes AS 'Notes'
                FROM staff_schedule s
                LEFT JOIN employees e ON e.id = s.employee_id
                WHERE s.job_id = ?
                ORDER BY COALESCE(NULLIF(s.period_start, ''), s.schedule_date), s.start_time
            """, (selected_job_id,))
            if employee_schedule_df.empty:
                st.info("No staff schedule has been saved for this job yet.")
            else:
                st.dataframe(employee_schedule_df, width="stretch", hide_index=True)

            st.markdown("### Colours / Materials Schedule")
            employee_materials_df = df_query("""
                SELECT COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS 'Product / Material',
                       COALESCE(NULLIF(m.custom_colour, ''), '') AS 'Colour / Finish',
                       COALESCE(NULLIF(m.custom_unit, ''), p.unit, '') AS 'Unit',
                       m.qty_required AS 'Qty Required',
                       m.qty_received AS 'Qty Received',
                       COALESCE(NULLIF(m.supplier, ''), NULLIF(m.custom_supplier, ''), p.supplier, '') AS 'Supplier',
                       m.date_ordered AS 'Date Ordered',
                       m.notes AS 'Notes'
                FROM material_entries m
                LEFT JOIN products p ON p.id = m.product_id
                WHERE m.job_id = ?
                ORDER BY m.id
            """, (selected_job_id,))
            employee_imported_materials_df = df_query("""
                SELECT product AS 'Product / Material',
                       colour AS 'Colour / Finish',
                       qty_required AS 'Qty Required',
                       qty_loaded AS 'Qty Loaded',
                       source_file AS 'Source File',
                       notes AS 'Notes'
                FROM imported_material_entries
                WHERE job_id = ?
                ORDER BY id
            """, (selected_job_id,))
            if employee_materials_df.empty and employee_imported_materials_df.empty:
                st.info("No colours or material schedule lines are saved for this job yet.")
            else:
                if not employee_materials_df.empty:
                    st.dataframe(employee_materials_df, width="stretch", hide_index=True)
                if not employee_imported_materials_df.empty:
                    st.markdown("#### Imported PDF material lines")
                    st.dataframe(employee_imported_materials_df, width="stretch", hide_index=True)

            st.markdown("### Job Documents / Plans / Specs")
            employee_documents_df = df_query("""
                SELECT id,
                       document_type AS 'Document Type',
                       file_name AS 'File Name',
                       file_path,
                       created_at AS 'Created At',
                       notes AS 'Notes'
                FROM job_documents
                WHERE job_id = ?
                ORDER BY id DESC
            """, (selected_job_id,))
            if employee_documents_df.empty:
                st.info("No job documents, plans or specs have been attached to this job yet.")
            else:
                for _, doc in employee_documents_df.iterrows():
                    st.write(f"**{doc['Document Type']}** - {doc['File Name']}")
                    st.caption(f"Created: {doc['Created At']}")
                    file_path = str(doc["file_path"])
                    if os.path.exists(file_path):
                        with open(file_path, "rb") as f:
                            st.download_button(
                                label=f"Download {doc['File Name']}",
                                data=f,
                                file_name=doc["File Name"],
                                mime="application/pdf",
                                key=f"employee_download_job_doc_{doc['id']}",
                            )
                    else:
                        st.warning("File path not found on disk.")

    with tab_hours:
        timesheets_page(employee_restricted=True)

    with tab_equipment:
        st.subheader("View Job Equipment Master List")
        if not job_options:
            st.info("No jobs available.")
        else:
            selected_job = st.selectbox("Select Job", list(job_options.keys()), key="employee_equipment_job")
            selected_job_id = job_options[selected_job]

            equipment_df = df_query("""
                SELECT j.job_no AS 'Job No',
                       j.job_name AS 'Job Name',
                       i.category AS 'Category',
                       i.item_name AS 'Equipment Item',
                       COALESCE(SUM(r.qty_required), 0) AS 'Total Required',
                       COALESCE(SUM(r.qty_taken), 0) AS 'Total Taken',
                       COALESCE(SUM(r.qty_returned), 0) AS 'Total Returned',
                       COALESCE(SUM(r.qty_taken - r.qty_returned), 0) AS 'Still Out'
                FROM equipment_checklist_items i
                CROSS JOIN jobs j
                LEFT JOIN equipment_checklist_records r
                    ON r.checklist_item_id = i.id
                   AND r.job_id = j.id
                WHERE j.id = ?
                GROUP BY j.job_no, j.job_name, i.category, i.item_name
                ORDER BY i.category, i.item_name
            """, (selected_job_id,))
            st.dataframe(equipment_df, width="stretch", hide_index=True)

    with tab_photos:
        job_photos_page(employee_restricted=True)
    with tab_forms:
        st.subheader("Generate Job Forms")
        st.caption("Employees can generate job forms without seeing pricing, contract values or financial reports.")

        job_options = get_job_options()

        if not job_options:
            st.info("No jobs available.")
        else:
            selected_job = st.selectbox(
                "Select Job",
                list(job_options.keys()),
                key="employee_generate_forms_job"
            )
            selected_job_id = job_options[selected_job]

            render_employee_material_orders(
                selected_job_id,
                employee_id,
                employee_name,
                int(user.get("id")),
            )

            st.divider()

            st.markdown("### Equipment Checklist")
            st.caption("Generates a fillable equipment checklist PDF and attaches it to the selected job.")

            if st.button("Generate Equipment Checklist", key=f"employee_generate_equipment_{selected_job_id}"):
                try:
                    pdf_path = generate_equipment_checklist_pdf(selected_job_id)
                    st.success("Equipment Checklist generated and attached to this job.")

                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            "Download Equipment Checklist",
                            data=f,
                            file_name=os.path.basename(pdf_path),
                            mime="application/pdf",
                            key=f"employee_download_equipment_{selected_job_id}",
                        )

                except Exception as e:
                    st.error(f"Could not generate Equipment Checklist: {e}")

            st.divider()

            st.markdown("### Variation Form")
            st.caption("Creates a draft variation request and generates a fillable variation form for the selected job.")

            variation_result_key = f"employee_variation_result_{selected_job_id}"

            with st.form(f"employee_variation_form_generator_{selected_job_id}"):
                variation_description = st.text_area("Variation Description")
                variation_reason = st.text_area("Reason / Details")
                variation_notes = st.text_area("Notes")
                generate_variation = st.form_submit_button("Generate Variation Form")

                if generate_variation:
                    try:
                        requested_by = employee_name or user.get("username", "")
                        pdf_path, variation_no = generate_variation_form_pdf(
                            selected_job_id,
                            requested_by=requested_by,
                            description=variation_description,
                            reason=variation_reason,
                            notes=variation_notes,
                        )
                        st.session_state[variation_result_key] = {
                            "pdf_path": pdf_path,
                            "variation_no": variation_no,
                        }
                    except Exception as e:
                        st.error(f"Could not generate Variation Form: {e}")

            if variation_result_key in st.session_state:
                variation_result = st.session_state[variation_result_key]
                pdf_path = variation_result["pdf_path"]
                variation_no = variation_result["variation_no"]

                st.success(f"Variation Form {variation_no} generated and attached to this job.")

                with open(pdf_path, "rb") as f:
                    st.download_button(
                        "Download Variation Form",
                        data=f,
                        file_name=os.path.basename(pdf_path),
                        mime="application/pdf",
                        key=f"employee_download_variation_{selected_job_id}_{variation_no}",
                    )
    with tab_password:
        st.subheader("Change My Password")
        with st.form("employee_change_password"):
            old_password = st.text_input("Current Password", type="password")
            new_password = st.text_input("New Password", type="password")
            confirm_password = st.text_input("Confirm New Password", type="password")
            submitted = st.form_submit_button("Change Password")

            if submitted:
                user_df = df_query("SELECT password_hash FROM app_users WHERE id = ?", (user["id"],))
                if user_df.empty:
                    st.error("User account not found.")
                elif not check_password(old_password, user_df.iloc[0]["password_hash"]):
                    st.error("Current password is incorrect.")
                elif len(new_password) < 6:
                    st.error("Password must be at least 6 characters.")
                elif new_password != confirm_password:
                    st.error("New passwords do not match.")
                else:
                    execute("UPDATE app_users SET password_hash = ? WHERE id = ?", (hash_password(new_password), user["id"]))
                    st.success("Password changed.")

def user_access_page():
    st.header("User Access")
    st.caption("Admin only. Create logins and control who can access the app.")

    if not is_admin():
        st.error("Only admin users can access this page.")
        return

    st.markdown("### Restore / Update Haymes & Taubmans Product Lists")
    st.caption("One button to restore/update both saved paint product lists. Existing matching product codes are updated instead of duplicated.")

    pc1, pc2, pc3 = st.columns(3)
    pc1.metric("Haymes products", haymes_product_count())
    pc2.metric("Taubmans products", taubmans_product_count())
    pc3.metric("Combined saved paint products", combined_paint_product_count())

    paint_confirm = st.text_input(
        "To restore/update Haymes and Taubmans products, type: RESTORE PAINT LISTS",
        key="restore_combined_paint_lists_confirm"
    )

    if st.button("Restore / Update Haymes & Taubmans Product Lists", key="restore_haymes_taubmans_products_btn"):
        if paint_confirm.strip().upper() != "RESTORE PAINT LISTS":
            st.error("Type RESTORE PAINT LISTS exactly before restoring.")
        else:
            restored = restore_haymes_and_taubmans_product_lists()
            st.success(f"Restored/updated {restored} Haymes and Taubmans products.")
            refresh()


    st.divider()

    st.markdown("### Restore Master Builders/Clients & Employees")
    st.caption("Use this if builders, clients, employee names, or employee logins are missing.")

    rc1, rc2 = st.columns(2)
    rc1.metric("Builders/clients currently in database", builders_clients_count())
    rc2.metric("Employees currently in database", employees_count())

    restore_master_confirm = st.text_input(
        "To restore the saved master builders/clients and employees, type: RESTORE MASTER DATA",
        key="restore_master_data_confirm"
    )

    if st.button("Restore Builders/Clients & Employees", key="restore_builders_clients_employees_btn"):
        if restore_master_confirm.strip().upper() != "RESTORE MASTER DATA":
            st.error("Type RESTORE MASTER DATA exactly before restoring.")
        else:
            restored_builders, restored_employees = restore_builders_clients_and_employees()
            st.success(
                f"Restored/updated {restored_builders} builders/clients and {restored_employees} employees. "
                "Missing employee login accounts were recreated where needed."
            )
            refresh()

    st.divider()

    st.markdown("### Clean Up Duplicate User Accounts")
    st.caption("Use this if the same employee/user login appears more than once.")

    duplicates_df = user_duplicate_summary()

    if duplicates_df.empty:
        st.success("No duplicate user accounts detected.")
    else:
        st.warning(f"Found {len(duplicates_df)} duplicate/suspect user account rows.")
        st.dataframe(
            duplicates_df[["id", "username", "role", "employee_name", "active", "notes"]],
            width="stretch",
            hide_index=True,
        )

        clean_confirm = st.text_input(
            "To clean duplicate user accounts, type: CLEAN USERS",
            key="clean_duplicate_users_confirm"
        )

        if st.button("Clean Duplicate User Accounts", key="clean_duplicate_users_button"):
            if clean_confirm.strip().upper() != "CLEAN USERS":
                st.error("Type CLEAN USERS exactly before cleaning duplicate accounts.")
            else:
                result = clean_duplicate_user_accounts()
                st.success(
                    f"Duplicate cleanup complete. Deleted {result['deleted']} duplicate login(s). "
                    f"Skipped/disabled {result['skipped']}."
                )
                refresh()

    st.divider()

    tab_add, tab_edit, tab_list = st.tabs(["Add User", "Edit / Disable / Delete User", "User List"])

    employee_options = get_employee_options(active_only=False)
    employee_labels = ["Not linked"] + list(employee_options.keys())

    with tab_add:
        st.subheader("Add User")
        with st.form("add_user_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            role = st.selectbox("Role", ["employee", "manager", "admin"])
            employee_label = st.selectbox("Link to Employee", employee_labels)
            notes = st.text_area("Notes")
            submitted = st.form_submit_button("Create User")

            if submitted:
                if not username or not password:
                    st.error("Username and password are required.")
                elif len(password) < 6:
                    st.error("Password must be at least 6 characters.")
                else:
                    employee_id = employee_options.get(employee_label) if employee_label != "Not linked" else None
                    try:
                        execute("""
                            INSERT INTO app_users
                            (username, password_hash, role, employee_id, active, notes)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (username.strip(), hash_password(password), role, employee_id, 1, notes))
                        st.success(f"Created user {username}.")
                        refresh()
                    except Exception as e:
                        st.error(f"Could not create user: {e}")

    with tab_edit:
        st.subheader("Edit / Disable User")
        users_df = df_query("""
            SELECT u.id, u.username, u.role, u.employee_id, u.active, u.notes,
                   COALESCE(e.name, '') AS employee_name
            FROM app_users u
            LEFT JOIN employees e ON e.id = u.employee_id
            ORDER BY u.username
        """)

        if users_df.empty:
            st.info("No users.")
        else:
            user_map = {row["username"]: int(row["id"]) for _, row in users_df.iterrows()}
            selected_username = st.selectbox("Select User", list(user_map.keys()))
            selected_user_id = user_map[selected_username]
            current = users_df[users_df["id"] == selected_user_id].iloc[0]

            current_employee = str(current["employee_name"] or "Not linked")
            employee_index = employee_labels.index(current_employee) if current_employee in employee_labels else 0
            roles = ["employee", "manager", "admin"]
            role_index = roles.index(str(current["role"])) if str(current["role"]) in roles else 0
            active_options = ["Active", "Inactive"]
            active_index = 0 if int(current["active"] or 0) == 1 else 1

            with st.form("edit_user_form"):
                username = st.text_input("Username", value=str(current["username"]))
                new_password = st.text_input("New Password (leave blank to keep current)", type="password")
                role = st.selectbox("Role", roles, index=role_index)
                employee_label = st.selectbox("Link to Employee", employee_labels, index=employee_index)
                active_label = st.selectbox("Status", active_options, index=active_index)
                notes = st.text_area("Notes", value=str(current["notes"] or ""))
                submitted = st.form_submit_button("Update User")

                if submitted:
                    employee_id = employee_options.get(employee_label) if employee_label != "Not linked" else None
                    active = 1 if active_label == "Active" else 0

                    if new_password and len(new_password) < 6:
                        st.error("Password must be at least 6 characters.")
                    else:
                        success, message = safe_update_user_account(
                            selected_user_id=selected_user_id,
                            username=username,
                            role=role,
                            employee_id=employee_id,
                            active=active,
                            notes=notes,
                        )

                        if success:
                            if new_password:
                                execute("UPDATE app_users SET password_hash = ? WHERE id = ?", (hash_password(new_password), selected_user_id))
                            st.success(message)
                            refresh()
                        else:
                            st.error(message)

            st.markdown("### Delete User Account")
            st.warning(
                "This deletes the selected login account and will also delete the linked employee record where safe. "
                "If the employee has wages, timesheets or job history, they will be marked Inactive instead."
            )

            admin_count_df = df_query("""
                SELECT COUNT(*) AS 'count'
                FROM app_users
                WHERE role = 'admin' AND active = 1
            """)
            active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0

            current_user = get_current_user() or {}
            selected_is_current_user = int(current_user.get("id", -1)) == int(selected_user_id)
            selected_is_last_active_admin = (
                str(current["role"]) == "admin"
                and int(current["active"] or 0) == 1
                and active_admin_count <= 1
            )

            delete_confirm = st.text_input(
                "To delete this user login, type: DELETE USER",
                key=f"delete_user_confirm_{selected_user_id}"
            )

            if st.button("Delete Selected User Account", key=f"delete_user_button_{selected_user_id}"):
                if delete_confirm.strip().upper() != "DELETE USER":
                    st.error("Type DELETE USER exactly before deleting this account.")
                elif selected_is_current_user:
                    st.error("You cannot delete the account you are currently logged in with.")
                elif selected_is_last_active_admin:
                    st.error("You cannot delete the last active admin account. Create another admin first, then delete this one.")
                else:
                    result = delete_user_and_linked_employee(selected_user_id)

                    if result["deleted_users"]:
                        st.success(f"Deleted {result['deleted_users']} user login account(s).")

                    if result["deleted_employee"]:
                        st.success(f"Deleted {result['deleted_employee']} linked employee record(s).")

                    if result["deactivated_employee"]:
                        st.info(f"Marked {result['deactivated_employee']} linked employee(s) as Inactive because they had job history or other linked records.")

                    if result["skipped"]:
                        st.warning(f"Skipped {result['skipped']} item(s).")

                    with st.expander("Delete details"):
                        for msg in result["messages"]:
                            st.write(msg)

                    refresh()

            st.markdown("### Unlink Employee From This User")
            st.caption("Use this if this login is incorrectly linked to the wrong employee.")
            if st.button("Unlink Employee From Selected User", key=f"unlink_employee_user_{selected_user_id}"):
                execute("UPDATE app_users SET employee_id = NULL WHERE id = ?", (selected_user_id,))
                st.success("Employee link removed from this user account.")
                refresh()

    st.markdown("### Start Fresh / Clear All Jobs")
    st.warning(
        "This permanently deletes all jobs and all job-linked data, including materials, wages, "
        "equipment checklist records and imported checklist materials. Builders, employees, products, "
        "users and checklist item templates will stay."
    )
    clear_confirm = st.text_input("To clear all jobs, type: CLEAR JOBS", key="clear_jobs_confirm")
    if st.button("Clear All Jobs and Start at 0"):
        if clear_confirm.strip().upper() != "CLEAR JOBS":
            st.error("Type CLEAR JOBS exactly before clearing the job register.")
        else:
            clear_all_jobs_and_linked_data()
            st.success("All jobs and job-linked data have been cleared. Job Register is now at 0.")
            refresh()


    with tab_list:
        st.subheader("User List")

        users_df = df_query("""
            SELECT u.id AS 'ID',
                   u.username AS 'Username',
                   u.role AS 'Role',
                   COALESCE(e.name, '') AS 'Linked Employee',
                   CASE WHEN u.active = 1 THEN 'Active' ELSE 'Inactive' END AS 'Status',
                   u.notes AS 'Notes'
            FROM app_users u
            LEFT JOIN employees e ON e.id = u.employee_id
            ORDER BY u.role, u.username, u.id
        """)

        if users_df.empty:
            st.info("No user accounts found.")
        else:
            st.dataframe(users_df, width="stretch", hide_index=True)

            st.markdown("### Remove Multiple User Accounts")
            st.warning(
                "This deletes selected user login accounts. If a selected login is linked to an employee, "
                "the linked employee will also be deleted where safe. If that employee has wages/timesheets, "
                "they will be marked Inactive instead to protect history."
            )

            delete_options = {
                f"{row['Username']} | {row['Role']} | {row['Linked Employee'] or 'No Employee'} | {row['Status']} | ID {row['ID']}": int(row["ID"])
                for _, row in users_df.iterrows()
            }

            selected_delete_labels = st.multiselect(
                "Select user login accounts to delete",
                list(delete_options.keys()),
                key="bulk_user_delete_multiselect"
            )

            selected_delete_ids = [delete_options[label] for label in selected_delete_labels]

            if selected_delete_ids:
                selected_preview = users_df[users_df["ID"].astype(int).isin(selected_delete_ids)]
                st.markdown("Selected accounts:")
                st.dataframe(selected_preview, width="stretch", hide_index=True)

            bulk_confirm = st.text_input(
                "To delete the selected user login accounts, type: DELETE SELECTED USERS",
                key="bulk_user_delete_confirm"
            )

            if st.button("Delete Selected User Accounts", key="bulk_user_delete_button"):
                if not selected_delete_ids:
                    st.error("Select at least one user account first.")
                elif bulk_confirm.strip().upper() != "DELETE SELECTED USERS":
                    st.error("Type DELETE SELECTED USERS exactly before deleting multiple accounts.")
                else:
                    result = delete_selected_user_accounts(selected_delete_ids)

                    if result["deleted_users"]:
                        st.success(f"Deleted {result['deleted_users']} selected user login account(s).")

                    if result["deleted_employee"]:
                        st.success(f"Deleted {result['deleted_employee']} linked employee record(s).")

                    if result["deactivated_employee"]:
                        st.info(f"Marked {result['deactivated_employee']} linked employee(s) as Inactive because they had job history or other linked records.")

                    if result["skipped"]:
                        st.warning(f"Skipped {result['skipped']} item(s).")

                    with st.expander("Deletion details"):
                        for msg in result["messages"]:
                            st.write(msg)

                    refresh()

def mark_seeded_if_existing_data_present():
    try:
        if starter_data_already_seeded():
            return

        conn = connect()
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM jobs")
        job_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM builders_clients")
        builder_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM employees")
        employee_count = cur.fetchone()[0]

        # If this database already has data, assume starter data has already been seeded.
        # This stops old/deleted jobs reappearing on first run after this update.
        if job_count > 0 or builder_count > 0 or employee_count > 0:
            cur.execute("""
                INSERT INTO app_settings (setting_key, setting_value)
                VALUES (?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value
            """, ("starter_data_seeded", "yes"))
            conn.commit()

        conn.close()
    except Exception:
        pass

def clear_all_jobs_and_linked_data():
    conn = connect()
    try:
        cur = conn.cursor()
        delete_job_linked_records(cur, job_id=None)
        cur.execute("DELETE FROM jobs")
        cur.execute("""
            INSERT INTO app_settings (setting_key, setting_value)
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value
        """, ("starter_data_seeded", "yes"))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()

