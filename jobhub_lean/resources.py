from __future__ import annotations

from datetime import date

import streamlit as st

from .auth import can_manage
from .common import AppContext, Field, _clean, _date_value, _float, _int, _option_map, job_options, product_options, render_crud
from .ui import header, rerun_success, selected_row


def materials_page(ctx: AppContext) -> None:
    header("Materials", "Required, ordered and received materials by job.")
    jobs = job_options(ctx)
    products = product_options(ctx)
    if not jobs:
        st.info("Add a job first.")
        return
    selected_job_label = st.selectbox("Job", list(jobs), key="materials_job")
    job_id = jobs[selected_job_label]
    frame = ctx.db.query(
        """
        SELECT m.id,COALESCE(p.product_code,m.custom_product_code,'') AS "Code",
               COALESCE(p.product_name,m.custom_product_name,'') AS "Product",
               COALESCE(NULLIF(m.custom_supplier,''),NULLIF(m.supplier,''),p.supplier,'') AS "Supplier",
               COALESCE(m.qty_required,0) AS "Required",COALESCE(m.qty_received,0) AS "Received",
               COALESCE(p.unit,m.custom_unit,'') AS "Unit",COALESCE(m.date_ordered,'') AS "Ordered",
               COALESCE(m.notes,'') AS "Notes"
        FROM material_entries m LEFT JOIN products p ON p.id=m.product_id
        WHERE m.job_id=? ORDER BY m.id DESC
        """,
        (job_id,),
    )
    row = selected_row(frame, key=f"materials_table_{job_id}")
    if row:
        st.session_state[f"lean_selected_material_{job_id}"] = _int(row.get("id"))
    selected_id = _int(st.session_state.get(f"lean_selected_material_{job_id}"))
    product_labels = ["Custom item"] + list(products)
    with st.expander("Add material", expanded=frame.empty):
        with st.form(f"material_add_{job_id}"):
            product_label = st.selectbox("Product", product_labels)
            c1, c2 = st.columns(2)
            required = c1.number_input("Quantity required", min_value=0.0, step=1.0)
            received = c2.number_input("Quantity received", min_value=0.0, step=1.0)
            custom_code = custom_name = custom_unit = ""
            custom_price = 0.0
            if product_label == "Custom item":
                c3, c4 = st.columns(2)
                custom_code = c3.text_input("Custom code")
                custom_name = c4.text_input("Custom product name")
                c5, c6 = st.columns(2)
                custom_unit = c5.text_input("Unit")
                custom_price = c6.number_input("Unit price ex GST", min_value=0.0, step=1.0)
            supplier = st.text_input("Supplier override")
            ordered = st.date_input("Date ordered", value=date.today())
            notes = st.text_area("Notes")
            save = st.form_submit_button("Save material", type="primary")
        if save:
            if product_label == "Custom item" and not custom_name.strip():
                st.error("Enter a custom product name.")
            else:
                material_id = ctx.db.insert_id(
                    """
                    INSERT INTO material_entries
                    (job_id,product_id,qty_required,qty_received,date_ordered,supplier,notes,custom_product_code,custom_product_name,custom_supplier,custom_unit,custom_unit_price)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (job_id, products.get(product_label), required, received, ordered.isoformat(), supplier.strip(), notes.strip(), custom_code.strip(), custom_name.strip(), supplier.strip(), custom_unit.strip(), custom_price),
                )
                ctx.audit("create", "material_entries", material_id, selected_job_label)
                rerun_success("Material saved.")
    if selected_id and can_manage():
        detail = ctx.db.query("SELECT * FROM material_entries WHERE id=? AND job_id=?", (selected_id, job_id))
        if not detail.empty:
            item = detail.iloc[0].to_dict()
            with st.expander("Edit selected material", expanded=True):
                with st.form(f"material_edit_{selected_id}"):
                    c1, c2 = st.columns(2)
                    required = c1.number_input("Quantity required", min_value=0.0, value=_float(item.get("qty_required")), step=1.0)
                    received = c2.number_input("Quantity received", min_value=0.0, value=_float(item.get("qty_received")), step=1.0)
                    supplier = st.text_input("Supplier override", value=_clean(item.get("supplier")))
                    ordered = st.date_input("Date ordered", value=_date_value(item.get("date_ordered")))
                    notes = st.text_area("Notes", value=_clean(item.get("notes")))
                    update = st.form_submit_button("Update material", type="primary")
                if update:
                    ctx.db.execute("UPDATE material_entries SET qty_required=?,qty_received=?,supplier=?,date_ordered=?,notes=? WHERE id=?", (required, received, supplier.strip(), ordered.isoformat(), notes.strip(), selected_id))
                    ctx.audit("update", "material_entries", selected_id)
                    rerun_success("Material updated.")
                confirm = st.checkbox("Delete selected material", key=f"delete_material_confirm_{selected_id}")
                if st.button("Delete", disabled=not confirm, key=f"delete_material_{selected_id}"):
                    ctx.db.execute("DELETE FROM material_entries WHERE id=?", (selected_id,))
                    ctx.audit("delete", "material_entries", selected_id)
                    st.session_state.pop(f"lean_selected_material_{job_id}", None)
                    rerun_success("Material deleted.")


def equipment_page(ctx: AppContext) -> None:
    header("Equipment", "Master equipment list and job packing records.")
    master_tab, allocation_tab = st.tabs(["Equipment master", "Job allocation"])
    with master_tab:
        render_crud(
            ctx,
            title="Equipment Items",
            subtitle="Reusable equipment checklist items.",
            table="equipment_checklist_items",
            fields=(
                Field("item_name", "Item name", required=True), Field("category", "Category"),
                Field("unit", "Unit", "select", "Each", ("Each", "Set", "Metre", "Litre")),
                Field("default_qty", "Default quantity", "number", 0), Field("notes", "Notes", "textarea"),
            ),
            display_columns=("item_name", "category", "unit", "default_qty", "notes"),
            order_by="category,item_name", search_columns=("item_name", "category"), key="equipment_master",
        )
    with allocation_tab:
        jobs = job_options(ctx)
        if not jobs:
            st.info("Add a job first.")
            return
        job_label = st.selectbox("Job", list(jobs), key="equipment_job")
        job_id = jobs[job_label]
        frame = ctx.db.query(
            """
            SELECT r.id,i.item_name AS "Item",COALESCE(i.category,'') AS "Category",
                   COALESCE(r.qty_required,0) AS "Required",COALESCE(r.qty_taken,0) AS "Taken",
                   COALESCE(r.qty_returned,0) AS "Returned",
                   CASE WHEN COALESCE(r.is_packed,0)=1 THEN 'Yes' ELSE 'No' END AS "Packed",
                   CASE WHEN COALESCE(r.is_returned,0)=1 THEN 'Yes' ELSE 'No' END AS "Returned Complete",
                   COALESCE(r.notes,'') AS "Notes"
            FROM equipment_checklist_records r
            JOIN equipment_checklist_items i ON i.id=r.checklist_item_id
            WHERE r.job_id=? ORDER BY i.category,i.item_name
            """,
            (job_id,),
        )
        row = selected_row(frame, key=f"equipment_alloc_table_{job_id}")
        if row:
            st.session_state[f"lean_selected_equipment_alloc_{job_id}"] = _int(row.get("id"))
        items = _option_map(ctx.db.query("SELECT id,item_name,category FROM equipment_checklist_items ORDER BY category,item_name"), "id", ("item_name", "category"))
        if items:
            with st.expander("Add equipment to job", expanded=frame.empty):
                with st.form(f"equipment_alloc_add_{job_id}"):
                    item_label = st.selectbox("Equipment item", list(items))
                    quantity = st.number_input("Quantity required", min_value=0.0, value=1.0, step=1.0)
                    notes = st.text_area("Notes")
                    save = st.form_submit_button("Add to job", type="primary")
                if save:
                    existing_id = _int(ctx.db.scalar("SELECT id FROM equipment_checklist_records WHERE job_id=? AND checklist_item_id=? ORDER BY id LIMIT 1", (job_id, items[item_label]), 0))
                    if existing_id:
                        ctx.db.execute("UPDATE equipment_checklist_records SET qty_required=?,is_required=1,notes=? WHERE id=?", (quantity, notes.strip(), existing_id))
                    else:
                        ctx.db.execute("INSERT INTO equipment_checklist_records(job_id,checklist_item_id,qty_required,is_required,notes) VALUES (?,?,?,1,?)", (job_id, items[item_label], quantity, notes.strip()))
                    ctx.audit("upsert", "equipment_checklist_records", None, item_label)
                    rerun_success("Equipment allocation saved.")
        else:
            st.info("Add equipment master items first.")
        selected_id = _int(st.session_state.get(f"lean_selected_equipment_alloc_{job_id}"))
        if selected_id:
            detail = ctx.db.query("SELECT * FROM equipment_checklist_records WHERE id=? AND job_id=?", (selected_id, job_id))
            if not detail.empty:
                item = detail.iloc[0].to_dict()
                with st.expander("Update selected allocation", expanded=True):
                    with st.form(f"equipment_alloc_edit_{selected_id}"):
                        c1, c2, c3 = st.columns(3)
                        required = c1.number_input("Required", min_value=0.0, value=_float(item.get("qty_required")), step=1.0)
                        taken = c2.number_input("Taken", min_value=0.0, value=_float(item.get("qty_taken")), step=1.0)
                        returned = c3.number_input("Returned", min_value=0.0, value=_float(item.get("qty_returned")), step=1.0)
                        c4, c5 = st.columns(2)
                        packed = c4.checkbox("Packed", value=bool(_int(item.get("is_packed"))))
                        returned_complete = c5.checkbox("Returned complete", value=bool(_int(item.get("is_returned"))))
                        notes = st.text_area("Notes", value=_clean(item.get("notes")))
                        update = st.form_submit_button("Update", type="primary")
                    if update:
                        ctx.db.execute(
                            """
                            UPDATE equipment_checklist_records SET qty_required=?,qty_taken=?,qty_returned=?,is_packed=?,is_returned=?,notes=?,date_out=?,date_in=? WHERE id=?
                            """,
                            (required, taken, returned, int(packed), int(returned_complete), notes.strip(), date.today().isoformat() if packed else None, date.today().isoformat() if returned_complete else None, selected_id),
                        )
                        ctx.audit("update", "equipment_checklist_records", selected_id)
                        rerun_success("Equipment allocation updated.")
