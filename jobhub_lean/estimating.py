from __future__ import annotations

import re
from datetime import date, datetime

import pandas as pd
import streamlit as st

from .auth import can_manage
from .common import AppContext, _clean, _date_value, _float, _int, job_options
from .ui import header, rerun_success, selected_row


def recalc_estimate(ctx: AppContext, estimate_id: int) -> tuple[float, float]:
    estimate = ctx.db.query(
        "SELECT labour_hours,labour_rate,material_allowance FROM estimate_working_sheets WHERE id=?",
        (estimate_id,),
    )
    if estimate.empty:
        return 0.0, 0.0
    row = estimate.iloc[0]
    line_total = _float(ctx.db.scalar("SELECT COALESCE(SUM(line_total),0) FROM estimate_line_items WHERE estimate_id=?", (estimate_id,), 0))
    labour = _float(row.get("labour_hours")) * _float(row.get("labour_rate"))
    material = _float(row.get("material_allowance"))
    ex_gst = round(line_total + labour + material, 2)
    inc_gst = round(ex_gst * 1.1, 2)
    ctx.db.execute(
        "UPDATE estimate_working_sheets SET total_ex_gst=?,gst_amount=?,total_inc_gst=?,updated_at=? WHERE id=?",
        (ex_gst, round(ex_gst * 0.1, 2), inc_gst, datetime.now().isoformat(timespec="seconds"), estimate_id),
    )
    return ex_gst, inc_gst


def estimating_page(ctx: AppContext) -> None:
    header("Estimating", "Lean working sheets with line items, labour and material allowances.")
    jobs = job_options(ctx, include_archived=True)
    if not jobs:
        st.info("Add a job first.")
        return
    job_label = st.selectbox("Job", list(jobs), key="estimate_job")
    job_id = jobs[job_label]
    estimates = ctx.db.query(
        """
        SELECT id,estimate_no AS "Estimate",COALESCE(revision,'') AS "Revision",
               COALESCE(status,'') AS "Status",estimate_date AS "Date",
               COALESCE(labour_hours,0) AS "Labour Hours",COALESCE(labour_rate,0) AS "Labour Rate",
               COALESCE(material_allowance,0) AS "Materials",COALESCE(total_ex_gst,0) AS "Total Ex GST",
               COALESCE(total_inc_gst,0) AS "Total Inc GST"
        FROM estimate_working_sheets
        WHERE job_id=? AND COALESCE(archived,0)=0 ORDER BY id DESC
        """,
        (job_id,),
    )
    row = selected_row(
        estimates,
        key=f"estimates_table_{job_id}",
        column_config={
            "Labour Rate": st.column_config.NumberColumn(format="$%.2f"),
            "Materials": st.column_config.NumberColumn(format="$%.2f"),
            "Total Ex GST": st.column_config.NumberColumn(format="$%.2f"),
            "Total Inc GST": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    if row:
        st.session_state["lean_selected_estimate_id"] = _int(row.get("id"))
    estimate_id = _int(st.session_state.get("lean_selected_estimate_id"))

    with st.expander("Create estimate", expanded=estimates.empty):
        with st.form(f"estimate_add_{job_id}"):
            c1, c2, c3 = st.columns(3)
            number = c1.text_input("Estimate number", value=f"EST-{date.today().strftime('%Y%m%d')}")
            revision = c2.text_input("Revision", value="A")
            status = c3.selectbox("Status", ["Draft", "Submitted", "Approved", "Declined"])
            c4, c5, c6 = st.columns(3)
            estimate_date = c4.date_input("Estimate date", value=date.today())
            labour_hours = c5.number_input("Labour hours", min_value=0.0, step=8.0)
            labour_rate = c6.number_input("Labour rate", min_value=0.0, value=125.0, step=5.0)
            materials = st.number_input("Material allowance ex GST", min_value=0.0, step=100.0)
            notes = st.text_area("Notes")
            save = st.form_submit_button("Create estimate", type="primary")
        if save:
            new_id = ctx.db.insert_id(
                """
                INSERT INTO estimate_working_sheets
                (job_id,estimate_no,estimate_date,revision,status,labour_hours,labour_rate,material_allowance,total_ex_gst,gst_amount,total_inc_gst,notes,archived,updated_at)
                VALUES (?,?,?,?,?,?,?,?,0,0,0,?,0,?)
                """,
                (job_id, number.strip(), estimate_date.isoformat(), revision.strip(), status, labour_hours, labour_rate, materials, notes.strip(), datetime.now().isoformat(timespec="seconds")),
            )
            recalc_estimate(ctx, new_id)
            ctx.audit("create", "estimate_working_sheets", new_id, number.strip())
            st.session_state["lean_selected_estimate_id"] = new_id
            rerun_success("Estimate created.")

    if not estimate_id:
        return
    estimate = ctx.db.query("SELECT * FROM estimate_working_sheets WHERE id=? AND job_id=?", (estimate_id, job_id))
    if estimate.empty:
        st.session_state.pop("lean_selected_estimate_id", None)
        st.rerun()
    item = estimate.iloc[0].to_dict()
    tab_summary, tab_lines, tab_import = st.tabs(["Summary", "Line items", "CSV import"])

    with tab_summary:
        with st.form(f"estimate_edit_{estimate_id}"):
            c1, c2, c3 = st.columns(3)
            number = c1.text_input("Estimate number", value=_clean(item.get("estimate_no")))
            revision = c2.text_input("Revision", value=_clean(item.get("revision")))
            statuses = ["Draft", "Submitted", "Approved", "Declined"]
            current_status = _clean(item.get("status")) or "Draft"
            if current_status not in statuses:
                statuses.append(current_status)
            status = c3.selectbox("Status", statuses, index=statuses.index(current_status))
            c4, c5, c6 = st.columns(3)
            estimate_date = c4.date_input("Estimate date", value=_date_value(item.get("estimate_date")))
            labour_hours = c5.number_input("Labour hours", min_value=0.0, value=_float(item.get("labour_hours")), step=8.0)
            labour_rate = c6.number_input("Labour rate", min_value=0.0, value=_float(item.get("labour_rate")), step=5.0)
            materials = st.number_input("Material allowance ex GST", min_value=0.0, value=_float(item.get("material_allowance")), step=100.0)
            notes = st.text_area("Notes", value=_clean(item.get("notes")))
            update = st.form_submit_button("Update estimate", type="primary")
        if update:
            ctx.db.execute(
                """
                UPDATE estimate_working_sheets SET estimate_no=?,estimate_date=?,revision=?,status=?,labour_hours=?,labour_rate=?,material_allowance=?,notes=?,updated_at=? WHERE id=?
                """,
                (number.strip(), estimate_date.isoformat(), revision.strip(), status, labour_hours, labour_rate, materials, notes.strip(), datetime.now().isoformat(timespec="seconds"), estimate_id),
            )
            ex_gst, inc_gst = recalc_estimate(ctx, estimate_id)
            ctx.audit("update", "estimate_working_sheets", estimate_id, number.strip())
            rerun_success(f"Estimate updated: ${ex_gst:,.2f} ex GST / ${inc_gst:,.2f} inc GST.")
        ex_gst, inc_gst = recalc_estimate(ctx, estimate_id)
        c1, c2 = st.columns(2)
        c1.metric("Total ex GST", f"${ex_gst:,.2f}")
        c2.metric("Total inc GST", f"${inc_gst:,.2f}")
        if can_manage() and st.button("Archive estimate", key=f"archive_estimate_{estimate_id}"):
            ctx.db.execute("UPDATE estimate_working_sheets SET archived=1,updated_at=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), estimate_id))
            ctx.audit("archive", "estimate_working_sheets", estimate_id)
            st.session_state.pop("lean_selected_estimate_id", None)
            rerun_success("Estimate archived.")

    with tab_lines:
        lines = ctx.db.query(
            """
            SELECT id,COALESCE(section,'') AS "Section",COALESCE(item_description,'') AS "Description",
                   COALESCE(qty,0) AS "Qty",COALESCE(unit,'') AS "Unit",COALESCE(unit_rate,0) AS "Rate",
                   COALESCE(line_total,0) AS "Line Total",COALESCE(substrate,'') AS "Substrate",
                   COALESCE(work_location,'') AS "Location",COALESCE(notes,'') AS "Notes"
            FROM estimate_line_items WHERE estimate_id=? ORDER BY id
            """,
            (estimate_id,),
        )
        line = selected_row(
            lines,
            key=f"estimate_lines_{estimate_id}",
            column_config={"Rate": st.column_config.NumberColumn(format="$%.2f"), "Line Total": st.column_config.NumberColumn(format="$%.2f")},
        )
        if line:
            st.session_state[f"lean_selected_line_{estimate_id}"] = _int(line.get("id"))
        line_id = _int(st.session_state.get(f"lean_selected_line_{estimate_id}"))
        with st.expander("Add line item", expanded=lines.empty):
            with st.form(f"estimate_line_add_{estimate_id}"):
                c1, c2 = st.columns(2)
                section = c1.text_input("Section")
                description = c2.text_input("Description")
                c3, c4, c5 = st.columns(3)
                qty = c3.number_input("Quantity", min_value=0.0, step=1.0)
                unit = c4.text_input("Unit", value="m²")
                rate = c5.number_input("Unit rate", min_value=0.0, step=1.0)
                c6, c7 = st.columns(2)
                substrate = c6.text_input("Substrate")
                location = c7.text_input("Work location")
                notes = st.text_area("Notes")
                add_line = st.form_submit_button("Add line", type="primary")
            if add_line:
                total = round(qty * rate, 2)
                new_id = ctx.db.insert_id(
                    """
                    INSERT INTO estimate_line_items
                    (estimate_id,section,item_description,qty,unit,unit_rate,line_total,substrate,work_location,notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (estimate_id, section.strip(), description.strip(), qty, unit.strip(), rate, total, substrate.strip(), location.strip(), notes.strip()),
                )
                recalc_estimate(ctx, estimate_id)
                ctx.audit("create", "estimate_line_items", new_id, description.strip())
                rerun_success("Estimate line added.")
        if line_id:
            detail = ctx.db.query("SELECT * FROM estimate_line_items WHERE id=? AND estimate_id=?", (line_id, estimate_id))
            if not detail.empty:
                data = detail.iloc[0].to_dict()
                with st.expander("Edit selected line", expanded=True):
                    with st.form(f"estimate_line_edit_{line_id}"):
                        c1, c2 = st.columns(2)
                        section = c1.text_input("Section", value=_clean(data.get("section")))
                        description = c2.text_input("Description", value=_clean(data.get("item_description")))
                        c3, c4, c5 = st.columns(3)
                        qty = c3.number_input("Quantity", min_value=0.0, value=_float(data.get("qty")), step=1.0)
                        unit = c4.text_input("Unit", value=_clean(data.get("unit")))
                        rate = c5.number_input("Unit rate", min_value=0.0, value=_float(data.get("unit_rate")), step=1.0)
                        c6, c7 = st.columns(2)
                        substrate = c6.text_input("Substrate", value=_clean(data.get("substrate")))
                        location = c7.text_input("Work location", value=_clean(data.get("work_location")))
                        notes = st.text_area("Notes", value=_clean(data.get("notes")))
                        update_line = st.form_submit_button("Update line", type="primary")
                    if update_line:
                        ctx.db.execute(
                            """
                            UPDATE estimate_line_items SET section=?,item_description=?,qty=?,unit=?,unit_rate=?,line_total=?,substrate=?,work_location=?,notes=? WHERE id=?
                            """,
                            (section.strip(), description.strip(), qty, unit.strip(), rate, round(qty * rate, 2), substrate.strip(), location.strip(), notes.strip(), line_id),
                        )
                        recalc_estimate(ctx, estimate_id)
                        ctx.audit("update", "estimate_line_items", line_id, description.strip())
                        rerun_success("Estimate line updated.")
                    confirm = st.checkbox("Delete selected line", key=f"delete_estimate_line_confirm_{line_id}")
                    if st.button("Delete line", disabled=not confirm, key=f"delete_estimate_line_{line_id}"):
                        ctx.db.execute("DELETE FROM estimate_line_items WHERE id=?", (line_id,))
                        recalc_estimate(ctx, estimate_id)
                        ctx.audit("delete", "estimate_line_items", line_id)
                        st.session_state.pop(f"lean_selected_line_{estimate_id}", None)
                        rerun_success("Estimate line deleted.")

    with tab_import:
        upload = st.file_uploader("Upload estimate lines CSV", type=["csv"], key=f"estimate_csv_{estimate_id}")
        st.caption("Recommended columns: section, description, qty, unit, unit_rate, substrate, work_location, notes")
        if upload is not None:
            try:
                frame = pd.read_csv(upload).fillna("")
                normalised = {re.sub(r"[^a-z0-9]", "", str(column).lower()): column for column in frame.columns}
                def pick(*names: str) -> pd.Series:
                    source = next((normalised[name] for name in names if name in normalised), None)
                    return frame[source] if source else pd.Series([""] * len(frame))
                prepared = pd.DataFrame({
                    "section": pick("section", "category"),
                    "description": pick("description", "itemdescription", "item"),
                    "qty": pick("qty", "quantity", "m2", "sqm"),
                    "unit": pick("unit"),
                    "unit_rate": pick("unitrate", "rate", "price"),
                    "substrate": pick("substrate"),
                    "work_location": pick("worklocation", "location", "area"),
                    "notes": pick("notes"),
                })
                st.dataframe(prepared.head(100), hide_index=True, use_container_width=True)
                if st.button("Import line items", type="primary", key=f"import_estimate_lines_{estimate_id}"):
                    rows = []
                    for _, source in prepared.iterrows():
                        description = _clean(source["description"])
                        if not description:
                            continue
                        qty = _float(source["qty"])
                        rate = _float(source["unit_rate"])
                        rows.append((estimate_id, _clean(source["section"]), description, qty, _clean(source["unit"]), rate, round(qty * rate, 2), _clean(source["substrate"]), _clean(source["work_location"]), _clean(source["notes"])))
                    ctx.db.execute_many(
                        """
                        INSERT INTO estimate_line_items
                        (estimate_id,section,item_description,qty,unit,unit_rate,line_total,substrate,work_location,notes)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                        """,
                        rows,
                    )
                    recalc_estimate(ctx, estimate_id)
                    ctx.audit("import", "estimate_line_items", estimate_id, f"{len(rows)} rows")
                    rerun_success(f"Imported {len(rows)} estimate lines.")
            except Exception as exc:
                st.error(str(exc))
