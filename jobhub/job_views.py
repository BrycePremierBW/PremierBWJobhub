"""Job lookup, linked information and job folder page renderers.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *


def safe_df_query(sql, params=()):
    try:
        return df_query(sql, params)
    except Exception:
        return pd.DataFrame()

def go_to_linked_job_view(job_id=None, builder_id=None, mode=None):
    if job_id is not None:
        st.session_state["linked_view_selected_job_id"] = int(job_id)
    if builder_id is not None:
        st.session_state["linked_view_selected_builder_id"] = int(builder_id)
    if mode:
        st.session_state["linked_view_mode"] = mode

    # Job lookup now lives inside the Control Centre.
    # Use pending navigation keys so Streamlit widget state is changed before widgets are instantiated on rerun.
    st.session_state["go_to_menu"] = "Control Centre"
    st.session_state["go_to_control_centre_section"] = "Job Lookup / Links"
    st.rerun()

def job_lookup_dataframe(include_archived=True):
    where_clause = "" if include_archived else "WHERE COALESCE(j.status, '') != 'Archived'"
    return df_query(f"""
        SELECT j.id AS job_id,
               j.job_no AS "Job No",
               j.job_name AS "Job Name",
               COALESCE(bc.id, 0) AS builder_id,
               COALESCE(bc.name, '') AS "Builder / Client",
               COALESCE(bc.contact_name, '') AS "Contact",
               COALESCE(bc.phone, '') AS "Phone",
               COALESCE(bc.email, '') AS "Email",
               COALESCE(j.site_address, '') AS "Site Address",
               COALESCE(j.status, '') AS "Status",
               COALESCE(j.leading_hand, '') AS "Leading Hand",
               COALESCE(j.start_date, '') AS "Start Date",
               COALESCE(j.end_date, '') AS "End Date",
               COALESCE(j.contract_value, 0) AS "Contract Value"
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        {where_clause}
        ORDER BY j.job_no
    """)

def make_job_label(row):
    return (
        f"{row['Job No']} - {row['Job Name']} | "
        f"{row['Builder / Client']} | {row['Site Address']} | {row['Status']}"
    )

def select_job_from_dataframe(jobs_df, label, key, default_job_id=None):
    if jobs_df.empty:
        st.info("No matching jobs found.")
        return None

    job_map = {make_job_label(row): int(row["job_id"]) for _, row in jobs_df.iterrows()}
    labels = list(job_map.keys())

    default_index = 0
    if default_job_id is not None:
        for i, item in enumerate(labels):
            if int(job_map[item]) == int(default_job_id):
                default_index = i
                break

    selected = st.selectbox(label, labels, index=default_index, key=key)
    return job_map[selected]

def display_job_table_with_open_button(jobs_df, table_label, key_prefix):
    if jobs_df.empty:
        st.info("No matching jobs found.")
        return None

    visible_df = jobs_df.drop(columns=["job_id", "builder_id"], errors="ignore")
    st.dataframe(visible_df, width="stretch", hide_index=True)

    selected_job_id = select_job_from_dataframe(
        jobs_df,
        f"Open one of these jobs - {table_label}",
        key=f"{key_prefix}_job_select"
    )

    if st.button("Open selected job and all linked info", key=f"{key_prefix}_open_job"):
        go_to_linked_job_view(job_id=selected_job_id, mode="Open Job")

    return selected_job_id

def render_job_linked_info(job_id, expanded=True):
    job_id = int(job_id)

    job_details = safe_df_query("""
        SELECT j.job_no AS "Job No",
               j.job_name AS "Job Name",
               COALESCE(bc.name, '') AS "Builder / Client",
               COALESCE(bc.contact_name, '') AS "Contact",
               COALESCE(bc.phone, '') AS "Phone",
               COALESCE(bc.email, '') AS "Email",
               COALESCE(bc.terms, '') AS "Terms",
               COALESCE(j.site_address, '') AS "Site Address",
               COALESCE(j.status, '') AS "Status",
               COALESCE(j.leading_hand, '') AS "Leading Hand",
               COALESCE(j.start_date, '') AS "Start Date",
               COALESCE(j.end_date, '') AS "End Date",
               COALESCE(j.contract_value, 0) AS "Contract Value Ex GST",
               COALESCE(j.notes, '') AS "Notes"
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.id = ?
    """, (job_id,))

    if job_details.empty:
        st.warning("Selected job could not be found.")
        return

    job_no = str(job_details.iloc[0]["Job No"])
    job_name = str(job_details.iloc[0]["Job Name"])
    pb_job_header(job_details.iloc[0])
    material_details = safe_df_query("""
    
        SELECT m.id AS "ID",
               COALESCE(NULLIF(m.custom_product_code, ''), p.product_code, '') AS "Product Code",
               COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS "Product Name",
               COALESCE(NULLIF(m.supplier, ''), NULLIF(m.custom_supplier, ''), p.supplier, '') AS "Supplier",
               COALESCE(NULLIF(m.custom_unit, ''), p.unit, '') AS "Unit",
               COALESCE(m.custom_unit_price, p.price_ex_gst, 0) AS "Unit Price Ex GST",
               COALESCE(NULLIF(m.custom_colour, ''), '') AS "Colour / Finish",
               m.qty_required AS "Qty Required",
               m.qty_received AS "Qty Received",
               ROUND(CAST((COALESCE(m.custom_unit_price, p.price_ex_gst, 0) * COALESCE(m.qty_required, 0)) AS numeric), 2) AS "Total Cost Ex GST",
               m.date_ordered AS "Date Ordered",
               m.supplier AS "Supplier Override",
               m.notes AS "Notes"
        FROM material_entries m
        LEFT JOIN products p ON p.id = m.product_id
        WHERE m.job_id = ?
        ORDER BY m.id DESC
    """, (job_id,))

    imported_materials = safe_df_query("""
        SELECT id AS "ID",
               product AS "Product",
               colour AS "Colour",
               qty_required AS "Qty Required",
               qty_loaded AS "Qty Loaded",
               source_file AS "Source File",
               imported_at AS "Imported At",
               notes AS "Notes"
        FROM imported_material_entries
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    wage_details = safe_df_query("""
        SELECT w.id AS "ID",
               COALESCE(NULLIF(w.period_type, ''), 'Single Day') AS "Period",
               COALESCE(NULLIF(w.period_start, ''), w.work_date) AS "From Date",
               COALESCE(NULLIF(w.period_end, ''), w.work_date) AS "Week Ending / To Date",
               e.name AS "Employee",
               w.hours AS "Hours",
               e.base_hourly_rate AS "Base Rate",
               e.rate_plus_10 AS "Rate + 10%",
               ROUND(w.hours * e.rate_plus_10, 2) AS "Total Wage Cost",
               w.notes AS "Notes"
        FROM wage_entries w
        JOIN employees e ON e.id = w.employee_id
        WHERE w.job_id = ?
        ORDER BY COALESCE(NULLIF(w.period_start, ''), w.work_date) DESC, w.id DESC
    """, (job_id,))

    timesheet_details = safe_df_query("""
        SELECT t.id AS "ID",
               COALESCE(NULLIF(t.period_type, ''), 'Single Day') AS "Period",
               COALESCE(NULLIF(t.period_start, ''), t.work_date) AS "From Date",
               COALESCE(NULLIF(t.period_end, ''), t.work_date) AS "Week Ending / To Date",
               e.name AS "Employee",
               t.start_time AS "Start",
               t.finish_time AS "Finish",
               t.break_minutes AS "Break Minutes",
               t.total_hours AS "Hours",
               t.work_type AS "Work Type",
               COALESCE(t.status, 'Submitted') AS "Status",
               t.notes AS "Notes"
        FROM timesheet_entries t
        JOIN employees e ON e.id = t.employee_id
        WHERE t.job_id = ?
        ORDER BY COALESCE(NULLIF(t.period_start, ''), t.work_date) DESC, t.id DESC
    """, (job_id,))

    estimate_summary = safe_df_query("""
        SELECT estimate_no AS "Estimate No",
               revision AS "Revision",
               estimate_date AS "Date",
               status AS "Status",
               labour_hours AS "Labour Hours",
               labour_rate AS "Labour Rate",
               material_allowance AS "Material Allowance",
               access_equipment_allowance AS "Access / Equipment",
               subcontractor_allowance AS "Subcontractor",
               sundries_allowance AS "Sundries",
               margin_percent AS "Margin %",
               contingency_percent AS "Contingency %",
               total_ex_gst AS "Total Ex GST",
               gst_amount AS "GST",
               total_inc_gst AS "Total Inc GST",
               notes AS "Notes"
        FROM estimate_working_sheets
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    estimate_lines = safe_df_query("""
        SELECT e.estimate_no AS "Estimate No",
               l.section AS "Section",
               l.item_description AS "Description",
               l.qty AS "Qty",
               l.unit AS "Unit",
               l.unit_rate AS "Unit Rate",
               l.line_total AS "Line Total",
               l.notes AS "Notes"
        FROM estimate_line_items l
        JOIN estimate_working_sheets e ON e.id = l.estimate_id
        WHERE e.job_id = ?
        ORDER BY e.id DESC, l.id ASC
    """, (job_id,))

    equipment_master = safe_df_query("""
        SELECT id AS "ID",
               equipment_item AS "Equipment Item",
               category AS "Category",
               serial_no AS "Serial No",
               date_out AS "Date Out",
               date_in AS "Date In",
               condition_out AS "Condition Out",
               condition_in AS "Condition In",
               assigned_to AS "Assigned To",
               notes AS "Notes"
        FROM equipment_entries
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    equipment_detail = safe_df_query("""
        SELECT r.id AS "ID",
               i.category AS "Category",
               i.item_name AS "Item",
               r.qty_required AS "Qty Required",
               r.qty_taken AS "Qty Taken",
               r.qty_returned AS "Qty Returned",
               CASE WHEN r.is_required = 1 THEN 'Yes' ELSE '' END AS "Required",
               CASE WHEN r.is_packed = 1 THEN 'Yes' ELSE '' END AS "Packed",
               CASE WHEN r.is_returned = 1 THEN 'Yes' ELSE '' END AS "Returned",
               r.date_out AS "Date Out",
               r.date_in AS "Date In",
               r.taken_by AS "Taken By",
               r.returned_by AS "Returned By",
               r.condition_out AS "Condition Out",
               r.condition_in AS "Condition In",
               r.notes AS "Notes"
        FROM equipment_checklist_records r
        JOIN equipment_checklist_items i ON i.id = r.checklist_item_id
        WHERE r.job_id = ?
        ORDER BY i.category, i.item_name
    """, (job_id,))

    budget_df = safe_df_query("""
        SELECT quoted_labour_hours AS "Quoted Labour Hours",
               quoted_labour_cost AS "Quoted Labour Cost",
               quoted_materials AS "Quoted Materials",
               quoted_access_equipment AS "Access / Equipment",
               quoted_subcontractors AS "Subcontractors",
               quoted_sundries AS "Sundries",
               target_gp_percent AS "Target GP %",
               locked_at AS "Locked At",
               locked_by AS "Locked By",
               notes AS "Notes"
        FROM job_budgets
        WHERE job_id = ?
    """, (job_id,))

    variations_df = safe_df_query("""
        SELECT variation_no AS "Variation No",
               description AS "Description",
               reason AS "Reason",
               amount_ex_gst AS "Amount Ex GST",
               status AS "Status",
               sent_date AS "Sent Date",
               approved_date AS "Approved Date",
               approved_by AS "Approved By",
               notes AS "Notes"
        FROM job_variations
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    claims_df = safe_df_query("""
        SELECT claim_no AS "Claim / Invoice No",
               description AS "Description",
               amount_ex_gst AS "Amount Ex GST",
               invoice_date AS "Invoice Date",
               due_date AS "Due Date",
               paid_date AS "Paid Date",
               status AS "Status",
               notes AS "Notes"
        FROM invoice_claims
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    schedule_df = safe_df_query("""
        SELECT COALESCE(NULLIF(s.period_type, ''), 'Single Day') AS "Schedule Type",
               COALESCE(NULLIF(s.period_start, ''), s.schedule_date) AS "From Date",
               COALESCE(NULLIF(s.period_end, ''), s.schedule_date) AS "Week Ending / To Date",
               e.name AS "Staff Member",
               s.start_time AS "Start",
               s.finish_time AS "Finish",
               COALESCE(s.planned_hours, 0) AS "Planned Hours",
               s.site_role AS "Role",
               s.notes AS "Notes"
        FROM staff_schedule s
        JOIN employees e ON e.id = s.employee_id
        WHERE s.job_id = ?
        ORDER BY COALESCE(NULLIF(s.period_start, ''), s.schedule_date) DESC, e.name
    """, (job_id,))

    photos_meta = safe_df_query("""
        SELECT id AS "Photo ID",
               photo_name AS "Photo Name",
               category AS "Category",
               caption AS "Caption",
               uploaded_by AS "Uploaded By",
               uploaded_at AS "Uploaded At",
               notes AS "Notes"
        FROM job_photos
        WHERE job_id = ?
        ORDER BY uploaded_at DESC, id DESC
    """, (job_id,))

    photos_full = safe_df_query("""
        SELECT id, photo_name, photo_type, photo_data, category, caption, uploaded_by, uploaded_at, notes
        FROM job_photos
        WHERE job_id = ?
        ORDER BY uploaded_at DESC, id DESC
    """, (job_id,))

    material_total = float(material_details["Total Cost Ex GST"].fillna(0).sum()) if not material_details.empty else 0.0
    wage_total = float(wage_details["Total Wage Cost"].fillna(0).sum()) if not wage_details.empty else 0.0
    timesheet_hours = float(timesheet_details["Hours"].fillna(0).sum()) if not timesheet_details.empty else 0.0
    contract_value = float(job_details.iloc[0]["Contract Value Ex GST"] or 0)
    approved_variations = float(variations_df[variations_df["Status"].astype(str).str.lower() == "approved"]["Amount Ex GST"].fillna(0).sum()) if not variations_df.empty else 0.0
    adjusted_contract = contract_value + approved_variations
    gross_position = adjusted_contract - material_total - wage_total

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Contract Ex GST", f"${contract_value:,.2f}")
    m2.metric("Approved Variations", f"${approved_variations:,.2f}")
    m3.metric("Materials", f"${material_total:,.2f}")
    m4.metric("Wages", f"${wage_total:,.2f}")
    m5.metric("Basic Position", f"${gross_position:,.2f}")

    tab_summary, tab_documents, tab_import, tab_progress, tab_3d_model, tab_costs, tab_materials, tab_wages, tab_equipment, tab_control, tab_photos = st.tabs([
        "Summary",
        "Plans / Docs",
        "Import Take-off / Model",
        "Progress / Billing",
        "3D Model",
        "Costs & Estimates",
        "Materials",
        "Wages & Timesheets",
        "Equipment",
        "Control / Claims",
        "Photos",
    ])

    with tab_summary:
        st.markdown("### Job Details")
        st.dataframe(job_details, width="stretch", hide_index=True)

        st.markdown("### Staff Schedule")
        if schedule_df.empty:
            st.info("No staff schedule entries saved for this job.")
        else:
            st.dataframe(schedule_df, width="stretch", hide_index=True)

    with tab_documents:
        render_job_documents_panel(job_id, allow_upload=True, allow_delete=True, key_prefix="linked_job_docs")

    with tab_import:
        takeoff_import_page(default_job_id=job_id)

    with tab_progress:
        progress_billing_model_page(default_job_id=job_id)

    with tab_3d_model:
        three_d_model_viewer_page(default_job_id=job_id)

    with tab_costs:
        c1, c2, c3 = st.columns(3)
        c1.metric("Timesheet Hours", f"{timesheet_hours:.2f}")
        c2.metric("Adjusted Contract", f"${adjusted_contract:,.2f}")
        c3.metric("Materials + Wages", f"${(material_total + wage_total):,.2f}")

        st.markdown("### Budget Lock-In")
        if budget_df.empty:
            st.info("No budget lock-in saved for this job.")
        else:
            st.dataframe(budget_df, width="stretch", hide_index=True)

        st.markdown("### Estimate Summary")
        if estimate_summary.empty:
            st.info("No estimate working sheets saved for this job.")
        else:
            st.dataframe(estimate_summary, width="stretch", hide_index=True)

        st.markdown("### Estimate Line Items")
        if estimate_lines.empty:
            st.info("No estimate line items saved for this job.")
        else:
            st.dataframe(estimate_lines, width="stretch", hide_index=True)

    with tab_materials:
        st.markdown("### Material Costs")
        if material_details.empty:
            st.info("No material cost entries saved for this job.")
        else:
            st.dataframe(material_details, width="stretch", hide_index=True)

        st.markdown("### Imported PDF Checklist Paint & Materials")
        if imported_materials.empty:
            st.info("No imported checklist material lines saved for this job.")
        else:
            st.dataframe(imported_materials, width="stretch", hide_index=True)

    with tab_wages:
        st.markdown("### Wage Entries")
        if wage_details.empty:
            st.info("No wage entries saved for this job.")
        else:
            st.dataframe(wage_details, width="stretch", hide_index=True)

        st.markdown("### Timesheets")
        if timesheet_details.empty:
            st.info("No timesheets saved for this job.")
        else:
            st.dataframe(timesheet_details, width="stretch", hide_index=True)

    with tab_equipment:
        st.markdown("### Equipment Master Entries")
        if equipment_master.empty:
            st.info("No equipment master entries saved for this job.")
        else:
            st.dataframe(equipment_master, width="stretch", hide_index=True)

        st.markdown("### Equipment Checklist Detail")
        if equipment_detail.empty:
            st.info("No equipment checklist detail saved for this job.")
        else:
            st.dataframe(equipment_detail, width="stretch", hide_index=True)
 
    with tab_control:
        st.markdown("### Variations")

        if variations_df.empty:
            st.info("No variations saved for this job.")
        else:
            st.dataframe(variations_df, width="stretch", hide_index=True)

        st.markdown("### Claims / Invoices")

        if claims_df.empty:
            st.info("No claims or invoices saved for this job.")
        else:
            st.dataframe(claims_df, width="stretch", hide_index=True)

        st.divider()

        st.markdown("### Generate Job PDFs")

        pdf_col1, pdf_col2, pdf_col3 = st.columns(3)

        with pdf_col1:
            if st.button("Generate Paint & Materials Order PDF", key=f"generate_paint_order_{job_id}"):
                try:
                    pdf_path = generate_paint_order_pdf(job_id)
                    st.success("Paint & Materials Order PDF generated and attached to this job.")

                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            "Download Paint & Materials Order PDF",
                            data=f,
                            file_name=os.path.basename(pdf_path),
                            mime="application/pdf",
                            key=f"download_paint_order_{job_id}",
                        )

                except Exception as e:
                    st.error(f"Could not generate paint order PDF: {e}")

        with pdf_col2:
            if st.button("Generate Equipment Checklist PDF", key=f"generate_equipment_pdf_{job_id}"):
                try:
                    pdf_path = generate_equipment_checklist_pdf(job_id)
                    st.success("Equipment Checklist PDF generated and attached to this job.")

                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            "Download Equipment Checklist PDF",
                            data=f,
                            file_name=os.path.basename(pdf_path),
                            mime="application/pdf",
                            key=f"download_equipment_pdf_{job_id}",
                        )

                except Exception as e:
                    st.error(f"Could not generate equipment checklist PDF: {e}")

        with pdf_col3:
            st.caption("Variation form")
            variation_result_key = f"linked_variation_result_{job_id}"
            with st.form(f"linked_variation_form_generator_{job_id}"):
                variation_description = st.text_area("Variation Description", key=f"linked_variation_description_{job_id}")
                variation_reason = st.text_area("Reason / Details", key=f"linked_variation_reason_{job_id}")
                variation_notes = st.text_area("Notes", key=f"linked_variation_notes_{job_id}")
                generate_variation = st.form_submit_button("Generate Variation Form")

                if generate_variation:
                    try:
                        requested_by = current_username()
                        pdf_path, variation_no = generate_variation_form_pdf(
                            job_id,
                            requested_by=requested_by,
                            description=variation_description,
                            reason=variation_reason,
                            notes=variation_notes,
                        )
                        st.session_state[variation_result_key] = {
                            "pdf_path": pdf_path,
                            "variation_no": variation_no,
                        }
                    except Exception as e:
                        st.error(f"Could not generate variation form PDF: {e}")

            if variation_result_key in st.session_state:
                variation_result = st.session_state[variation_result_key]
                pdf_path = variation_result["pdf_path"]
                variation_no = variation_result["variation_no"]
                st.success(f"Variation Form {variation_no} generated and attached to this job.")

                with open(pdf_path, "rb") as f:
                    st.download_button(
                        "Download Variation Form PDF",
                        data=f,
                        file_name=os.path.basename(pdf_path),
                        mime="application/pdf",
                        key=f"download_variation_pdf_{job_id}_{variation_no}",
                    )

        st.divider()

        st.markdown("### Job Documents")

        documents_df = df_query("""
            SELECT id,
                   document_type AS 'Document Type',
                   file_name AS 'File Name',
                   file_path,
                   created_at AS 'Created At',
                   notes AS 'Notes'
            FROM job_documents
            WHERE job_id = ?
            ORDER BY id DESC
        """, (job_id,))

        if documents_df.empty:
            st.info("No documents attached to this job yet.")
        else:
            for _, doc in documents_df.iterrows():
                st.write(f"**{doc['Document Type']}** - {doc['File Name']}")
                st.caption(f"Created: {doc['Created At']}")

                file_path = str(doc["file_path"])

                if os.path.exists(file_path):
                    with open(file_path, "rb") as f:
                        st.download_button(
                            label=f"Download {doc['File Name']}",
                            data=f,
                            file_name=doc["File Name"],
                            mime="application/pdf",
                            key=f"download_job_doc_{doc['id']}",
                        )
                else:
                    st.warning("File path not found on disk.")
    
    with tab_photos:
        st.markdown("### Photo Register")
        if photos_meta.empty:
            st.info("No photos saved for this job.")
        else:
            st.dataframe(photos_meta, width="stretch", hide_index=True)

            with st.expander("View Photo Gallery"):
                for _, photo_row in photos_full.iterrows():
                    title_parts = [
                        str(photo_row["category"] or ""),
                        str(photo_row["caption"] or photo_row["photo_name"] or ""),
                    ]
                    st.markdown("#### " + " - ".join([p for p in title_parts if p]))
                    try:
                        st.image(photo_data_to_bytes(photo_row["photo_data"]), width="stretch")
                    except Exception:
                        st.warning("Could not display photo.")
                    st.caption(f"Uploaded: {photo_row['uploaded_at']} by {photo_row['uploaded_by']}")

def job_lookup_links_page():
    st.header("Job Lookup / Links")
    st.caption("Select a job number, job name, builder/client, address or leading hand and open all linked information for that job.")

    include_archived = st.checkbox("Include archived jobs", value=True, key="linked_include_archived")
    jobs_df = job_lookup_dataframe(include_archived=include_archived)

    if jobs_df.empty:
        st.info("No jobs found.")
        return

    mode_options = ["Open Job", "Jobs by Builder / Client", "Jobs by Leading Hand", "Search Anything"]
    requested_mode = st.session_state.get("linked_view_mode", "Open Job")
    mode_index = mode_options.index(requested_mode) if requested_mode in mode_options else 0
    mode = st.radio("Lookup Mode", mode_options, index=mode_index, horizontal=True, key="linked_view_mode_radio")
    st.session_state["linked_view_mode"] = mode

    selected_job_id = None

    if mode == "Open Job":
        default_job_id = st.session_state.get("linked_view_selected_job_id")
        selected_job_id = select_job_from_dataframe(
            jobs_df,
            "Select job number / job name / builder / address",
            key="linked_open_job_select",
            default_job_id=default_job_id
        )

    elif mode == "Jobs by Builder / Client":
        builders_df = df_query("""
            SELECT id, name
            FROM builders_clients
            ORDER BY name
        """)
        if builders_df.empty:
            st.info("No builders or clients saved yet.")
            return

        builder_map = {str(row["name"]): int(row["id"]) for _, row in builders_df.iterrows()}
        builder_names = list(builder_map.keys())
        default_builder_id = st.session_state.get("linked_view_selected_builder_id")
        builder_index = 0
        if default_builder_id is not None:
            for i, name in enumerate(builder_names):
                if int(builder_map[name]) == int(default_builder_id):
                    builder_index = i
                    break

        selected_builder = st.selectbox("Select builder/client", builder_names, index=builder_index, key="linked_builder_select")
        builder_id = builder_map[selected_builder]
        st.session_state["linked_view_selected_builder_id"] = int(builder_id)

        builder_jobs = jobs_df[jobs_df["builder_id"].astype(int) == int(builder_id)]
        st.markdown(f"### Jobs for {selected_builder}")
        selected_job_id = display_job_table_with_open_button(builder_jobs, selected_builder, "linked_builder_jobs")

    elif mode == "Jobs by Leading Hand":
        leading_hands = sorted([x for x in jobs_df["Leading Hand"].dropna().astype(str).unique().tolist() if x.strip()])
        if not leading_hands:
            st.info("No leading hands saved against jobs yet.")
            return

        selected_leading_hand = st.selectbox("Select leading hand", leading_hands, key="linked_leading_hand")
        lh_jobs = jobs_df[jobs_df["Leading Hand"].astype(str) == selected_leading_hand]
        st.markdown(f"### Jobs for {selected_leading_hand}")
        selected_job_id = display_job_table_with_open_button(lh_jobs, selected_leading_hand, "linked_lh_jobs")

    else:
        search_text = st.text_input("Search job number, job name, builder/client, address, status or leading hand", key="linked_any_search")
        filtered_jobs = jobs_df.copy()
        if search_text.strip():
            haystack = (
                filtered_jobs["Job No"].astype(str) + " " +
                filtered_jobs["Job Name"].astype(str) + " " +
                filtered_jobs["Builder / Client"].astype(str) + " " +
                filtered_jobs["Site Address"].astype(str) + " " +
                filtered_jobs["Status"].astype(str) + " " +
                filtered_jobs["Leading Hand"].astype(str)
            ).str.lower()
            filtered_jobs = filtered_jobs[haystack.str.contains(search_text.strip().lower(), na=False)]
        st.markdown("### Search Results")
        selected_job_id = display_job_table_with_open_button(filtered_jobs, "search results", "linked_search_jobs")

    if selected_job_id:
        st.divider()
        st.session_state["linked_view_selected_job_id"] = int(selected_job_id)
        render_job_linked_info(selected_job_id)

def job_folders_page():
    st.header("Job Folders")
    st.caption("Open one job and access the full linked job file from one place: summary, plans/specs, colours/materials, timesheets, equipment, photos, documents, variations, forms and financials.")

    include_archived = st.checkbox("Include archived jobs", value=True, key="job_folder_include_archived")
    jobs_df = job_lookup_dataframe(include_archived=include_archived)

    if jobs_df.empty:
        st.info("No jobs found. Create a job first from Jobs > Add Job.")
        return

    quick_search = st.text_input(
        "Search job number, job name, builder/client, address, status or leading hand",
        key="job_folder_quick_search",
        placeholder="Start typing to filter jobs...",
    )

    filtered_jobs = jobs_df.copy()
    if quick_search.strip():
        haystack = (
            filtered_jobs["Job No"].astype(str) + " " +
            filtered_jobs["Job Name"].astype(str) + " " +
            filtered_jobs["Builder / Client"].astype(str) + " " +
            filtered_jobs["Site Address"].astype(str) + " " +
            filtered_jobs["Status"].astype(str) + " " +
            filtered_jobs["Leading Hand"].astype(str)
        ).str.lower()
        filtered_jobs = filtered_jobs[haystack.str.contains(quick_search.strip().lower(), na=False)]

    selected_job_id = select_job_from_dataframe(
        filtered_jobs,
        "Open Job Folder",
        key="job_folder_selected_job",
        default_job_id=st.session_state.get("linked_view_selected_job_id"),
    )

    if not selected_job_id:
        return

    st.session_state["linked_view_selected_job_id"] = int(selected_job_id)

    folder_action_cols = st.columns(4)
    if folder_action_cols[0].button("Go to Job Register", key="folder_go_job_register"):
        st.session_state["go_to_menu"] = "Jobs"
        st.rerun()
    if folder_action_cols[1].button("Go to Materials", key="folder_go_materials"):
        st.session_state["go_to_menu"] = "Material Costs"
        st.rerun()
    if folder_action_cols[2].button("Go to Timesheets", key="folder_go_timesheets"):
        st.session_state["go_to_menu"] = "Timesheets"
        st.rerun()
    if folder_action_cols[3].button("Go to Photos", key="folder_go_photos"):
        st.session_state["go_to_menu"] = "Job Photos"
        st.rerun()

    st.divider()
    render_job_linked_info(selected_job_id)

