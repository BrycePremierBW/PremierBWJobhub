"""Materials page with lazy section rendering."""
from __future__ import annotations

from ..runtime import *


MATERIAL_SECTIONS = [
    "Material Orders", "Add Material Cost", "Cost Register",
    "Imported PDF Lines", "Import PDFs",
]


def _render_material_orders():
    pb_section_heading("Material orders", "Review and approve employee material-order requests.")
    render_material_order_admin_queue()


def _render_material_pdf_import():
    pb_section_heading("Import material PDFs", "Import paint orders, purchase orders, colour schedules or specifications against a job.")
    render_context_pdf_import_for_selected_job(
        context="materials",
        title="Import paint/material order, PO, colour schedule or spec PDFs",
        key_prefix="materials_pdf_import",
    )


def _selected_product_details(product_id):
    return df_query("""
        SELECT id, product_code, product_name, supplier, unit, price_ex_gst, notes
        FROM products
        WHERE id = ?
    """, (product_id,))


def _render_add_material_cost():
    pb_section_heading("Add material cost", "Use a saved product or record a one-off material against a job.")
    job_options = get_job_options()
    if not job_options:
        pb_empty_state("Create a job first", "Material costs need a job before they can be recorded.")
        return

    product_code_options = get_product_options()
    product_name_options = get_product_name_options()
    job_label = st.selectbox("Job", list(job_options.keys()), key="material_job_select")
    entry_type_options = ["Saved Product", "One-off / Not Listed"] if product_code_options else ["One-off / Not Listed"]
    entry_type = st.radio(
        "Material entry type",
        entry_type_options,
        horizontal=True,
        key="material_entry_type",
    )

    product_id = None
    matched_code = ""
    matched_name = ""
    matched_supplier = ""
    matched_unit = ""
    matched_price = 0.0
    matched_notes = ""

    if entry_type == "Saved Product":
        product_search_type = st.radio(
            "Select product by",
            ["Product Code", "Product Name"],
            horizontal=True,
            key="material_product_search_type",
        )
        if product_search_type == "Product Code":
            selected_product = st.selectbox(
                "Product Code",
                list(product_code_options.keys()),
                key="material_product_code_select",
            )
            product_id = product_code_options[selected_product]
        else:
            selected_product = st.selectbox(
                "Product Name",
                list(product_name_options.keys()),
                key="material_product_name_select",
            )
            product_id = product_name_options[selected_product]

        product = _selected_product_details(product_id)
        if not product.empty:
            product_row = product.iloc[0]
            matched_code = str(product_row["product_code"] or "")
            matched_name = str(product_row["product_name"] or "")
            matched_supplier = str(product_row["supplier"] or "")
            matched_unit = str(product_row["unit"] or "")
            matched_price = float(product_row["price_ex_gst"] or 0)
            matched_notes = str(product_row["notes"] or "")

            st.success(f"Selected product: {matched_code} — {matched_name}")
            match_cols = st.columns(5)
            match_cols[0].metric("Code", matched_code)
            match_cols[1].metric("Product", matched_name[:28] + ("..." if len(matched_name) > 28 else ""))
            match_cols[2].metric("Supplier", matched_supplier[:18] + ("..." if len(matched_supplier) > 18 else ""))
            match_cols[3].metric("Unit", matched_unit)
            match_cols[4].metric("Unit Ex GST", f"${matched_price:,.2f}")
            with st.expander("View full matched product details"):
                st.write({
                    "Product Code": matched_code,
                    "Product Name": matched_name,
                    "Supplier": matched_supplier,
                    "Unit": matched_unit,
                    "Price Ex GST": f"${matched_price:,.2f}",
                    "Notes": matched_notes,
                })

    with st.form("material_form"):
        custom_product_code = ""
        custom_product_name = ""
        custom_supplier = ""
        custom_unit = ""
        custom_unit_price = None
        custom_colour = ""

        if entry_type == "One-off / Not Listed":
            st.caption("This is saved to this cost entry only and is not added to the master product list.")
            c1, c2 = st.columns(2)
            custom_product_code = c1.text_input("Product Code / Ref", value="CUSTOM")
            custom_product_name = c2.text_input("Product / Material Name")
            c3, c4, c5 = st.columns(3)
            custom_supplier = c3.text_input("Supplier")
            custom_unit = c4.text_input("Unit", value="each")
            custom_unit_price = c5.number_input("Unit Price Ex GST", min_value=0.0, step=1.0)
            custom_colour = st.text_input("Colour / Finish")
            display_unit_price = custom_unit_price or 0
            default_supplier = custom_supplier
        else:
            st.caption(f"This entry will be saved against **{job_label}** using **{matched_code} — {matched_name}**.")
            display_unit_price = matched_price
            default_supplier = matched_supplier

        col1, col2, col3 = st.columns(3)
        qty_required = col1.number_input("Qty Required", min_value=0.0, step=1.0)
        qty_received = col2.number_input("Qty Received", min_value=0.0, step=1.0)
        date_ordered = col3.text_input("Date Ordered", value=str(jobhub_today()))
        estimated_total = float(qty_required or 0) * float(display_unit_price or 0)
        st.info(f"Estimated material cost ex GST: ${estimated_total:,.2f}")
        supplier = st.text_input("Supplier Override", value=default_supplier)
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save Material Entry", type="primary")

    if not submitted:
        return
    if entry_type == "Saved Product" and not product_id:
        st.error("Select a saved product first.")
        return
    if entry_type == "One-off / Not Listed" and not custom_product_name.strip():
        st.error("Enter a product/material name.")
        return

    execute("""
        INSERT INTO material_entries
        (
            job_id, product_id, qty_required, qty_received, date_ordered,
            supplier, notes, custom_product_code, custom_product_name,
            custom_supplier, custom_unit, custom_unit_price, custom_colour
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_options[job_label], product_id, qty_required, qty_received,
        date_ordered, supplier, notes, custom_product_code,
        custom_product_name, custom_supplier, custom_unit,
        custom_unit_price, custom_colour,
    ))
    st.success("Material entry saved.")
    refresh()


def _material_cost_register():
    return df_query("""
        SELECT m.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               COALESCE(NULLIF(m.custom_product_code, ''), p.product_code, '') AS 'Product Code',
               COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS 'Product Name',
               COALESCE(NULLIF(m.supplier, ''), NULLIF(m.custom_supplier, ''), p.supplier, '') AS 'Supplier',
               COALESCE(NULLIF(m.custom_unit, ''), p.unit, '') AS 'Unit',
               COALESCE(m.custom_unit_price, p.price_ex_gst, 0) AS 'Unit Price',
               COALESCE(NULLIF(m.custom_colour, ''), '') AS 'Colour / Finish',
               m.qty_required AS 'Qty Required',
               m.qty_received AS 'Qty Received',
               ROUND(CAST((COALESCE(m.custom_unit_price, p.price_ex_gst, 0) * COALESCE(m.qty_required, 0)) AS numeric), 2) AS 'Total Cost',
               m.date_ordered AS 'Date Ordered',
               m.notes AS 'Notes'
        FROM material_entries m
        JOIN jobs j ON j.id = m.job_id
        LEFT JOIN products p ON p.id = m.product_id
        ORDER BY m.id DESC
    """)


def _render_cost_register():
    pb_section_heading("Material cost register", "Review and clean up saved job material costs.")
    df = _material_cost_register()
    if df.empty:
        pb_empty_state("No material cost entries", "Saved material costs will appear here.")
        return

    st.dataframe(df, width="stretch", hide_index=True)
    total_cost = float(df["Total Cost"].fillna(0).sum())
    st.metric("Total material cost ex GST", f"${total_cost:,.2f}")

    pb_section_heading("Delete material cost entries", "This removes cost rows only; products remain in the product list.")
    material_options = {
        f"ID {row['ID']} | {row['Job No']} - {row['Job Name']} | {row['Product Code']} | {row['Product Name']} | Qty {row['Qty Required']} | ${float(row['Total Cost'] or 0):,.2f}": int(row["ID"])
        for _, row in df.iterrows()
    }
    selected_material_labels = st.multiselect(
        "Select material cost entries to delete",
        list(material_options.keys()),
        key="delete_material_entries_select",
    )
    selected_material_ids = [material_options[label] for label in selected_material_labels]
    delete_materials_confirm = st.text_input(
        "To delete selected material cost entries, type: DELETE MATERIALS",
        key="delete_material_entries_confirm",
    )

    if st.button("Delete Selected Material Cost Entries", key="delete_material_entries_button"):
        if not selected_material_ids:
            st.error("Select at least one material cost entry first.")
        elif delete_materials_confirm.strip().upper() != "DELETE MATERIALS":
            st.error("Type DELETE MATERIALS exactly before deleting material entries.")
        else:
            execute_many(
                "DELETE FROM material_entries WHERE id = ?",
                [(int(material_id),) for material_id in selected_material_ids],
            )
            st.success(f"Deleted {len(selected_material_ids)} material cost entr{'y' if len(selected_material_ids) == 1 else 'ies'}.")
            refresh()


def _imported_material_register():
    return df_query("""
        SELECT im.id AS 'ID',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               im.product AS 'Product',
               im.colour AS 'Colour',
               im.qty_required AS 'Qty Required',
               im.qty_loaded AS 'Qty Loaded',
               im.source_file AS 'Source File',
               im.imported_at AS 'Imported At',
               im.notes AS 'Notes'
        FROM imported_material_entries im
        JOIN jobs j ON j.id = im.job_id
        ORDER BY im.id DESC
    """)


def _render_imported_material_lines():
    pb_section_heading("Imported PDF material lines", "Review material lines created from imported checklists and PDFs.")
    imported_df = _imported_material_register()
    if imported_df.empty:
        pb_empty_state("No imported PDF material lines", "Imported checklist material lines will appear here.")
        return

    st.dataframe(imported_df, width="stretch", hide_index=True)
    imported_options = {
        f"ID {row['ID']} | {row['Job No']} - {row['Job Name']} | {row['Product']} | Colour {row['Colour']} | Qty {row['Qty Required']}": int(row["ID"])
        for _, row in imported_df.iterrows()
    }
    selected_imported_labels = st.multiselect(
        "Select imported PDF material lines to delete",
        list(imported_options.keys()),
        key="delete_imported_material_entries_select",
    )
    selected_imported_ids = [imported_options[label] for label in selected_imported_labels]
    delete_imported_confirm = st.text_input(
        "To delete selected imported PDF material lines, type: DELETE IMPORTED MATERIALS",
        key="delete_imported_material_entries_confirm",
    )

    if st.button("Delete Selected Imported PDF Material Lines", key="delete_imported_material_entries_button"):
        if not selected_imported_ids:
            st.error("Select at least one imported PDF material line first.")
        elif delete_imported_confirm.strip().upper() != "DELETE IMPORTED MATERIALS":
            st.error("Type DELETE IMPORTED MATERIALS exactly before deleting imported material lines.")
        else:
            execute_many(
                "DELETE FROM imported_material_entries WHERE id = ?",
                [(int(imported_id),) for imported_id in selected_imported_ids],
            )
            st.success(f"Deleted {len(selected_imported_ids)} imported PDF material line{'s' if len(selected_imported_ids) != 1 else ''}.")
            refresh()


def render_material_costs():
    pb_page_header(
        "Materials",
        "Approve orders, record costs and review imported material data without loading every materials workflow at once.",
    )
    section = st.radio(
        "Materials section",
        MATERIAL_SECTIONS,
        horizontal=True,
        key="materials_page_section",
        label_visibility="collapsed",
    )

    if section == "Add Material Cost":
        _render_add_material_cost()
    elif section == "Cost Register":
        _render_cost_register()
    elif section == "Imported PDF Lines":
        _render_imported_material_lines()
    elif section == "Import PDFs":
        _render_material_pdf_import()
    else:
        _render_material_orders()


# =============================
# WAGES
# =============================
