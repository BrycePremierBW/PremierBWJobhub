"""Replace the layered PO upload screen with one lightweight native page.

The previous route was assembled from several global Streamlit monkey-patches.
Those patches wrapped select boxes, text fields, number fields and the renderer
itself, which made the page difficult to reason about and could leave the browser
spinning even after database work was removed.

This module keeps the existing menu injection, storage helpers and database
schema, but replaces the page renderer with one self-contained implementation:

* no schema DDL while opening or interacting with the page;
* no global widget interception;
* one database transaction per upload;
* one file copy per upload;
* split POs use stable ``-INT``/``-EXT`` line numbers, avoiding constraint DDL;
* recent POs are loaded only when the user asks to see them;
* the deployed Render commit is displayed so production rollout is verifiable.
"""

from __future__ import annotations

from datetime import datetime
import importlib
import mimetypes
import os
from pathlib import Path
import sys
import time
from typing import Any


PATCH_MARKER = "_pb_native_po_upload_page"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
JOB_CACHE_KEY = "_pb_native_po_jobs_cache"
STAGE_CACHE_PREFIX = "_pb_native_po_stages_"
RECENT_VISIBLE_KEY = "_pb_native_po_recent_visible"
CACHE_SECONDS = 60.0


def _po_module() -> Any:
    return sys.modules.get("jobhub.po_upload_guard") or importlib.import_module(
        "jobhub.po_upload_guard"
    )


def _st() -> Any:
    return sys.modules.get("streamlit")


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _uploaded_size(uploaded: Any) -> int:
    try:
        size = getattr(uploaded, "size", None)
        if size is not None:
            return max(0, int(size))
    except Exception:
        pass
    getbuffer = getattr(uploaded, "getbuffer", None)
    if callable(getbuffer):
        return len(getbuffer())
    getvalue = getattr(uploaded, "getvalue", None)
    if callable(getvalue):
        return len(getvalue())
    return 0


def _cache_get(st: Any, key: str) -> Any:
    try:
        item = st.session_state.get(key)
        if not isinstance(item, dict):
            return None
        if time.monotonic() - float(item.get("at", 0.0)) > CACHE_SECONDS:
            return None
        return item.get("value")
    except Exception:
        return None


def _cache_set(st: Any, key: str, value: Any) -> None:
    try:
        st.session_state[key] = {"at": time.monotonic(), "value": value}
    except Exception:
        pass


def _job_options(po: Any, st: Any) -> dict[str, int]:
    cached = _cache_get(st, JOB_CACHE_KEY)
    if isinstance(cached, dict) and cached:
        return dict(cached)
    options = dict(po._job_options() or {})
    if options:
        _cache_set(st, JOB_CACHE_KEY, options)
    return options


def _stage_options(po: Any, st: Any, job_id: int) -> dict[str, int | None]:
    key = f"{STAGE_CACHE_PREFIX}{int(job_id)}"
    cached = _cache_get(st, key)
    if isinstance(cached, dict) and cached:
        return dict(cached)

    raw = dict(po._stage_options(int(job_id)) or {})
    result: dict[str, int | None] = {"Whole job": None}

    def matching_stage(word: str) -> int | None:
        for label, stage_id in raw.items():
            if word.casefold() in str(label or "").casefold():
                return stage_id
        return None

    result["Internal"] = matching_stage("internal")
    result["External"] = matching_stage("external")
    for label, stage_id in raw.items():
        clean = str(label or "").strip()
        if not clean or clean == "Whole job / not stage-specific":
            continue
        if clean not in result:
            result[clean] = stage_id
    _cache_set(st, key, result)
    return result


def _storage_ready(po: Any) -> tuple[bool, str]:
    try:
        documents = set(po._table_columns("job_documents") or ())
        purchase_orders = set(po._table_columns("job_purchase_orders") or ())
    except Exception as exc:
        return False, str(exc)

    document_ready = (
        "job_id" in documents
        and bool({"file_name", "filename", "name"}.intersection(documents))
        and bool({"file_path", "path"}.intersection(documents))
    )
    po_ready = (
        "job_id" in purchase_orders
        and "po_number" in purchase_orders
        and bool(
            {"po_value_ex_gst", "value_ex_gst", "amount_ex_gst"}.intersection(
                purchase_orders
            )
        )
    )
    if document_ready and po_ready:
        return True, ""
    missing = []
    if not document_ready:
        missing.append("job_documents")
    if not po_ready:
        missing.append("job_purchase_orders")
    return False, ", ".join(missing)


def _insert_existing(
    cursor: Any,
    table: str,
    existing: set[str],
    values: dict[str, Any],
) -> None:
    columns = [column for column in values if column in existing]
    if not columns:
        raise RuntimeError(f"No compatible columns are available in {table}.")
    placeholders = ",".join("?" for _ in columns)
    cursor.execute(
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
        tuple(values[column] for column in columns),
    )


def _po_exists(po: Any, job_id: int, po_number: str) -> bool:
    frame = po._df_query(
        """
        SELECT COUNT(*) AS count
        FROM job_purchase_orders
        WHERE job_id=? AND LOWER(TRIM(po_number))=LOWER(TRIM(?))
        """,
        (int(job_id), str(po_number).strip()),
    )
    if frame is None or getattr(frame, "empty", True):
        return False
    return int(frame.iloc[0].get("count", 0) or 0) > 0


def _line_number(base: str, suffix: str) -> str:
    clean = str(base or "").strip()
    suffix = suffix.strip("-").upper()
    if clean.upper().endswith(f"-{suffix}"):
        return clean
    return f"{clean}-{suffix}"


def _document_values(
    *,
    job_id: int,
    po_number: str,
    file_name: str,
    file_path: str,
    uploaded_by: str,
    notes: str,
    mime_type: str,
    now: str,
) -> dict[str, Any]:
    description = notes or f"Purchase order {po_number}"
    return {
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
        "notes": description,
        "description": description,
        "mime_type": mime_type,
    }


def _po_values(
    *,
    job_id: int,
    stage_id: int | None,
    po_number: str,
    original_po_number: str,
    amount: float,
    scope_label: str,
    scope_base: float,
    job_value: float,
    file_name: str,
    file_path: str,
    uploaded_by: str,
    notes: str,
    mode: str,
    now: str,
) -> dict[str, Any]:
    scope_percent = amount / scope_base * 100.0 if scope_base else 0.0
    job_percent = amount / job_value * 100.0 if job_value else 0.0
    combined_notes = notes.strip()
    if po_number != original_po_number:
        split_note = f"Original builder PO: {original_po_number}"
        combined_notes = f"{combined_notes}\n{split_note}".strip()
    description = scope_label or combined_notes or f"PO {original_po_number}"
    return {
        "job_id": int(job_id),
        "job_stage_id": stage_id,
        "stage_id": stage_id,
        "po_number": po_number,
        "description": description,
        "po_value_ex_gst": round(float(amount), 2),
        "value_ex_gst": round(float(amount), 2),
        "amount_ex_gst": round(float(amount), 2),
        "file_name": file_name,
        "filename": file_name,
        "file_path": file_path,
        "path": file_path,
        "status": "Uploaded",
        "received_date": now[:10],
        "uploaded_at": now,
        "created_at": now,
        "updated_at": now,
        "uploaded_by": uploaded_by,
        "created_by": uploaded_by,
        "notes": combined_notes,
        "po_scope_label": scope_label,
        "po_scope_base_ex_gst": round(float(scope_base), 2),
        "po_scope_percent": round(scope_percent, 4),
        "po_percent_of_job": round(job_percent, 4),
        "po_calculation_mode": mode,
    }


def _save_transaction(
    po: Any,
    *,
    document: dict[str, Any],
    lines: list[dict[str, Any]],
) -> None:
    connect = po._app_attr("connect")
    if not callable(connect):
        raise RuntimeError("JobHub database connection is not available.")
    document_columns = set(po._table_columns("job_documents") or ())
    po_columns = set(po._table_columns("job_purchase_orders") or ())
    connection = connect()
    try:
        cursor = connection.cursor()
        _insert_existing(cursor, "job_documents", document_columns, document)
        for line in lines:
            _insert_existing(cursor, "job_purchase_orders", po_columns, line)
        connection.commit()
    except Exception:
        try:
            connection.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            connection.close()
        except Exception:
            pass


def _current_user(po: Any) -> str:
    try:
        user = (po._app_attr("get_current_user", lambda: {})() or {})
        return str(user.get("username") or user.get("name") or "JobHub user")
    except Exception:
        return "JobHub user"


def _return_to_dashboard(po: Any, st: Any) -> None:
    try:
        st.session_state[po.PO_UPLOAD_STATE_KEY] = False
        st.session_state[RECENT_VISIBLE_KEY] = False
        st.session_state["main_menu"] = "Dashboard"
    except Exception:
        pass
    po._safe_rerun(st)


def _render_recent(po: Any, st: Any, job_id: int) -> None:
    if st.button(
        "Show recent POs for this job",
        key=f"native_po_show_recent_{int(job_id)}",
        width="stretch",
    ):
        st.session_state[RECENT_VISIBLE_KEY] = True
    if not bool(st.session_state.get(RECENT_VISIBLE_KEY, False)):
        return
    recent = po._recent_pos(int(job_id))
    if recent is None or getattr(recent, "empty", True):
        st.info("No purchase orders have been saved for this job yet.")
        return
    st.markdown("### Recent POs")
    try:
        st.dataframe(
            recent,
            width="stretch",
            hide_index=True,
            on_select="ignore",
        )
    except TypeError:
        st.dataframe(recent, width="stretch", hide_index=True)


def render_native_po_upload_page() -> None:
    po = _po_module()
    st = _st()
    if st is None:
        return

    left, right = st.columns([3, 1])
    with left:
        st.header("Upload PO")
        st.caption(
            "Fast purchase-order upload with optional Internal / External split."
        )
    with right:
        if st.button(
            "← Dashboard",
            key="native_po_return_dashboard",
            width="stretch",
        ):
            _return_to_dashboard(po, st)
            return

    commit = str(os.getenv("RENDER_GIT_COMMIT", "") or "").strip()
    build = commit[:8] if commit else "local"
    st.caption(f"PO Upload native build · {build}")

    ready, detail = _storage_ready(po)
    if not ready:
        st.error(
            "PO storage is not ready. No schema changes were attempted from this page. "
            f"Missing or incompatible: {detail or 'unknown storage table'}."
        )
        return

    jobs = _job_options(po, st)
    if not jobs:
        st.info("Create a job first, then return here to upload its PO.")
        return

    selected_job = st.selectbox(
        "Job",
        list(jobs),
        key="native_po_job",
    )
    job_id = int(jobs[selected_job])
    job_value = max(0.0, _safe_float(po._job_value(job_id)))
    stages = _stage_options(po, st, job_id)

    split_enabled = st.checkbox(
        "Split this builder PO into Internal and External lines",
        key="native_po_split_enabled",
        help=(
            "The file is saved once. JobHub creates unique -INT and -EXT tracking "
            "lines so no database constraint change is needed."
        ),
    )

    if split_enabled:
        with st.form("native_po_split_form", clear_on_submit=False):
            c1, c2 = st.columns(2)
            po_number = c1.text_input("Builder PO number", placeholder="e.g. PO-12345")
            total_amount = c2.number_input(
                "Total PO value ex GST",
                min_value=0.0,
                step=100.0,
                value=0.0,
            )

            s1, s2 = st.columns(2)
            internal_amount = s1.number_input(
                "Internal PO amount ex GST",
                min_value=0.0,
                step=100.0,
                value=0.0,
            )
            external_amount = s2.number_input(
                "External PO amount ex GST",
                min_value=0.0,
                step=100.0,
                value=0.0,
            )

            b1, b2 = st.columns(2)
            internal_scope = b1.number_input(
                "Internal contract / scope value ex GST",
                min_value=0.0,
                step=100.0,
                value=0.0,
            )
            external_scope = b2.number_input(
                "External contract / scope value ex GST",
                min_value=0.0,
                step=100.0,
                value=0.0,
            )

            difference = round(total_amount - internal_amount - external_amount, 2)
            st.caption(
                f"Split check: Internal + External = ${internal_amount + external_amount:,.2f}; "
                f"difference to PO = ${difference:,.2f}."
            )
            uploaded = st.file_uploader(
                "PO file",
                type=["pdf", "png", "jpg", "jpeg", "doc", "docx", "xlsx", "csv"],
                key="native_po_split_file",
            )
            notes = st.text_area(
                "Notes",
                placeholder="Anything important about this PO",
                key="native_po_split_notes",
            )
            submitted = st.form_submit_button(
                "Upload split PO",
                type="primary",
                width="stretch",
            )

        if submitted:
            base_number = str(po_number or "").strip()
            errors = []
            if not base_number:
                errors.append("Enter the builder PO number.")
            if uploaded is None:
                errors.append("Select the PO file.")
            if total_amount <= 0:
                errors.append("Enter the total PO value.")
            if internal_amount <= 0 and external_amount <= 0:
                errors.append("Enter an Internal or External amount.")
            if abs(difference) > 0.01:
                errors.append("Internal plus External must equal the total PO value.")
            if internal_amount > 0 and internal_scope <= 0:
                errors.append("Enter the Internal contract / scope value.")
            if external_amount > 0 and external_scope <= 0:
                errors.append("Enter the External contract / scope value.")
            if uploaded is not None and _uploaded_size(uploaded) > MAX_UPLOAD_BYTES:
                errors.append("The PO file is larger than the 25 MB upload limit.")

            line_numbers = []
            if internal_amount > 0:
                line_numbers.append(_line_number(base_number, "INT"))
            if external_amount > 0:
                line_numbers.append(_line_number(base_number, "EXT"))
            for line_number in line_numbers:
                if base_number and _po_exists(po, job_id, line_number):
                    errors.append(f"{line_number} already exists for this job.")

            if errors:
                for message in errors:
                    po._error(message)
            else:
                file_path = ""
                try:
                    file_name, file_path = po._save_uploaded_file(
                        job_id, base_number, uploaded
                    )
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    uploaded_by = _current_user(po)
                    mime_type = str(
                        getattr(uploaded, "type", "")
                        or mimetypes.guess_type(file_name)[0]
                        or "application/octet-stream"
                    )
                    document = _document_values(
                        job_id=job_id,
                        po_number=base_number,
                        file_name=file_name,
                        file_path=file_path,
                        uploaded_by=uploaded_by,
                        notes=str(notes or "").strip(),
                        mime_type=mime_type,
                        now=now,
                    )
                    lines = []
                    if internal_amount > 0:
                        lines.append(
                            _po_values(
                                job_id=job_id,
                                stage_id=stages.get("Internal"),
                                po_number=_line_number(base_number, "INT"),
                                original_po_number=base_number,
                                amount=internal_amount,
                                scope_label="Internal",
                                scope_base=internal_scope,
                                job_value=job_value,
                                file_name=file_name,
                                file_path=file_path,
                                uploaded_by=uploaded_by,
                                notes=str(notes or ""),
                                mode="Native split - Internal",
                                now=now,
                            )
                        )
                    if external_amount > 0:
                        lines.append(
                            _po_values(
                                job_id=job_id,
                                stage_id=stages.get("External"),
                                po_number=_line_number(base_number, "EXT"),
                                original_po_number=base_number,
                                amount=external_amount,
                                scope_label="External",
                                scope_base=external_scope,
                                job_value=job_value,
                                file_name=file_name,
                                file_path=file_path,
                                uploaded_by=uploaded_by,
                                notes=str(notes or ""),
                                mode="Native split - External",
                                now=now,
                            )
                        )
                    _save_transaction(po, document=document, lines=lines)
                    po._success(
                        f"PO {base_number} uploaded: Internal ${internal_amount:,.2f}, "
                        f"External ${external_amount:,.2f}."
                    )
                    st.session_state[RECENT_VISIBLE_KEY] = True
                    po._safe_rerun(st)
                except Exception as exc:
                    if file_path:
                        try:
                            Path(file_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                    po._error(f"PO upload stopped safely: {exc}")
    else:
        with st.form("native_po_standard_form", clear_on_submit=False):
            c1, c2 = st.columns(2)
            po_number = c1.text_input("PO number", placeholder="e.g. PO-12345")
            selected_scope = c2.selectbox(
                "Stage / area",
                list(stages),
                key="native_po_scope",
            )
            scope_default = job_value if selected_scope == "Whole job" else 0.0
            c3, c4 = st.columns(2)
            amount = c3.number_input(
                "PO value ex GST",
                min_value=0.0,
                step=100.0,
                value=0.0,
            )
            scope_base = c4.number_input(
                "Selected scope value ex GST",
                min_value=0.0,
                step=100.0,
                value=float(scope_default),
                help="Used only to calculate the PO percentage for this scope.",
            )
            scope_percent = amount / scope_base * 100.0 if scope_base else 0.0
            job_percent = amount / job_value * 100.0 if job_value else 0.0
            st.caption(
                f"PO equals {scope_percent:.2f}% of {selected_scope} and "
                f"{job_percent:.2f}% of the whole job."
            )
            uploaded = st.file_uploader(
                "PO file",
                type=["pdf", "png", "jpg", "jpeg", "doc", "docx", "xlsx", "csv"],
                key="native_po_standard_file",
            )
            notes = st.text_area(
                "Notes",
                placeholder="Anything important about this PO",
                key="native_po_standard_notes",
            )
            submitted = st.form_submit_button(
                "Upload PO",
                type="primary",
                width="stretch",
            )

        if submitted:
            clean_number = str(po_number or "").strip()
            errors = []
            if not clean_number:
                errors.append("Enter the PO number.")
            if uploaded is None:
                errors.append("Select the PO file.")
            if amount <= 0:
                errors.append("Enter a PO value greater than zero.")
            if scope_base <= 0:
                errors.append("Enter the selected scope value.")
            if uploaded is not None and _uploaded_size(uploaded) > MAX_UPLOAD_BYTES:
                errors.append("The PO file is larger than the 25 MB upload limit.")
            if clean_number and _po_exists(po, job_id, clean_number):
                errors.append(f"PO {clean_number} already exists for this job.")

            if errors:
                for message in errors:
                    po._error(message)
            else:
                file_path = ""
                try:
                    file_name, file_path = po._save_uploaded_file(
                        job_id, clean_number, uploaded
                    )
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    uploaded_by = _current_user(po)
                    mime_type = str(
                        getattr(uploaded, "type", "")
                        or mimetypes.guess_type(file_name)[0]
                        or "application/octet-stream"
                    )
                    document = _document_values(
                        job_id=job_id,
                        po_number=clean_number,
                        file_name=file_name,
                        file_path=file_path,
                        uploaded_by=uploaded_by,
                        notes=str(notes or "").strip(),
                        mime_type=mime_type,
                        now=now,
                    )
                    line = _po_values(
                        job_id=job_id,
                        stage_id=stages[selected_scope],
                        po_number=clean_number,
                        original_po_number=clean_number,
                        amount=amount,
                        scope_label=selected_scope,
                        scope_base=scope_base,
                        job_value=job_value,
                        file_name=file_name,
                        file_path=file_path,
                        uploaded_by=uploaded_by,
                        notes=str(notes or ""),
                        mode="Native standard upload",
                        now=now,
                    )
                    _save_transaction(po, document=document, lines=[line])
                    po._success(
                        f"PO {clean_number} uploaded: ${amount:,.2f} ex GST, "
                        f"{scope_percent:.2f}% of {selected_scope}."
                    )
                    st.session_state[RECENT_VISIBLE_KEY] = True
                    po._safe_rerun(st)
                except Exception as exc:
                    if file_path:
                        try:
                            Path(file_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                    po._error(f"PO upload stopped safely: {exc}")

    _render_recent(po, st, job_id)


def install_po_upload_native_guard() -> bool:
    po = _po_module()
    current = getattr(po, "render_po_upload_page", None)
    if getattr(current, PATCH_MARKER, False):
        return False
    render_native_po_upload_page._pb_original_render_po_upload_page = current
    setattr(render_native_po_upload_page, PATCH_MARKER, True)
    po.render_po_upload_page = render_native_po_upload_page
    return True
