"""Dedicated Purchase Order upload page for JobHub.

JobHub had PO data tables and job-document storage, but the upload path was too
hard to find from the live menus.  This guard adds an explicit "Upload PO" route
without changing the main app router file, and protects that route from the main
app's hard-coded dashboard reset checks.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sys
from typing import Any


PO_UPLOAD_LABEL = "Upload PO"
PO_UPLOAD_STATE_KEY = "_pb_show_po_upload_page"
SESSION_GET_PATCH_KEY = "_pb_po_upload_session_get_guard"
PO_UPLOAD_DOC_TYPES = ("Purchase Order", "PO", "Builder Purchase Order")

RESET_SAFE_VALUES = {
    "main_menu": "Dashboard",
    "management_menu": "Builders & Clients",
    "site_operations_menu": "Staff Scheduler",
    "estimating_menu": "Import / Create Job Pack",
    "ai_menu": "JobHub AI Assistant",
}


def _st() -> Any:
    return sys.modules.get("streamlit")


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _use_postgres() -> bool:
    try:
        return bool(_app_attr("USE_POSTGRES", False))
    except Exception:
        return False


def _safe_rerun(st: Any) -> None:
    rerun = _app_attr("pb_rerun") or _app_attr("refresh") or getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


def _session_value(st: Any, key: str, default: Any = None) -> Any:
    try:
        return st.session_state[key]
    except Exception:
        try:
            get_fn = getattr(st.session_state, "get", None)
            if callable(get_fn):
                return get_fn(key, default)
        except Exception:
            pass
    return default


def _install_session_state_reset_guard(st: Any) -> bool:
    """Stop JobHub's menu validation from wiping Upload PO to Dashboard."""
    try:
        state_cls = type(st.session_state)
        original_get = getattr(state_cls, "get", None)
    except Exception:
        return False
    if original_get is None or getattr(original_get, SESSION_GET_PATCH_KEY, False):
        return False

    def guarded_get(self: Any, key: Any, default: Any = None) -> Any:
        value = original_get(self, key, default)
        key_text = str(key or "")
        if key_text in RESET_SAFE_VALUES and str(value) == PO_UPLOAD_LABEL:
            return RESET_SAFE_VALUES[key_text]
        return value

    guarded_get._pb_original_get = original_get
    setattr(guarded_get, SESSION_GET_PATCH_KEY, True)
    setattr(state_cls, "get", guarded_get)
    return True


def _execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    execute = _app_attr("execute")
    if not callable(execute):
        raise RuntimeError("JobHub database execute helper is not available.")
    execute(sql, params)


def _df_query(sql: str, params: tuple[Any, ...] = ()) -> Any:
    df_query = _app_attr("df_query") or _app_attr("safe_df_query")
    if not callable(df_query):
        raise RuntimeError("JobHub database query helper is not available.")
    return df_query(sql, params)


def _success(message: str) -> None:
    st = _st()
    fn = _app_attr("pb_success") or (getattr(st, "success", None) if st is not None else None)
    if callable(fn):
        fn(message)


def _error(message: str) -> None:
    st = _st()
    fn = _app_attr("pb_error") or (getattr(st, "error", None) if st is not None else None)
    if callable(fn):
        fn(message)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_filename(value: str) -> str:
    name = Path(value or "purchase_order.pdf").name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name or "purchase_order.pdf"


def _job_options() -> dict[str, int]:
    try:
        jobs = _df_query(
            """
            SELECT j.id,j.job_no,j.job_name,COALESCE(b.name,'') AS builder
            FROM jobs j
            LEFT JOIN builders_clients b ON b.id=j.builder_client_id
            WHERE LOWER(COALESCE(j.status,'')) NOT IN ('archived','cancelled','deleted')
            ORDER BY j.job_no,j.job_name
            """
        )
    except Exception:
        return {}
    if jobs is None or getattr(jobs, "empty", True):
        return {}
    return {
        f"{row['job_no']} · {row['job_name']} · {row['builder']}": int(row["id"])
        for _, row in jobs.iterrows()
    }


def _stage_options(job_id: int) -> dict[str, int | None]:
    options: dict[str, int | None] = {"Whole job / not stage-specific": None}
    try:
        stages = _df_query(
            """
            SELECT id,stage_name
            FROM job_stages
            WHERE job_id=?
            ORDER BY sequence_order,id
            """,
            (int(job_id),),
        )
    except Exception:
        return options
    if stages is not None and not getattr(stages, "empty", True):
        for _, row in stages.iterrows():
            options[str(row["stage_name"] or f"Stage {int(row['id'])}")] = int(row["id"])
    return options


def _ensure_schema() -> None:
    pk = "SERIAL PRIMARY KEY" if _use_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    _execute(
        f"""
        CREATE TABLE IF NOT EXISTS job_documents (
            id {pk},
            job_id INTEGER NOT NULL,
            document_type TEXT,
            file_name TEXT,
            file_path TEXT,
            uploaded_at TEXT,
            created_at TEXT,
            uploaded_by TEXT,
            notes TEXT,
            mime_type TEXT
        )
        """
    )
    _execute(
        f"""
        CREATE TABLE IF NOT EXISTS job_purchase_orders (
            id {pk},
            job_id INTEGER NOT NULL,
            job_stage_id INTEGER,
            po_number TEXT,
            po_value_ex_gst REAL DEFAULT 0,
            file_name TEXT,
            file_path TEXT,
            status TEXT DEFAULT 'Uploaded',
            received_date TEXT,
            uploaded_at TEXT,
            uploaded_by TEXT,
            notes TEXT
        )
        """
    )


def _save_uploaded_file(job_id: int, po_number: str, uploaded_file: Any) -> tuple[str, str]:
    base_dir = Path("/var/data/jobhub_uploads/purchase_orders")
    try:
        data_dir = Path(str(_app_attr("DATA_DIR", "/var/data") or "/var/data"))
        base_dir = data_dir / "jobhub_uploads" / "purchase_orders"
    except Exception:
        pass
    job_dir = base_dir / f"job_{int(job_id)}"
    job_dir.mkdir(parents=True, exist_ok=True)
    original_name = _clean_filename(getattr(uploaded_file, "name", "purchase_order.pdf"))
    prefix = _clean_filename(po_number or "PO")
    file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{prefix}_{original_name}"
    file_path = job_dir / file_name
    file_path.write_bytes(uploaded_file.getbuffer())
    return file_name, str(file_path)


def _record_po(job_id: int, stage_id: int | None, po_number: str, value_ex_gst: float, file_name: str, file_path: str, notes: str, uploaded_by: str) -> None:
    now = _now()
    _execute(
        """
        INSERT INTO job_documents
        (job_id,document_type,file_name,file_path,uploaded_at,created_at,uploaded_by,notes,mime_type)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            int(job_id), "Purchase Order", file_name, file_path, now, now,
            uploaded_by, notes or f"PO {po_number}".strip(), "application/pdf",
        ),
    )
    _execute(
        """
        INSERT INTO job_purchase_orders
        (job_id,job_stage_id,po_number,po_value_ex_gst,file_name,file_path,status,received_date,uploaded_at,uploaded_by,notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(job_id), stage_id, po_number.strip(), float(value_ex_gst or 0),
            file_name, file_path, "Uploaded", now[:10], now, uploaded_by, notes,
        ),
    )


def _recent_pos(job_id: int) -> Any:
    try:
        return _df_query(
            """
            SELECT po_number AS 'PO Number',po_value_ex_gst AS 'Value ex GST',
                   status AS 'Status',received_date AS 'Received',file_name AS 'File',notes AS 'Notes'
            FROM job_purchase_orders
            WHERE job_id=?
            ORDER BY id DESC
            LIMIT 20
            """,
            (int(job_id),),
        )
    except Exception:
        return None


def render_po_upload_page() -> None:
    st = _st()
    if st is None:
        return
    _ensure_schema()
    st.header("Upload PO")
    st.caption("Upload a builder/client purchase order and attach it directly to the selected job and stage.")

    jobs = _job_options()
    if not jobs:
        st.info("Create a job first, then upload the PO against that job.")
        return
    selected_job = st.selectbox("Job", list(jobs), key="po_upload_job")
    job_id = jobs[selected_job]
    stages = _stage_options(job_id)

    with st.form("po_upload_form", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        po_number = c1.text_input("PO Number", placeholder="e.g. PO-12345")
        value_ex_gst = c2.number_input("PO Value ex GST", min_value=0.0, step=100.0, value=0.0)
        selected_stage = c3.selectbox("Stage", list(stages), key="po_upload_stage")
        uploaded = st.file_uploader(
            "PO file",
            type=["pdf", "png", "jpg", "jpeg", "doc", "docx", "xlsx", "csv"],
            key="po_upload_file",
        )
        notes = st.text_area("Notes", placeholder="Anything important about this PO")
        submitted = st.form_submit_button("Upload PO", type="primary")

    if submitted:
        if uploaded is None:
            _error("Select the PO file first.")
            return
        if not po_number.strip():
            _error("Enter the PO number first.")
            return
        try:
            user = (_app_attr("get_current_user", lambda: {})() or {})
            uploaded_by = str(user.get("username") or user.get("name") or "JobHub user")
            file_name, file_path = _save_uploaded_file(job_id, po_number, uploaded)
            _record_po(
                job_id,
                stages[selected_stage],
                po_number,
                float(value_ex_gst or 0),
                file_name,
                file_path,
                notes.strip(),
                uploaded_by,
            )
            _success(f"PO {po_number.strip()} uploaded and attached to the job.")
            _safe_rerun(st)
        except Exception as exc:
            _error(f"PO upload failed: {exc}")

    recent = _recent_pos(job_id)
    if recent is not None and not getattr(recent, "empty", True):
        st.markdown("### Recent POs for this job")
        st.dataframe(recent, width="stretch", hide_index=True)


def _show_page(st: Any) -> None:
    st.session_state[PO_UPLOAD_STATE_KEY] = True
    render_po_upload_page()
    st.stop()


def _labels(options: Any) -> list[str]:
    try:
        return [str(item) for item in list(options)]
    except Exception:
        return []


def _should_inject(label: Any, key: Any, options: Any) -> bool:
    labels = set(_labels(options))
    label_text = str(label or "")
    key_text = str(key or "")
    menu_markers = {
        "Dashboard", "Jobs", "Job Folders", "Estimating", "Site Operations",
        "Management", "Reports", "Staff Scheduler", "Job Progress Tracker",
        "Import / Create Job Pack", "Estimate Working Sheet",
    }
    if PO_UPLOAD_LABEL in labels:
        return True
    if label_text == "Menu" or key_text == "main_menu":
        return bool(labels.intersection(menu_markers))
    if label_text in {"Management Section", "Site Section"} or key_text in {"management_menu", "site_operations_menu", "estimating_menu"}:
        return bool(labels.intersection(menu_markers))
    return len(labels.intersection(menu_markers)) >= 2


def _patch_radio(owner: Any, st: Any) -> bool:
    original = getattr(owner, "radio", None)
    if original is None or getattr(original, "_pb_po_upload_guard", False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        arg_list = list(args)
        options_index = None
        label = kwargs.get("label", "")
        if len(arg_list) >= 2 and isinstance(arg_list[0], str):
            label = arg_list[0]
            options_index = 1
        elif len(arg_list) >= 3:
            label = arg_list[1]
            options_index = 2
        elif "options" in kwargs and args:
            label = args[0]
        options = arg_list[options_index] if options_index is not None else kwargs.get("options")
        key = str(kwargs.get("key") or "")
        should_inject = _should_inject(label, key, options)

        if should_inject:
            try:
                labels = _labels(options)
                if PO_UPLOAD_LABEL not in labels:
                    values = list(options)
                    values.append(PO_UPLOAD_LABEL)
                    if options_index is not None:
                        arg_list[options_index] = values
                    else:
                        kwargs["options"] = values
            except Exception:
                pass
            try:
                if key and str(_session_value(st, key, "")) == PO_UPLOAD_LABEL:
                    _show_page(st)
                if bool(_session_value(st, PO_UPLOAD_STATE_KEY, False)):
                    _show_page(st)
            except Exception:
                pass

        result = original(*tuple(arg_list), **kwargs)
        if should_inject and str(result) == PO_UPLOAD_LABEL:
            st.session_state[PO_UPLOAD_STATE_KEY] = True
            _show_page(st)
        return result

    wrapper._pb_po_upload_guard = True
    wrapper._pb_original_radio = original
    setattr(owner, "radio", wrapper)
    return True


def install_po_upload_guard() -> bool:
    st = _st()
    if st is None:
        return False
    installed = _install_session_state_reset_guard(st)
    installed = _patch_radio(st, st) or installed
    delta_module = sys.modules.get("streamlit.delta_generator")
    delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    if delta_cls is not None:
        installed = _patch_radio(delta_cls, st) or installed
    return installed
