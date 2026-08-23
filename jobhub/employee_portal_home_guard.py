"""Simple, mobile-first Employee Portal home for Premier Brushworks JobHub.

Employees see today's scheduled job and its site address first, followed by a
short timesheet form.  The existing full Employee Portal remains available
behind one secondary button for photos, requests, forms and uncommon tasks.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
import html
import sys
from typing import Any
from urllib.parse import quote_plus

from jobhub_time import jobhub_today


PATCH_MARKER = "_pb_employee_portal_home_guard"
FULL_PORTAL_KEY = "_pb_employee_full_portal"


def _st() -> Any:
    return sys.modules.get("streamlit")


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _current_user(st: Any) -> dict[str, Any]:
    getter = _app_attr("get_current_user")
    if callable(getter):
        try:
            value = getter() or {}
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    try:
        value = st.session_state.get("user") or {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _role(st: Any) -> str:
    return str(_current_user(st).get("role") or "").strip().lower()


def _query(sql: str, params: tuple[Any, ...] = ()) -> Any:
    query = _app_attr("safe_df_query") or _app_attr("df_query")
    if not callable(query):
        return None
    try:
        return query(sql, params)
    except Exception:
        return None


def _today() -> date:
    """Always use JobHub's Queensland business date."""
    return jobhub_today()


def calculate_hours(start_value: time, finish_value: time, break_minutes: float = 0) -> float:
    start_dt = datetime.combine(date(2000, 1, 1), start_value)
    finish_dt = datetime.combine(date(2000, 1, 1), finish_value)
    if finish_dt < start_dt:
        finish_dt += timedelta(days=1)
    hours = (finish_dt - start_dt).total_seconds() / 3600.0
    hours -= max(0.0, float(break_minutes or 0)) / 60.0
    return round(max(0.0, hours), 2)


def _parse_time(value: Any, default: time) -> time:
    text = str(value or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M", "%I:%M %p"):
        try:
            return datetime.strptime(text, fmt).time().replace(second=0, microsecond=0)
        except Exception:
            pass
    return default


def _rows(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    try:
        return [dict(row) for _, row in frame.iterrows()]
    except Exception:
        return []


def _scheduled_jobs(employee_id: int, work_date: date) -> list[dict[str, Any]]:
    day = work_date.isoformat()
    frame = _query(
        """
        SELECT ss.id AS schedule_id,
               ss.job_id,
               ss.job_stage_id,
               COALESCE(ss.start_time,'') AS start_time,
               COALESCE(ss.finish_time,'') AS finish_time,
               COALESCE(ss.site_role,'') AS site_role,
               COALESCE(ss.notes,'') AS schedule_notes,
               COALESCE(j.job_no,'') AS job_no,
               COALESCE(j.job_name,'') AS job_name,
               COALESCE(j.site_address,'') AS site_address,
               COALESCE(j.leading_hand,'') AS leading_hand,
               COALESCE(j.notes,'') AS job_notes,
               COALESCE(js.stage_name,'') AS stage_name
        FROM staff_schedule ss
        JOIN jobs j ON j.id=ss.job_id
        LEFT JOIN job_stages js ON js.id=ss.job_stage_id
        WHERE ss.employee_id=?
          AND (
                ss.schedule_date=?
                OR (
                    COALESCE(ss.period_start,'')<>''
                    AND COALESCE(ss.period_end,'')<>''
                    AND ? BETWEEN ss.period_start AND ss.period_end
                )
              )
          AND LOWER(COALESCE(j.status,'')) NOT IN
              ('archived','cancelled','deleted')
        ORDER BY COALESCE(ss.start_time,''),j.job_no,ss.id
        """,
        (int(employee_id), day, day),
    )
    return _rows(frame)


def _active_jobs() -> list[dict[str, Any]]:
    return _rows(
        _query(
            """
            SELECT id AS job_id,
                   COALESCE(job_no,'') AS job_no,
                   COALESCE(job_name,'') AS job_name,
                   COALESCE(site_address,'') AS site_address,
                   COALESCE(leading_hand,'') AS leading_hand,
                   COALESCE(notes,'') AS job_notes
            FROM jobs
            WHERE LOWER(COALESCE(status,'')) NOT IN
                ('complete','completed','closed','paid','archived','cancelled','deleted')
            ORDER BY job_no,job_name
            """
        )
    )


def _job_label(row: dict[str, Any]) -> str:
    number = str(row.get("job_no") or "").strip()
    name = str(row.get("job_name") or "").strip()
    address = str(row.get("site_address") or "").strip()
    title = " · ".join(value for value in (number, name) if value) or "Job"
    return f"{title} — {address}" if address else title


def _timesheet_jobs(scheduled: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}
    ordered: list[int] = []
    for row in scheduled + _active_jobs():
        try:
            job_id = int(row.get("job_id") or 0)
        except Exception:
            continue
        if job_id <= 0:
            continue
        if job_id not in by_id:
            by_id[job_id] = dict(row)
            ordered.append(job_id)
        else:
            for key, value in row.items():
                if value not in (None, "") and by_id[job_id].get(key) in (None, ""):
                    by_id[job_id][key] = value
    return [by_id[job_id] for job_id in ordered]


def _job_card(st: Any, row: dict[str, Any]) -> None:
    job_no = html.escape(str(row.get("job_no") or ""))
    job_name = html.escape(str(row.get("job_name") or ""))
    raw_address = str(row.get("site_address") or "").strip()
    address = html.escape(raw_address or "Address not supplied")
    leading = html.escape(str(row.get("leading_hand") or "Not listed"))
    start_text = html.escape(str(row.get("start_time") or ""))
    finish_text = html.escape(str(row.get("finish_time") or ""))
    area = html.escape(str(row.get("stage_name") or row.get("site_role") or "").strip())
    notes = html.escape(
        str(row.get("schedule_notes") or row.get("job_notes") or "").strip()
    )
    shift_html = ""
    if start_text or finish_text:
        shift_html = (
            "<div class='pb-staff-detail'><b>TIME</b>"
            f"<span>{start_text or '—'} to {finish_text or '—'}</span></div>"
        )
    area_html = (
        f"<div class='pb-staff-detail'><b>WORK AREA</b><span>{area}</span></div>"
        if area else ""
    )
    notes_html = (
        f"<div class='pb-staff-notes'><b>NOTES</b><br>{notes}</div>"
        if notes else ""
    )
    st.markdown(
        f"""
        <div class="pb-staff-job-card">
          <div class="pb-staff-eyebrow">TODAY'S JOB</div>
          <div class="pb-staff-job-title">{job_no} {job_name}</div>
          <div class="pb-staff-address">📍 {address}</div>
          <div class="pb-staff-detail"><b>LEADING HAND</b><span>{leading}</span></div>
          {shift_html}{area_html}{notes_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
    if raw_address:
        try:
            st.link_button(
                "📍 OPEN ADDRESS IN MAPS",
                "https://www.google.com/maps/search/?api=1&query=" + quote_plus(raw_address),
                use_container_width=True,
            )
        except Exception:
            pass


def _existing_timesheet(employee_id: int, job_id: int, work_date: date) -> Any:
    return _query(
        """
        SELECT id,total_hours,status
        FROM timesheet_entries
        WHERE employee_id=? AND job_id=? AND work_date=?
          AND LOWER(COALESCE(status,''))<>'rejected'
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(employee_id), int(job_id), work_date.isoformat()),
    )


def _today_total(employee_id: int, work_date: date) -> float:
    frame = _query(
        """
        SELECT COALESCE(SUM(COALESCE(total_hours,0)),0) AS hours
        FROM timesheet_entries
        WHERE employee_id=? AND work_date=?
          AND LOWER(COALESCE(status,''))<>'rejected'
        """,
        (int(employee_id), work_date.isoformat()),
    )
    try:
        if frame is not None and not frame.empty:
            return round(float(frame.iloc[0]["hours"] or 0), 2)
    except Exception:
        pass
    return 0.0


def _rerun(st: Any) -> None:
    rerun = _app_attr("pb_rerun") or getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


def _render_timesheet(
    st: Any,
    employee_id: int,
    scheduled_rows: list[dict[str, Any]],
) -> None:
    business_today = _today()
    available_jobs = _timesheet_jobs(scheduled_rows)

    st.markdown("## ⏱️ My Timesheet")
    st.caption("Check the job and times, then press the big button. That's it.")

    saved_hours = _today_total(employee_id, business_today)
    if saved_hours > 0:
        st.success(f"Timesheets already saved today: {saved_hours:g} hours")
    if not available_jobs:
        st.warning("No active jobs are available. Contact the office.")
        return

    labels = [_job_label(row) for row in available_jobs]
    by_label = dict(zip(labels, available_jobs))
    default_row = available_jobs[0]

    with st.form("pb_employee_home_timesheet", clear_on_submit=False):
        work_date = st.date_input(
            "DATE", value=business_today, key="pb_home_timesheet_date"
        )
        selected_label = st.selectbox(
            "JOB", labels, index=0, key="pb_home_timesheet_job"
        )
        selected = by_label[selected_label]
        start_value = _parse_time(selected.get("start_time"), time(7, 0))
        finish_value = _parse_time(selected.get("finish_time"), time(15, 0))
        col_start, col_finish = st.columns(2)
        with col_start:
            start_value = st.time_input(
                "START", value=start_value, step=900, key="pb_home_timesheet_start"
            )
        with col_finish:
            finish_value = st.time_input(
                "FINISH", value=finish_value, step=900, key="pb_home_timesheet_finish"
            )
        break_minutes = st.selectbox(
            "BREAK",
            [0, 30, 60],
            index=0,
            key="pb_home_timesheet_break",
            format_func=lambda value: f"{value} minutes",
        )
        area = st.radio(
            "AREA",
            ["All", "Internal", "External"],
            horizontal=True,
            key="pb_home_timesheet_area",
        )
        work = st.selectbox(
            "WORK",
            ["Painting", "Prep", "Spraying", "Touch-ups", "Site Setup", "Other"],
            key="pb_home_timesheet_work",
        )
        notes = st.text_area(
            "NOTES (optional)", key="pb_home_timesheet_notes", height=70
        )
        submitted = st.form_submit_button(
            "✅ SUBMIT TIMESHEET", type="primary", use_container_width=True
        )

    if not submitted:
        return

    hours = calculate_hours(start_value, finish_value, float(break_minutes))
    if hours <= 0:
        st.error("Finish time must be after start time.")
        return

    job_id = int(selected.get("job_id") or 0)
    try:
        stage_id = int(selected.get("job_stage_id")) if selected.get("job_stage_id") else None
    except Exception:
        stage_id = None

    save_values = dict(
        job_id=job_id,
        employee_id=int(employee_id),
        work_date=work_date.isoformat(),
        start_time=start_value.strftime("%H:%M"),
        finish_time=finish_value.strftime("%H:%M"),
        break_minutes=float(break_minutes),
        total_hours=float(hours),
        work_type=f"{area} — {work}",
        notes=str(notes or ""),
        job_stage_id=stage_id,
        site_location=str(selected.get("site_address") or ""),
    )

    existing = _existing_timesheet(employee_id, job_id, work_date)
    existing_id = None
    try:
        if existing is not None and not existing.empty:
            existing_id = int(existing.iloc[0]["id"])
    except Exception:
        pass

    try:
        if existing_id is not None:
            updater = _app_attr("update_timesheet_entry")
            if not callable(updater):
                st.error("This timesheet already exists. Open More employee tools to edit it.")
                return
            updater(timesheet_id=existing_id, **save_values)
            message = f"Timesheet updated — {hours:g} hours saved."
        else:
            saver = _app_attr("save_timesheet_entry")
            if not callable(saver):
                st.error("Timesheet saving is temporarily unavailable.")
                return
            saver(**save_values)
            message = f"Timesheet submitted — {hours:g} hours saved."
    except Exception as exc:
        st.error(f"Timesheet could not be saved: {exc}")
        return

    success = _app_attr("pb_success") or getattr(st, "success", None)
    if callable(success):
        success(message)
    _rerun(st)


def render_employee_home(st: Any) -> None:
    user = _current_user(st)
    try:
        employee_id = int(user.get("employee_id") or 0)
    except Exception:
        employee_id = 0
    name = str(user.get("employee_name") or user.get("username") or "there").strip()

    st.markdown(
        """
        <style>
        .pb-staff-home-title{font-size:2rem;font-weight:850;line-height:1.05;margin:.15rem 0 .3rem;color:#2b2520}
        .pb-staff-home-sub{font-size:1.05rem;color:#665d54;margin-bottom:1rem}
        .pb-staff-job-card{background:#fffdfa;border:2px solid #2b2520;border-radius:18px;padding:1.15rem;margin:.4rem 0 .65rem;box-shadow:0 5px 18px rgba(0,0,0,.08)}
        .pb-staff-eyebrow{font-size:.78rem;font-weight:850;letter-spacing:.08em;color:#765f48;margin-bottom:.25rem}
        .pb-staff-job-title{font-size:1.65rem;font-weight:850;line-height:1.12;color:#201d1a;margin-bottom:.65rem}
        .pb-staff-address{font-size:1.35rem;font-weight:800;line-height:1.22;background:#f2e8dc;border-radius:12px;padding:.8rem;margin-bottom:.8rem;color:#201d1a}
        .pb-staff-detail{display:flex;justify-content:space-between;gap:.8rem;border-top:1px solid #e7ddd3;padding:.62rem 0;font-size:1rem}
        .pb-staff-detail b{font-size:.78rem;letter-spacing:.04em;color:#71675e}.pb-staff-detail span{font-weight:750;text-align:right;color:#201d1a}
        .pb-staff-notes{border-top:1px solid #e7ddd3;padding-top:.7rem;font-size:1rem;line-height:1.35}
        @media(max-width:768px){.pb-staff-home-title{font-size:1.8rem}.pb-staff-job-title{font-size:1.45rem}.pb-staff-address{font-size:1.2rem}.pb-staff-detail{display:block}.pb-staff-detail b,.pb-staff-detail span{display:block;text-align:left}.pb-staff-detail span{margin-top:.18rem;font-size:1.08rem}div[data-testid="stForm"] button[kind="primaryFormSubmit"]{min-height:58px!important;font-size:1.05rem!important;font-weight:850!important}}
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="pb-staff-home-title">Hi {html.escape(name)} 👋</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="pb-staff-home-sub">Here is what you need for today.</div>',
        unsafe_allow_html=True,
    )

    if employee_id <= 0:
        st.error("Your login is not linked to an employee record. Ask the office.")
        return

    scheduled_rows = _scheduled_jobs(employee_id, _today())
    if not scheduled_rows:
        st.warning(
            "NO JOB IS SCHEDULED FOR YOU TODAY. If that looks wrong, contact the office."
        )
    else:
        for row in scheduled_rows:
            _job_card(st, row)

    st.divider()
    _render_timesheet(st, employee_id, scheduled_rows)
    st.divider()
    if st.button(
        "More employee tools — photos, requests, forms",
        use_container_width=True,
        key="pb_open_full_employee_portal",
    ):
        st.session_state[FULL_PORTAL_KEY] = True
        _rerun(st)


def _patch_header(st: Any) -> bool:
    original = getattr(st, "header", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def employee_home_header(body: Any, *args: Any, **kwargs: Any):
        if str(body or "").strip() != "Employee Portal" or _role(st) != "employee":
            return original(body, *args, **kwargs)

        if bool(st.session_state.get(FULL_PORTAL_KEY, False)):
            result = original("Employee Portal — More Tools", *args, **kwargs)
            if st.button(
                "← Back to simple home",
                use_container_width=True,
                key="pb_back_to_employee_home",
            ):
                st.session_state[FULL_PORTAL_KEY] = False
                _rerun(st)
            return result

        render_employee_home(st)
        st.stop()
        return None

    employee_home_header._pb_original_header = original
    setattr(employee_home_header, PATCH_MARKER, True)
    st.header = employee_home_header
    return True


def install_employee_portal_home_guard() -> bool:
    st = _st()
    if st is None:
        return False
    return _patch_header(st)
