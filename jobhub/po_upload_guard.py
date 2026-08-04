"""Dedicated Purchase Order upload page for JobHub.

JobHub had PO data tables and job-document storage, but the upload path was too
hard to find from the live menus. This guard adds an explicit "Upload PO" route
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

CALC_BY_AMOUNT = "Enter PO amount → calculate %"
CALC_BY_PERCENT = "Enter % → calculate PO amount"
BASIS_TOTAL_JOB = "Whole job value"
BASIS_MANUAL_SCOPE = "Manual area / stage value"

RESET_SAFE_VALUES = {
    "main_menu": "Dashboard",
    "management_menu": "Builders & Clients",
    "site_operations_menu": "Staff Scheduler",
    "estimating_menu": "Import / Create Job Pack",
    "ai_menu": "JobHub AI Assistant",
}

MENU_MARKERS = {
    "Dashboard", "Jobs", "Job Folders", "Estimating", "Site Operations",
    "Management", "Reports", "Staff Scheduler", "Job Progress Tracker",
    "Import / Create Job Pack", "Estimate Working Sheet",
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


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _table_columns(table: str) -> set[str]:
    """Read live columns so inserts match Render/Postgres and local SQLite."""
    try:
        if _use_postgres():
            df = _df_query(
                """
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_name=?
                """,
                (table,),
            )
        else:
            df = _df_query(f"PRAGMA table_info({table})")
        if df is not None and not getattr(df, "empty", True):
            return set(df.get("name", []).astype(str).tolist())
    except Exception:
        pass
    return set()


def _ensure_table_column(table: str, column: str, definition: str) -> None:
    try:
        if _use_postgres():
            _execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
            return
        existing = _table_columns(table)
        if column not in existing:
            _execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except Exception:
        pass


def _insert_existing_columns(table: str, values: dict[str, Any]) -> None:
    """Insert only columns that exist in the live table.

    Older JobHub databases have slightly different document-column names. This
    prevents errors like missing ``uploaded_at`` while still saving every column
    that the live schema supports.
    """
    existing = _table_columns(table)
    if not existing:
        existing = set(values)
    columns = [column for column in values if column in existing]
    if not columns:
        raise RuntimeError(f"No matching columns found for {table}.")
    placeholders = ",".join("?" for _ in columns)
    column_sql = ",".join(columns)
    params = tuple(values[column] for column in columns)
    _execute(f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})", params)


def _select_column_expr(existing: set[str], column: str, alias: str, default: str = "''") -> str:
    if column in existing:
        return f'{column} AS "{alias}"'
    return f'{default} AS "{alias}"'


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


def _job_value(job_id: int) -> float:
    try:
        job = _df_query(
            "SELECT COALESCE(contract_value,0) AS contract_value FROM jobs WHERE id=?",
            (int(job_id),),
        )
        if job is not None and not getattr(job, "empty", True):
            return _safe_float(job.iloc[0]["contract_value"])
    except Exception:
        pass
    return 0.0


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
    for column, definition in (
        ("document_type", "TEXT"),
        ("file_name", "TEXT"),
        ("file_path", "TEXT"),
        ("uploaded_at", "TEXT"),
        ("created_at", "TEXT"),
        ("uploaded_by", "TEXT"),
        ("notes", "TEXT"),
        ("mime_type", "TEXT"),
    ):
        _ensure_table_column("job_documents", column, definition)

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
            notes TEXT,
            po_scope_label TEXT,
            po_scope_base_ex_gst REAL DEFAULT 0,
            po_scope_percent REAL DEFAULT 0,
            po_percent_of_job REAL DEFAULT 0,
            po_calculation_mode TEXT
        )
        """
    )
    for column, definition in (
        ("job_stage_id", "INTEGER"),
        ("po_number", "TEXT"),
        ("po_value_ex_gst", "REAL DEFAULT 0"),
        ("file_name", "TEXT"),
        ("file_path", "TEXT"),
        ("status", "TEXT DEFAULT 'Uploaded'"),
        ("received_date", "TEXT"),
        ("uploaded_at", "TEXT"),
        ("uploaded_by", "TEXT"),
        ("notes", "TEXT"),
        ("po_scope_label", "TEXT"),
        ("po_scope_base_ex_gst", "REAL DEFAULT 0"),
        ("po_scope_percent", "REAL DEFAULT 0"),
        ("po_percent_of_job", "REAL DEFAULT 0"),
        ("po_calculation_mode", "TEXT"),
    ):
        _ensure_table_column("job_purchase_orders", column, definition)


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


def _calculate_po_values(mode: str, scope_base: float, job_value: float, entered_amount: float, entered_percent: float) -> dict[str, float]:
    clean_scope_base = max(0.0, _safe_float(scope_base))
    clean_job_value = max(0.0, _safe_float(job_value))
    if mode == CALC_BY_PERCENT:
        scope_percent = max(0.0, _safe_float(entered_percent))
        amount = clean_scope_base * scope_percent / 100.0 if clean_scope_base else 0.0
    else:
        amount = max(0.0, _safe_float(entered_amount))
        scope_percent = amount / clean_scope_base * 100.0 if clean_scope_base else 0.0
    job_percent = amount / clean_job_value * 100.0 if clean_job_value else 0.0
    return {
        "amount": round(amount, 2),
        "scope_percent": round(scope_percent, 4),
        "job_percent": round(job_percent, 4),
    }


def _record_po(
    job_id: int,
    stage_id: int | None,
    po_number: str,
    value_ex_gst: float,
    file_name: str,
    file_path: str,
    notes: str,
    uploaded_by: str,
    scope_label: str,
    scope_base_ex_gst: float,
    scope_percent: float,
    percent_of_job: float,
    calculation_mode: str,
) -> None:
    now = _now()
    document_values = {
        "job_id": int(job_id),
        "document_type": "Purchase Order",
        "doc_type": "Purchase Order",
        "type": "Purchase Order",
        "file_name": file_name,
        "filename": file_name,
        "name": file_name,
        "file_path": file_path,
        "path": file_path,
        "uploaded_at": now,
        "created_at": now,
        "upload_date": now,
        "date_uploaded": now,
        "uploaded_by": uploaded_by,
        "created_by": uploaded_by,
        "notes": notes or f"PO {po_number}".strip(),
        "description": notes or f"PO {po_number}".strip(),
        "mime_type": "application/pdf",
    }
    _insert_existing_columns("job_documents", document_values)

    po_values = {
        "job_id": int(job_id),
        "job_stage_id": stage_id,
        "stage_id": stage_id,
        "po_number": po_number.strip(),
        "po_value_ex_gst": float(value_ex_gst or 0),
        "value_ex_gst": float(value_ex_gst or 0),
        "amount_ex_gst": float(value_ex_gst or 0),
        "file_name": file_name,
        "filename": file_name,
        "file_path": file_path,
        "path": file_path,
        "status": "Uploaded",
        "received_date": now[:10],
        "uploaded_at": now,
        "created_at": now,
        "uploaded_by": uploaded_by,
        "created_by": uploaded_by,
        "notes": notes,
        "po_scope_label": scope_label,
        "po_scope_base_ex_gst": float(scope_base_ex_gst or 0),
        "po_scope_percent": float(scope_percent or 0),
        "po_percent_of_job": float(percent_of_job or 0),
        "po_calculation_mode": calculation_mode,
    }
    _insert_existing_columns("job_purchase_orders", po_values)


def _recent_pos(job_id: int) -> Any:
    try:
        existing = _table_columns("job_purchase_orders")
        if not existing:
            return None
        select_sql = ",".join(
            [
                _select_column_expr(existing, "po_number", "PO Number"),
                _select_column_expr(existing, "po_value_ex_gst", "Value ex GST", "0"),
                _select_column_expr(existing, "po_scope_label", "Scope"),
                _select_column_expr(existing, "po_scope_base_ex_gst", "Scope Value ex GST", "0"),
                _select_column_expr(existing, "po_scope_percent", "% of Scope", "0"),
                _select_column_expr(existing, "po_percent_of_job", "% of Job", "0"),
                _select_column_expr(existing, "status", "Status"),
                _select_column_expr(existing, "received_date", "Received"),
                _select_column_expr(existing, "file_name", "File"),
                _select_column_expr(existing, "notes", "Notes"),
            ]
        )
        order_column = "id" if "id" in existing else "job_id"
        return _df_query(
            f"""
            SELECT {select_sql}
            FROM job_purchase_orders
            WHERE job_id=?
            ORDER BY {order_column} DESC
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
    job_value = _job_value(job_id)
    stages = _stage_options(job_id)

    with st.form("po_upload_form", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        po_number = c1.text_input("PO Number", placeholder="e.g. PO-12345")
        selected_stage = c2.selectbox("Stage / area", list(stages), key="po_upload_stage")
        basis = c3.selectbox(
            "Calculate % from",
            [BASIS_TOTAL_JOB, BASIS_MANUAL_SCOPE],
            help="Use manual area/stage value for Internal, External, upper scaffold, lower external or touch-ups.",
        )

        suggested_scope_label = selected_stage if selected_stage != "Whole job / not stage-specific" else "Whole job"
        scope_label = st.text_input(
            "Area / scope name",
            value=suggested_scope_label,
            placeholder="e.g. External, Internal, Upper scaff work",
        )

        if basis == BASIS_MANUAL_SCOPE:
            scope_base = st.number_input(
                "Area / stage value ex GST",
                min_value=0.0,
                step=100.0,
                value=0.0,
                help="Example: enter the external works total. The PO percentage is then calculated against this amount.",
            )
        else:
            scope_base = job_value
            st.metric("Whole job value ex GST", f"${job_value:,.2f}")

        mode = st.radio(
            "PO calculation mode",
            [CALC_BY_AMOUNT, CALC_BY_PERCENT],
            horizontal=True,
            help="Choose whether you want to enter the dollar amount or the percentage.",
        )
        if mode == CALC_BY_PERCENT:
            entered_percent = st.number_input(
                "% of selected area / scope",
                min_value=0.0,
                max_value=1000.0,
                step=1.0,
                value=0.0,
            )
            entered_amount = 0.0
        else:
            entered_amount = st.number_input(
                "PO Value ex GST",
                min_value=0.0,
                step=100.0,
                value=0.0,
            )
            entered_percent = 0.0

        calculated = _calculate_po_values(
            mode, scope_base, job_value, entered_amount, entered_percent,
        )
        m1, m2, m3 = st.columns(3)
        m1.metric("PO amount ex GST", f"${calculated['amount']:,.2f}")
        m2.metric("% of selected scope", f"{calculated['scope_percent']:.2f}%")
        m3.metric("% of whole job", f"{calculated['job_percent']:.2f}%")

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
        if _safe_float(scope_base) <= 0:
            _error("Enter a job, area or stage value first so JobHub can calculate the percentage.")
            return
        if calculated["amount"] <= 0:
            _error("Enter a PO amount or percentage greater than zero.")
            return
        try:
            user = (_app_attr("get_current_user", lambda: {})() or {})
            uploaded_by = str(user.get("username") or user.get("name") or "JobHub user")
            file_name, file_path = _save_uploaded_file(job_id, po_number, uploaded)
            _record_po(
                job_id,
                stages[selected_stage],
                po_number,
                calculated["amount"],
                file_name,
                file_path,
                notes.strip(),
                uploaded_by,
                scope_label.strip() or suggested_scope_label,
                float(scope_base or 0),
                calculated["scope_percent"],
                calculated["job_percent"],
                mode,
            )
            _success(
                f"PO {po_number.strip()} uploaded: ${calculated['amount']:,.2f} ex GST, "
                f"{calculated['scope_percent']:.2f}% of {scope_label.strip() or suggested_scope_label}."
            )
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
    if PO_UPLOAD_LABEL in labels:
        return True
    if label_text == "Menu" or key_text == "main_menu":
        return bool(labels.intersection(MENU_MARKERS))
    if label_text in {"Management Section", "Site Section"} or key_text in {"management_menu", "site_operations_menu", "estimating_menu"}:
        return bool(labels.intersection(MENU_MARKERS))
    return len(labels.intersection(MENU_MARKERS)) >= 2


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

        result = original(*tuple(arg_list), **kwargs)

        if should_inject:
            try:
                if str(result) == PO_UPLOAD_LABEL or (key and str(_session_value(st, key, "")) == PO_UPLOAD_LABEL):
                    st.session_state[PO_UPLOAD_STATE_KEY] = True
                    _show_page(st)
                elif bool(_session_value(st, PO_UPLOAD_STATE_KEY, False)):
                    _show_page(st)
            except Exception:
                pass
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
