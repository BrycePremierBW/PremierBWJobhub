"""Uploaded documents section for JobHub job folders."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


JOB_FOLDERS_LABELS = {"Job Folders", "Job Folder"}
DOCUMENTS_PANEL_KEY = "pb_job_folder_uploaded_documents_rendered"


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


def _df_query(sql: str, params: tuple[Any, ...] = ()) -> Any:
    df_query = _app_attr("df_query") or _app_attr("safe_df_query")
    if not callable(df_query):
        raise RuntimeError("JobHub database query helper is not available.")
    return df_query(sql, params)


def _table_columns(table: str) -> set[str]:
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


def _select_existing(existing: set[str], candidates: tuple[str, ...], alias: str, default: str = "''") -> str:
    for candidate in candidates:
        if candidate in existing:
            return f'{candidate} AS "{alias}"'
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


def _documents_for_job(job_id: int) -> Any:
    existing = _table_columns("job_documents")
    if not existing or "job_id" not in existing:
        return None
    select_sql = ",".join(
        [
            _select_existing(existing, ("document_type", "doc_type", "type"), "Document Type"),
            _select_existing(existing, ("file_name", "filename", "document_name", "name"), "File"),
            _select_existing(existing, ("file_path", "path"), "Path"),
            _select_existing(existing, ("created_at", "uploaded_at", "upload_date", "date_uploaded"), "Uploaded"),
            _select_existing(existing, ("uploaded_by", "created_by"), "Uploaded By"),
            _select_existing(existing, ("notes", "description"), "Notes"),
        ]
    )
    order_col = "id" if "id" in existing else "job_id"
    try:
        return _df_query(
            f"""
            SELECT {select_sql}
            FROM job_documents
            WHERE job_id=?
            ORDER BY {order_col} DESC
            """,
            (int(job_id),),
        )
    except Exception:
        return None


def _purchase_orders_for_job(job_id: int) -> Any:
    existing = _table_columns("job_purchase_orders")
    if not existing or "job_id" not in existing:
        return None
    select_sql = ",".join(
        [
            _select_existing(existing, ("po_number",), "PO Number"),
            _select_existing(existing, ("po_value_ex_gst", "value_ex_gst", "amount_ex_gst"), "Value ex GST", "0"),
            _select_existing(existing, ("po_scope_label",), "Scope"),
            _select_existing(existing, ("po_scope_percent",), "% of Scope", "0"),
            _select_existing(existing, ("po_percent_of_job",), "% of Job", "0"),
            _select_existing(existing, ("file_name", "filename"), "File"),
            _select_existing(existing, ("file_path", "path"), "Path"),
            _select_existing(existing, ("uploaded_at", "created_at", "received_date"), "Uploaded"),
            _select_existing(existing, ("notes",), "Notes"),
        ]
    )
    order_col = "id" if "id" in existing else "job_id"
    try:
        return _df_query(
            f"""
            SELECT {select_sql}
            FROM job_purchase_orders
            WHERE job_id=?
            ORDER BY {order_col} DESC
            """,
            (int(job_id),),
        )
    except Exception:
        return None


def _render_download_buttons(st: Any, df: Any, prefix: str) -> None:
    if df is None or getattr(df, "empty", True) or "Path" not in getattr(df, "columns", []):
        return
    shown = 0
    for index, row in df.iterrows():
        path_text = str(row.get("Path") or "")
        if not path_text:
            continue
        path = Path(path_text)
        if not path.exists() or not path.is_file():
            continue
        file_name = str(row.get("File") or path.name)
        try:
            st.download_button(
                f"Download {file_name}",
                data=path.read_bytes(),
                file_name=file_name,
                key=f"{prefix}_download_{index}_{path.name}",
            )
            shown += 1
            if shown >= 6:
                break
        except Exception:
            continue


def render_uploaded_documents_panel() -> None:
    st = _st()
    if st is None:
        return
    try:
        if bool(st.session_state.get(DOCUMENTS_PANEL_KEY, False)):
            return
        st.session_state[DOCUMENTS_PANEL_KEY] = True
    except Exception:
        pass

    jobs = _job_options()
    if not jobs:
        return

    with st.expander("Uploaded Documents", expanded=False):
        st.caption("View uploaded POs, SWMS, forms and other job documents saved against each job folder.")
        selected_job = st.selectbox(
            "Job folder documents",
            list(jobs),
            key="job_folder_uploaded_documents_job",
        )
        job_id = jobs[selected_job]
        docs = _documents_for_job(job_id)
        pos = _purchase_orders_for_job(job_id)

        if docs is None or getattr(docs, "empty", True):
            st.info("No uploaded documents found for this job yet.")
        else:
            st.markdown("#### Uploaded documents")
            display_docs = docs.drop(columns=["Path"], errors="ignore")
            st.dataframe(display_docs, width="stretch", hide_index=True)
            _render_download_buttons(st, docs, "job_docs")

        if pos is not None and not getattr(pos, "empty", True):
            st.markdown("#### Purchase orders")
            display_pos = pos.drop(columns=["Path"], errors="ignore")
            st.dataframe(display_pos, width="stretch", hide_index=True)
            _render_download_buttons(st, pos, "job_pos")


def _patch_header(owner: Any) -> bool:
    original = getattr(owner, "header", None)
    if original is None or getattr(original, "_pb_job_folder_documents_guard", False):
        return False

    def header_with_documents(body: Any, *args: Any, **kwargs: Any):
        result = original(body, *args, **kwargs)
        try:
            if str(body or "").strip() in JOB_FOLDERS_LABELS:
                render_uploaded_documents_panel()
        except Exception:
            pass
        return result

    header_with_documents._pb_job_folder_documents_guard = True
    header_with_documents._pb_original_header = original
    setattr(owner, "header", header_with_documents)
    return True


def install_job_folder_uploaded_documents_guard() -> bool:
    st = _st()
    if st is None:
        return False
    installed = _patch_header(st)
    return installed
