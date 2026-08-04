from __future__ import annotations

import mimetypes
import re
from datetime import date, datetime
from pathlib import Path

import streamlit as st

from .common import AppContext, _clean, _float, _int, job_options
from .ui import header, rerun_success


CALC_BY_AMOUNT = "Enter PO amount → calculate %"
CALC_BY_PERCENT = "Enter % → calculate PO amount"
BASIS_TOTAL_JOB = "Whole job value"
BASIS_MANUAL_SCOPE = "Manual area / stage value"


def _safe_file_name(value: str) -> str:
    name = Path(value or "purchase_order").name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip(" .")
    return name or "purchase_order"


def _calculate(mode: str, scope_value: float, job_value: float, amount: float, percent: float) -> dict[str, float]:
    scope_value = max(0.0, _float(scope_value))
    job_value = max(0.0, _float(job_value))
    if mode == CALC_BY_PERCENT:
        scope_percent = max(0.0, _float(percent))
        po_amount = scope_value * scope_percent / 100 if scope_value else 0.0
    else:
        po_amount = max(0.0, _float(amount))
        scope_percent = po_amount / scope_value * 100 if scope_value else 0.0
    return {
        "amount": round(po_amount, 2),
        "scope_percent": round(scope_percent, 4),
        "job_percent": round(po_amount / job_value * 100, 4) if job_value else 0.0,
    }


def _ensure_columns(ctx: AppContext) -> None:
    additions = {
        "job_stage_id": "INTEGER",
        "po_value_ex_gst": "REAL DEFAULT 0",
        "file_name": "TEXT",
        "file_path": "TEXT",
        "uploaded_at": "TEXT",
        "uploaded_by": "TEXT",
        "po_scope_label": "TEXT",
        "po_scope_base_ex_gst": "REAL DEFAULT 0",
        "po_scope_percent": "REAL DEFAULT 0",
        "po_percent_of_job": "REAL DEFAULT 0",
        "po_calculation_mode": "TEXT",
    }
    for column, definition in additions.items():
        ctx.db.ensure_column("job_purchase_orders", column, definition)
    for column, definition in {
        "uploaded_at": "TEXT", "uploaded_by": "TEXT", "mime_type": "TEXT",
    }.items():
        ctx.db.ensure_column("job_documents", column, definition)


def po_upload_page(ctx: AppContext) -> None:
    _ensure_columns(ctx)
    header("Upload PO", "Attach a builder/client purchase order to a job and calculate its scope percentage.")
    jobs = job_options(ctx)
    if not jobs:
        st.info("Create a job first.")
        return
    job_label = st.selectbox("Job", list(jobs), key="po_upload_job")
    job_id = jobs[job_label]
    job_row = ctx.db.query("SELECT job_no,COALESCE(contract_value,0) AS contract_value FROM jobs WHERE id=?", (job_id,))
    job_no = _clean(job_row.iloc[0].get("job_no")) if not job_row.empty else str(job_id)
    job_value = _float(job_row.iloc[0].get("contract_value")) if not job_row.empty else 0.0
    stage_frame = ctx.db.query("SELECT id,stage_name FROM job_stages WHERE job_id=? ORDER BY sequence_order,id", (job_id,))
    stage_map = {"Whole job / not stage-specific": None}
    if not stage_frame.empty:
        stage_map.update({str(row["stage_name"]): _int(row["id"]) for _, row in stage_frame.iterrows()})

    with st.form("po_upload_form"):
        c1, c2, c3 = st.columns(3)
        po_number = c1.text_input("PO number")
        stage_label = c2.selectbox("Stage / area", list(stage_map), key="po_upload_stage")
        basis = c3.selectbox("Calculate % from", [BASIS_TOTAL_JOB, BASIS_MANUAL_SCOPE])
        scope_label = st.text_input("Area / scope name", value="Whole job" if stage_label.startswith("Whole job") else stage_label)
        if basis == BASIS_MANUAL_SCOPE:
            scope_value = st.number_input("Area / stage value ex GST", min_value=0.0, step=100.0)
        else:
            scope_value = job_value
            st.metric("Whole job value ex GST", f"${job_value:,.2f}")
        mode = st.radio("PO calculation mode", [CALC_BY_AMOUNT, CALC_BY_PERCENT], horizontal=True)
        if mode == CALC_BY_AMOUNT:
            amount = st.number_input("PO value ex GST", min_value=0.0, step=100.0)
            percent = 0.0
        else:
            percent = st.number_input("% of selected scope", min_value=0.0, max_value=1000.0, step=1.0)
            amount = 0.0
        calculated = _calculate(mode, scope_value, job_value, amount, percent)
        m1, m2, m3 = st.columns(3)
        m1.metric("PO amount ex GST", f"${calculated['amount']:,.2f}")
        m2.metric("% of selected scope", f"{calculated['scope_percent']:.2f}%")
        m3.metric("% of whole job", f"{calculated['job_percent']:.2f}%")
        upload = st.file_uploader("PO file", type=["pdf", "png", "jpg", "jpeg", "doc", "docx", "xlsx", "csv"], key="po_upload_file")
        notes = st.text_area("Notes")
        submit = st.form_submit_button("Upload PO", type="primary")
    if submit:
        if upload is None:
            st.error("Select the PO file first.")
            return
        if not po_number.strip():
            st.error("Enter the PO number.")
            return
        if scope_value <= 0 or calculated["amount"] <= 0:
            st.error("Enter a valid job/scope value and PO amount or percentage.")
            return
        root = ctx.job_files_dir.resolve()
        folder = (root / re.sub(r"[^A-Za-z0-9._ -]", "_", job_no).strip(" .") / "Purchase Orders").resolve()
        if root not in folder.parents:
            raise ValueError("Unsafe PO storage path.")
        folder.mkdir(parents=True, exist_ok=True)
        original_name = _safe_file_name(str(upload.name))
        file_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{_safe_file_name(po_number)}_{original_name}"
        target = (folder / file_name).resolve()
        if folder not in target.parents:
            raise ValueError("Unsafe PO file name.")
        target.write_bytes(upload.getvalue())
        now = datetime.now().isoformat(timespec="seconds")
        mime = upload.type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
        ctx.db.execute(
            """
            INSERT INTO job_documents
            (job_id,document_type,file_name,file_path,created_at,uploaded_at,uploaded_by,notes,mime_type)
            VALUES (?,'Purchase Order',?,?,?,?,?,?,?)
            """,
            (job_id, file_name, str(target), now, now, ctx.user.get("username", ""), notes.strip() or f"PO {po_number.strip()}", mime),
        )
        po_id = ctx.db.insert_id(
            """
            INSERT INTO job_purchase_orders
            (job_id,job_stage_id,po_number,description,amount_ex_gst,po_value_ex_gst,file_name,file_path,
             status,received_date,uploaded_at,uploaded_by,notes,po_scope_label,po_scope_base_ex_gst,
             po_scope_percent,po_percent_of_job,po_calculation_mode,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id, stage_map[stage_label], po_number.strip(), scope_label.strip(), calculated["amount"],
                calculated["amount"], file_name, str(target), "Uploaded", date.today().isoformat(), now,
                ctx.user.get("username", ""), notes.strip(), scope_label.strip(), scope_value,
                calculated["scope_percent"], calculated["job_percent"], mode, now, now,
            ),
        )
        ctx.audit("upload", "job_purchase_orders", po_id, po_number.strip())
        rerun_success(
            f"PO {po_number.strip()} uploaded: ${calculated['amount']:,.2f} ex GST, "
            f"{calculated['scope_percent']:.2f}% of {scope_label.strip() or stage_label}."
        )

    recent = ctx.db.query(
        """
        SELECT po_number AS "PO Number",COALESCE(amount_ex_gst,po_value_ex_gst,0) AS "Value Ex GST",
               COALESCE(po_scope_label,'') AS "Scope",COALESCE(po_scope_percent,0) AS "% of Scope",
               COALESCE(po_percent_of_job,0) AS "% of Job",COALESCE(status,'') AS "Status",
               COALESCE(received_date,'') AS "Received",COALESCE(file_name,'') AS "File"
        FROM job_purchase_orders WHERE job_id=? ORDER BY id DESC LIMIT 30
        """,
        (job_id,),
    )
    if not recent.empty:
        st.subheader("Recent POs for this job")
        st.dataframe(recent, hide_index=True, use_container_width=True)
