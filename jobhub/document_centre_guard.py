"""Unified JobHub document centre.

One upload surface classifies every managed document before storage. Job-specific
files are also mirrored into ``job_documents`` so the existing Job Folder,
PlanReader bridge and legacy document views continue to see them.
"""

from __future__ import annotations

from datetime import date, datetime
import hashlib
from pathlib import Path
import re
import sys
from typing import Any

from jobhub_time import jobhub_now, jobhub_today
from .runtime import DATA_DIR, JOB_FILES_DIR


DOCUMENT_CENTRE_LABEL = "Document Centre"
PATCH_MARKER = "_pb_document_centre_guard"

CATEGORY_TYPES: dict[str, list[str]] = {
    "Job Specific": [
        "Plans / Architectural Drawings",
        "Colour Schedule",
        "Purchase Order",
        "Other Trade / Coordination Drawing",
        "Site-Specific Drawing / Detail",
        "SWMS / Safety Document",
        "PlanReader 3D Render",
        "Specification",
        "Contract / Scope of Works",
        "Variation",
        "RFI / Clarification",
        "Site Photo / Evidence",
        "QA / ITP / Defect Record",
        "SDS / Product Data",
        "Warranty / Manual",
        "Delivery Docket",
        "Progress Claim / Invoice",
        "Completion / Sign-off",
        "General Correspondence",
        "Other Job Document",
    ],
    "Job Packs": [
        "Tender / Estimate Pack",
        "Initial Job Pack",
        "Supervisor / Leading Hand Pack",
        "Site Pack",
        "Safety Pack",
        "Completion / Handover Pack",
        "Other Job Pack",
    ],
    "Employee Info": [
        "Employment / HR",
        "Licence / Qualification",
        "White Card / Induction",
        "Training / Competency",
        "Leave / Availability",
        "Performance / Review",
        "Other Employee Document",
    ],
    "Builder / Client Info": [
        "Contract / Agreement",
        "Company / Contact Information",
        "Insurance / Compliance",
        "Terms / Trading",
        "Standard Scope / Requirements",
        "Correspondence",
        "Other Builder / Client Document",
    ],
    "Company / General": [
        "Company Licence / Registration",
        "Insurance / Compliance",
        "Company SWMS / Safety Template",
        "Policies / Procedures",
        "Supplier / Product Information",
        "Forms / Templates",
        "Training / Reference Material",
        "Other Company Document",
    ],
}

UPLOAD_TYPES = [
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "txt",
    "jpg", "jpeg", "png", "webp", "heic",
    "zip", "dwg", "dxf",
]


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


def _fallback_execute(sql: str, params: tuple[Any, ...] = ()) -> Any:
    from .database import connect

    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        lastrowid = getattr(cur, "lastrowid", None)
        conn.commit()
        return lastrowid
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _execute(sql: str, params: tuple[Any, ...] = ()) -> Any:
    fn = _app_attr("execute")
    if callable(fn):
        return fn(sql, params)
    return _fallback_execute(sql, params)


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    query = _app_attr("df_query") or _app_attr("safe_df_query")
    if callable(query):
        frame = query(sql, params)
        if frame is None or getattr(frame, "empty", True):
            return []
        return [dict(row) for row in frame.to_dict("records")]

    from .database import connect

    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        values = cur.fetchall()
        columns = [str(item[0]) for item in (cur.description or [])]
        return [dict(zip(columns, row)) for row in values]
    finally:
        conn.close()


def _safe_name(value: Any, fallback: str = "document") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9._ -]+", "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._- ")
    return text[:160] or fallback


def _slug(value: Any, fallback: str = "general") -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip()).strip("_")
    return text[:100] or fallback


def _now() -> str:
    return jobhub_now().strftime("%Y-%m-%d %H:%M:%S")


def _current_user() -> dict[str, Any]:
    st = _st()
    if st is None:
        return {}
    try:
        value = st.session_state.get("user") or {}
        return dict(value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def _current_role() -> str:
    fn = _app_attr("current_role")
    if callable(fn):
        try:
            return str(fn() or "").strip().lower()
        except Exception:
            pass
    return str(_current_user().get("role") or "").strip().lower()


def _current_username() -> str:
    user = _current_user()
    for key in ("username", "name", "display_name"):
        if str(user.get(key) or "").strip():
            return str(user.get(key)).strip()
    return "JobHub user"


def _ensure_schema() -> None:
    pk = "SERIAL PRIMARY KEY" if _use_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    _execute(
        f"""
        CREATE TABLE IF NOT EXISTS document_library (
            id {pk},
            library_category TEXT NOT NULL,
            document_type TEXT NOT NULL,
            entity_type TEXT,
            entity_id INTEGER,
            entity_label TEXT,
            job_id INTEGER,
            job_document_id INTEGER,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            mime_type TEXT,
            file_sha256 TEXT,
            revision TEXT,
            document_date TEXT,
            notes TEXT,
            uploaded_by TEXT,
            source_app TEXT DEFAULT 'JobHub',
            created_at TEXT NOT NULL
        )
        """
    )
    _execute(
        "CREATE INDEX IF NOT EXISTS idx_document_library_category ON document_library(library_category, document_type)"
    )
    _execute(
        "CREATE INDEX IF NOT EXISTS idx_document_library_job ON document_library(job_id, created_at)"
    )
    _execute(
        "CREATE INDEX IF NOT EXISTS idx_document_library_entity ON document_library(entity_type, entity_id, created_at)"
    )
    # Keep the existing JobHub document contract available for all job-scoped
    # uploads. CREATE IF NOT EXISTS is intentionally compatible with the legacy table.
    _execute(
        f"""
        CREATE TABLE IF NOT EXISTS job_documents (
            id {pk},
            job_id INTEGER NOT NULL,
            document_type TEXT,
            file_name TEXT,
            file_path TEXT,
            created_at TEXT,
            notes TEXT
        )
        """
    )


def _jobs() -> list[dict[str, Any]]:
    return _rows(
        """
        SELECT id, COALESCE(job_no, '') AS job_no,
               COALESCE(job_name, '') AS job_name,
               COALESCE(status, '') AS status
        FROM jobs
        ORDER BY CASE WHEN LOWER(COALESCE(status,'')) IN ('active','in progress','current') THEN 0 ELSE 1 END,
                 COALESCE(job_no,''), COALESCE(job_name,''), id DESC
        """
    )


def _employees() -> list[dict[str, Any]]:
    return _rows(
        """
        SELECT id, COALESCE(name, '') AS name,
               COALESCE(role, '') AS role,
               COALESCE(status, '') AS status
        FROM employees
        ORDER BY CASE WHEN LOWER(COALESCE(status,''))='active' THEN 0 ELSE 1 END,
                 LOWER(COALESCE(name,'')), id
        """
    )


def _builders() -> list[dict[str, Any]]:
    return _rows(
        """
        SELECT id, COALESCE(type, '') AS type,
               COALESCE(name, '') AS name,
               COALESCE(contact_name, '') AS contact_name
        FROM builders_clients
        ORDER BY LOWER(COALESCE(name,'')), id
        """
    )


def _job_label(row: dict[str, Any]) -> str:
    number = str(row.get("job_no") or "").strip()
    name = str(row.get("job_name") or "").strip()
    status = str(row.get("status") or "").strip()
    core = " — ".join(part for part in (number, name) if part) or f"Job #{row.get('id')}"
    return f"{core} [{status}]" if status else core


def _entity_choice(st: Any, category: str) -> tuple[str, int | None, str, int | None, dict[str, Any] | None]:
    if category in {"Job Specific", "Job Packs"}:
        rows = _jobs()
        if not rows:
            st.warning("No jobs are available. Create the job first, then upload its documents.")
            return "job", None, "", None, None
        options = {_job_label(row): row for row in rows}
        label = st.selectbox("Job", list(options), key="document_centre_job")
        selected = options[label]
        return "job", int(selected["id"]), label, int(selected["id"]), selected

    if category == "Employee Info":
        rows = _employees()
        if not rows:
            st.warning("No employees are available.")
            return "employee", None, "", None, None
        options = {
            f"{row.get('name') or 'Unnamed'} — {row.get('role') or 'No role'} [{row.get('status') or 'No status'}]": row
            for row in rows
        }
        label = st.selectbox("Employee", list(options), key="document_centre_employee")
        selected = options[label]
        return "employee", int(selected["id"]), str(selected.get("name") or label), None, selected

    if category == "Builder / Client Info":
        rows = _builders()
        if not rows:
            st.warning("No builders or clients are available.")
            return "builder_client", None, "", None, None
        options = {
            f"{row.get('name') or 'Unnamed'} — {row.get('type') or 'Builder / Client'}": row
            for row in rows
        }
        label = st.selectbox("Builder / client", list(options), key="document_centre_builder")
        selected = options[label]
        return "builder_client", int(selected["id"]), str(selected.get("name") or label), None, selected

    return "company", None, "Premier Brushworks", None, None


def _job_folder(job: dict[str, Any]) -> Path:
    job_no = _safe_name(job.get("job_no"), f"job_{job.get('id')}")
    folder = Path(JOB_FILES_DIR) / job_no / "documents"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _library_folder(category: str, entity_type: str, entity_id: int | None, entity_label: str) -> Path:
    root = Path(DATA_DIR) / "document_library" / _slug(category)
    if entity_type and (entity_id is not None or entity_label):
        root = root / _slug(f"{entity_type}_{entity_id or ''}_{entity_label}")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _bytes_from_upload(upload: Any) -> bytes:
    if hasattr(upload, "getvalue"):
        return bytes(upload.getvalue())
    if hasattr(upload, "read"):
        data = upload.read()
        return bytes(data)
    raise RuntimeError("The uploaded file could not be read.")


def _insert_job_document(job_id: int, document_type: str, file_name: str, file_path: str, notes: str) -> int | None:
    created_at = _now()
    _execute(
        """
        INSERT INTO job_documents(job_id, document_type, file_name, file_path, created_at, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (int(job_id), document_type, file_name, file_path, created_at, notes),
    )
    try:
        rows = _rows(
            """
            SELECT id FROM job_documents
            WHERE job_id=? AND file_path=?
            ORDER BY id DESC LIMIT 1
            """,
            (int(job_id), file_path),
        )
        return int(rows[0]["id"]) if rows else None
    except Exception:
        return None


def _store_one(
    upload: Any,
    category: str,
    document_type: str,
    entity_type: str,
    entity_id: int | None,
    entity_label: str,
    job_id: int | None,
    job_record: dict[str, Any] | None,
    revision: str,
    document_date: str,
    notes: str,
) -> dict[str, Any]:
    data = _bytes_from_upload(upload)
    if not data:
        raise RuntimeError(f"{getattr(upload, 'name', 'File')} is empty.")

    original_name = _safe_name(getattr(upload, "name", "document"), "document")
    stem = _safe_name(Path(original_name).stem, "document")
    suffix = Path(original_name).suffix.lower()
    timestamp = jobhub_now().strftime("%Y%m%d_%H%M%S_%f")
    stored_name = f"{timestamp}_{stem}{suffix}"

    if job_id is not None and job_record is not None:
        folder = _job_folder(job_record)
    else:
        folder = _library_folder(category, entity_type, entity_id, entity_label)
    path = folder / stored_name
    path.write_bytes(data)

    digest = hashlib.sha256(data).hexdigest()
    mime_type = str(getattr(upload, "type", "") or "")
    job_document_id = None
    if job_id is not None:
        job_document_id = _insert_job_document(
            int(job_id), document_type, original_name, str(path), notes
        )

    _execute(
        """
        INSERT INTO document_library(
            library_category, document_type, entity_type, entity_id, entity_label,
            job_id, job_document_id, file_name, file_path, mime_type, file_sha256,
            revision, document_date, notes, uploaded_by, source_app, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'JobHub', ?)
        """,
        (
            category,
            document_type,
            entity_type,
            entity_id,
            entity_label,
            job_id,
            job_document_id,
            original_name,
            str(path),
            mime_type,
            digest,
            revision,
            document_date,
            notes,
            _current_username(),
            _now(),
        ),
    )
    return {
        "file_name": original_name,
        "file_path": str(path),
        "job_document_id": job_document_id,
    }


def _recent_documents(limit: int = 250) -> list[dict[str, Any]]:
    # LIMIT is intentionally literal rather than a placeholder for compatibility
    # with the legacy SQLite/Postgres query adapters.
    limit = max(10, min(int(limit), 1000))
    return _rows(
        f"""
        SELECT id, library_category, document_type, entity_type, entity_id,
               entity_label, job_id, file_name, file_path, revision,
               document_date, notes, uploaded_by, source_app, created_at
        FROM document_library
        ORDER BY id DESC
        LIMIT {limit}
        """
    )


def _render_register(st: Any) -> None:
    st.subheader("Document register")
    rows = _recent_documents()
    if not rows:
        st.info("No documents have been added through the Document Centre yet.")
        return

    categories = ["All"] + list(CATEGORY_TYPES)
    c1, c2 = st.columns([1, 2])
    selected_category = c1.selectbox("Filter category", categories, key="document_register_category")
    search_text = c2.text_input(
        "Search documents",
        placeholder="file name, job, employee, builder, document type, notes...",
        key="document_register_search",
    ).strip().lower()

    filtered = []
    for row in rows:
        if selected_category != "All" and str(row.get("library_category")) != selected_category:
            continue
        if search_text:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in (
                    "file_name", "library_category", "document_type", "entity_label",
                    "revision", "notes", "uploaded_by", "document_date", "created_at",
                )
            ).lower()
            if search_text not in haystack:
                continue
        filtered.append(row)

    if not filtered:
        st.info("No documents match those filters.")
        return

    display_rows = [
        {
            "ID": row.get("id"),
            "Category": row.get("library_category"),
            "Type": row.get("document_type"),
            "Linked to": row.get("entity_label"),
            "File": row.get("file_name"),
            "Rev": row.get("revision"),
            "Document date": row.get("document_date"),
            "Uploaded by": row.get("uploaded_by"),
            "Uploaded": row.get("created_at"),
        }
        for row in filtered
    ]
    st.dataframe(display_rows, width="stretch", hide_index=True)

    download_options = {
        f"#{row.get('id')} · {row.get('file_name')} · {row.get('document_type')}": row
        for row in filtered
    }
    selected_label = st.selectbox(
        "Download / inspect",
        list(download_options),
        key="document_register_download_choice",
    )
    selected = download_options[selected_label]
    selected_path = Path(str(selected.get("file_path") or ""))
    if selected_path.exists() and selected_path.is_file():
        st.download_button(
            "Download selected document",
            data=selected_path.read_bytes(),
            file_name=str(selected.get("file_name") or selected_path.name),
            mime="application/octet-stream",
            key=f"document_register_download_{selected.get('id')}",
        )
    else:
        st.warning("The database record exists, but the stored file is not available on this server.")


def render_document_centre_page() -> None:
    st = _st()
    if st is None:
        return
    _ensure_schema()

    role = _current_role()
    if role not in {"admin", "manager"}:
        st.header("Document Centre")
        st.warning(
            "The central document library is limited to managers and administrators. "
            "Employees can continue using their assigned JobHub field workflows for site photos, forms and SWMS."
        )
        return

    st.header("Document Centre")
    st.caption(
        "One upload point for employee, builder/client, job-pack, job-specific and company documents. "
        "Job-scoped files remain linked to the existing Job Folder and PlanReader document bridge."
    )

    st.subheader("Upload documents")
    category = st.selectbox(
        "Document category",
        list(CATEGORY_TYPES),
        key="document_centre_category",
    )
    document_type = st.selectbox(
        "Document type",
        CATEGORY_TYPES[category],
        key="document_centre_type",
    )
    entity_type, entity_id, entity_label, job_id, entity_record = _entity_choice(st, category)

    c1, c2 = st.columns(2)
    revision = c1.text_input(
        "Revision / version",
        placeholder="e.g. Rev C, V2, Issued for Construction",
        key="document_centre_revision",
    ).strip()
    document_date_value = c2.date_input(
        "Document date",
        value=jobhub_today(),
        key="document_centre_document_date",
    )
    notes = st.text_area(
        "Notes",
        placeholder="Optional description, package details, drawing set, supplier, reason for issue, etc.",
        key="document_centre_notes",
    ).strip()
    uploads = st.file_uploader(
        "Files",
        type=UPLOAD_TYPES,
        accept_multiple_files=True,
        key="document_centre_files",
        help="Upload one or several files under the same classification. Use ZIP for unsupported grouped source formats.",
    )

    can_upload = bool(uploads) and (entity_id is not None or category == "Company / General")
    if st.button(
        f"Upload {len(uploads) if uploads else 0} document(s)",
        type="primary",
        disabled=not can_upload,
        key="document_centre_upload_button",
    ):
        saved: list[str] = []
        errors: list[str] = []
        for upload in uploads or []:
            try:
                _store_one(
                    upload=upload,
                    category=category,
                    document_type=document_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    entity_label=entity_label,
                    job_id=job_id,
                    job_record=entity_record if job_id is not None else None,
                    revision=revision,
                    document_date=str(document_date_value),
                    notes=notes,
                )
                saved.append(str(getattr(upload, "name", "document")))
            except Exception as exc:
                errors.append(f"{getattr(upload, 'name', 'document')}: {exc}")
        if saved:
            st.success(f"Uploaded {len(saved)} document(s) to {category} → {document_type}.")
        for message in errors:
            st.error(message)
        if saved and not errors:
            rerun = _app_attr("pb_rerun") or _app_attr("refresh") or getattr(st, "rerun", None)
            if callable(rerun):
                rerun()

    st.divider()
    _render_register(st)


def _labels(options: Any) -> list[str]:
    try:
        return [str(item) for item in list(options)]
    except Exception:
        return []


def _replace_legacy_label(options: Any) -> tuple[Any, bool]:
    labels = _labels(options)
    changed = False
    values = list(options) if options is not None else []
    for index, label in enumerate(labels):
        if label in {"PDF Import", "PDF Import Centre"}:
            values[index] = DOCUMENT_CENTRE_LABEL
            changed = True
    return values if changed else options, changed


def _patch_choice(owner: Any, method_name: str, st: Any) -> bool:
    original = getattr(owner, method_name, None)
    marker = f"{PATCH_MARKER}_{method_name}"
    if original is None or getattr(original, marker, False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        arg_list = list(args)
        options_index = None
        if len(arg_list) >= 2 and isinstance(arg_list[0], str):
            options_index = 1
        elif len(arg_list) >= 3:
            options_index = 2
        options = arg_list[options_index] if options_index is not None else kwargs.get("options")
        replaced, changed = _replace_legacy_label(options)
        if changed:
            if options_index is not None:
                arg_list[options_index] = replaced
            else:
                kwargs["options"] = replaced
        result = original(*tuple(arg_list), **kwargs)
        if str(result) == DOCUMENT_CENTRE_LABEL:
            render_document_centre_page()
            st.stop()
        return result

    setattr(wrapper, marker, True)
    setattr(wrapper, "_pb_original_choice", original)
    setattr(owner, method_name, wrapper)
    return True


def install_document_centre_guard() -> bool:
    """Replace the legacy PDF-import navigation label with Document Centre.

    The old page remains available in source for compatibility, but selecting its
    navigation slot now renders the unified library before legacy dispatch runs.
    """
    st = _st()
    if st is None:
        return False
    installed = False
    for method_name in ("radio", "selectbox"):
        installed = _patch_choice(st, method_name, st) or installed
    try:
        delta_module = sys.modules.get("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        for method_name in ("radio", "selectbox"):
            installed = _patch_choice(delta_cls, method_name, st) or installed
    return installed
