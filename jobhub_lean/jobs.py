from __future__ import annotations

from datetime import date, datetime
from typing import Any

import streamlit as st

from .auth import can_manage
from .common import (
    AppContext, _clean, _date_value, _float, _int, _option_map,
    builder_options, employee_options,
)
from .ui import header, rerun_success, selected_row


def jobs_page(ctx: AppContext) -> None:
    header("Jobs", "Contracts, purchase orders, stages and linked job information.")
    show_archived = st.checkbox("Show archived jobs", value=False)
    search = st.text_input("Search jobs", placeholder="Job number, name, builder or address").strip().lower()
    where = []
    params: list[Any] = []
    if not show_archived:
        where.append("LOWER(COALESCE(j.status,'')) <> 'archived'")
    if search:
        where.append("(LOWER(COALESCE(j.job_no,'')) LIKE ? OR LOWER(COALESCE(j.job_name,'')) LIKE ? OR LOWER(COALESCE(b.name,'')) LIKE ? OR LOWER(COALESCE(j.site_address,'')) LIKE ?)")
        params.extend([f"%{search}%"] * 4)
    clause = "WHERE " + " AND ".join(where) if where else ""
    frame = ctx.db.query(
        f"""
        SELECT j.id,j.job_no AS "Job No",j.job_name AS "Job Name",COALESCE(b.name,'') AS "Builder",
               COALESCE(j.site_address,'') AS "Address",COALESCE(j.status,'') AS "Status",
               COALESCE(j.leading_hand,'') AS "Leading Hand",j.start_date AS "Start",j.end_date AS "Finish",
               COALESCE(j.contract_value,0) AS "Contract Ex GST"
        FROM jobs j LEFT JOIN builders_clients b ON b.id=j.builder_client_id
        {clause}
        ORDER BY CASE LOWER(COALESCE(j.status,'')) WHEN 'active' THEN 0 WHEN 'booked' THEN 1 ELSE 2 END,j.job_no
        """,
        params,
    )
    row = selected_row(
        frame,
        key="jobs_table",
        column_config={"Contract Ex GST": st.column_config.NumberColumn(format="$%.2f")},
    )
    if row:
        st.session_state["lean_selected_job_id"] = _int(row.get("id"))
    selected_id = _int(st.session_state.get("lean_selected_job_id"))

    builders = builder_options(ctx)
    employees = employee_options(ctx)
    builder_labels = [""] + list(builders)
    employee_labels = [""] + list(employees)
    statuses = ["Not Started", "Quoted", "Booked", "Active", "On Hold", "Completed", "Invoiced", "Paid", "Archived"]

    with st.expander("Add job", expanded=frame.empty):
        with st.form("job_add"):
            c1, c2 = st.columns(2)
            job_no = c1.text_input("Job number")
            job_name = c2.text_input("Job name")
            builder_label = st.selectbox("Builder / client", builder_labels)
            address = st.text_input("Site address")
            c3, c4, c5 = st.columns(3)
            status = c3.selectbox("Status", statuses)
            leader_label = c4.selectbox("Leading hand", employee_labels)
            contract = c5.number_input("Contract value ex GST", min_value=0.0, step=1000.0)
            c6, c7 = st.columns(2)
            start = c6.date_input("Start date", value=date.today())
            finish = c7.date_input("Finish date", value=date.today())
            notes = st.text_area("Notes")
            add = st.form_submit_button("Create job", type="primary")
        if add:
            if not job_no.strip() or not job_name.strip():
                st.error("Job number and job name are required.")
            else:
                try:
                    job_id = ctx.db.insert_id(
                        """
                        INSERT INTO jobs
                        (job_no,job_name,builder_client_id,site_address,status,leading_hand,start_date,end_date,contract_value,notes,row_version)
                        VALUES (?,?,?,?,?,?,?,?,?,?,1)
                        """,
                        (
                            job_no.strip(), job_name.strip(), builders.get(builder_label), address.strip(), status,
                            leader_label.split(" — ", 1)[0] if leader_label else "", start.isoformat(), finish.isoformat(), contract, notes.strip(),
                        ),
                    )
                    ctx.audit("create", "jobs", job_id, job_no.strip())
                    st.session_state["lean_selected_job_id"] = job_id
                    rerun_success("Job created.")
                except Exception as exc:
                    st.error(str(exc))

    if not selected_id:
        return
    detail = ctx.db.query("SELECT * FROM jobs WHERE id=?", (selected_id,))
    if detail.empty:
        st.session_state.pop("lean_selected_job_id", None)
        st.rerun()
    job = detail.iloc[0].to_dict()
    st.subheader(f"{_clean(job.get('job_no'))} — {_clean(job.get('job_name'))}")
    tab_details, tab_po, tab_stages = st.tabs(["Job details", "Purchase orders", "Stages"])

    with tab_details:
        current_builder = next((label for label, value in builders.items() if value == _int(job.get("builder_client_id"))), "")
        current_leader = next((label for label in employee_labels if label.split(" — ", 1)[0] == _clean(job.get("leading_hand"))), "")
        with st.form(f"job_edit_{selected_id}"):
            c1, c2 = st.columns(2)
            job_no = c1.text_input("Job number", value=_clean(job.get("job_no")))
            job_name = c2.text_input("Job name", value=_clean(job.get("job_name")))
            builder_label = st.selectbox("Builder / client", builder_labels, index=builder_labels.index(current_builder) if current_builder in builder_labels else 0)
            address = st.text_input("Site address", value=_clean(job.get("site_address")))
            c3, c4, c5 = st.columns(3)
            current_status = _clean(job.get("status")) or statuses[0]
            if current_status not in statuses:
                statuses.append(current_status)
            status = c3.selectbox("Status", statuses, index=statuses.index(current_status))
            leader_label = c4.selectbox("Leading hand", employee_labels, index=employee_labels.index(current_leader) if current_leader in employee_labels else 0)
            contract = c5.number_input("Contract value ex GST", value=_float(job.get("contract_value")), step=1000.0)
            c6, c7 = st.columns(2)
            start = c6.date_input("Start date", value=_date_value(job.get("start_date")))
            finish = c7.date_input("Finish date", value=_date_value(job.get("end_date")))
            notes = st.text_area("Notes", value=_clean(job.get("notes")))
            save = st.form_submit_button("Update job", type="primary")
        if save:
            ctx.db.execute(
                """
                UPDATE jobs SET job_no=?,job_name=?,builder_client_id=?,site_address=?,status=?,leading_hand=?,
                    start_date=?,end_date=?,contract_value=?,notes=?,row_version=COALESCE(row_version,1)+1
                WHERE id=?
                """,
                (
                    job_no.strip(), job_name.strip(), builders.get(builder_label), address.strip(), status,
                    leader_label.split(" — ", 1)[0] if leader_label else "", start.isoformat(), finish.isoformat(), contract, notes.strip(), selected_id,
                ),
            )
            ctx.audit("update", "jobs", selected_id, job_no.strip())
            rerun_success("Job updated.")
        if can_manage() and status != "Archived":
            if st.button("Archive job", key=f"archive_job_{selected_id}"):
                ctx.db.execute(
                    "UPDATE jobs SET status='Archived',archived_at=?,archived_by=?,row_version=COALESCE(row_version,1)+1 WHERE id=?",
                    (datetime.now().isoformat(timespec="seconds"), ctx.user.get("username", ""), selected_id),
                )
                ctx.audit("archive", "jobs", selected_id)
                rerun_success("Job archived without deleting linked records.")

    with tab_po:
        purchase_orders_page(ctx, selected_id)
    with tab_stages:
        stages_page(ctx, selected_id)


def purchase_orders_page(ctx: AppContext, job_id: int) -> None:
    frame = ctx.db.query(
        """
        SELECT id,po_number AS "PO Number",COALESCE(description,'') AS "Description",
               COALESCE(amount_ex_gst,0) AS "Amount Ex GST",COALESCE(status,'') AS "Status",
               received_date AS "Received",COALESCE(notes,'') AS "Notes"
        FROM job_purchase_orders WHERE job_id=? ORDER BY id DESC
        """,
        (job_id,),
    )
    row = selected_row(frame, key=f"po_table_{job_id}", column_config={"Amount Ex GST": st.column_config.NumberColumn(format="$%.2f")})
    if row:
        st.session_state[f"selected_po_{job_id}"] = _int(row.get("id"))
    selected_id = _int(st.session_state.get(f"selected_po_{job_id}"))
    with st.expander("Add purchase order", expanded=frame.empty):
        with st.form(f"po_add_{job_id}"):
            c1, c2 = st.columns(2)
            number = c1.text_input("PO number")
            amount = c2.number_input("Amount ex GST", min_value=0.0, step=1000.0)
            description = st.text_input("Description")
            c3, c4 = st.columns(2)
            status = c3.selectbox("Status", ["Active", "Part Claimed", "Claimed", "Cancelled"])
            received = c4.date_input("Received date", value=date.today())
            notes = st.text_area("Notes")
            save = st.form_submit_button("Save PO", type="primary")
        if save:
            if not number.strip():
                st.error("PO number is required.")
            else:
                po_id = ctx.db.insert_id(
                    """
                    INSERT INTO job_purchase_orders
                    (job_id,po_number,description,amount_ex_gst,status,received_date,notes,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (job_id, number.strip(), description.strip(), amount, status, received.isoformat(), notes.strip(), datetime.now().isoformat(), datetime.now().isoformat()),
                )
                ctx.audit("create", "job_purchase_orders", po_id, number.strip())
                rerun_success("Purchase order saved.")
    if selected_id:
        detail = ctx.db.query("SELECT * FROM job_purchase_orders WHERE id=? AND job_id=?", (selected_id, job_id))
        if not detail.empty:
            po = detail.iloc[0].to_dict()
            with st.expander("Edit selected PO", expanded=True):
                with st.form(f"po_edit_{selected_id}"):
                    c1, c2 = st.columns(2)
                    number = c1.text_input("PO number", value=_clean(po.get("po_number")))
                    amount = c2.number_input("Amount ex GST", value=_float(po.get("amount_ex_gst")), step=1000.0)
                    description = st.text_input("Description", value=_clean(po.get("description")))
                    statuses = ["Active", "Part Claimed", "Claimed", "Cancelled"]
                    current = _clean(po.get("status")) or "Active"
                    if current not in statuses:
                        statuses.append(current)
                    c3, c4 = st.columns(2)
                    status = c3.selectbox("Status", statuses, index=statuses.index(current))
                    received = c4.date_input("Received date", value=_date_value(po.get("received_date")))
                    notes = st.text_area("Notes", value=_clean(po.get("notes")))
                    update = st.form_submit_button("Update PO", type="primary")
                if update:
                    ctx.db.execute(
                        """
                        UPDATE job_purchase_orders SET po_number=?,description=?,amount_ex_gst=?,status=?,received_date=?,notes=?,updated_at=?
                        WHERE id=? AND job_id=?
                        """,
                        (number.strip(), description.strip(), amount, status, received.isoformat(), notes.strip(), datetime.now().isoformat(), selected_id, job_id),
                    )
                    ctx.audit("update", "job_purchase_orders", selected_id, number.strip())
                    rerun_success("Purchase order updated.")


def stages_page(ctx: AppContext, job_id: int) -> None:
    frame = ctx.db.query(
        """
        SELECT s.id,s.sequence_order AS "Order",s.stage_name AS "Stage",COALESCE(p.po_number,'') AS "PO",
               COALESCE(s.job_percent,0) AS "Job %",COALESCE(s.status,'') AS "Status",
               s.start_date AS "Start",s.end_date AS "Finish",COALESCE(s.budget_hours,0) AS "Budget Hours"
        FROM job_stages s LEFT JOIN job_purchase_orders p ON p.id=s.purchase_order_id
        WHERE s.job_id=? ORDER BY s.sequence_order,s.id
        """,
        (job_id,),
    )
    row = selected_row(frame, key=f"stage_table_{job_id}")
    if row:
        st.session_state[f"selected_stage_{job_id}"] = _int(row.get("id"))
    selected_id = _int(st.session_state.get(f"selected_stage_{job_id}"))
    po_frame = ctx.db.query("SELECT id,po_number FROM job_purchase_orders WHERE job_id=? ORDER BY po_number", (job_id,))
    po_map = {"Shared / no separate PO": 0, **_option_map(po_frame, "id", ("po_number",))}
    with st.expander("Add stage", expanded=frame.empty):
        with st.form(f"stage_add_{job_id}"):
            name = st.text_input("Stage name")
            c1, c2, c3 = st.columns(3)
            order = c1.number_input("Sequence", min_value=1, value=max(1, len(frame) + 1), step=1)
            percent = c2.number_input("Job percentage", min_value=0.0, max_value=100.0, step=5.0)
            hours = c3.number_input("Budget hours", min_value=0.0, step=8.0)
            po_label = st.selectbox("Purchase order", list(po_map))
            c4, c5, c6 = st.columns(3)
            status = c4.selectbox("Status", ["Planned", "Ready", "In Progress", "Complete", "On Hold"])
            start = c5.date_input("Start", value=date.today())
            finish = c6.date_input("Finish", value=date.today())
            notes = st.text_area("Notes")
            save = st.form_submit_button("Save stage", type="primary")
        if save:
            if not name.strip():
                st.error("Stage name is required.")
            else:
                stage_id = ctx.db.insert_id(
                    """
                    INSERT INTO job_stages
                    (job_id,purchase_order_id,stage_name,sequence_order,job_percent,status,start_date,end_date,budget_hours,notes,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (job_id, po_map.get(po_label) or None, name.strip(), int(order), percent, status, start.isoformat(), finish.isoformat(), hours, notes.strip(), datetime.now().isoformat(), datetime.now().isoformat()),
                )
                ctx.audit("create", "job_stages", stage_id, name.strip())
                rerun_success("Stage saved.")
    if selected_id:
        detail = ctx.db.query("SELECT * FROM job_stages WHERE id=? AND job_id=?", (selected_id, job_id))
        if not detail.empty:
            stage = detail.iloc[0].to_dict()
            current_po = next((label for label, value in po_map.items() if value == _int(stage.get("purchase_order_id"))), "Shared / no separate PO")
            with st.expander("Edit selected stage", expanded=True):
                with st.form(f"stage_edit_{selected_id}"):
                    name = st.text_input("Stage name", value=_clean(stage.get("stage_name")))
                    c1, c2, c3 = st.columns(3)
                    order = c1.number_input("Sequence", min_value=1, value=max(1, _int(stage.get("sequence_order"))), step=1)
                    percent = c2.number_input("Job percentage", min_value=0.0, max_value=100.0, value=_float(stage.get("job_percent")), step=5.0)
                    hours = c3.number_input("Budget hours", min_value=0.0, value=_float(stage.get("budget_hours")), step=8.0)
                    po_label = st.selectbox("Purchase order", list(po_map), index=list(po_map).index(current_po))
                    statuses = ["Planned", "Ready", "In Progress", "Complete", "On Hold"]
                    current_status = _clean(stage.get("status")) or "Planned"
                    if current_status not in statuses:
                        statuses.append(current_status)
                    c4, c5, c6 = st.columns(3)
                    status = c4.selectbox("Status", statuses, index=statuses.index(current_status))
                    start = c5.date_input("Start", value=_date_value(stage.get("start_date")))
                    finish = c6.date_input("Finish", value=_date_value(stage.get("end_date")))
                    notes = st.text_area("Notes", value=_clean(stage.get("notes")))
                    update = st.form_submit_button("Update stage", type="primary")
                if update:
                    ctx.db.execute(
                        """
                        UPDATE job_stages SET purchase_order_id=?,stage_name=?,sequence_order=?,job_percent=?,status=?,
                            start_date=?,end_date=?,budget_hours=?,notes=?,updated_at=? WHERE id=? AND job_id=?
                        """,
                        (po_map.get(po_label) or None, name.strip(), int(order), percent, status, start.isoformat(), finish.isoformat(), hours, notes.strip(), datetime.now().isoformat(), selected_id, job_id),
                    )
                    ctx.audit("update", "job_stages", selected_id, name.strip())
                    rerun_success("Stage updated.")
