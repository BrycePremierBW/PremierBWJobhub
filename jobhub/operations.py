"""Job photos, timesheets and operational entry helpers.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *


def safe_photo_file_name(name):
    name = str(name or "photo").strip()
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    return name[:120]

def get_job_no_for_id(job_id):
    df = df_query("SELECT job_no FROM jobs WHERE id = ?", (job_id,))
    if df.empty:
        return f"job_{job_id}"
    return str(df.iloc[0]["job_no"] or f"job_{job_id}")

def save_photo_to_job_folder(job_id, uploaded_file, max_size=(1600, 1600), quality=80):
    job_no = get_job_no_for_id(job_id)

    job_folder = get_job_folder(job_no)
    photos_folder = os.path.join(job_folder, "photos")
    os.makedirs(photos_folder, exist_ok=True)

    image = Image.open(uploaded_file)

    if image.mode not in ["RGB", "L"]:
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")

    image.thumbnail(max_size)

    original_name = safe_photo_file_name(uploaded_file.name)
    base_name = os.path.splitext(original_name)[0]
    timestamp = jobhub_now().strftime("%Y%m%d_%H%M%S_%f")

    file_name = f"{timestamp}_{base_name}.jpg"
    file_path = os.path.join(photos_folder, file_name)

    image.save(file_path, format="JPEG", quality=quality, optimize=True)

    return file_path, "image/jpeg"

def photo_data_to_bytes(photo_data):
    """
    Supports both:
    - old photos saved as base64 in database
    - new photos saved as files with FILEPATH:/var/data/...
    """
    if not photo_data:
        return b""

    photo_data = str(photo_data)

    if photo_data.startswith("FILEPATH:"):
        file_path = photo_data.replace("FILEPATH:", "", 1)
        with open(file_path, "rb") as f:
            return f.read()

    return base64.b64decode(photo_data.encode("utf-8"))

def save_job_photo(job_id, uploaded_file, category, caption, notes):
    uploaded_by = ""
    try:
        user = get_current_user()
        if user:
            uploaded_by = user.get("username", "")
    except Exception:
        uploaded_by = ""

    file_path, photo_type = save_photo_to_job_folder(job_id, uploaded_file)

    execute("""
        INSERT INTO job_photos
        (job_id, photo_name, photo_type, photo_data, category, caption, uploaded_by, uploaded_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        uploaded_file.name,
        photo_type,
        f"FILEPATH:{file_path}",
        category,
        caption,
        uploaded_by,
        jobhub_now().strftime("%Y-%m-%d %H:%M:%S"),
        notes,
    ))

def delete_job_photo(photo_id):
    try:
        photo_df = df_query("SELECT photo_data FROM job_photos WHERE id = ?", (photo_id,))
        if not photo_df.empty:
            photo_data = str(photo_df.iloc[0]["photo_data"] or "")
            if photo_data.startswith("FILEPATH:"):
                file_path = photo_data.replace("FILEPATH:", "", 1)
                if os.path.exists(file_path):
                    os.remove(file_path)
    except Exception:
        pass

    execute("DELETE FROM job_photos WHERE id = ?", (photo_id,))

def job_photos_page(employee_restricted=False):
    st.header("Job Photos")
    st.caption("Upload photos against a specific job. Photos will appear in Job Pack reports.")

    job_options = get_job_options()

    if not job_options:
        st.info("Create a job first, then upload photos.")
        return

    tab_upload, tab_view = st.tabs(["Upload Photos", "View / Delete Photos"])

    with tab_upload:
        st.subheader("Upload Job Photos")

        with st.form("upload_job_photos_form"):
            selected_job = st.selectbox("Select Job", list(job_options.keys()), key="photo_upload_job")
            category = st.selectbox(
                "Photo Category",
                [
                    "Before",
                    "During Works",
                    "After",
                    "Defect / Damage",
                    "Access / Safety",
                    "Materials",
                    "Equipment",
                    "Completion / Sign-off",
                    "Other",
                ],
            )
            caption = st.text_input("Caption / Description")
            notes = st.text_area("Notes")
            uploaded_files = st.file_uploader(
                "Upload photos",
                type=["jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
            )
            submitted = st.form_submit_button("Save Photos to Job")

            if submitted:
                if not uploaded_files:
                    st.error("Please select at least one photo.")
                else:
                    saved_count = 0
                    for uploaded_file in uploaded_files:
                        try:
                            save_job_photo(
                                job_id=job_options[selected_job],
                                uploaded_file=uploaded_file,
                                category=category,
                                caption=caption,
                                notes=notes,
                            )
                            saved_count += 1
                        except Exception as e:
                            st.error(f"Could not save {uploaded_file.name}: {e}")

                    if saved_count:
                        st.success(f"Saved {saved_count} photo(s) to {selected_job}.")
                        refresh()

    with tab_view:
        st.subheader("View Job Photos")

        selected_job = st.selectbox("Select Job", list(job_options.keys()), key="photo_view_job")
        selected_job_id = job_options[selected_job]

        photos_df = df_query("""
            SELECT id, photo_name, photo_type, photo_data, category, caption, uploaded_by, uploaded_at, notes
            FROM job_photos
            WHERE job_id = ?
            ORDER BY uploaded_at DESC, id DESC
        """, (selected_job_id,))

        if photos_df.empty:
            st.info("No photos saved for this job.")
        else:
            for _, row in photos_df.iterrows():
                photo_id = int(row["id"])
                caption = str(row["caption"] or "")
                category = str(row["category"] or "")
                uploaded_at = str(row["uploaded_at"] or "")
                uploaded_by = str(row["uploaded_by"] or "")
                notes = str(row["notes"] or "")

                st.markdown(f"### {category} - {caption if caption else row['photo_name']}")
                try:
                    st.image(photo_data_to_bytes(row["photo_data"]), width="stretch")
                except Exception:
                    st.warning("Could not display this photo.")

                st.caption(f"Uploaded: {uploaded_at} by {uploaded_by}")
                if notes:
                    st.write(notes)

                if not employee_restricted:
                    delete_confirm = st.checkbox("Delete this photo", key=f"delete_photo_confirm_{photo_id}")
                    if st.button("Delete Photo", key=f"delete_photo_{photo_id}"):
                        if not delete_confirm:
                            st.error("Tick the delete checkbox first.")
                        else:
                            delete_job_photo(photo_id)
                            st.success("Photo deleted.")
                            refresh()

                st.divider()

def calculate_hours_from_times(start_time, finish_time, break_minutes):
    try:
        if not start_time or not finish_time:
            return 0.0
        sh, sm = [int(x) for x in str(start_time).split(":")[:2]]
        fh, fm = [int(x) for x in str(finish_time).split(":")[:2]]
        start_minutes = sh * 60 + sm
        finish_minutes = fh * 60 + fm
        if finish_minutes < start_minutes:
            finish_minutes += 24 * 60
        total_minutes = finish_minutes - start_minutes - float(break_minutes or 0)
        return max(round(total_minutes / 60, 2), 0.0)
    except Exception:
        return 0.0

def save_timesheet_entry(job_id, employee_id, work_date, start_time, finish_time, break_minutes, total_hours, work_type, notes, period_type="Single Day", period_start="", period_end=""):
    user = get_current_user() or {}
    submitted_by = user.get("username", "")
    submitted_at = jobhub_now().strftime("%Y-%m-%d %H:%M:%S")

    period_type = str(period_type or "Single Day")
    period_start = str(period_start or work_date)
    period_end = str(period_end or work_date)

    period_note = ""
    if period_type == "Week Ending":
        period_note = f"Week entry from {period_start} to week ending {period_end}. "

    execute("""
        INSERT INTO timesheet_entries
        (job_id, employee_id, work_date, start_time, finish_time, break_minutes, total_hours,
         work_type, submitted_by, submitted_at, status, notes, period_type, period_start, period_end)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        employee_id,
        work_date,
        start_time,
        finish_time,
        break_minutes,
        total_hours,
        work_type,
        submitted_by,
        submitted_at,
        "Submitted",
        period_note + str(notes or ""),
        period_type,
        period_start,
        period_end,
    ))

    execute("""
        INSERT INTO wage_entries (job_id, employee_id, work_date, hours, notes, period_type, period_start, period_end)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        employee_id,
        work_date,
        total_hours,
        f"Timesheet: {period_type}. {period_note}{notes}",
        period_type,
        period_start,
        period_end,
    ))

def timesheet_entry_form(employee_id=None, employee_restricted=False, key_prefix="timesheet"):
    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first, then timesheets can be submitted.")
        return

    if employee_id is None:
        employee_options = get_employee_options(active_only=True)
        if not employee_options:
            st.info("Create employees first.")
            return
    else:
        employee_options = None

    with st.form(f"{key_prefix}_form"):
        selected_job = st.selectbox("Job", list(job_options.keys()), key=f"{key_prefix}_job")

        if employee_restricted and employee_id is not None:
            employee_df = df_query("SELECT name FROM employees WHERE id = ?", (employee_id,))
            employee_name = employee_df.iloc[0]["name"] if not employee_df.empty else "Current Employee"
            st.text_input("Employee", value=str(employee_name), disabled=True, key=f"{key_prefix}_employee_name")
            selected_employee_id = employee_id
        else:
            selected_employee = st.selectbox("Employee", list(employee_options.keys()), key=f"{key_prefix}_employee")
            selected_employee_id = employee_options[selected_employee]

        period_type = st.radio(
            "Entry Type",
            ["Single Day", "Week Ending"],
            horizontal=True,
            key=f"{key_prefix}_period_type",
        )

        if period_type == "Single Day":
            col1, col2, col3, col4 = st.columns(4)
            work_day = col1.date_input("Date", value=jobhub_today(), key=f"{key_prefix}_date")
            start_time = col2.text_input("Start Time", value="07:00", key=f"{key_prefix}_start")
            finish_time = col3.text_input("Finish Time", value="15:00", key=f"{key_prefix}_finish")
            break_minutes = col4.number_input("Break Minutes", min_value=0.0, step=15.0, value=0.0, key=f"{key_prefix}_break")
            calculated_hours = calculate_hours_from_times(start_time, finish_time, break_minutes)
            total_hours = st.number_input("Total Hours", min_value=0.0, step=0.25, value=float(calculated_hours), key=f"{key_prefix}_hours")
            work_date = str(work_day)
            period_start = str(work_day)
            period_end = str(work_day)
        else:
            col1, col2, col3 = st.columns(3)
            default_week_end = jobhub_today()
            default_week_start = default_week_end - timedelta(days=4)
            from_date = col1.date_input("From Date", value=default_week_start, key=f"{key_prefix}_from_date")
            week_ending = col2.date_input("Week Ending", value=default_week_end, key=f"{key_prefix}_week_ending")
            total_hours = col3.number_input("Total Hours for This Job / Week", min_value=0.0, step=0.25, value=38.0, key=f"{key_prefix}_week_hours")
            start_time = ""
            finish_time = ""
            break_minutes = 0.0
            work_date = str(from_date)
            period_start = str(from_date)
            period_end = str(week_ending)
            st.caption("Use this when the employee was on the same job for the full week. It saves one total-hours entry instead of daily entries.")

        work_type = st.selectbox("Work Type", ["Painting", "Prep", "Spraying", "Touch-ups", "Travel", "Site Setup", "Other"], key=f"{key_prefix}_work_type")
        notes = st.text_area("Notes", key=f"{key_prefix}_notes")
        submitted = st.form_submit_button("Submit Timesheet")

        if submitted:
            if total_hours <= 0:
                st.error("Total hours must be greater than 0.")
            elif period_type == "Week Ending" and period_end < period_start:
                st.error("Week ending date must be after the from date.")
            else:
                save_timesheet_entry(
                    job_options[selected_job],
                    selected_employee_id,
                    work_date,
                    start_time,
                    finish_time,
                    break_minutes,
                    total_hours,
                    work_type,
                    notes,
                    period_type=period_type,
                    period_start=period_start,
                    period_end=period_end,
                )
                st.success("Timesheet submitted and linked to the selected job.")
                refresh()

def timesheets_page(employee_restricted=False):
    st.header("Timesheets")
    st.caption("Employee hours linked directly to specific jobs.")
    user = get_current_user() or {}
    current_employee_id = user.get("employee_id")

    if not employee_restricted:
        render_context_pdf_import_for_selected_job(
            context="timesheets",
            title="Import timesheet, day labour or roster PDFs",
            key_prefix="timesheets_pdf_import",
        )
        st.divider()

    if employee_restricted:
        if not current_employee_id:
            st.warning("Your login is not linked to an employee record. Ask admin to link your user to your employee profile.")
            return
        tab_submit, tab_my = st.tabs(["Submit Timesheet", "My Timesheets"])
        with tab_submit:
            timesheet_entry_form(employee_id=current_employee_id, employee_restricted=True, key_prefix="employee_timesheet")
        with tab_my:
            my_df = df_query("""
                SELECT COALESCE(NULLIF(t.period_type, ''), 'Single Day') AS 'Period',
                       COALESCE(NULLIF(t.period_start, ''), t.work_date) AS 'From Date',
                       COALESCE(NULLIF(t.period_end, ''), t.work_date) AS 'Week Ending / To Date',
                       j.job_no AS 'Job No', j.job_name AS 'Job Name',
                       t.start_time AS 'Start', t.finish_time AS 'Finish', t.break_minutes AS 'Break Minutes',
                       t.total_hours AS 'Hours', t.work_type AS 'Work Type', t.status AS 'Status', t.notes AS 'Notes'
                FROM timesheet_entries t
                JOIN jobs j ON j.id = t.job_id
                WHERE t.employee_id = ?
                ORDER BY t.work_date DESC, t.id DESC
                LIMIT 100
            """, (current_employee_id,))
            st.dataframe(my_df, width="stretch", hide_index=True)
        return

    tab_submit, tab_review, tab_by_job = st.tabs(["Add Timesheet", "Review Timesheets", "Timesheets by Job"])
    with tab_submit:
        timesheet_entry_form(key_prefix="admin_timesheet")
    with tab_review:
        df = df_query("""
            SELECT t.id,
                   COALESCE(NULLIF(t.period_type, ''), 'Single Day') AS 'Period',
                   COALESCE(NULLIF(t.period_start, ''), t.work_date) AS 'From Date',
                   COALESCE(NULLIF(t.period_end, ''), t.work_date) AS 'Week Ending / To Date',
                   j.job_no AS 'Job No', j.job_name AS 'Job Name', e.name AS 'Employee',
                   t.start_time AS 'Start', t.finish_time AS 'Finish', t.break_minutes AS 'Break Minutes',
                   t.total_hours AS 'Hours', t.work_type AS 'Work Type', t.status AS 'Status',
                   t.submitted_by AS 'Submitted By', t.submitted_at AS 'Submitted At', t.notes AS 'Notes'
            FROM timesheet_entries t
            JOIN jobs j ON j.id = t.job_id
            JOIN employees e ON e.id = t.employee_id
            ORDER BY t.work_date DESC, t.id DESC
            LIMIT 500
        """)
        if df.empty:
            st.info("No timesheets submitted yet.")
        else:
            st.dataframe(df.drop(columns=["id"]), width="stretch", hide_index=True)
            options = {f"{r['From Date']} to {r['Week Ending / To Date']} - {r['Employee']} - {r['Job No']} - {r['Hours']} hrs": int(r["id"]) for _, r in df.iterrows()}
            selected = st.selectbox("Select timesheet to approve/delete", list(options.keys()))
            selected_id = options[selected]
            col1, col2, col3 = st.columns(3)
            if col1.button("Mark Approved"):
                execute("UPDATE timesheet_entries SET status = 'Approved' WHERE id = ?", (selected_id,))
                st.success("Timesheet approved.")
                refresh()
            if col2.button("Mark Paid"):
                execute("UPDATE timesheet_entries SET status = 'Paid' WHERE id = ?", (selected_id,))
                st.success("Timesheet marked as paid.")
                refresh()
            if col3.button("Delete Timesheet"):
                execute("DELETE FROM timesheet_entries WHERE id = ?", (selected_id,))
                st.success("Timesheet deleted.")
                refresh()
    with tab_by_job:
        job_options = get_job_options()
        if not job_options:
            st.info("No jobs found.")
        else:
            selected_job = st.selectbox("Select Job", list(job_options.keys()), key="timesheet_by_job_select")
            selected_job_id = job_options[selected_job]
            by_job = df_query("""
                SELECT COALESCE(NULLIF(t.period_type, ''), 'Single Day') AS 'Period',
                       COALESCE(NULLIF(t.period_start, ''), t.work_date) AS 'From Date',
                       COALESCE(NULLIF(t.period_end, ''), t.work_date) AS 'Week Ending / To Date',
                       e.name AS 'Employee', t.start_time AS 'Start', t.finish_time AS 'Finish',
                       t.break_minutes AS 'Break Minutes', t.total_hours AS 'Hours', t.work_type AS 'Work Type',
                       t.status AS 'Status', t.notes AS 'Notes'
                FROM timesheet_entries t
                JOIN employees e ON e.id = t.employee_id
                WHERE t.job_id = ?
                ORDER BY t.work_date DESC, e.name
            """, (selected_job_id,))
            if by_job.empty:
                st.info("No timesheets saved for this job.")
            else:
                st.metric("Total Hours for Job", f"{float(by_job['Hours'].fillna(0).sum()):.2f}")
                st.dataframe(by_job, width="stretch", hide_index=True)
