from __future__ import annotations

import io
import mimetypes
import re
import zipfile
from datetime import date, datetime
from pathlib import Path

import streamlit as st

from .auth import can_manage
from .common import AppContext, _clean, _int, job_options
from .ui import header, rerun_success, selected_row


def _safe_job_segment(value: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9._ -]", "_", _clean(value)).strip(" .")
    if not segment or segment in {".", ".."}:
        raise ValueError("Invalid job number.")
    return segment[:100]


def job_files_page(ctx: AppContext) -> None:
    header("Job Files", "Simple persistent file storage linked to each job.")
    jobs = job_options(ctx, include_archived=True)
    if not jobs:
        st.info("Add a job first.")
        return
    job_label = st.selectbox("Job", list(jobs), key="job_files_job")
    job_id = jobs[job_label]
    job_row = ctx.db.query("SELECT job_no FROM jobs WHERE id=?", (job_id,))
    job_no = _clean(job_row.iloc[0].get("job_no")) if not job_row.empty else str(job_id)
    root = ctx.job_files_dir.resolve()
    folder = (root / _safe_job_segment(job_no)).resolve()
    if root not in folder.parents:
        raise ValueError("Unsafe job folder path.")
    folder.mkdir(parents=True, exist_ok=True)

    with st.expander("Upload document", expanded=False):
        upload = st.file_uploader("Document", key=f"job_document_upload_{job_id}")
        document_type = st.text_input("Document type", value="General", key=f"job_document_type_{job_id}")
        notes = st.text_area("Notes", key=f"job_document_notes_{job_id}")
        if st.button("Save document", type="primary", disabled=upload is None, key=f"save_job_document_{job_id}"):
            name = Path(str(upload.name)).name
            target = (folder / name).resolve()
            if folder not in target.parents:
                st.error("Unsafe file name.")
            else:
                target.write_bytes(upload.getvalue())
                document_id = ctx.db.insert_id(
                    """
                    INSERT INTO job_documents(job_id,document_type,file_name,file_path,created_at,notes,mime_type)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (job_id, document_type.strip() or "General", name, str(target), datetime.now().isoformat(timespec="seconds"), notes.strip(), upload.type or mimetypes.guess_type(name)[0] or "application/octet-stream"),
                )
                ctx.audit("upload", "job_documents", document_id, name)
                rerun_success("Document uploaded.")

    frame = ctx.db.query(
        """
        SELECT id,COALESCE(document_type,'') AS "Type",file_name AS "File",
               COALESCE(created_at,'') AS "Uploaded",COALESCE(notes,'') AS "Notes",file_path,mime_type
        FROM job_documents WHERE job_id=? ORDER BY id DESC
        """,
        (job_id,),
    )
    row = selected_row(frame, key=f"job_documents_table_{job_id}", hide=("file_path", "mime_type"))
    if row:
        path = Path(_clean(row.get("file_path")))
        if path.exists() and (folder == path.resolve().parent or folder in path.resolve().parents):
            st.download_button(
                "Download selected document",
                data=path.read_bytes(),
                file_name=_clean(row.get("File")) or path.name,
                mime=_clean(row.get("mime_type")) or "application/octet-stream",
                key=f"download_job_document_{row.get('id')}",
            )
            if can_manage():
                confirm = st.checkbox("Delete selected document", key=f"delete_job_document_confirm_{row.get('id')}")
                if st.button("Delete document", disabled=not confirm, key=f"delete_job_document_{row.get('id')}"):
                    try:
                        path.unlink(missing_ok=True)
                    finally:
                        ctx.db.execute("DELETE FROM job_documents WHERE id=? AND job_id=?", (_int(row.get("id")), job_id))
                    ctx.audit("delete", "job_documents", _int(row.get("id")), path.name)
                    rerun_success("Document deleted.")


def reports_page(ctx: AppContext) -> None:
    header("Reports & Export", "Current operational data exported without loading every page.")
    tabs = st.tabs(["Job summary", "Timesheets", "Materials", "Full CSV pack"])
    with tabs[0]:
        jobs = ctx.db.query(
            """
            SELECT j.job_no AS "Job No",j.job_name AS "Job Name",COALESCE(b.name,'') AS "Builder",
                   COALESCE(j.status,'') AS "Status",COALESCE(j.leading_hand,'') AS "Leading Hand",
                   j.start_date AS "Start",j.end_date AS "Finish",COALESCE(j.contract_value,0) AS "Contract Ex GST",
                   COALESCE((SELECT SUM(t.total_hours) FROM timesheet_entries t WHERE t.job_id=j.id),0) AS "Timesheet Hours",
                   COALESCE((SELECT SUM(m.qty_required*COALESCE(p.price_ex_gst,m.custom_unit_price,0)) FROM material_entries m LEFT JOIN products p ON p.id=m.product_id WHERE m.job_id=j.id),0) AS "Material Allowance"
            FROM jobs j LEFT JOIN builders_clients b ON b.id=j.builder_client_id
            ORDER BY j.job_no
            """
        )
        st.dataframe(jobs, hide_index=True, use_container_width=True)
        st.download_button("Download job summary CSV", jobs.to_csv(index=False).encode(), "job_summary.csv", "text/csv")
    with tabs[1]:
        frame = ctx.db.query(
            """
            SELECT t.work_date AS "Date",COALESCE(e.name,'') AS "Employee",COALESCE(j.job_no,'') AS "Job",
                   COALESCE(j.job_name,'') AS "Job Name",COALESCE(t.work_type,'') AS "Area / Stage",
                   COALESCE(t.total_hours,0) AS "Hours",COALESCE(t.status,'') AS "Status",COALESCE(t.notes,'') AS "Notes"
            FROM timesheet_entries t LEFT JOIN employees e ON e.id=t.employee_id LEFT JOIN jobs j ON j.id=t.job_id
            ORDER BY t.work_date DESC,t.id DESC
            """
        )
        st.dataframe(frame, hide_index=True, use_container_width=True)
        st.download_button("Download timesheets CSV", frame.to_csv(index=False).encode(), "timesheets.csv", "text/csv")
    with tabs[2]:
        frame = ctx.db.query(
            """
            SELECT COALESCE(j.job_no,'') AS "Job",COALESCE(p.product_code,m.custom_product_code,'') AS "Code",
                   COALESCE(p.product_name,m.custom_product_name,'') AS "Product",COALESCE(NULLIF(m.custom_supplier,''),NULLIF(m.supplier,''),p.supplier,'') AS "Supplier",
                   COALESCE(m.qty_required,0) AS "Required",COALESCE(m.qty_received,0) AS "Received",
                   COALESCE(p.price_ex_gst,m.custom_unit_price,0) AS "Unit Price Ex GST",
                   COALESCE(m.qty_required,0)*COALESCE(p.price_ex_gst,m.custom_unit_price,0) AS "Required Value Ex GST"
            FROM material_entries m LEFT JOIN jobs j ON j.id=m.job_id LEFT JOIN products p ON p.id=m.product_id
            ORDER BY j.job_no,m.id
            """
        )
        st.dataframe(frame, hide_index=True, use_container_width=True)
        st.download_button("Download materials CSV", frame.to_csv(index=False).encode(), "materials.csv", "text/csv")
    with tabs[3]:
        tables = [
            "builders_clients", "employees", "jobs", "products", "job_purchase_orders", "job_stages",
            "timesheet_entries", "wage_entries", "material_entries", "equipment_checklist_items",
            "equipment_checklist_records", "job_documents", "estimate_working_sheets", "estimate_line_items",
        ]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for table in tables:
                if ctx.db.table_exists(table):
                    archive.writestr(f"{table}.csv", ctx.db.query(f"SELECT * FROM {table}").to_csv(index=False))
        st.download_button("Download full CSV pack", buffer.getvalue(), f"jobhub_export_{date.today().isoformat()}.zip", "application/zip")
