"""Take-off, progress billing and 3D viewer page renderers.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *


def render_takeoff_package(package_id, key_prefix="takeoff"):
    try:
        recalc_takeoff_package(package_id)
    except Exception:
        pass
    pkg, lines = takeoff_summary_data(package_id)
    if pkg.empty:
        st.warning("Selected take-off package could not be found.")
        return
    p = pkg.iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Internal m²", f"{float(p.get('interior_total_m2') or 0):,.2f}")
    c2.metric("External m²", f"{float(p.get('exterior_total_m2') or 0):,.2f}")
    c3.metric("Total Labour Hours", f"{float(p.get('total_labour_hours') or 0):,.2f}")
    c4.metric("Status", str(p.get("status") or "Draft"))

    b1, b2, b3, b4, b5 = st.columns(5)
    b1.metric("Walls", f"{float(p.get('wall_labour_hours') or 0):,.2f} hrs")
    b2.metric("Ceilings", f"{float(p.get('ceiling_labour_hours') or 0):,.2f} hrs")
    b3.metric("Woodwork", f"{float(p.get('woodwork_labour_hours') or 0):,.2f} hrs")
    b4.metric("Features", f"{float(p.get('feature_labour_hours') or 0):,.2f} hrs")
    b5.metric("Exterior", f"{float(p.get('exterior_labour_hours') or 0):,.2f} hrs")

    paint_total = float(p.get("total_paint_litres") or 0)
    paint_standard = float(p.get("standard_paint_litres") or 0)
    paint_gloss = float(p.get("gloss_paint_litres") or 0)
    if lines is not None and not lines.empty and paint_total <= 0:
        paint_summary_calc = takeoff_paint_summary_from_lines(lines)
        paint_total = paint_summary_calc["total_paint_litres"]
        paint_standard = paint_summary_calc["standard_paint_litres"]
        paint_gloss = paint_summary_calc["gloss_paint_litres"]
    p1, p2, p3 = st.columns(3)
    p1.metric("Total Paint Required", f"{paint_total:,.2f} L")
    p2.metric("Standard Paint", f"{paint_standard:,.2f} L")
    p3.metric("Gloss / Enamel", f"{paint_gloss:,.2f} L")
    st.caption("Paint allowance rule: standard paint = m² × coats ÷ 12. Gloss/enamel = 100ml per frame/jamb/window item, 500ml per door, and 1L per 100 lineal metres of skirting where counts/lineal metres are entered.")

    if str(p.get("assumptions") or "").strip():
        st.info(str(p.get("assumptions") or ""))
    if str(p.get("ai_notes") or "").strip():
        st.warning(str(p.get("ai_notes") or "")[:3000])

    st.markdown("### Take-off Lines")
    if lines.empty:
        st.info("No take-off lines added yet.")
    else:
        view_lines = lines.drop(columns=["ID"])
        st.dataframe(view_lines, width="stretch", hide_index=True)

        area_summary = view_lines.groupby(["Area", "Substrate"], dropna=False).agg({"m2": "sum", "Labour Hours": "sum"}).reset_index()
        labour_summary = view_lines.groupby(["Area", "Labour Category"], dropna=False).agg({"m2": "sum", "Labour Hours": "sum"}).reset_index()
        s1, s2 = st.columns(2)
        with s1:
            st.markdown("#### Substrate m² Totals")
            st.dataframe(area_summary, width="stretch", hide_index=True)
        with s2:
            st.markdown("#### Labour Breakdown")
            st.dataframe(labour_summary, width="stretch", hide_index=True)

        paint_summary = takeoff_paint_summary_from_lines(view_lines)
        ps1, ps2 = st.columns(2)
        with ps1:
            st.markdown("#### Paint Required by Finish")
            if paint_summary["by_finish"].empty:
                st.info("No paint summary available yet.")
            else:
                st.dataframe(paint_summary["by_finish"], width="stretch", hide_index=True)
        with ps2:
            st.markdown("#### Paint Required by Substrate")
            if paint_summary["by_substrate"].empty:
                st.info("No paint summary available yet.")
            else:
                st.dataframe(paint_summary["by_substrate"], width="stretch", hide_index=True)

    with st.expander("Add manual take-off line", expanded=lines.empty):
        with st.form(f"{key_prefix}_add_line_{package_id}"):
            col1, col2, col3 = st.columns(3)
            area_type = col1.selectbox("Internal / External", TAKEOFF_AREA_TYPES, key=f"{key_prefix}_area_type_{package_id}")
            labour_category = col2.selectbox("Labour Category", TAKEOFF_LABOUR_CATEGORIES, key=f"{key_prefix}_lab_cat_{package_id}")
            substrate = col3.selectbox("Substrate", TAKEOFF_SUBSTRATES, key=f"{key_prefix}_substrate_{package_id}")
            col4, col5, col6, col7 = st.columns(4)
            location_area = col4.text_input("Room / Elevation / Area", key=f"{key_prefix}_location_{package_id}")
            m2 = col5.number_input("m²", min_value=0.0, step=1.0, key=f"{key_prefix}_m2_{package_id}")
            coats = col6.number_input("Coats", min_value=0.0, step=1.0, value=2.0, key=f"{key_prefix}_coats_{package_id}")
            default_prod = takeoff_default_productivity(area_type, labour_category, substrate)
            productivity = col7.number_input("m² / labour hr / coat", min_value=0.1, step=0.5, value=float(default_prod), key=f"{key_prefix}_prod_{package_id}")
            paint_col1, paint_col2, paint_col3 = st.columns(3)
            default_finish = "Gloss / Enamel" if labour_category == "Woodwork" else "Standard Paint"
            finish_index = TAKEOFF_FINISH_TYPES.index(default_finish) if default_finish in TAKEOFF_FINISH_TYPES else 0
            finish_type = paint_col1.selectbox("Paint / Finish Type", TAKEOFF_FINISH_TYPES, index=finish_index, key=f"{key_prefix}_finish_type_{package_id}")
            element_count = paint_col2.number_input("Door / frame / window count", min_value=0.0, step=1.0, value=0.0, key=f"{key_prefix}_element_count_{package_id}")
            lineal_metres = paint_col3.number_input("Skirting lineal metres", min_value=0.0, step=1.0, value=0.0, key=f"{key_prefix}_lineal_metres_{package_id}")
            flags_selected = st.multiselect("Flags / Allowances", TAKEOFF_FLAGS, key=f"{key_prefix}_flags_{package_id}")
            notes = st.text_area("Notes", key=f"{key_prefix}_notes_{package_id}")
            preview_hours = takeoff_line_hours(m2, coats, productivity)
            preview_litres = takeoff_line_paint_litres(substrate, labour_category, m2, coats, finish_type, element_count, lineal_metres)
            st.caption(f"Labour preview: {preview_hours:,.2f} hours | Paint preview: {preview_litres:,.2f} litres")
            add_line = st.form_submit_button("Add Take-off Line")
            if add_line:
                add_takeoff_line(
                    package_id, area_type, location_area, substrate, labour_category, m2, coats, productivity,
                    ", ".join(flags_selected), notes, finish_type=finish_type, element_count=element_count, lineal_metres=lineal_metres
                )
                st.success("Take-off line added.")
                refresh()

    if not lines.empty:
        with st.expander("Delete a take-off line"):
            delete_options = {
                f"{row['Area']} - {row['Location / Area']} - {row['Substrate']} - {float(row['m2'] or 0):,.2f}m²": int(row["ID"])
                for _, row in lines.iterrows()
            }
            selected_delete = st.selectbox("Line to delete", list(delete_options.keys()), key=f"{key_prefix}_delete_select_{package_id}")
            confirm_delete = st.checkbox("Confirm delete selected line", key=f"{key_prefix}_delete_confirm_{package_id}")
            if st.button("Delete Take-off Line", key=f"{key_prefix}_delete_button_{package_id}"):
                if not confirm_delete:
                    st.error("Tick confirm first.")
                else:
                    linked_progress_count, _deleted_package_id = delete_takeoff_line_safely(delete_options[selected_delete])
                    if linked_progress_count:
                        st.info(f"Also removed {linked_progress_count} linked progress model section(s) so the take-off and progress model stay in sync.")
                    st.success("Take-off line deleted.")
                    refresh()

    st.divider()
    render_takeoff_audit_panel(package_id, key_prefix=f"{key_prefix}_audit_{package_id}")

    export_data = takeoff_export_excel(package_id)
    file_name = f"{safe_file_name(str(p.get('takeoff_no') or 'painting_takeoff'))}_Painting_Takeoff.xlsx"
    st.download_button(
        "Download Painting Take-off Excel",
        data=export_data,
        file_name=file_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{key_prefix}_download_excel_{package_id}",
    )

def painting_takeoff_generator_page(default_job_id=None):
    pb_page_header(
        "Painting Take-off Generator",
        "Upload plans/specs, generate an editable painting take-off, break totals down by substrate/labour, and calculate basic paint litres required.",
        "Estimating"
    )
    ai_cost_control_notice("painting_takeoff")

    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first, then upload plans and generate a painting take-off.")
        return

    labels = list(job_options.keys())
    index = 0
    if default_job_id:
        for i, label in enumerate(labels):
            if int(job_options[label]) == int(default_job_id):
                index = i
                break
    selected_job_label = st.selectbox("Select Job", labels, index=index, key=f"takeoff_job_select_{default_job_id or 'main'}")
    job_id = int(job_options[selected_job_label])

    st.markdown("### 1. Upload plans, specs, colour schedules or scope")
    st.caption("Use this upload area for the take-off source documents. They save directly into the selected Job Folder.")
    render_smart_plan_set_import(job_id, key_prefix=f"takeoff_smart_import_{job_id}", expanded=True)
    render_job_documents_panel(job_id, allow_upload=True, allow_delete=False, key_prefix=f"takeoff_docs_{job_id}")

    st.divider()
    st.markdown("### 2. Generate or create a take-off package")
    docs = takeoff_source_documents(job_id)
    selected_doc_ids = []
    if docs.empty:
        st.info("No documents uploaded yet. Upload the architectural plans/specifications first, or create a blank manual take-off package.")
    else:
        source_options = {f"{row['Document Type']} - {row['File Name']}": int(row["id"]) for _, row in docs.iterrows()}
        default_selection = [label for label in source_options.keys() if any(x in label.lower() for x in ["architectural", "spec", "colour", "scope"])]
        selected_sources = st.multiselect("Source documents for AI draft", list(source_options.keys()), default=default_selection, key=f"takeoff_source_docs_{job_id}")
        selected_doc_ids = [source_options[label] for label in selected_sources]

    extra_scope_notes = st.text_area(
        "Extra scope notes for the take-off",
        placeholder="Example: include internal walls, ceilings, timberwork, grooved doors, external render, eaves/soffits, dark colours, high ceilings. Note door/window/frame counts and skirting lm where known for gloss allowance.",
        key=f"takeoff_extra_notes_{job_id}",
    )


    with st.expander("Import structured take-off CSV / progress model data", expanded=False):
        st.caption("Use this for JobHub import tables, including the King Street Progress Model Import Table CSV. It creates real take-off lines and prepares the Progress / Billing + 3D model straight away.")
        csv_import_file = st.file_uploader(
            "Upload take-off CSV",
            type=["csv"],
            key=f"takeoff_csv_import_file_{job_id}",
        )
        import_notes = st.text_area(
            "CSV import notes",
            value="Structured take-off import. Review all measurements before issuing claims.",
            key=f"takeoff_csv_import_notes_{job_id}",
        )
        if st.button("Import CSV and Create Progress Model", key=f"takeoff_csv_import_button_{job_id}", disabled=csv_import_file is None):
            try:
                package_id, imported_count = import_takeoff_csv_to_package(job_id, csv_import_file, notes=import_notes)
                st.session_state[f"selected_takeoff_package_{job_id}"] = package_id
                st.success(f"Imported {imported_count} take-off line(s) and prepared the progress/3D model. Open Progress / Billing to view it.")
                refresh()
            except Exception as e:
                st.error(f"Could not import CSV: {e}")

    c_manual, c_ai = st.columns(2)
    with c_manual:
        st.caption("Manual/basic mode does not use OpenAI API credit.")
        if st.button("Create Blank Manual Take-off", key=f"create_manual_takeoff_{job_id}"):
            package_id = create_takeoff_package(job_id, method="Manual", notes=extra_scope_notes)
            st.session_state[f"selected_takeoff_package_{job_id}"] = package_id
            st.success("Blank take-off package created. Add lines below.")
            refresh()

    with c_ai:
        ai_ready, ai_msg = ai_backend_ready()
        existing_ai_packages = df_query("""
            SELECT id, takeoff_no, updated_at
            FROM painting_takeoff_packages
            WHERE job_id = ?
              AND LOWER(COALESCE(generated_method, '')) LIKE '%ai%'
            ORDER BY id DESC
            LIMIT 1
        """, (job_id,))
        has_existing_ai = not existing_ai_packages.empty
        if not ai_ready:
            st.caption("AI draft unavailable: " + ai_msg)
        elif has_existing_ai:
            row = existing_ai_packages.iloc[0]
            st.info(f"Existing AI take-off found: {row['takeoff_no']}. Select it below instead of re-running AI unless the plans changed.")

        re_run_ai = False
        if has_existing_ai:
            re_run_ai = st.checkbox("Re-run AI anyway because the plans/scope changed", value=False, key=f"rerun_ai_takeoff_{job_id}")
        else:
            re_run_ai = True

        ai_confirm = confirm_ai_api_spend("Confirm: use OpenAI API credit for this take-off", key=f"confirm_ai_takeoff_spend_{job_id}")
        ai_button_disabled = (not ai_ready) or (not ai_confirm) or (not re_run_ai)
        if st.button("Run AI Take-off (uses API credit)", key=f"generate_ai_takeoff_{job_id}", disabled=ai_button_disabled):
            with st.spinner("Reading uploaded plan/spec text and preparing take-off draft..."):
                ai_data, err, warnings = generate_ai_takeoff_lines(job_id, selected_doc_ids=selected_doc_ids, extra_scope_notes=extra_scope_notes)
            for warning in warnings or []:
                st.warning(warning)
            if err:
                st.error(err)
            else:
                used_names = ai_data.get("_used_names", []) if isinstance(ai_data, dict) else []
                package_id = save_ai_takeoff_package(job_id, ai_data, selected_doc_names=used_names)
                run_twenty_point_takeoff_check(package_id, save_result=True)
                ensure_progress_sections_for_package(package_id, reset_values=False)
                st.session_state[f"selected_takeoff_package_{job_id}"] = package_id
                st.success("AI draft take-off created, checked through the 20-point review, and a progress/billing model was prepared. Review and adjust every line before pricing.")
                refresh()

    packages = df_query("""
        SELECT id, takeoff_no, takeoff_date, status, generated_method, interior_total_m2, exterior_total_m2, total_labour_hours, updated_at
        FROM painting_takeoff_packages
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

    st.divider()
    st.markdown("### 3. Review, edit and export the take-off")
    if packages.empty:
        st.info("No take-off packages for this job yet.")
        return

    package_options = {
        f"{row['takeoff_no']} - {row['status']} - {float(row['interior_total_m2'] or 0):,.0f}m² internal / {float(row['exterior_total_m2'] or 0):,.0f}m² external - {float(row['total_labour_hours'] or 0):,.1f} hrs": int(row["id"])
        for _, row in packages.iterrows()
    }
    default_pkg = st.session_state.get(f"selected_takeoff_package_{job_id}")
    pkg_labels = list(package_options.keys())
    pkg_index = 0
    if default_pkg:
        for i, label in enumerate(pkg_labels):
            if int(package_options[label]) == int(default_pkg):
                pkg_index = i
                break
    selected_pkg_label = st.selectbox("Select Take-off Package", pkg_labels, index=pkg_index, key=f"select_takeoff_package_{job_id}")
    package_id = int(package_options[selected_pkg_label])
    st.session_state[f"selected_takeoff_package_{job_id}"] = package_id

    with st.expander("Package status / notes"):
        pkg = df_query("SELECT * FROM painting_takeoff_packages WHERE id = ?", (package_id,))
        if not pkg.empty:
            p = pkg.iloc[0]
            with st.form(f"takeoff_package_update_{package_id}"):
                c1, c2 = st.columns(2)
                status_options = ["Draft", "Reviewed", "Issued", "Superseded"]
                current_status = str(p.get("status") or "Draft")
                status_index = status_options.index(current_status) if current_status in status_options else 0
                status = c1.selectbox("Status", status_options, index=status_index)
                takeoff_date = c2.text_input("Take-off Date", value=str(p.get("takeoff_date") or str(jobhub_today())))
                assumptions = st.text_area("Assumptions / measurement notes", value=str(p.get("assumptions") or ""))
                notes = st.text_area("Internal Notes", value=str(p.get("notes") or ""))
                save_pkg = st.form_submit_button("Save Package Details")
                if save_pkg:
                    execute("""
                        UPDATE painting_takeoff_packages
                        SET status = ?, takeoff_date = ?, assumptions = ?, notes = ?, updated_at = ?
                        WHERE id = ?
                    """, (status, takeoff_date, assumptions, notes, jobhub_now().strftime("%Y-%m-%d %H:%M:%S"), package_id))
                    st.success("Take-off package updated.")
                    refresh()

    render_takeoff_package(package_id, key_prefix=f"takeoff_page_{job_id}")

