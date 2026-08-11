"""Estimate worksheets, product restoration and job cost forecasting.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *


def estimate_totals(estimate_id, labour_hours, labour_rate, material_allowance, access_equipment_allowance, subcontractor_allowance, sundries_allowance, margin_percent, contingency_percent, gst_percent):
    line_df = df_query("SELECT COALESCE(SUM(line_total), 0) AS line_total FROM estimate_line_items WHERE estimate_id = ?", (estimate_id,))
    line_total = float(line_df.iloc[0]["line_total"] or 0) if not line_df.empty else 0.0
    labour_total = float(labour_hours or 0) * float(labour_rate or 0)
    direct_total = line_total + labour_total + float(material_allowance or 0) + float(access_equipment_allowance or 0) + float(subcontractor_allowance or 0) + float(sundries_allowance or 0)
    contingency_amount = direct_total * (float(contingency_percent or 0) / 100)
    subtotal = direct_total + contingency_amount
    margin_amount = subtotal * (float(margin_percent or 0) / 100)
    total_ex_gst = subtotal + margin_amount
    gst_amount = total_ex_gst * (float(gst_percent or 0) / 100)
    total_inc_gst = total_ex_gst + gst_amount
    return {
        "line_total": round(line_total, 2),
        "labour_total": round(labour_total, 2),
        "direct_total": round(direct_total, 2),
        "contingency_amount": round(contingency_amount, 2),
        "margin_amount": round(margin_amount, 2),
        "total_ex_gst": round(total_ex_gst, 2),
        "gst_amount": round(gst_amount, 2),
        "total_inc_gst": round(total_inc_gst, 2),
    }

def recalc_estimate_totals(estimate_id):
    est = df_query("SELECT * FROM estimate_working_sheets WHERE id = ?", (estimate_id,))
    if est.empty:
        return
    r = est.iloc[0]
    totals = estimate_totals(
        estimate_id,
        r["labour_hours"], r["labour_rate"], r["material_allowance"], r["access_equipment_allowance"],
        r["subcontractor_allowance"], r["sundries_allowance"], r["margin_percent"], r["contingency_percent"], r["gst_percent"]
    )
    execute("""
        UPDATE estimate_working_sheets
        SET total_ex_gst = ?, gst_amount = ?, total_inc_gst = ?, updated_at = ?
        WHERE id = ?
    """, (totals["total_ex_gst"], totals["gst_amount"], totals["total_inc_gst"], jobhub_now().strftime("%Y-%m-%d %H:%M:%S"), estimate_id))

def estimate_working_sheet_page():
    st.header("Estimate Working Sheet")
    st.caption("Build a working estimate and link it directly to the job it relates to.")

    render_context_pdf_import_for_selected_job(
        context="estimating",
        title="Import quote, plans, specs, scope or PO PDFs",
        key_prefix="estimate_pdf_import",
    )
    st.divider()

    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first, then you can create an estimate working sheet.")
        return

    selected_job = st.selectbox("Select Job", list(job_options.keys()), key="estimate_job_select")
    selected_job_id = job_options[selected_job]

    job_details = df_query("""
        SELECT j.job_no AS 'Job No', j.job_name AS 'Job Name', bc.name AS 'Builder / Client',
               j.site_address AS 'Site Address', j.status AS 'Status', j.contract_value AS 'Contract Value'
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.id = ?
    """, (selected_job_id,))
    if not job_details.empty:
        st.dataframe(job_details, width="stretch", hide_index=True)

    estimates = df_query("""
        SELECT id, estimate_no, revision, estimate_date, status, total_ex_gst, total_inc_gst
        FROM estimate_working_sheets
        WHERE job_id = ?
        ORDER BY id DESC
    """, (selected_job_id,))

    with st.expander("Create New Estimate Working Sheet", expanded=estimates.empty):
        next_rev = len(estimates) + 1
        default_job_no = "EST"
        if not job_details.empty:
            default_job_no = str(job_details.iloc[0]["Job No"])
        with st.form("create_estimate_form"):
            col1, col2, col3 = st.columns(3)
            estimate_no = col1.text_input("Estimate No", value=f"{default_job_no}-EST-{next_rev:02d}")
            estimate_date = col2.text_input("Estimate Date", value=str(jobhub_today()))
            revision = col3.text_input("Revision", value=f"Rev {next_rev}")
            notes = st.text_area("Initial Notes")
            created = st.form_submit_button("Create Estimate Working Sheet")
            if created:
                now = jobhub_now().strftime("%Y-%m-%d %H:%M:%S")
                execute("""
                    INSERT INTO estimate_working_sheets
                    (job_id, estimate_no, estimate_date, revision, status, labour_hours, labour_rate,
                     material_allowance, access_equipment_allowance, subcontractor_allowance, sundries_allowance,
                     margin_percent, contingency_percent, gst_percent, total_ex_gst, gst_amount, total_inc_gst,
                     created_at, updated_at, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (selected_job_id, estimate_no, estimate_date, revision, "Draft", 0, 120, 0, 0, 0, 0, 20, 0, 10, 0, 0, 0, now, now, notes))
                st.success("Estimate working sheet created.")
                refresh()

    estimates = df_query("""
        SELECT id, estimate_no, revision, estimate_date, status, total_ex_gst, total_inc_gst
        FROM estimate_working_sheets
        WHERE job_id = ?
        ORDER BY id DESC
    """, (selected_job_id,))

    if estimates.empty:
        st.info("No estimate working sheets saved for this job yet.")
        return

    estimate_options = {
        f"{row['estimate_no']} - {row['revision']} - {row['status']} - ${float(row['total_inc_gst'] or 0):,.2f} inc GST": int(row["id"])
        for _, row in estimates.iterrows()
    }
    selected_estimate_label = st.selectbox("Select Estimate Working Sheet", list(estimate_options.keys()), key="estimate_select")
    selected_estimate_id = estimate_options[selected_estimate_label]

    current = df_query("SELECT * FROM estimate_working_sheets WHERE id = ?", (selected_estimate_id,))
    if current.empty:
        st.warning("Selected estimate could not be found.")
        return
    current = current.iloc[0]

    tab_summary, tab_lines, tab_view = st.tabs(["Summary / Pricing", "Line Items", "View / Export"])

    with tab_summary:
        with st.form("estimate_summary_form"):
            col1, col2, col3, col4 = st.columns(4)
            estimate_no = col1.text_input("Estimate No", value=str(current["estimate_no"] or ""))
            estimate_date = col2.text_input("Estimate Date", value=str(current["estimate_date"] or str(jobhub_today())))
            revision = col3.text_input("Revision", value=str(current["revision"] or ""))
            statuses = ["Draft", "Sent", "Approved", "Lost", "Superseded"]
            current_status = str(current["status"] or "Draft")
            status_index = statuses.index(current_status) if current_status in statuses else 0
            status = col4.selectbox("Status", statuses, index=status_index)

            col5, col6 = st.columns(2)
            labour_hours = col5.number_input("Labour Hours", min_value=0.0, step=1.0, value=float(current["labour_hours"] or 0))
            labour_rate = col6.number_input("Labour Rate", min_value=0.0, step=5.0, value=float(current["labour_rate"] or 120))

            col7, col8, col9, col10 = st.columns(4)
            material_allowance = col7.number_input("Material Allowance", min_value=0.0, step=100.0, value=float(current["material_allowance"] or 0))
            access_equipment_allowance = col8.number_input("Access / Equipment Allowance", min_value=0.0, step=100.0, value=float(current["access_equipment_allowance"] or 0))
            subcontractor_allowance = col9.number_input("Subcontractor Allowance", min_value=0.0, step=100.0, value=float(current["subcontractor_allowance"] or 0))
            sundries_allowance = col10.number_input("Sundries / Consumables", min_value=0.0, step=50.0, value=float(current["sundries_allowance"] or 0))

            col11, col12, col13 = st.columns(3)
            margin_percent = col11.number_input("Margin %", min_value=0.0, step=1.0, value=float(current["margin_percent"] or 0))
            contingency_percent = col12.number_input("Contingency %", min_value=0.0, step=1.0, value=float(current["contingency_percent"] or 0))
            gst_percent = col13.number_input("GST %", min_value=0.0, step=1.0, value=float(current["gst_percent"] or 10))
            notes = st.text_area("Notes / Scope Notes", value=str(current["notes"] or ""))

            preview = estimate_totals(selected_estimate_id, labour_hours, labour_rate, material_allowance, access_equipment_allowance, subcontractor_allowance, sundries_allowance, margin_percent, contingency_percent, gst_percent)
            st.markdown("### Pricing Preview")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Direct Cost", f"${preview['direct_total']:,.2f}")
            c2.metric("Margin", f"${preview['margin_amount']:,.2f}")
            c3.metric("Total Ex GST", f"${preview['total_ex_gst']:,.2f}")
            c4.metric("Total Inc GST", f"${preview['total_inc_gst']:,.2f}")

            saved = st.form_submit_button("Save Estimate Summary")
            if saved:
                execute("""
                    UPDATE estimate_working_sheets
                    SET estimate_no = ?, estimate_date = ?, revision = ?, status = ?, labour_hours = ?, labour_rate = ?,
                        material_allowance = ?, access_equipment_allowance = ?, subcontractor_allowance = ?, sundries_allowance = ?,
                        margin_percent = ?, contingency_percent = ?, gst_percent = ?, total_ex_gst = ?, gst_amount = ?, total_inc_gst = ?,
                        updated_at = ?, notes = ?
                    WHERE id = ?
                """, (estimate_no, estimate_date, revision, status, labour_hours, labour_rate, material_allowance,
                      access_equipment_allowance, subcontractor_allowance, sundries_allowance, margin_percent, contingency_percent,
                      gst_percent, preview["total_ex_gst"], preview["gst_amount"], preview["total_inc_gst"],
                      jobhub_now().strftime("%Y-%m-%d %H:%M:%S"), notes, selected_estimate_id))
                st.success("Estimate summary saved.")
                refresh()

    with tab_lines:
        st.subheader("Estimate Line Items")
        with st.form("add_estimate_line_form"):
            col1, col2 = st.columns(2)
            section = col1.selectbox("Section", ["Preliminaries", "Labour", "Materials", "Access / Equipment", "Subcontractor", "Variations", "Other"])
            item_description = col2.text_input("Item Description")
            col3, col4, col5 = st.columns(3)
            qty = col3.number_input("Qty", min_value=0.0, step=1.0)
            unit = col4.text_input("Unit", value="item")
            unit_rate = col5.number_input("Unit Rate", min_value=0.0, step=10.0)
            line_notes = st.text_area("Line Notes")
            added = st.form_submit_button("Add Line Item")
            if added and item_description:
                line_total = round(float(qty or 0) * float(unit_rate or 0), 2)
                execute("""
                    INSERT INTO estimate_line_items
                    (estimate_id, section, item_description, qty, unit, unit_rate, line_total, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (selected_estimate_id, section, item_description, qty, unit, unit_rate, line_total, line_notes))
                recalc_estimate_totals(selected_estimate_id)
                st.success("Line item added.")
                refresh()

        lines_df = df_query("""
            SELECT id, section AS 'Section', item_description AS 'Description', qty AS 'Qty', unit AS 'Unit',
                   unit_rate AS 'Unit Rate', line_total AS 'Line Total', notes AS 'Notes'
            FROM estimate_line_items
            WHERE estimate_id = ?
            ORDER BY id
        """, (selected_estimate_id,))
        if lines_df.empty:
            st.info("No line items added yet.")
        else:
            st.dataframe(lines_df.drop(columns=["id"]), width="stretch", hide_index=True)
            st.metric("Line Item Total", f"${float(lines_df['Line Total'].fillna(0).sum()):,.2f}")
            delete_options = {f"{r['Section']} - {r['Description']} - ${float(r['Line Total'] or 0):,.2f}": int(r["id"]) for _, r in lines_df.iterrows()}
            selected_delete = st.selectbox("Line item to delete", list(delete_options.keys()))
            confirm = st.checkbox("Confirm delete selected line item")
            if st.button("Delete Selected Line Item"):
                if not confirm:
                    st.error("Tick the confirm box first.")
                else:
                    execute("DELETE FROM estimate_line_items WHERE id = ?", (delete_options[selected_delete],))
                    recalc_estimate_totals(selected_estimate_id)
                    st.success("Line item deleted.")
                    refresh()

    with tab_view:
        summary_df = df_query("""
            SELECT e.estimate_no AS 'Estimate No', e.revision AS 'Revision', e.estimate_date AS 'Date', e.status AS 'Status',
                   j.job_no AS 'Job No', j.job_name AS 'Job Name', e.labour_hours AS 'Labour Hours', e.labour_rate AS 'Labour Rate',
                   e.material_allowance AS 'Material Allowance', e.access_equipment_allowance AS 'Access / Equipment',
                   e.subcontractor_allowance AS 'Subcontractor', e.sundries_allowance AS 'Sundries', e.margin_percent AS 'Margin %',
                   e.contingency_percent AS 'Contingency %', e.total_ex_gst AS 'Total Ex GST', e.gst_amount AS 'GST',
                   e.total_inc_gst AS 'Total Inc GST', e.notes AS 'Notes'
            FROM estimate_working_sheets e
            JOIN jobs j ON j.id = e.job_id
            WHERE e.id = ?
        """, (selected_estimate_id,))
        lines_export = df_query("""
            SELECT section AS 'Section', item_description AS 'Description', qty AS 'Qty', unit AS 'Unit',
                   unit_rate AS 'Unit Rate', line_total AS 'Line Total', notes AS 'Notes'
            FROM estimate_line_items
            WHERE estimate_id = ?
            ORDER BY id
        """, (selected_estimate_id,))
        st.markdown("### Estimate Summary")
        st.dataframe(summary_df, width="stretch", hide_index=True)
        st.markdown("### Estimate Lines")
        st.dataframe(lines_export, width="stretch", hide_index=True)

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            summary_df.to_excel(writer, index=False, sheet_name="Estimate Summary")
            lines_export.to_excel(writer, index=False, sheet_name="Estimate Lines")
            for ws in writer.book.worksheets:
                for column_cells in ws.columns:
                    max_len = 0
                    col_letter = column_cells[0].column_letter
                    for cell in column_cells:
                        value = "" if cell.value is None else str(cell.value)
                        max_len = max(max_len, len(value))
                    ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 45)
        output.seek(0)
        clean_name = str(summary_df.iloc[0]["Estimate No"] if not summary_df.empty else "estimate_working_sheet").replace("/", "-").replace("\\", "-")
        st.download_button(
            "Download Estimate Working Sheet Excel",
            data=output.getvalue(),
            file_name=f"{clean_name}_Estimate_Working_Sheet.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

def restore_product_list():
    products = [('PB-H00001', 'Coverplus Interior L/S White', 'Haymes', '', 168.0, ''), ('PB-H00002', 'Elite Ceiling Toned White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00003', 'Elite Ceiling White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00004', 'Elite Interior Low Sheen White', 'Haymes', '', 118.0, ''), ('PB-H00005', 'Elite Interior Matt White, 15L', 'Haymes', '15L', 125.0, ''), ('PB-H00006', 'Elite Acrylic Sealer Undercoat', 'Haymes', '', 105.36, ''), ('PB-H00007', 'Elite Quick Dry Primer Undercoat', 'Haymes', '', 123.55, ''), ('PB-H00008', 'Expressions Low Sheen DKT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00009', 'Expressions Low Sheen EDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00010', 'Expressions Low Sheen UDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00011', 'Expressions Low Sheen White', 'Haymes', '', 107.48, ''), ('PB-H00012', 'Expressions Low Sheen White', 'Haymes', '', 145.0, ''), ('PB-H00013', 'Expressions Low Sheen White, 4L', 'Haymes', '4L', 67.26, ''), ('PB-H00014', 'Solashield Low Sheen DKT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00015', 'Solashield Low Sheen DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00016', 'Solashield Low Sheen DKT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00017', 'Solashield Low Sheen EDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00018', 'Solashield Low Sheen EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00019', 'Solashield Low Sheen EDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00020', 'Solashield Low Sheen UDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00021', 'Solashield Low Sheen UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00022', 'Solashield Low Sheen UDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00023', 'Solashield Low Sheen White, 10L', 'Haymes', '10L', 107.42, ''), ('PB-H00024', 'Solashield Low Sheen White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00025', 'Solashield Low Sheen White, 4L', 'Haymes', '4L', 67.4, ''), ('PB-H00026', 'R/Tex Roll On Coarse, 15L', 'Haymes', '15L', 175.0, ''), ('PB-H00027', 'Solashield Satin DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00028', 'Solashield Satin EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00029', 'Solashield Satin UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00030', 'Solashield Satin White, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00031', 'Solashield Satin White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00032', 'Ultra Premium Primer Sealer', 'Haymes', '', 167.46, ''), ('PB-H00033', 'Acrylic Sealer Undercoat', 'Haymes', '', 120.0, ''), ('PB-H00034', 'Ultratrim High Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00035', 'Ultratrim Semi Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00036', 'Woodcare Aqualac Floor Satin', 'Haymes', '', 250.44, '')]

    restored = 0
    for row in products:
        execute("""
            INSERT INTO products
            (product_code, product_name, supplier, unit, price_ex_gst, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_code) DO UPDATE SET
                product_name = excluded.product_name,
                supplier = excluded.supplier,
                unit = excluded.unit,
                price_ex_gst = excluded.price_ex_gst,
                notes = excluded.notes
        """, row)
        restored += 1

    return restored

def product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0

def restore_taubmans_product_list():
    products = [('T ALL WEATHER L/S W15L 18', '187200/15L', '30001918', '15L', 145.0), ('T ALL WEATHER L/S A15L 18', '187204/15L', '30001923', '15L', 150.0), ('T ALL WEATHER L/S N15L 18', '187205/15L', '30001928', '15L', 150.0), ('T ALL WEATHER L/S D15L 18', '187209/15L', '30001942', '15L', 150.0), ('T ALL WEATHER L/S W10L 18', '187200/10L', '30001917', '10L', 120.0), ('T ALL WEATHER L/S A10L 18', '187204/10L', '30001922', '10L', 122.5), ('T ALL WEATHER L/S N10L 18', '187205/10L', '30001927', '10L', 122.5), ('T ALL WEATHER L/S D10L 18', '187209/10L', '30001941', '10L', 122.5), ('T ALL WEATHER L/S W4L 18', '187200/4L', '30001921', '4L', 57.5), ('T ALL WEATHER L/S A4L 18', '187204/4L', '30001926', '4L', 60.0), ('T ALL WEATHER L/S N4L 18', '187205/4L', '30001931', '4L', 60.0), ('T ALL WEATHER L/S D4L 18', '187209/4L', '30001944', '4L', 60.0), ('T ALL WEATHER MATT W15L 18', '187100/15L', '30001906', '15L', 145.0), ('T ALL WEATHER MATT A15L 18', '187104/15L', '30001910', '15L', 150.0), ('T ALL WEATHER MATT N15L 18', '187105/15L', '30001914', '15L', 150.0), ('T ALL WEATHER S/G W15L 18', '187400/15L', '30001950', '15L', 145.0), ('T ALL WEATHER S/G D15L 19', '187409/15L', '30001963', '15L', 150.0), ('T ALL WEATHER S/G A10L 19', '187404/10L', '30001954', '10L', 122.5), ('T ENDURE INT L/S W15L 18', '124200/15L', '30001368', '15L', 145.0), ('T ENDURE INT L/S W10L 18', '124200/10L', '30001367', '10L', 120.0), ('T ENDURE INT L/S W4L 18', '124200/4L', '30001371', '4L', 57.5), ('T ENDURE INT MATT W15L 18', '124100/15L', '30001356', '15L', 160.0), ('T ENDURE INT MATT W10L 18', '124100/10L', '30001355', '10L', 135.0), ('T ENDURE INT MATT W4L 18', '124100/4L', '30001359', '4L', 60.0), ('T PURE PERF L/S W15L 21', '279250/15L', '30008591', '15L', 145.0), ('T PURE PERF MATT W15L 21', '279150/15L', '30008588', '15L', 145.0), ('T PURE PERF CEILING W15L 21', '279050/15L', '30008581', '15L', 120.0), ('T Ceiling Premium W15L 22', '128000/15L', '30010919', '15L', 120.0), ('T PURE PERF WB ENAMEL GLOSS W10L 21', '279950/10L', '30008738', '10L', 122.0), ('T PURE PERF WB ENAMEL S/G W10L 21', '279850/10L', '30008596', '10L', 122.0), ('T PURE PERF WB ENAMEL GLOSS W4L 21', '279950/4L', '30008739', '4L', 65.0), ('T PURE PERF WB ENAMEL S/G W4L 21', '279850/4L', '30008737', '4L', 65.0), ('T WB ENAMEL GLOSS W10L 19', '121610/10L', '30001326', '10L', 125.0), ('T WB ENAMEL S/G W10L 19', '121410/10L', '30001294', '10L', 125.0), ('T WB ENAMEL GLOSS W4L 19', '121610/4L', '30001329', '4L', 65.0), ('T WB ENAMEL S/G W4L 19', '121410/4L', '30001297', '4L', 65.0), ('T ULTIMATE ENAMEL S/G W10L 19', '132810/10L', '30001427', '10L', 170.0), ('T ULTIMATE ENAMEL GLOSS W10L 19', '132910/10L', '30001441', '10L', 170.0), ('T ULTIMATE ENAMEL S/G W4L 19', '132810/4L', '30001429', '4L', 80.0), ('T ULTIMATE ENAMEL GLOSS W4L 19', '132910/4L', '30001443', '4L', 80.0), ('T TRADE EDGE UC W15L 16', '259500/15L', '30002265', '15L', 90.0), ('T ULTRA PREP W15L 09', '288500/15L', '30002664', '15L', 110.0), ('T TRADEX ULTRAPREP 15L', '274520/15L', '30002331', '15L', 105.0), ('T PURE PERF PREP W15L 21', '279550/15L', '30008595', '15L', 120.0), ('T TRADEX CEILING W15L 15', '274000/15L', '30002310', '15L', 100.0), ('T PRO INT L/S W15L 20', '278200/15L', '30002370', '15L', 120.0), ('T PRO EXT L/S W15L 20', '278710/15L', '30002387', '15L', 135.0), ('T PRO ENAMEL W/B GLOSS W10L20', '278600/10L', '30002381', '10L', 120.0), ('T PRO ENAMEL W/B S/G W10L 20', '278400/10L', '30002376', '10L', 120.0), ('T PRO CEILING W15L 20', '278000/15L', '30002364', '15L', 105.0), ('T 3IN1 W15L 15', '108100/15L', '30000957', '15L', 130.0), ('T 3IN1 W4L 15', '108100/4L', '30000960', '4L', 60.0), ('J PRO DECK OIL NAT 10L 17', '481200/10L', '30004332', '10L', 170.0), ('J PRO DECK OIL NAT 4L 17', '481200/4L', '30004334', '4L', 75.0), ('J PRO EXT CLEAR GLOSS 4L 17', '481121/4L', '30004331', '4L', 80.0), ('J PRO EXT CLEAR SATIN 4L 17', '481120/4L', '30004328', '4L', 80.0), ('T ARMAWALL A/SHIELD W15L 09', '310400/15L', '30003018', '15L', 150.0), ('T ARMAWALL PRIMER 15L 09', '315500/15L', '30003036', '15L', 135.0), ('T ARMAWALL SEALER BOND C10L', '315705/10L', '30003039', '10L', 135.0), ('T ARMAWALL SEALER BOND W10L', '315700/10L', '30003038', '10L', 135.0)]

    restored = 0
    for product_name, product_code, taubmans_sku, unit, price_ex_gst in products:
        execute("""
            INSERT INTO products
            (product_code, product_name, supplier, unit, price_ex_gst, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_code) DO UPDATE SET
                product_name = excluded.product_name,
                supplier = excluded.supplier,
                unit = excluded.unit,
                price_ex_gst = excluded.price_ex_gst,
                notes = excluded.notes
        """, (
            product_code,
            product_name,
            "Taubmans",
            unit,
            float(price_ex_gst),
            f"Taubmans SKU: {taubmans_sku} | Source: uploaded Premier Brushworks Taubmans price list"
        ))
        restored += 1

    return restored

def taubmans_product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products WHERE supplier = 'Taubmans'")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0

def restore_haymes_and_taubmans_product_lists():
    products = [('PB-H00001', 'Coverplus Interior L/S White', 'Haymes', '', 168.0, ''), ('PB-H00002', 'Elite Ceiling Toned White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00003', 'Elite Ceiling White, 15L', 'Haymes', '15L', 90.0, ''), ('PB-H00004', 'Elite Interior Low Sheen White', 'Haymes', '', 118.0, ''), ('PB-H00005', 'Elite Interior Matt White, 15L', 'Haymes', '15L', 125.0, ''), ('PB-H00006', 'Elite Acrylic Sealer Undercoat', 'Haymes', '', 105.36, ''), ('PB-H00007', 'Elite Quick Dry Primer Undercoat', 'Haymes', '', 123.55, ''), ('PB-H00008', 'Expressions Low Sheen DKT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00009', 'Expressions Low Sheen EDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00010', 'Expressions Low Sheen UDT, 4L', 'Haymes', '4L', 74.13, ''), ('PB-H00011', 'Expressions Low Sheen White', 'Haymes', '', 107.48, ''), ('PB-H00012', 'Expressions Low Sheen White', 'Haymes', '', 145.0, ''), ('PB-H00013', 'Expressions Low Sheen White, 4L', 'Haymes', '4L', 67.26, ''), ('PB-H00014', 'Solashield Low Sheen DKT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00015', 'Solashield Low Sheen DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00016', 'Solashield Low Sheen DKT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00017', 'Solashield Low Sheen EDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00018', 'Solashield Low Sheen EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00019', 'Solashield Low Sheen EDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00020', 'Solashield Low Sheen UDT, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00021', 'Solashield Low Sheen UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00022', 'Solashield Low Sheen UDT, 4L', 'Haymes', '4L', 73.55, ''), ('PB-H00023', 'Solashield Low Sheen White, 10L', 'Haymes', '10L', 107.42, ''), ('PB-H00024', 'Solashield Low Sheen White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00025', 'Solashield Low Sheen White, 4L', 'Haymes', '4L', 67.4, ''), ('PB-H00026', 'R/Tex Roll On Coarse, 15L', 'Haymes', '15L', 175.0, ''), ('PB-H00027', 'Solashield Satin DKT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00028', 'Solashield Satin EDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00029', 'Solashield Satin UDT, 15L', 'Haymes', '15L', 160.0, ''), ('PB-H00030', 'Solashield Satin White, 10L', 'Haymes', '10L', 115.0, ''), ('PB-H00031', 'Solashield Satin White, 15L', 'Haymes', '15L', 148.0, ''), ('PB-H00032', 'Ultra Premium Primer Sealer', 'Haymes', '', 167.46, ''), ('PB-H00033', 'Acrylic Sealer Undercoat', 'Haymes', '', 120.0, ''), ('PB-H00034', 'Ultratrim High Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00035', 'Ultratrim Semi Gloss White', 'Haymes', '', 130.0, ''), ('PB-H00036', 'Woodcare Aqualac Floor Satin', 'Haymes', '', 250.44, ''), ('187200/15L', 'T ALL WEATHER L/S W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001918 | Source: uploaded Premier Brushworks Taubmans price list'), ('187204/15L', 'T ALL WEATHER L/S A15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001923 | Source: uploaded Premier Brushworks Taubmans price list'), ('187205/15L', 'T ALL WEATHER L/S N15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001928 | Source: uploaded Premier Brushworks Taubmans price list'), ('187209/15L', 'T ALL WEATHER L/S D15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001942 | Source: uploaded Premier Brushworks Taubmans price list'), ('187200/10L', 'T ALL WEATHER L/S W10L 18', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30001917 | Source: uploaded Premier Brushworks Taubmans price list'), ('187204/10L', 'T ALL WEATHER L/S A10L 18', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001922 | Source: uploaded Premier Brushworks Taubmans price list'), ('187205/10L', 'T ALL WEATHER L/S N10L 18', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001927 | Source: uploaded Premier Brushworks Taubmans price list'), ('187209/10L', 'T ALL WEATHER L/S D10L 18', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001941 | Source: uploaded Premier Brushworks Taubmans price list'), ('187200/4L', 'T ALL WEATHER L/S W4L 18', 'Taubmans', '4L', 57.5, 'Taubmans SKU: 30001921 | Source: uploaded Premier Brushworks Taubmans price list'), ('187204/4L', 'T ALL WEATHER L/S A4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001926 | Source: uploaded Premier Brushworks Taubmans price list'), ('187205/4L', 'T ALL WEATHER L/S N4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001931 | Source: uploaded Premier Brushworks Taubmans price list'), ('187209/4L', 'T ALL WEATHER L/S D4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001944 | Source: uploaded Premier Brushworks Taubmans price list'), ('187100/15L', 'T ALL WEATHER MATT W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001906 | Source: uploaded Premier Brushworks Taubmans price list'), ('187104/15L', 'T ALL WEATHER MATT A15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001910 | Source: uploaded Premier Brushworks Taubmans price list'), ('187105/15L', 'T ALL WEATHER MATT N15L 18', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001914 | Source: uploaded Premier Brushworks Taubmans price list'), ('187400/15L', 'T ALL WEATHER S/G W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001950 | Source: uploaded Premier Brushworks Taubmans price list'), ('187409/15L', 'T ALL WEATHER S/G D15L 19', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30001963 | Source: uploaded Premier Brushworks Taubmans price list'), ('187404/10L', 'T ALL WEATHER S/G A10L 19', 'Taubmans', '10L', 122.5, 'Taubmans SKU: 30001954 | Source: uploaded Premier Brushworks Taubmans price list'), ('124200/15L', 'T ENDURE INT L/S W15L 18', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30001368 | Source: uploaded Premier Brushworks Taubmans price list'), ('124200/10L', 'T ENDURE INT L/S W10L 18', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30001367 | Source: uploaded Premier Brushworks Taubmans price list'), ('124200/4L', 'T ENDURE INT L/S W4L 18', 'Taubmans', '4L', 57.5, 'Taubmans SKU: 30001371 | Source: uploaded Premier Brushworks Taubmans price list'), ('124100/15L', 'T ENDURE INT MATT W15L 18', 'Taubmans', '15L', 160.0, 'Taubmans SKU: 30001356 | Source: uploaded Premier Brushworks Taubmans price list'), ('124100/10L', 'T ENDURE INT MATT W10L 18', 'Taubmans', '10L', 135.0, 'Taubmans SKU: 30001355 | Source: uploaded Premier Brushworks Taubmans price list'), ('124100/4L', 'T ENDURE INT MATT W4L 18', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30001359 | Source: uploaded Premier Brushworks Taubmans price list'), ('279250/15L', 'T PURE PERF L/S W15L 21', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30008591 | Source: uploaded Premier Brushworks Taubmans price list'), ('279150/15L', 'T PURE PERF MATT W15L 21', 'Taubmans', '15L', 145.0, 'Taubmans SKU: 30008588 | Source: uploaded Premier Brushworks Taubmans price list'), ('279050/15L', 'T PURE PERF CEILING W15L 21', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30008581 | Source: uploaded Premier Brushworks Taubmans price list'), ('128000/15L', 'T Ceiling Premium W15L 22', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30010919 | Source: uploaded Premier Brushworks Taubmans price list'), ('279950/10L', 'T PURE PERF WB ENAMEL GLOSS W10L 21', 'Taubmans', '10L', 122.0, 'Taubmans SKU: 30008738 | Source: uploaded Premier Brushworks Taubmans price list'), ('279850/10L', 'T PURE PERF WB ENAMEL S/G W10L 21', 'Taubmans', '10L', 122.0, 'Taubmans SKU: 30008596 | Source: uploaded Premier Brushworks Taubmans price list'), ('279950/4L', 'T PURE PERF WB ENAMEL GLOSS W4L 21', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30008739 | Source: uploaded Premier Brushworks Taubmans price list'), ('279850/4L', 'T PURE PERF WB ENAMEL S/G W4L 21', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30008737 | Source: uploaded Premier Brushworks Taubmans price list'), ('121610/10L', 'T WB ENAMEL GLOSS W10L 19', 'Taubmans', '10L', 125.0, 'Taubmans SKU: 30001326 | Source: uploaded Premier Brushworks Taubmans price list'), ('121410/10L', 'T WB ENAMEL S/G W10L 19', 'Taubmans', '10L', 125.0, 'Taubmans SKU: 30001294 | Source: uploaded Premier Brushworks Taubmans price list'), ('121610/4L', 'T WB ENAMEL GLOSS W4L 19', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30001329 | Source: uploaded Premier Brushworks Taubmans price list'), ('121410/4L', 'T WB ENAMEL S/G W4L 19', 'Taubmans', '4L', 65.0, 'Taubmans SKU: 30001297 | Source: uploaded Premier Brushworks Taubmans price list'), ('132810/10L', 'T ULTIMATE ENAMEL S/G W10L 19', 'Taubmans', '10L', 170.0, 'Taubmans SKU: 30001427 | Source: uploaded Premier Brushworks Taubmans price list'), ('132910/10L', 'T ULTIMATE ENAMEL GLOSS W10L 19', 'Taubmans', '10L', 170.0, 'Taubmans SKU: 30001441 | Source: uploaded Premier Brushworks Taubmans price list'), ('132810/4L', 'T ULTIMATE ENAMEL S/G W4L 19', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30001429 | Source: uploaded Premier Brushworks Taubmans price list'), ('132910/4L', 'T ULTIMATE ENAMEL GLOSS W4L 19', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30001443 | Source: uploaded Premier Brushworks Taubmans price list'), ('259500/15L', 'T TRADE EDGE UC W15L 16', 'Taubmans', '15L', 90.0, 'Taubmans SKU: 30002265 | Source: uploaded Premier Brushworks Taubmans price list'), ('288500/15L', 'T ULTRA PREP W15L 09', 'Taubmans', '15L', 110.0, 'Taubmans SKU: 30002664 | Source: uploaded Premier Brushworks Taubmans price list'), ('274520/15L', 'T TRADEX ULTRAPREP 15L', 'Taubmans', '15L', 105.0, 'Taubmans SKU: 30002331 | Source: uploaded Premier Brushworks Taubmans price list'), ('279550/15L', 'T PURE PERF PREP W15L 21', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30008595 | Source: uploaded Premier Brushworks Taubmans price list'), ('274000/15L', 'T TRADEX CEILING W15L 15', 'Taubmans', '15L', 100.0, 'Taubmans SKU: 30002310 | Source: uploaded Premier Brushworks Taubmans price list'), ('278200/15L', 'T PRO INT L/S W15L 20', 'Taubmans', '15L', 120.0, 'Taubmans SKU: 30002370 | Source: uploaded Premier Brushworks Taubmans price list'), ('278710/15L', 'T PRO EXT L/S W15L 20', 'Taubmans', '15L', 135.0, 'Taubmans SKU: 30002387 | Source: uploaded Premier Brushworks Taubmans price list'), ('278600/10L', 'T PRO ENAMEL W/B GLOSS W10L20', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30002381 | Source: uploaded Premier Brushworks Taubmans price list'), ('278400/10L', 'T PRO ENAMEL W/B S/G W10L 20', 'Taubmans', '10L', 120.0, 'Taubmans SKU: 30002376 | Source: uploaded Premier Brushworks Taubmans price list'), ('278000/15L', 'T PRO CEILING W15L 20', 'Taubmans', '15L', 105.0, 'Taubmans SKU: 30002364 | Source: uploaded Premier Brushworks Taubmans price list'), ('108100/15L', 'T 3IN1 W15L 15', 'Taubmans', '15L', 130.0, 'Taubmans SKU: 30000957 | Source: uploaded Premier Brushworks Taubmans price list'), ('108100/4L', 'T 3IN1 W4L 15', 'Taubmans', '4L', 60.0, 'Taubmans SKU: 30000960 | Source: uploaded Premier Brushworks Taubmans price list'), ('481200/10L', 'J PRO DECK OIL NAT 10L 17', 'Taubmans', '10L', 170.0, 'Taubmans SKU: 30004332 | Source: uploaded Premier Brushworks Taubmans price list'), ('481200/4L', 'J PRO DECK OIL NAT 4L 17', 'Taubmans', '4L', 75.0, 'Taubmans SKU: 30004334 | Source: uploaded Premier Brushworks Taubmans price list'), ('481121/4L', 'J PRO EXT CLEAR GLOSS 4L 17', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30004331 | Source: uploaded Premier Brushworks Taubmans price list'), ('481120/4L', 'J PRO EXT CLEAR SATIN 4L 17', 'Taubmans', '4L', 80.0, 'Taubmans SKU: 30004328 | Source: uploaded Premier Brushworks Taubmans price list'), ('310400/15L', 'T ARMAWALL A/SHIELD W15L 09', 'Taubmans', '15L', 150.0, 'Taubmans SKU: 30003018 | Source: uploaded Premier Brushworks Taubmans price list'), ('315500/15L', 'T ARMAWALL PRIMER 15L 09', 'Taubmans', '15L', 135.0, 'Taubmans SKU: 30003036 | Source: uploaded Premier Brushworks Taubmans price list'), ('315705/10L', 'T ARMAWALL SEALER BOND C10L', 'Taubmans', '10L', 135.0, 'Taubmans SKU: 30003039 | Source: uploaded Premier Brushworks Taubmans price list'), ('315700/10L', 'T ARMAWALL SEALER BOND W10L', 'Taubmans', '10L', 135.0, 'Taubmans SKU: 30003038 | Source: uploaded Premier Brushworks Taubmans price list')]

    restored = 0
    for product_code, product_name, supplier, unit, price_ex_gst, notes in products:
        execute("""
            INSERT INTO products
            (product_code, product_name, supplier, unit, price_ex_gst, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_code) DO UPDATE SET
                product_name = excluded.product_name,
                supplier = excluded.supplier,
                unit = excluded.unit,
                price_ex_gst = excluded.price_ex_gst,
                notes = excluded.notes
        """, (
            product_code,
            product_name,
            supplier,
            unit,
            float(price_ex_gst or 0),
            notes
        ))
        restored += 1

    return restored

def haymes_product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products WHERE supplier = 'Haymes'")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0

def combined_paint_product_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM products WHERE supplier IN ('Haymes', 'Taubmans')")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0

def restore_builders_clients_and_employees():
    builders = [('Builder', 'Ausmar Homes Pty Ltd', 'Compliance Team', '07 5319 1500', 'compliance@ausmargroup.com.au', '8 Flinders Lane, Maroochydore QLD 4558', '1083000', '55 087 236 208', '30 Days', 'Annual Period Trade Contract'), ('Developer / Builder', 'OneLife Property Group', 'Bryce Curran', '0421 069 817', 'brycecurran@hotmail.com', 'Sunshine Coast', '', '', '30 Days', 'Multi-residential complexes'), ('Builder', 'Thompson Homes', '', '', '', '', '', '', '30 Days', 'Existing JobHub builder'), ('Client / Developer', 'Palm Lakes', '', '', '', 'Pelican Waters', '', '', '30 Days', 'Palm Lakes Pelican Waters'), ('Interior Designer', 'Box Clever Interiors', 'Design Team', '07 5309 5640', 'info@boxcleverinteriors.com.au', 'PO Box 208, Moffat Beach QLD 4551', '', '08 007 428 613', '', 'Bannister project designer'), ('Interior Designer', 'Inka Interiors', 'Sheena Hanks', '0438 308 672', 'info@inkainteriors.com.au', 'Basement Level, 811 Stanley St, Woolloongabba', '', '', '', 'Cunningham project designer'), ('Painting Contractor', 'Emerald Painting Company Pty Ltd', 'Anthony Des Johnston', '0410 949 719', 'des@emeraldpainting.com.au', '20 Warenna Crescent, Glenvale QLD 4350', '', '85 169 333 957', '', 'Industry contact'), ('Supplier', 'Dulux Australia', '', '07 5443 7255', '', 'Cnr Amaroo St & Maroochydore Rd, Maroochydore QLD 4558', '', '67 000 049 427', '', 'Supplier'), ('Builder', 'Greenrock Building', '', '', '', '', '', '', '30 Days', 'Client history'), ('Builder', 'Rejuvenate Group', '', '', '', '', '', '', '30 Days', 'School works'), ('Builder', 'Adlar Homes', '', '', '', 'Maroochydore', '', '', '30 Days', 'Client history'), ('Builder', 'Darren Hunt Homes', '', '', '', '', '', '', '30 Days', 'Custom homes'), ('Builder', 'Watherston Building', '', '', '', '', '', '', '30 Days', 'Custom homes'), ('Commercial Client', 'Stockland Aura', '', '', '', 'Aura', '', '', '', 'Commercial developments'), ('Commercial Builder', 'FDC Constructions', 'Simon Hawkins / Adam Pickering', '', '', '', '', '', '', 'Outreach'), ('Commercial Client', 'Comiskey Group', 'Paul / David / Rob & team', '', '', 'Sunshine Coast', '', '', '', 'Hospitality venue'), ('Education Client', 'Nambour State College', '', '', '', 'Nambour', '', '', '', 'School works'), ('Education Client', 'Currimundi State School', '', '', '', 'Currimundi', '', '', '', 'School works'), ('Education Client', 'Currimundi Special School', '', '', '', 'Currimindi', '', '', '', 'School works'), ('Education Client', 'Gympie South State School', '', '', '', 'Gympie', '', '', '', 'School works'), ('Education Client', 'Good Shepherd Lutheran School', '', '', '', '', '', '', '', 'School works')]

    employees = [('Bryce', '', '', 60.0, 66.0, 'Active', ''), ('Brodrick', '', '', 45.0, 49.5, 'Active', ''), ('Sol', '', '', 50.0, 55.0, 'Active', ''), ('Critter', '', '', 40.0, 44.0, 'Active', ''), ('Greg', '', '', 46.0, 50.6, 'Active', ''), ('Chris Nagy', '', '', 50.0, 55.0, 'Active', ''), ('Isaac', '', '', 46.0, 50.6, 'Active', ''), ('Rob Pullin', '', '', 45.0, 49.5, 'Active', ''), ('Ian', '', '', 46.0, 50.6, 'Active', ''), ('Tim', '', '', 45.0, 49.5, 'Active', ''), ('Anth', '', '', 35.0, 38.5, 'Active', ''), ('River', '', '', 32.5, 35.75, 'Active', ''), ('Dipper', '', '', 45.0, 49.5, 'Active', ''), ('Vlad 1', '', '', 45.0, 49.5, 'Active', ''), ('Vlad 2', '', '', 45.0, 49.5, 'Active', ''), ('Ryan', '', '', 45.0, 49.5, 'Active', '')]

    restored_builders = 0
    restored_employees = 0

    for row in builders:
        execute("""
            INSERT INTO builders_clients
            (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                type = excluded.type,
                contact_name = excluded.contact_name,
                phone = excluded.phone,
                email = excluded.email,
                address = excluded.address,
                qbcc = excluded.qbcc,
                abn = excluded.abn,
                terms = excluded.terms,
                notes = excluded.notes
        """, row)
        restored_builders += 1

    for row in employees:
        execute("""
            INSERT INTO employees
            (name, role, phone, base_hourly_rate, rate_plus_10, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                role = excluded.role,
                phone = excluded.phone,
                base_hourly_rate = excluded.base_hourly_rate,
                rate_plus_10 = excluded.rate_plus_10,
                status = excluded.status,
                notes = excluded.notes
        """, row)
        restored_employees += 1

    # Recreate employee login accounts where missing, without duplicating existing logins.
    try:
        seed_app_users()
    except Exception:
        pass

    return restored_builders, restored_employees

def builders_clients_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM builders_clients")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0

def employees_count():
    try:
        df = df_query("SELECT COUNT(*) AS 'count' FROM employees")
        if not df.empty:
            return int(df.iloc[0]["count"])
    except Exception:
        pass
    return 0

def normalise_username_value(username):
    return str(username or "").strip().lower()

def user_duplicate_summary():
    try:
        users = df_query("""
            SELECT u.id,
                   u.username,
                   u.role,
                   u.employee_id,
                   u.active,
                   COALESCE(e.name, '') AS employee_name,
                   u.notes
            FROM app_users u
            LEFT JOIN employees e ON e.id = u.employee_id
            ORDER BY LOWER(TRIM(u.username)), u.id
        """)
    except Exception:
        return pd.DataFrame()

    if users.empty:
        return users

    duplicate_ids = set()

    # Same username duplicates, ignoring case/spaces.
    username_groups = {}
    for _, row in users.iterrows():
        key = normalise_username_value(row["username"])
        if key:
            username_groups.setdefault(key, []).append(int(row["id"]))

    for ids in username_groups.values():
        if len(ids) > 1:
            duplicate_ids.update(ids)

    # Same linked employee duplicates.
    employee_groups = {}
    for _, row in users.iterrows():
        try:
            emp_id = int(row["employee_id"]) if row["employee_id"] not in [None, "", "None"] and pd.notna(row["employee_id"]) else None
        except Exception:
            emp_id = None
        if emp_id:
            employee_groups.setdefault(emp_id, []).append(int(row["id"]))

    for ids in employee_groups.values():
        if len(ids) > 1:
            duplicate_ids.update(ids)

    if not duplicate_ids:
        return pd.DataFrame()

    return users[users["id"].isin(duplicate_ids)].copy()

def clean_duplicate_user_accounts():
    """
    Deletes duplicate login rows.
    Keeps:
    - the currently logged-in user if they are in a duplicate group
    - otherwise an active admin where possible
    - otherwise an active account
    - otherwise the lowest id
    """
    users = df_query("""
        SELECT u.id,
               u.username,
               u.role,
               u.employee_id,
               u.active,
               COALESCE(e.name, '') AS employee_name,
               u.notes
        FROM app_users u
        LEFT JOIN employees e ON e.id = u.employee_id
        ORDER BY u.id
    """)

    if users.empty:
        return {"deleted": 0, "kept": 0, "skipped": 0}

    current_user = get_current_user() or {}
    current_user_id = int(current_user.get("id", -1))

    ids_to_delete = set()
    keep_ids = set()

    def choose_keep(group_df):
        # Keep current logged-in user if present.
        current_rows = group_df[group_df["id"].astype(int) == current_user_id]
        if not current_rows.empty:
            return int(current_rows.iloc[0]["id"])

        # Prefer active admin.
        active_admin = group_df[
            (group_df["role"].astype(str) == "admin") &
            (group_df["active"].fillna(0).astype(int) == 1)
        ]
        if not active_admin.empty:
            return int(active_admin.sort_values("id").iloc[0]["id"])

        # Prefer active account.
        active = group_df[group_df["active"].fillna(0).astype(int) == 1]
        if not active.empty:
            return int(active.sort_values("id").iloc[0]["id"])

        # Otherwise keep first row.
        return int(group_df.sort_values("id").iloc[0]["id"])

    # Duplicates by username.
    users["_username_key"] = users["username"].apply(normalise_username_value)
    for key, group in users.groupby("_username_key"):
        if key and len(group) > 1:
            keep_id = choose_keep(group)
            keep_ids.add(keep_id)
            for uid in group["id"].astype(int).tolist():
                if uid != keep_id:
                    ids_to_delete.add(uid)

    # Duplicates by linked employee.
    linked = users[users["employee_id"].notna()].copy()
    if not linked.empty:
        for emp_id, group in linked.groupby("employee_id"):
            if emp_id not in [None, "", "None"] and len(group) > 1:
                keep_id = choose_keep(group)
                keep_ids.add(keep_id)
                for uid in group["id"].astype(int).tolist():
                    if uid != keep_id:
                        ids_to_delete.add(uid)

    # Never delete current user.
    ids_to_delete.discard(current_user_id)

    # Never delete last active admin.
    admin_count_df = df_query("""
        SELECT COUNT(*) AS 'count'
        FROM app_users
        WHERE role = 'admin' AND active = 1
    """)
    active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0

    skipped = 0
    deleted = 0

    for uid in sorted(ids_to_delete):
        row_df = users[users["id"].astype(int) == int(uid)]
        if row_df.empty:
            continue

        row = row_df.iloc[0]
        is_active_admin = str(row["role"]) == "admin" and int(row["active"] or 0) == 1

        if is_active_admin and active_admin_count <= 1:
            skipped += 1
            continue

        try:
            execute("DELETE FROM app_users WHERE id = ?", (int(uid),))
            deleted += 1
            if is_active_admin:
                active_admin_count -= 1
        except Exception:
            # If deletion fails, safely disable it instead.
            try:
                execute("UPDATE app_users SET active = 0, notes = COALESCE(notes, '') || ' | duplicate disabled' WHERE id = ?", (int(uid),))
                skipped += 1
            except Exception:
                skipped += 1

    # Add unique indexes after cleanup so they cannot double up again.
    try:
        execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_username_lower_unique ON app_users (LOWER(TRIM(username)))")
    except Exception:
        pass

    try:
        execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_employee_unique ON app_users (employee_id) WHERE employee_id IS NOT NULL")
    except Exception:
        pass

    return {"deleted": deleted, "kept": len(keep_ids), "skipped": skipped}

def employee_linked_to_other_user(employee_id, selected_user_id):
    """
    Returns the other user account already linked to an employee, if any.
    Prevents app_users.employee_id unique constraint crashes.
    """
    if employee_id in [None, "", "None"]:
        return pd.DataFrame()

    try:
        return df_query("""
            SELECT id, username, role, active
            FROM app_users
            WHERE employee_id = ? AND id <> ?
            LIMIT 1
        """, (employee_id, selected_user_id))
    except Exception:
        return pd.DataFrame()

def safe_update_user_account(selected_user_id, username, role, employee_id, active, notes):
    """
    Safely updates app_users and prevents duplicate employee login links.
    Returns (success, message).
    """
    username = str(username or "").strip()

    if not username:
        return False, "Username cannot be blank."

    # Check username duplicate, ignoring case/spaces.
    existing_username = df_query("""
        SELECT id, username
        FROM app_users
        WHERE LOWER(TRIM(username)) = LOWER(TRIM(?)) AND id <> ?
        LIMIT 1
    """, (username, selected_user_id))

    if not existing_username.empty:
        return False, f"Username '{username}' is already used by another account."

    # Check employee duplicate link.
    other_link = employee_linked_to_other_user(employee_id, selected_user_id)
    if not other_link.empty:
        other = other_link.iloc[0]
        return False, (
            f"This employee is already linked to user account '{other['username']}'. "
            "Delete, disable, or unlink that duplicate account first, or choose 'No Employee Link'."
        )

    try:
        execute("""
            UPDATE app_users
            SET username = ?, role = ?, employee_id = ?, active = ?, notes = ?
            WHERE id = ?
        """, (username, role, employee_id, active, notes, selected_user_id))
        return True, "User updated."
    except Exception as e:
        message = str(e)
        if "idx_app_users_employee_unique" in message or "app_users_employee_id" in message or "duplicate key" in message:
            return False, (
                "That employee is already linked to another user account. "
                "Open User Access and use Clean Duplicate User Accounts, or select No Employee Link."
            )
        return False, f"User update failed: {message}"

def employee_has_job_history(employee_id):
    """
    Employees with wage/timesheet history should not be fully deleted because
    deleting them can break job costing history. They are marked Inactive instead.
    """
    linked = []

    for table, column, label in [
        ("wage_entries", "employee_id", "wage records"),
        ("timesheet_entries", "employee_id", "timesheets"),
    ]:
        try:
            if has_related_records(table, column, employee_id):
                linked.append(label)
        except Exception:
            pass

    return linked

def delete_employee_and_linked_users(employee_id):
    """
    Employee delete button behaviour:
    - Deletes linked app user login account(s).
    - Deletes the employee record only if there is no wage/timesheet history.
    - If history exists, the employee is marked Inactive.
    - Protects current logged-in user and last active admin.
    """
    result = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    try:
        employee_id = int(employee_id)
    except Exception:
        result["skipped"] += 1
        result["messages"].append("Invalid employee id.")
        return result

    emp_df = df_query("SELECT id, name, status FROM employees WHERE id = ? LIMIT 1", (employee_id,))
    if emp_df.empty:
        result["skipped"] += 1
        result["messages"].append(f"Employee id {employee_id} not found.")
        return result

    employee_name = str(emp_df.iloc[0]["name"])

    current_user = get_current_user() or {}
    try:
        current_user_id = int(current_user.get("id", -1))
    except Exception:
        current_user_id = -1

    linked_users = df_query("""
        SELECT id, username, role, active
        FROM app_users
        WHERE employee_id = ?
        ORDER BY id
    """, (employee_id,))

    for _, user_row in linked_users.iterrows():
        user_id = int(user_row["id"])
        username = str(user_row["username"])
        role = str(user_row["role"])
        active = int(user_row["active"] or 0)

        if user_id == current_user_id:
            result["skipped"] += 1
            result["messages"].append(f"Skipped linked user {username}: cannot delete the account currently logged in.")
            continue

        if role == "admin" and active == 1:
            admin_count_df = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE role = 'admin' AND active = 1")
            active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0
            if active_admin_count <= 1:
                result["skipped"] += 1
                result["messages"].append(f"Skipped linked user {username}: cannot delete the last active admin account.")
                continue

        try:
            execute("DELETE FROM app_users WHERE id = ?", (user_id,))
            result["deleted_users"] += 1
            result["messages"].append(f"Deleted linked user login: {username}")
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not delete linked user {username}: {e}")

    # If a protected linked user remains, do not fully delete the employee.
    remaining_users = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE employee_id = ?", (employee_id,))
    remaining_user_count = int(remaining_users.iloc[0]["count"]) if not remaining_users.empty else 0

    if remaining_user_count > 0:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(f"Marked {employee_name} inactive because a protected linked user account remains.")
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate {employee_name}: {e}")
        return result

    history = employee_has_job_history(employee_id)

    if history:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(
                f"Deleted linked login(s), but marked {employee_name} inactive because they have: " + ", ".join(history)
            )
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate {employee_name}: {e}")
    else:
        try:
            execute("DELETE FROM employees WHERE id = ?", (employee_id,))
            result["deleted_employee"] += 1
            result["messages"].append(f"Deleted employee record: {employee_name}")
        except Exception as e:
            try:
                execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
                result["deactivated_employee"] += 1
                result["messages"].append(f"Could not fully delete {employee_name}, so marked inactive instead. Reason: {e}")
            except Exception:
                result["skipped"] += 1
                result["messages"].append(f"Could not delete or deactivate {employee_name}: {e}")

    return result

def delete_user_and_linked_employee(user_id):
    """
    User delete button behaviour:
    - Deletes the app user login account.
    - If linked to an employee, also deletes that employee if there is no wage/timesheet history.
    - If history exists, the employee is marked Inactive.
    - Protects current logged-in user and last active admin.
    """
    result = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    try:
        user_id = int(user_id)
    except Exception:
        result["skipped"] += 1
        result["messages"].append("Invalid user id.")
        return result

    user_df = df_query("""
        SELECT id, username, role, employee_id, active
        FROM app_users
        WHERE id = ?
        LIMIT 1
    """, (user_id,))

    if user_df.empty:
        result["skipped"] += 1
        result["messages"].append(f"User id {user_id} not found.")
        return result

    user_row = user_df.iloc[0]
    username = str(user_row["username"])
    role = str(user_row["role"])
    active = int(user_row["active"] or 0)

    try:
        employee_id = int(user_row["employee_id"]) if user_row["employee_id"] not in [None, "", "None"] and pd.notna(user_row["employee_id"]) else None
    except Exception:
        employee_id = None

    current_user = get_current_user() or {}
    try:
        current_user_id = int(current_user.get("id", -1))
    except Exception:
        current_user_id = -1

    if user_id == current_user_id:
        result["skipped"] += 1
        result["messages"].append(f"Skipped {username}: cannot delete the account currently logged in.")
        return result

    if role == "admin" and active == 1:
        admin_count_df = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE role = 'admin' AND active = 1")
        active_admin_count = int(admin_count_df.iloc[0]["count"]) if not admin_count_df.empty else 0
        if active_admin_count <= 1:
            result["skipped"] += 1
            result["messages"].append(f"Skipped {username}: cannot delete the last active admin account.")
            return result

    try:
        execute("DELETE FROM app_users WHERE id = ?", (user_id,))
        result["deleted_users"] += 1
        result["messages"].append(f"Deleted user login: {username}")
    except Exception as e:
        result["skipped"] += 1
        result["messages"].append(f"Could not delete user {username}: {e}")
        return result

    if not employee_id:
        return result

    emp_df = df_query("SELECT id, name, status FROM employees WHERE id = ? LIMIT 1", (employee_id,))
    if emp_df.empty:
        result["messages"].append("Linked employee record was not found.")
        return result

    employee_name = str(emp_df.iloc[0]["name"])

    # If other user accounts still link to this employee, do not fully delete employee.
    other_users = df_query("SELECT COUNT(*) AS 'count' FROM app_users WHERE employee_id = ?", (employee_id,))
    other_user_count = int(other_users.iloc[0]["count"]) if not other_users.empty else 0

    if other_user_count > 0:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(f"Marked linked employee {employee_name} inactive because another login still references them.")
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate linked employee {employee_name}: {e}")
        return result

    history = employee_has_job_history(employee_id)

    if history:
        try:
            execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
            result["deactivated_employee"] += 1
            result["messages"].append(f"Marked linked employee {employee_name} inactive because they have: " + ", ".join(history))
        except Exception as e:
            result["skipped"] += 1
            result["messages"].append(f"Could not deactivate linked employee {employee_name}: {e}")
    else:
        try:
            execute("DELETE FROM employees WHERE id = ?", (employee_id,))
            result["deleted_employee"] += 1
            result["messages"].append(f"Deleted linked employee record: {employee_name}")
        except Exception as e:
            try:
                execute("UPDATE employees SET status = 'Inactive' WHERE id = ?", (employee_id,))
                result["deactivated_employee"] += 1
                result["messages"].append(f"Could not fully delete linked employee {employee_name}, so marked inactive instead. Reason: {e}")
            except Exception:
                result["skipped"] += 1
                result["messages"].append(f"Could not delete or deactivate linked employee {employee_name}: {e}")

    return result

def delete_or_deactivate_selected_employees(employee_ids):
    """
    Bulk employee delete:
    Deletes linked user login(s) too. If the employee has job history,
    the login is deleted and the employee is marked Inactive.
    """
    combined = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    if not employee_ids:
        combined["messages"].append("No employees selected.")
        return combined

    for emp_id in employee_ids:
        result = delete_employee_and_linked_users(emp_id)
        for key in ["deleted_users", "deleted_employee", "deactivated_employee", "skipped"]:
            combined[key] += result.get(key, 0)
        combined["messages"].extend(result.get("messages", []))

    return combined

def delete_selected_user_accounts(user_ids):
    """
    Bulk user delete:
    Deletes selected user login(s) and linked employee record(s) where safe.
    If linked employee has job history, employee is marked Inactive.
    """
    combined = {
        "deleted_users": 0,
        "deleted_employee": 0,
        "deactivated_employee": 0,
        "skipped": 0,
        "messages": [],
    }

    if not user_ids:
        combined["messages"].append("No user accounts selected.")
        return combined

    for uid in user_ids:
        result = delete_user_and_linked_employee(uid)
        for key in ["deleted_users", "deleted_employee", "deactivated_employee", "skipped"]:
            combined[key] += result.get(key, 0)
        combined["messages"].extend(result.get("messages", []))

    return combined

def jc_float(value, default=0.0):
    try:
        if value is None or value == "" or pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)

def jc_percent(numerator, denominator):
    denominator = jc_float(denominator)
    if denominator == 0:
        return 0.0
    return round((jc_float(numerator) / denominator) * 100, 2)

def jc_parse_date(value):
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()[:10]
    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            pass
    return None

def jc_business_days(start_value, end_value):
    start = jc_parse_date(start_value)
    end = jc_parse_date(end_value)
    if not start or not end or end < start:
        return 0
    days = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days

def jc_add_business_days(start_date, days):
    current = start_date or jobhub_today()
    added = 0
    days = int(max(days, 0))
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current

def jc_month_label(value):
    d = jc_parse_date(value)
    return d.strftime("%Y-%m") if d else "Unscheduled"

def job_cost_summary_dataframe():
    jobs = df_query("""
        SELECT j.id AS 'job_id',
               j.job_no AS 'Job No',
               j.job_name AS 'Job Name',
               COALESCE(bc.name, '') AS 'Builder / Client',
               j.site_address AS 'Site Address',
               j.status AS 'Status',
               j.leading_hand AS 'Leading Hand',
               j.start_date AS 'Start Date',
               j.end_date AS 'End Date',
               COALESCE(j.contract_value, 0) AS 'Contract Value',
               j.notes AS 'Notes'
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        ORDER BY j.job_no
    """)

    if jobs.empty:
        return jobs

    materials = df_query("""
        SELECT m.job_id,
               COALESCE(SUM(COALESCE(m.qty_required, 0) * COALESCE(m.custom_unit_price, p.price_ex_gst, 0)), 0) AS 'Actual Material Cost',
               COALESCE(SUM(COALESCE(m.qty_required, 0)), 0) AS 'Material Qty Required',
               COALESCE(SUM(COALESCE(m.qty_received, 0)), 0) AS 'Material Qty Received',
               COUNT(*) AS 'Material Lines'
        FROM material_entries m
        LEFT JOIN products p ON p.id = m.product_id
        GROUP BY m.job_id
    """)

    wages = df_query("""
        SELECT w.job_id,
               COALESCE(SUM(COALESCE(w.hours, 0)), 0) AS 'Wage Hours',
               COALESCE(SUM(COALESCE(w.hours, 0) * COALESCE(e.rate_plus_10, e.base_hourly_rate, 0)), 0) AS 'Actual Labour Cost',
               COUNT(*) AS 'Wage Lines'
        FROM wage_entries w
        LEFT JOIN employees e ON e.id = w.employee_id
        GROUP BY w.job_id
    """)

    timesheets = df_query("""
        SELECT job_id,
               COALESCE(SUM(COALESCE(total_hours, 0)), 0) AS 'Timesheet Hours',
               COUNT(*) AS 'Timesheet Lines'
        FROM timesheet_entries
        GROUP BY job_id
    """)

    estimates = df_query("""
        SELECT e.job_id,
               e.estimate_no AS 'Latest Estimate',
               e.revision AS 'Estimate Revision',
               COALESCE(e.labour_hours, 0) AS 'Estimated Labour Hours',
               COALESCE(e.labour_rate, 0) AS 'Estimated Labour Rate',
               COALESCE(e.material_allowance, 0) AS 'Estimated Materials',
               COALESCE(e.access_equipment_allowance, 0) AS 'Estimated Access / Equipment',
               COALESCE(e.subcontractor_allowance, 0) AS 'Estimated Subcontractor',
               COALESCE(e.sundries_allowance, 0) AS 'Estimated Sundries',
               COALESCE(e.total_ex_gst, 0) AS 'Estimate Total Ex GST',
               COALESCE(e.total_inc_gst, 0) AS 'Estimate Total Inc GST'
        FROM estimate_working_sheets e
        JOIN (
            SELECT job_id, MAX(id) AS max_id
            FROM estimate_working_sheets
            GROUP BY job_id
        ) latest ON latest.max_id = e.id
    """)

    df = jobs.copy()
    for extra in [materials, wages, timesheets, estimates]:
        if extra is not None and not extra.empty:
            df = df.merge(extra, on="job_id", how="left")

    number_cols = [
        "Contract Value", "Actual Material Cost", "Material Qty Required", "Material Qty Received",
        "Material Lines", "Wage Hours", "Actual Labour Cost", "Wage Lines", "Timesheet Hours",
        "Timesheet Lines", "Estimated Labour Hours", "Estimated Labour Rate", "Estimated Materials",
        "Estimated Access / Equipment", "Estimated Subcontractor", "Estimated Sundries",
        "Estimate Total Ex GST", "Estimate Total Inc GST"
    ]

    for col in number_cols:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0)

    for col in ["Latest Estimate", "Estimate Revision"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    df["Actual Labour Hours"] = df["Wage Hours"]
    df["Total Actual Cost"] = df["Actual Material Cost"] + df["Actual Labour Cost"]
    df["Gross Profit"] = df["Contract Value"] - df["Total Actual Cost"]
    df["Gross Profit %"] = df.apply(lambda r: jc_percent(r["Gross Profit"], r["Contract Value"]), axis=1)
    df["Cost to Date %"] = df.apply(lambda r: jc_percent(r["Total Actual Cost"], r["Contract Value"]), axis=1)
    df["Remaining Labour Hours"] = (df["Estimated Labour Hours"] - df["Timesheet Hours"]).clip(lower=0)
    df["Working Days Scheduled"] = df.apply(lambda r: jc_business_days(r["Start Date"], r["End Date"]), axis=1)
    df["Forecast Month"] = df["Start Date"].apply(jc_month_label)
    return df

def job_costs_forecasting_page():
    st.header("Job Costs / Forecasting")
    st.caption("Job cost breakdowns, financial forecasting and labour/schedule forecasting.")

    df = job_cost_summary_dataframe()
    if df.empty:
        st.info("No jobs found yet.")
        return

    section = st.radio(
        "Section",
        ["Selected Job Breakdown", "Financial Forecast", "Scheduling Forecast", "Export"],
        horizontal=True,
        key="job_cost_forecast_section",
    )

    if section == "Selected Job Breakdown":
        job_options = {f"{r['Job No']} - {r['Job Name']}": int(r["job_id"]) for _, r in df.iterrows()}
        selected = st.selectbox("Select Job", list(job_options.keys()), key="job_cost_selected")
        row = df[df["job_id"].astype(int) == int(job_options[selected])].iloc[0]

        st.subheader(f"{row['Job No']} - {row['Job Name']}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Contract Value", f"${jc_float(row['Contract Value']):,.2f}")
        c2.metric("Actual Cost to Date", f"${jc_float(row['Total Actual Cost']):,.2f}")
        c3.metric("Gross Profit", f"${jc_float(row['Gross Profit']):,.2f}")
        c4.metric("Gross Profit %", f"{jc_float(row['Gross Profit %']):.2f}%")

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Material Cost", f"${jc_float(row['Actual Material Cost']):,.2f}")
        c6.metric("Labour Cost", f"${jc_float(row['Actual Labour Cost']):,.2f}")
        c7.metric("Timesheet Hours", f"{jc_float(row['Timesheet Hours']):.2f}")
        c8.metric("Remaining Est. Hours", f"{jc_float(row['Remaining Labour Hours']):.2f}")

        st.markdown("### Forecast Inputs")
        i1, i2, i3, i4 = st.columns(4)
        target_gp = i1.number_input("Target GP %", min_value=0.0, max_value=100.0, value=35.0, step=1.0)
        labour_cost_hour = i2.number_input("Labour Cost / Hour", min_value=0.0, value=120.0, step=5.0)
        crew_size = i3.number_input("Crew Size", min_value=1.0, value=3.0, step=1.0)
        hours_day = i4.number_input("Hours / Person / Day", min_value=1.0, value=8.0, step=0.5)

        target_cost = jc_float(row["Contract Value"]) * (1 - target_gp / 100)
        remaining_cost_budget = max(target_cost - jc_float(row["Total Actual Cost"]), 0)
        remaining_by_budget = remaining_cost_budget / labour_cost_hour if labour_cost_hour else 0
        remaining_hours = jc_float(row["Remaining Labour Hours"]) or remaining_by_budget
        daily_capacity = crew_size * hours_day
        days_required = int((remaining_hours + daily_capacity - 0.001) // daily_capacity) if daily_capacity else 0
        if daily_capacity and remaining_hours % daily_capacity:
            days_required += 1
        finish_date = jc_add_business_days(jobhub_today(), days_required)

        forecast_cost = jc_float(row["Total Actual Cost"]) + remaining_hours * labour_cost_hour
        forecast_profit = jc_float(row["Contract Value"]) - forecast_cost
        forecast_gp = jc_percent(forecast_profit, row["Contract Value"])

        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Remaining Cost Budget", f"${remaining_cost_budget:,.2f}")
        f2.metric("Forecast Remaining Hours", f"{remaining_hours:,.2f}")
        f3.metric("Forecast Finish", str(finish_date))
        f4.metric("Forecast GP %", f"{forecast_gp:.2f}%")

        if forecast_gp < target_gp:
            st.warning("Forecast is below target. Check labour, materials, scope changes and variations.")
        else:
            st.success("Forecast is at or above target based on these inputs.")

        detail_cols = [
            "Job No", "Job Name", "Builder / Client", "Status", "Leading Hand", "Start Date", "End Date",
            "Contract Value", "Actual Material Cost", "Actual Labour Cost", "Total Actual Cost",
            "Gross Profit", "Gross Profit %", "Estimate Total Ex GST", "Estimated Labour Hours",
            "Timesheet Hours", "Remaining Labour Hours", "Working Days Scheduled"
        ]
        st.dataframe(pd.DataFrame([row[detail_cols]]), width="stretch", hide_index=True)

    elif section == "Financial Forecast":
        st.subheader("Financial Forecast by Job")
        statuses = ["All"] + sorted([str(x) for x in df["Status"].fillna("").unique() if str(x).strip()])
        selected_status = st.selectbox("Status Filter", statuses)
        filtered = df.copy()
        if selected_status != "All":
            filtered = filtered[filtered["Status"].astype(str) == selected_status]

        total_contract = jc_float(filtered["Contract Value"].sum()) if not filtered.empty else 0
        total_cost = jc_float(filtered["Total Actual Cost"].sum()) if not filtered.empty else 0
        total_profit = total_contract - total_cost
        total_gp = jc_percent(total_profit, total_contract)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Contract Value", f"${total_contract:,.2f}")
        c2.metric("Cost to Date", f"${total_cost:,.2f}")
        c3.metric("Gross Profit", f"${total_profit:,.2f}")
        c4.metric("Gross Profit %", f"{total_gp:.2f}%")

        show_cols = [
            "Job No", "Job Name", "Builder / Client", "Status", "Start Date", "End Date",
            "Contract Value", "Total Actual Cost", "Gross Profit", "Gross Profit %",
            "Actual Material Cost", "Actual Labour Cost", "Timesheet Hours", "Estimate Total Ex GST"
        ]
        st.dataframe(filtered[[c for c in show_cols if c in filtered.columns]], width="stretch", hide_index=True)

        monthly = filtered.groupby("Forecast Month", dropna=False).agg({
            "Contract Value": "sum",
            "Total Actual Cost": "sum",
            "Gross Profit": "sum",
            "Timesheet Hours": "sum",
        }).reset_index()
        if not monthly.empty:
            monthly["Gross Profit %"] = monthly.apply(lambda r: jc_percent(r["Gross Profit"], r["Contract Value"]), axis=1)
            st.markdown("### Forecast by Month")
            st.dataframe(monthly, width="stretch", hide_index=True)

    elif section == "Scheduling Forecast":
        st.subheader("Scheduling / Labour Forecast")
        hours_day = st.number_input("Default Hours / Person / Day", min_value=1.0, value=8.0, step=0.5)
        sched = df.copy()
        sched["Budget Labour Hours"] = sched["Estimated Labour Hours"]
        sched["Budget Labour Hours"] = sched.apply(
            lambda r: jc_float(r["Budget Labour Hours"]) if jc_float(r["Budget Labour Hours"]) > 0 else jc_float(r["Contract Value"]) / 120,
            axis=1,
        )
        sched["Remaining Hours"] = (sched["Budget Labour Hours"] - sched["Timesheet Hours"]).clip(lower=0)
        sched["Remaining Painter Days"] = (sched["Remaining Hours"] / hours_day).round(2)
        sched["Required Painters"] = sched.apply(
            lambda r: round(jc_float(r["Budget Labour Hours"]) / (jc_float(r["Working Days Scheduled"]) * hours_day), 2)
            if jc_float(r["Working Days Scheduled"]) > 0 else 0,
            axis=1,
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("Remaining Labour Hours", f"{jc_float(sched['Remaining Hours'].sum()):,.2f}")
        c2.metric("Remaining Painter Days", f"{jc_float(sched['Remaining Painter Days'].sum()):,.2f}")
        c3.metric("Jobs in Forecast", len(sched))

        cols = [
            "Job No", "Job Name", "Status", "Leading Hand", "Start Date", "End Date",
            "Working Days Scheduled", "Budget Labour Hours", "Timesheet Hours",
            "Remaining Hours", "Required Painters", "Remaining Painter Days"
        ]
        st.dataframe(sched[[c for c in cols if c in sched.columns]], width="stretch", hide_index=True)

    else:
        st.subheader("Export Job Cost / Forecast Data")
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df.drop(columns=["job_id"], errors="ignore").to_excel(writer, index=False, sheet_name="Job Forecast")
            monthly = df.groupby("Forecast Month", dropna=False).agg({
                "Contract Value": "sum",
                "Total Actual Cost": "sum",
                "Gross Profit": "sum",
                "Timesheet Hours": "sum",
            }).reset_index()
            if not monthly.empty:
                monthly["Gross Profit %"] = monthly.apply(lambda r: jc_percent(r["Gross Profit"], r["Contract Value"]), axis=1)
            monthly.to_excel(writer, index=False, sheet_name="Monthly Forecast")
            for ws in writer.book.worksheets:
                for column_cells in ws.columns:
                    max_len = 0
                    col_letter = column_cells[0].column_letter
                    for cell in column_cells:
                        value = "" if cell.value is None else str(cell.value)
                        max_len = max(max_len, len(value))
                    ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 45)
        output.seek(0)
        st.download_button(
            "Download Job Cost / Forecast Excel",
            data=output.getvalue(),
            file_name="PB_JobHub_Job_Cost_Forecast.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
