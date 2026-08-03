"""Split one uploaded PO into Internal and External purchase-order lines."""

from __future__ import annotations

import importlib
import sys
from typing import Any


SPLIT_TOGGLE_KEY = "po_upload_split_internal_external"
SPLIT_BY_AMOUNTS = "Split by amounts"
SPLIT_BY_PERCENTAGES = "Split by percentages"


def _st() -> Any:
    return sys.modules.get("streamlit")


def _po_module() -> Any:
    return sys.modules.get("jobhub.po_upload_guard") or importlib.import_module("jobhub.po_upload_guard")


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _amount_percent(amount: float, base: float) -> float:
    return round((float(amount or 0) / float(base or 0) * 100.0), 4) if _safe_float(base) else 0.0


def _app_user(po: Any) -> str:
    try:
        user = (po._app_attr("get_current_user", lambda: {})() or {})
        return str(user.get("username") or user.get("name") or "JobHub user")
    except Exception:
        return "JobHub user"


def _record_document_once(po: Any, job_id: int, po_number: str, file_name: str, file_path: str, uploaded_by: str, notes: str) -> None:
    now = po._now() if hasattr(po, "_now") else ""
    values = {
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
        "notes": notes or f"Split PO {po_number}".strip(),
        "description": notes or f"Split PO {po_number}".strip(),
        "mime_type": "application/pdf",
    }
    po._insert_existing_columns("job_documents", values)


def _record_po_line(
    po: Any,
    *,
    job_id: int,
    stage_id: int | None,
    po_number: str,
    amount: float,
    file_name: str,
    file_path: str,
    uploaded_by: str,
    scope_label: str,
    scope_base: float,
    percent_of_scope: float,
    percent_of_job: float,
    calculation_mode: str,
    notes: str,
) -> None:
    now = po._now() if hasattr(po, "_now") else ""
    values = {
        "job_id": int(job_id),
        "job_stage_id": stage_id,
        "stage_id": stage_id,
        "po_number": po_number.strip(),
        "po_value_ex_gst": float(amount or 0),
        "value_ex_gst": float(amount or 0),
        "amount_ex_gst": float(amount or 0),
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
        "po_scope_base_ex_gst": float(scope_base or 0),
        "po_scope_percent": float(percent_of_scope or 0),
        "po_percent_of_job": float(percent_of_job or 0),
        "po_calculation_mode": calculation_mode,
    }
    po._insert_existing_columns("job_purchase_orders", values)


def _stage_id_for_label(stages: dict[str, int | None], label: str) -> int | None:
    label_lower = label.casefold()
    for name, stage_id in stages.items():
        if label_lower in str(name or "").casefold():
            return stage_id
    return None


def _render_split_upload_page(po: Any, st: Any) -> None:
    po._ensure_schema()
    st.header("Upload PO")
    st.caption("Upload one PO file and split it into separate Internal and External PO lines.")

    jobs = po._job_options()
    if not jobs:
        st.info("Create a job first, then upload the PO against that job.")
        return

    selected_job = st.selectbox("Job", list(jobs), key="po_split_job")
    job_id = jobs[selected_job]
    job_value = po._job_value(job_id)
    stages = po._stage_options(job_id)

    st.info(
        "Use this when the builder gives you one PO but you want JobHub to track part of it as Internal "
        "and part of it as External. The same PO file is saved once, then two PO lines are created."
    )

    with st.expander("Stage / area options guide", expanded=False):
        st.markdown(
            """
            **Use Internal** for walls, ceilings, doors, trims and other inside work.  
            **Use External** for outside walls, cladding, soffits, eaves, screens and external touch-ups.  
            The split tool creates both lines at once from the same uploaded PO.
            """
        )

    with st.form("po_split_upload_form", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        po_number = c1.text_input("PO Number", placeholder="e.g. PO-12345")
        total_po_amount = c2.number_input("Total PO value ex GST", min_value=0.0, step=100.0, value=0.0)
        split_method = c3.radio("Split method", [SPLIT_BY_AMOUNTS, SPLIT_BY_PERCENTAGES], horizontal=False)

        st.markdown("#### Internal / External split")
        s1, s2 = st.columns(2)
        internal_scope_value = s1.number_input(
            "Internal contract/scope value ex GST",
            min_value=0.0,
            step=100.0,
            value=0.0,
            help="The total internal value for this job or area. Used to calculate % of Internal.",
        )
        external_scope_value = s2.number_input(
            "External contract/scope value ex GST",
            min_value=0.0,
            step=100.0,
            value=0.0,
            help="The total external value for this job or area. Used to calculate % of External.",
        )

        if split_method == SPLIT_BY_PERCENTAGES:
            p1, p2 = st.columns(2)
            internal_percent_of_po = p1.number_input("Internal % of this PO", min_value=0.0, max_value=100.0, step=1.0, value=0.0)
            external_percent_of_po = p2.number_input("External % of this PO", min_value=0.0, max_value=100.0, step=1.0, value=0.0)
            internal_amount = round(total_po_amount * internal_percent_of_po / 100.0, 2) if total_po_amount else 0.0
            external_amount = round(total_po_amount * external_percent_of_po / 100.0, 2) if total_po_amount else 0.0
        else:
            a1, a2 = st.columns(2)
            internal_amount = a1.number_input("Internal PO amount ex GST", min_value=0.0, step=100.0, value=0.0)
            external_amount = a2.number_input("External PO amount ex GST", min_value=0.0, step=100.0, value=0.0)
            internal_percent_of_po = _amount_percent(internal_amount, total_po_amount)
            external_percent_of_po = _amount_percent(external_amount, total_po_amount)

        split_total = round(float(internal_amount or 0) + float(external_amount or 0), 2)
        difference = round(float(total_po_amount or 0) - split_total, 2)
        internal_scope_percent = _amount_percent(internal_amount, internal_scope_value)
        external_scope_percent = _amount_percent(external_amount, external_scope_value)
        internal_job_percent = _amount_percent(internal_amount, job_value)
        external_job_percent = _amount_percent(external_amount, job_value)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Internal amount", f"${internal_amount:,.2f}")
        m2.metric("External amount", f"${external_amount:,.2f}")
        m3.metric("Split total", f"${split_total:,.2f}")
        m4.metric("Difference to PO", f"${difference:,.2f}")

        st.dataframe(
            [
                {
                    "Line": "Internal",
                    "Amount ex GST": round(float(internal_amount or 0), 2),
                    "% of this PO": round(float(internal_percent_of_po or 0), 2),
                    "% of Internal scope": round(float(internal_scope_percent or 0), 2),
                    "% of whole job": round(float(internal_job_percent or 0), 2),
                },
                {
                    "Line": "External",
                    "Amount ex GST": round(float(external_amount or 0), 2),
                    "% of this PO": round(float(external_percent_of_po or 0), 2),
                    "% of External scope": round(float(external_scope_percent or 0), 2),
                    "% of whole job": round(float(external_job_percent or 0), 2),
                },
            ],
            width="stretch",
            hide_index=True,
        )

        uploaded = st.file_uploader(
            "PO file",
            type=["pdf", "png", "jpg", "jpeg", "doc", "docx", "xlsx", "csv"],
            key="po_split_upload_file",
        )
        notes = st.text_area("Notes", placeholder="Anything important about this split PO")
        submitted = st.form_submit_button("Upload split PO", type="primary")

    if submitted:
        if uploaded is None:
            po._error("Select the PO file first.")
            return
        if not str(po_number or "").strip():
            po._error("Enter the PO number first.")
            return
        if _safe_float(total_po_amount) <= 0:
            po._error("Enter the total PO value first.")
            return
        if internal_amount <= 0 and external_amount <= 0:
            po._error("Enter an internal amount, external amount, or split percentages greater than zero.")
            return
        if abs(difference) > 0.01:
            po._error("Internal + External must equal the total PO value before saving.")
            return
        try:
            uploaded_by = _app_user(po)
            file_name, file_path = po._save_uploaded_file(job_id, po_number, uploaded)
            _record_document_once(po, job_id, po_number, file_name, file_path, uploaded_by, notes.strip())
            internal_stage_id = _stage_id_for_label(stages, "internal")
            external_stage_id = _stage_id_for_label(stages, "external")
            if internal_amount > 0:
                _record_po_line(
                    po,
                    job_id=job_id,
                    stage_id=internal_stage_id,
                    po_number=po_number,
                    amount=internal_amount,
                    file_name=file_name,
                    file_path=file_path,
                    uploaded_by=uploaded_by,
                    scope_label="Internal",
                    scope_base=internal_scope_value,
                    percent_of_scope=internal_scope_percent,
                    percent_of_job=internal_job_percent,
                    calculation_mode="Split PO - Internal",
                    notes=(notes.strip() + "\n" if notes.strip() else "") + "Split PO line: Internal",
                )
            if external_amount > 0:
                _record_po_line(
                    po,
                    job_id=job_id,
                    stage_id=external_stage_id,
                    po_number=po_number,
                    amount=external_amount,
                    file_name=file_name,
                    file_path=file_path,
                    uploaded_by=uploaded_by,
                    scope_label="External",
                    scope_base=external_scope_value,
                    percent_of_scope=external_scope_percent,
                    percent_of_job=external_job_percent,
                    calculation_mode="Split PO - External",
                    notes=(notes.strip() + "\n" if notes.strip() else "") + "Split PO line: External",
                )
            po._success(
                f"PO {str(po_number).strip()} uploaded and split: "
                f"Internal ${internal_amount:,.2f}, External ${external_amount:,.2f}."
            )
            po._safe_rerun(st)
        except Exception as exc:
            po._error(f"Split PO upload failed: {exc}")

    recent = po._recent_pos(job_id)
    if recent is not None and not getattr(recent, "empty", True):
        st.markdown("### Recent POs for this job")
        st.dataframe(recent, width="stretch", hide_index=True)


def install_po_upload_split_guard() -> bool:
    st = _st()
    if st is None:
        return False
    po = _po_module()
    original = getattr(po, "render_po_upload_page", None)
    if original is None or getattr(original, "_pb_po_upload_split_guard", False):
        return False

    def render_po_upload_page_with_split() -> None:
        try:
            split_enabled = st.toggle(
                "Split one PO into Internal + External",
                value=bool(st.session_state.get(SPLIT_TOGGLE_KEY, False)),
                key=SPLIT_TOGGLE_KEY,
                help="Use this when one builder/client PO covers both internal and external works.",
            )
        except Exception:
            split_enabled = bool(st.session_state.get(SPLIT_TOGGLE_KEY, False))
        if split_enabled:
            return _render_split_upload_page(po, st)
        return original()

    render_po_upload_page_with_split._pb_po_upload_split_guard = True
    render_po_upload_page_with_split._pb_original_render_po_upload_page = original
    po.render_po_upload_page = render_po_upload_page_with_split
    return True
