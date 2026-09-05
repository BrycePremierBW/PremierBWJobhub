"""Equipment page with lazy workflow rendering and batched checklist persistence."""
from __future__ import annotations

from ..runtime import *


EQUIPMENT_SECTIONS = [
    "Job Equipment Checklist",
    "Job Equipment Master List",
    "Import Filled PDF Checklist",
    "All Saved Equipment",
    "Manage Checklist Items",
    "Import PDFs",
]


def _render_filled_checklist_import(job_options):
    pb_section_heading(
        "Import filled master checklist",
        "Upload a completed fillable checklist and assign it to the correct job.",
    )
    if not job_options:
        pb_empty_state("Create a job first", "A job is required before checklist data can be imported.")
        return

    uploaded_checklist = st.file_uploader(
        "Upload completed Master Site Checklist PDF",
        type=["pdf"],
        key="equipment_filled_checklist_pdf",
    )
    if uploaded_checklist is None:
        return

    try:
        job_info, import_equipment_df, import_materials_df = parse_master_checklist_pdf(uploaded_checklist)
    except Exception as exc:
        st.error(f"Could not import this PDF checklist: {exc}")
        return

    pb_section_heading("Details found in PDF", "Confirm the detected job details before saving.")
    st.dataframe(pd.DataFrame([job_info]), width="stretch", hide_index=True)

    suggested_job = None
    if job_info.get("job_number"):
        for label in job_options:
            if label.startswith(job_info["job_number"]):
                suggested_job = label
                break
    if suggested_job is None and job_info.get("job_name"):
        for label in job_options:
            if str(job_info["job_name"]).lower() in label.lower():
                suggested_job = label
                break

    job_labels = list(job_options.keys())
    default_index = job_labels.index(suggested_job) if suggested_job in job_labels else 0
    selected_import_job = st.selectbox(
        "Import this checklist against job",
        job_labels,
        index=default_index,
        key="pdf_import_job_select",
    )
    update_job = st.checkbox("Update job details from the PDF where provided", value=True)
    replace_materials = st.checkbox("Replace existing imported PDF material lines for this job", value=True)

    pb_section_heading("Equipment / consumables found", "Equipment quantities detected in the uploaded checklist.")
    if import_equipment_df.empty:
        pb_empty_state("No equipment quantities found", "No equipment or consumable quantities were detected in this PDF.")
    else:
        st.dataframe(import_equipment_df, width="stretch", hide_index=True)

    pb_section_heading("Paint & materials found", "Material register lines detected in the uploaded checklist.")
    if import_materials_df.empty:
        pb_empty_state("No material lines found", "No paint or material register lines were detected in this PDF.")
    else:
        st.dataframe(import_materials_df, width="stretch", hide_index=True)

    if st.button("Import Checklist Into Selected Job", type="primary"):
        try:
            equipment_count, material_count = import_master_checklist_to_job(
                job_id=job_options[selected_import_job],
                job_info=job_info,
                equipment_df=import_equipment_df,
                materials_df=import_materials_df,
                source_file=uploaded_checklist.name,
                update_job=update_job,
                replace_imported_materials=replace_materials,
            )
            st.success(
                f"Imported checklist into {selected_import_job}. "
                f"Equipment/consumable lines saved: {equipment_count}. "
                f"Paint/material lines saved: {material_count}."
            )
            refresh()
        except Exception as exc:
            st.error(f"Could not import this PDF checklist: {exc}")


def _existing_records_by_item(existing_df):
    """Return every existing record grouped by checklist item, preserving duplicate IDs."""
    grouped = {}
    if existing_df.empty:
        return grouped
    for _, row in existing_df.sort_values("id").iterrows():
        grouped.setdefault(int(row["checklist_item_id"]), []).append(row)
    return grouped


def _persist_equipment_checklist(save_rows, existing_by_item, details):
    """Persist one checklist form without per-item read queries or one-row transactions."""
    insert_rows = []
    update_rows = []
    delete_rows = []

    for row in save_rows:
        existing_rows = existing_by_item.get(int(row["item_id"]), [])
        should_save = (
            row["is_required"] == 1
            or row["is_packed"] == 1
            or row["is_returned"] == 1
            or row["qty_taken"] > 0
            or row["qty_returned"] > 0
        )

        if should_save:
            values = (
                row["qty_required"], row["qty_taken"], row["qty_returned"],
                row["is_required"], row["is_packed"], row["is_returned"],
                details["date_out"], details["date_in"], details["taken_by"], details["returned_by"],
                details["condition_out"], details["condition_in"], details["notes"],
            )
            if existing_rows:
                keep_id = int(existing_rows[0]["id"])
                update_rows.append(values + (keep_id,))
                delete_rows.extend((int(record["id"]),) for record in existing_rows[1:])
            else:
                insert_rows.append((row["job_id"], row["item_id"]) + values)
        else:
            delete_rows.extend((int(record["id"]),) for record in existing_rows)

    if insert_rows:
        execute_many("""
            INSERT INTO equipment_checklist_records
            (job_id, checklist_item_id, qty_required, qty_taken, qty_returned,
             is_required, is_packed, is_returned, date_out, date_in, taken_by, returned_by,
             condition_out, condition_in, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, insert_rows)
    if update_rows:
        execute_many("""
            UPDATE equipment_checklist_records
            SET qty_required = ?, qty_taken = ?, qty_returned = ?,
                is_required = ?, is_packed = ?, is_returned = ?,
                date_out = ?, date_in = ?, taken_by = ?, returned_by = ?,
                condition_out = ?, condition_in = ?, notes = ?
            WHERE id = ?
        """, update_rows)
    if delete_rows:
        execute_many("DELETE FROM equipment_checklist_records WHERE id = ?", delete_rows)


def _render_job_checklist(job_options):
    pb_section_heading(
        "Job equipment checklist",
        "Record required, packed, returned and outstanding equipment for one job.",
    )
    if not job_options:
        pb_empty_state("Create a job first", "A job is required before an equipment checklist can be completed.")
        return

    selected_job_label = st.selectbox("Select Job", list(job_options.keys()), key="equipment_job")
    selected_job_id = job_options[selected_job_label]

    items_df = df_query("""
        SELECT id, category, item_name, default_qty, notes
        FROM equipment_checklist_items
        ORDER BY category, item_name
    """)
    if items_df.empty:
        pb_empty_state("No checklist items", "Add checklist items under Manage Checklist Items first.")
        return

    existing_df = df_query("""
        SELECT *
        FROM equipment_checklist_records
        WHERE job_id = ?
        ORDER BY id
    """, (selected_job_id,))
    existing_by_item = _existing_records_by_item(existing_df)

    with st.form("equipment_checklist_form"):
        save_rows = []
        for category in list(items_df["category"].dropna().unique()):
            st.markdown(f"### {category}")
            category_items = items_df[items_df["category"] == category]
            for _, item in category_items.iterrows():
                item_id = int(item["id"])
                existing_rows = existing_by_item.get(item_id, [])
                existing = existing_rows[0] if existing_rows else None
                item_name = str(item["item_name"])
                default_qty = float(item["default_qty"] or 0)
                req_default = bool(existing["is_required"]) if existing is not None else False
                packed_default = bool(existing["is_packed"]) if existing is not None else False
                returned_default = bool(existing["is_returned"]) if existing is not None else False
                qty_req_default = float(existing["qty_required"] or default_qty) if existing is not None else default_qty
                qty_taken_default = float(existing["qty_taken"] or 0) if existing is not None else 0.0
                qty_returned_default = float(existing["qty_returned"] or 0) if existing is not None else 0.0

                cols = st.columns([3, 1, 1, 1, 1, 1])
                required = cols[0].checkbox(item_name, value=req_default, key=f"required_{selected_job_id}_{item_id}")
                qty_required = cols[1].number_input("Req", min_value=0.0, value=qty_req_default, step=1.0, key=f"qty_required_{selected_job_id}_{item_id}")
                qty_taken = cols[2].number_input("Out", min_value=0.0, value=qty_taken_default, step=1.0, key=f"qty_taken_{selected_job_id}_{item_id}")
                qty_returned = cols[3].number_input("Back", min_value=0.0, value=qty_returned_default, step=1.0, key=f"qty_returned_{selected_job_id}_{item_id}")
                packed = cols[4].checkbox("Packed", value=packed_default, key=f"packed_{selected_job_id}_{item_id}")
                returned = cols[5].checkbox("Returned", value=returned_default, key=f"returned_{selected_job_id}_{item_id}")
                save_rows.append({
                    "job_id": selected_job_id,
                    "item_id": item_id,
                    "qty_required": qty_required,
                    "qty_taken": qty_taken,
                    "qty_returned": qty_returned,
                    "is_required": 1 if required else 0,
                    "is_packed": 1 if packed else 0,
                    "is_returned": 1 if returned else 0,
                })

        pb_section_heading("Sign out / return details", "These details are applied to the checklist lines saved in this submission.")
        col_a, col_b, col_c, col_d = st.columns(4)
        date_out = col_a.text_input("Date Out", value=str(jobhub_today()))
        date_in = col_b.text_input("Date In")
        taken_by = col_c.text_input("Taken By")
        returned_by = col_d.text_input("Returned By")
        col_e, col_f = st.columns(2)
        condition_out = col_e.text_input("Condition Out")
        condition_in = col_f.text_input("Condition In")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save Equipment Checklist to Job", type="primary")

    if submitted:
        _persist_equipment_checklist(save_rows, existing_by_item, {
            "date_out": date_out,
            "date_in": date_in,
            "taken_by": taken_by,
            "returned_by": returned_by,
            "condition_out": condition_out,
            "condition_in": condition_in,
            "notes": notes,
        })
        st.success("Equipment checklist saved to the selected job.")
        refresh()


def _render_master_list(job_options):
    pb_section_heading("Job equipment master list", "Review totals and outstanding equipment for one job.")
    if not job_options:
        pb_empty_state("Create a job first", "A job is required before the equipment master list can be viewed.")
        return

    selected_job_label = st.selectbox("Select Job for Master List", list(job_options.keys()), key="equipment_master_job")
    selected_job_id = job_options[selected_job_label]
    master_df = df_query("""
        SELECT j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               i.category AS 'Category',
               i.item_name AS 'Equipment Item',
               COALESCE(SUM(r.qty_required), 0) AS 'Total Required',
               COALESCE(SUM(r.qty_taken), 0) AS 'Total Taken',
               COALESCE(SUM(r.qty_returned), 0) AS 'Total Returned',
               COALESCE(SUM(r.qty_taken - r.qty_returned), 0) AS 'Still Out',
               COALESCE(MAX(r.date_out), '') AS 'Last Date Out',
               COALESCE(MAX(r.date_in), '') AS 'Last Date In',
               COALESCE(MAX(r.taken_by), '') AS 'Taken By',
               COALESCE(MAX(r.returned_by), '') AS 'Returned By',
               COALESCE(MAX(r.notes), '') AS 'Notes'
        FROM equipment_checklist_items i
        CROSS JOIN jobs j
        LEFT JOIN equipment_checklist_records r
            ON r.checklist_item_id = i.id AND r.job_id = j.id
        WHERE j.id = ?
        GROUP BY j.job_no, j.job_name, i.category, i.item_name
        ORDER BY i.category, i.item_name
    """, (selected_job_id,))
    if master_df.empty:
        pb_empty_state("No equipment checklist saved", "Complete the job equipment checklist to populate this view.")
        return

    st.dataframe(master_df, width="stretch", hide_index=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Items Taken", float(master_df["Total Taken"].fillna(0).sum()))
    c2.metric("Total Items Returned", float(master_df["Total Returned"].fillna(0).sum()))
    c3.metric("Total Still Out", float(master_df["Still Out"].fillna(0).sum()))
    st.download_button(
        "Download this Job Equipment Master List CSV",
        data=master_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"equipment_master_list_{selected_job_label.split(' - ')[0]}.csv",
        mime="text/csv",
    )


def _render_saved_equipment():
    pb_section_heading("All saved equipment", "Review saved checklist records across every job.")
    all_df = df_query("""
        SELECT r.id AS 'Record ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               i.category AS 'Category',
               i.item_name AS 'Equipment Item',
               r.qty_required AS 'Qty Required',
               r.qty_taken AS 'Qty Taken',
               r.qty_returned AS 'Qty Returned',
               CASE WHEN r.is_required = 1 THEN 'Yes' ELSE '' END AS 'Required',
               CASE WHEN r.is_packed = 1 THEN 'Yes' ELSE '' END AS 'Packed',
               CASE WHEN r.is_returned = 1 THEN 'Yes' ELSE '' END AS 'Returned',
               r.date_out AS 'Date Out',
               r.date_in AS 'Date In',
               r.taken_by AS 'Taken By',
               r.returned_by AS 'Returned By',
               r.condition_out AS 'Condition Out',
               r.condition_in AS 'Condition In',
               r.notes AS 'Notes'
        FROM equipment_checklist_records r
        JOIN jobs j ON j.id = r.job_id
        JOIN equipment_checklist_items i ON i.id = r.checklist_item_id
        ORDER BY j.job_no, i.category, i.item_name
    """)
    if all_df.empty:
        pb_empty_state("No saved equipment records", "Saved equipment checklist lines will appear here.")
        return

    st.dataframe(all_df.drop(columns=["Record ID"]), width="stretch", hide_index=True)
    with st.expander("Delete Saved Equipment Line"):
        delete_map = {
            f"ID {row['Record ID']} | {row['Job No']} - {row['Equipment Item']}": int(row["Record ID"])
            for _, row in all_df.iterrows()
        }
        selected = st.selectbox("Select line to delete", list(delete_map.keys()))
        if st.button("Delete Selected Equipment Line"):
            execute("DELETE FROM equipment_checklist_records WHERE id = ?", (delete_map[selected],))
            st.success("Equipment line deleted.")
            refresh()


def _render_manage_items():
    pb_section_heading("Manage checklist items", "Maintain the reusable equipment list shown on every job checklist.")
    with st.form("add_equipment_item_form"):
        col1, col2, col3 = st.columns(3)
        category = col1.text_input("Category")
        item_name = col2.text_input("Equipment Item")
        default_qty = col3.number_input("Default Qty", min_value=0.0, step=1.0, value=0.0)
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save Checklist Item", type="primary")

    if submitted and item_name:
        execute("""
            INSERT INTO equipment_checklist_items
            (category, item_name, default_qty, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(item_name) DO UPDATE SET
                category = excluded.category,
                default_qty = excluded.default_qty,
                notes = excluded.notes
        """, (category, item_name, default_qty, notes))
        st.success(f"Saved checklist item: {item_name}")
        refresh()

    items_df = df_query("""
        SELECT id,
               category AS 'Category',
               item_name AS 'Equipment Item',
               default_qty AS 'Default Qty',
               notes AS 'Notes'
        FROM equipment_checklist_items
        ORDER BY category, item_name
    """)
    if items_df.empty:
        pb_empty_state("No checklist items", "Add the first equipment checklist item above.")
    else:
        st.dataframe(items_df.drop(columns=["id"]), width="stretch", hide_index=True)


def render_equipment():
    pb_page_header(
        "Equipment",
        "Manage job equipment, checklist imports, returns and the reusable master item list.",
    )
    section = st.radio(
        "Equipment section",
        EQUIPMENT_SECTIONS,
        horizontal=True,
        key="equipment_section",
        label_visibility="collapsed",
    )

    # Only job-dependent workflows load the job selector data. Import and
    # secondary PDF tools are completely dormant until explicitly selected.
    if section == "All Saved Equipment":
        _render_saved_equipment()
    elif section == "Manage Checklist Items":
        _render_manage_items()
    elif section == "Import PDFs":
        pb_section_heading("Import supporting PDFs", "Import equipment checklist, safety or material-order PDFs into the selected job context.")
        render_context_pdf_import_for_selected_job(
            context="equipment",
            title="Import equipment checklist, safety or material order PDFs",
            key_prefix="equipment_pdf_import",
        )
    else:
        job_options = get_job_options()
        if section == "Job Equipment Checklist":
            _render_job_checklist(job_options)
        elif section == "Job Equipment Master List":
            _render_master_list(job_options)
        else:
            _render_filled_checklist_import(job_options)


# =============================
# REPORTS
# =============================
