from pathlib import Path

PATH = Path("pb_planreader_app.py")
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected exactly one anchor, found {count}: {old[:100]!r}")
    text = text.replace(old, new, 1)


replace_once(
    'ELEVATION_BOX_SOURCE = "Manual elevation box"\nAUTO_EXTERNAL_SOURCE = "Auto external take-off"\n',
    'ELEVATION_BOX_SOURCE = "Manual elevation box"\n'
    'AUTO_EXTERNAL_SOURCE = "Auto external take-off"\n'
    'FLOOR_AREA_SOURCE = "Manual internal floor area"\n\n'
    'FLOOR_M2_OPTIONS = [\n'
    '    "Internal works — Floor m² basis",\n'
    '    "Internal ceilings — Floor m²",\n'
    '    "Floor coating — Floor m²",\n'
    ']\n\n'
    'FLOOR_M2_OPTION_MAP = {\n'
    '    "Internal works — Floor m² basis": ("Internal works (Floor m² basis)", "General", False),\n'
    '    "Internal ceilings — Floor m²": ("Internal ceilings", "Ceilings", True),\n'
    '    "Floor coating — Floor m²": ("Floors", "Floor coating", True),\n'
    '}\n'
)

replace_once(
    '        row["paint_litres"] = litres_from_area(qty * (1 + waste), coats, coverage_m2_per_l)\n'
    '        row["labour_hours"] = labour_hours_for(max(qty, lineal), row.get("labour_category"), waste_pct)\n',
    '        floor_pricing_basis = (\n'
    '            str(row.get("measurement_basis") or "").strip() == "Floor m²"\n'
    '            and "floor m² basis" in str(row.get("substrate") or "").lower()\n'
    '        )\n'
    '        if floor_pricing_basis:\n'
    '            row["paint_litres"] = 0.0\n'
    '            row["labour_hours"] = 0.0\n'
    '        else:\n'
    '            row["paint_litres"] = litres_from_area(qty * (1 + waste), coats, coverage_m2_per_l)\n'
    '            row["labour_hours"] = labour_hours_for(max(qty, lineal), row.get("labour_category"), waste_pct)\n'
)

replace_once(
    '    rows = merge_elevation_box_rows(job, df.to_dict("records"))\n'
    '    rows = merge_auto_external_rows(job, rows)\n'
    '    return pd.DataFrame(rows)\n',
    '    rows = merge_elevation_box_rows(job, df.to_dict("records"))\n'
    '    rows = merge_floor_area_rows(job, rows)\n'
    '    rows = merge_auto_external_rows(job, rows)\n'
    '    return pd.DataFrame(rows)\n'
)

floor_helpers = r'''

def floor_plan_options(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Rendered floor-plan pages suitable for calibrated floor-area take-off."""
    preferred: List[Dict[str, Any]] = []
    fallback: List[Dict[str, Any]] = []
    seen = set()
    for analysis in job.get("analyses", []):
        for page in analysis.get("pages", []):
            image_path = str(page.get("image_path") or "")
            if not image_path or image_path in seen or not Path(image_path).exists():
                continue
            seen.add(image_path)
            option = {
                "label": f"{analysis.get('file', '')} · {page.get('title') or 'page'} · p{page.get('page', 1)}",
                "file": str(analysis.get("file") or ""),
                "page": int(page.get("page") or 1),
                "image_path": image_path,
                "render_dpi": int(page.get("render_dpi") or 150),
                "page_type": str(page.get("page_type") or ""),
            }
            fallback.append(option)
            title = f"{page.get('title') or ''} {analysis.get('file') or ''}".lower()
            if option["page_type"] == "floor_plan" or any(k in title for k in ["floor plan", "ground floor", "level 0", "level 1", "level 2", "level 3", "level 4", "level 5"]):
                preferred.append(option)
    return preferred or fallback


def floor_boxes_from_job(job: Dict[str, Any], img_path: str) -> List[Dict[str, Any]]:
    state = (job.get("floor_measurements") or {}).get(img_path, {}) or {}
    boxes = normalise_boxes(state.get("zones", []))
    for box in boxes:
        if box.get("substrate") not in FLOOR_M2_OPTION_MAP:
            box["substrate"] = FLOOR_M2_OPTIONS[0]
    return boxes


def merge_floor_area_rows(job: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Replace generated internal floor-m² basis rows with the latest measured boxes."""
    kept = [r for r in rows if r.get("source_note") != FLOOR_AREA_SOURCE]
    for img_path, entry in (job.get("floor_measurements") or {}).items():
        size = _image_pixel_size(img_path)
        img_w = size[0] if size else None
        img_h = size[1] if size else None
        cal = normalise_calibration(entry.get("calibration"))
        mpp = calibration_mpp(cal, img_w, img_h) if cal else None
        for box in floor_boxes_from_job(job, img_path):
            qty = effective_box_m2(box, mpp, img_w, img_h)
            if qty <= 0:
                continue
            option = str(box.get("substrate") or FLOOR_M2_OPTIONS[0])
            substrate, labour, paint_surface = FLOOR_M2_OPTION_MAP.get(
                option, FLOOR_M2_OPTION_MAP[FLOOR_M2_OPTIONS[0]]
            )
            label = str(box.get("label") or "Measured floor area").strip() or "Measured floor area"
            coats = 2 if paint_surface else 0
            kept.append({
                "internal_external": "Internal",
                "area_location": label[:120],
                "substrate": substrate,
                "labour_category": labour,
                "measurement_basis": "Floor m²",
                "qty_m2": round(qty, 2),
                "lineal_m": 0.0,
                "count": 0,
                "coats": coats,
                "rate_ex_gst": 0.0,
                "labour_hours": 0.0,
                "paint_litres": litres_from_area(qty, coats) if paint_surface else 0.0,
                "source_note": FLOOR_AREA_SOURCE,
                "confidence": "Measured floor area from calibrated plan",
            })
    return kept
'''
replace_once(
    '\ndef compute_room_takeoff_rows(\n',
    floor_helpers + '\n\ndef compute_room_takeoff_rows(\n'
)

replace_once(
    '        job["takeoff_rows"] = merge_elevation_box_rows(job, df.to_dict("records"))\n',
    '        job["takeoff_rows"] = merge_floor_area_rows(\n'
    '            job, merge_elevation_box_rows(job, df.to_dict("records"))\n'
    '        )\n'
)

replace_once(
    '        ("Elevations tracked", len(job.get("elevation_progress", {}))),\n'
    '        ("Take-off rows", len(job.get("takeoff_rows", []))),\n',
    '        ("Elevations tracked", len(job.get("elevation_progress", {}))),\n'
    '        ("Floor plan areas", sum(len((v or {}).get("zones", []) or []) for v in (job.get("floor_measurements") or {}).values())),\n'
    '        ("Take-off rows", len(job.get("takeoff_rows", []))),\n'
)

replace_once(
    '        "internal_external", "area_location", "substrate", "labour_category", "qty_m2", "lineal_m", "count",\n'
    '        "coats", "rate_ex_gst", "labour_hours", "paint_litres", "source_note", "confidence"\n',
    '        "internal_external", "area_location", "substrate", "labour_category", "measurement_basis", "qty_m2", "lineal_m", "count",\n'
    '        "coats", "rate_ex_gst", "labour_hours", "paint_litres", "source_note", "confidence"\n'
)

replace_once(
    '            "qty_m2": st.column_config.NumberColumn("m²", min_value=0.0, step=1.0),\n',
    '            "measurement_basis": st.column_config.SelectboxColumn(\n'
    '                "Measurement basis", options=["Surface m²", "Floor m²", "Lineal m", "Count"]\n'
    '            ),\n'
    '            "qty_m2": st.column_config.NumberColumn("m²", min_value=0.0, step=1.0),\n'
)

floor_page = r'''

def floor_measurements_page(job_id: str):
    """Bluebeam-style calibrated floor-area measurement for internal works."""
    job = load_job(job_id)
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("Floor m² — internal works")
    st.caption(
        "Choose a floor-plan page, use Calibrate scale to drag across a known dimension, "
        "enter the real length, then drag boxes over the floor areas. Each box updates its m² "
        "when moved or resized. Select Internal works — Floor m² basis for floor-area pricing."
    )
    options = floor_plan_options(job)
    if not options:
        st.warning("No rendered plan pages yet. Upload the PDF with 'Convert PDF pages to PNG' enabled first.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    labels = [o["label"] for o in options]
    box_key = f"pr_floor_page_{job_id}"
    if box_key not in st.session_state or st.session_state.get(box_key) not in labels:
        st.session_state[box_key] = labels[0]
    nav = st.columns([1, 6, 1])
    nav[0].button("◀ Prev", key=f"{box_key}_prev", width="stretch", on_click=_cycle_plan_page, args=(box_key, labels, -1))
    selected = nav[1].selectbox("Floor plan", labels, key=box_key, label_visibility="collapsed")
    nav[2].button("Next ▶", key=f"{box_key}_next", width="stretch", on_click=_cycle_plan_page, args=(box_key, labels, 1))
    opt = next((o for o in options if o["label"] == selected), options[0])
    st.caption(f"Floor plan {labels.index(selected) + 1} of {len(labels)}")

    img_path = str(opt["image_path"])
    img_file = Path(img_path)
    if not img_file.exists():
        st.warning("Rendered floor-plan image is missing. Re-upload the plan to regenerate it.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    state = (job.get("floor_measurements") or {}).get(img_path, {}) or {}
    stored = floor_boxes_from_job(job, img_path)
    stored_cal = normalise_calibration(state.get("calibration"))
    rev_key = f"pr_floor_rev_{job_id}_{safe_name(img_path)}"
    rev = int(st.session_state.get(rev_key, 0))
    size = _image_pixel_size(img_path)
    img_w = size[0] if size else None
    img_h = size[1] if size else None

    auto_cal = None
    manual_cal = None
    auto_note = ""
    if stored_cal is None and img_w:
        auto = plan_auto_scale(job, opt.get("file"), opt.get("page"), dpi=opt.get("render_dpi") or 150)
        if auto and auto.get("m_per_px"):
            auto_cal = {
                "x1": 0.0, "y1": 0.0, "x2": 100.0, "y2": 0.0,
                "len_m": round(img_w * auto["m_per_px"], 4),
            }
            near = nearest_scale_ratio(auto.get("m_per_pt"))
            auto_note = f"PDF scale auto-detected{f' ≈ {scale_ratio_label(near)}' if near else ''}."
        manual_ratio = load_manual_scale(job, opt.get("file"), opt.get("page"))
        if manual_ratio is not None:
            manual_cal = manual_calibration_from_scale(
                manual_ratio, opt.get("render_dpi") or 150, img_w, img_h or img_w
            )

    returned = substrate_box_editor(
        img_file.read_bytes(),
        boxes=stored,
        substrates=FLOOR_M2_OPTIONS,
        calibration=stored_cal or auto_cal or manual_cal,
        revision=rev,
        key=f"pr_floor_boxes_{job_id}_{safe_name(img_path)}",
        height=880,
    )
    current = stored
    cal = stored_cal
    auto_cal_n = normalise_calibration(auto_cal)
    manual_cal_n = normalise_calibration(manual_cal)
    if returned is not None:
        payload = returned if isinstance(returned, dict) else {}
        next_boxes = normalise_boxes(payload.get("boxes"))
        for box in next_boxes:
            if box.get("substrate") not in FLOOR_M2_OPTION_MAP:
                box["substrate"] = FLOOR_M2_OPTIONS[0]
        next_cal = normalise_calibration(payload.get("calibration"))
        if next_cal == auto_cal_n or next_cal == manual_cal_n:
            next_cal = None
        if next_boxes != stored or next_cal != stored_cal:
            current = next_boxes
            cal = next_cal
            job.setdefault("floor_measurements", {})[img_path] = {
                "zones": current,
                "calibration": cal,
                "file": opt.get("file"),
                "page": opt.get("page"),
                "render_dpi": opt.get("render_dpi") or 150,
                "updated_at": now_stamp(),
            }
            job["takeoff_rows"] = merge_floor_area_rows(job, job.get("takeoff_rows", []))
            save_job(job_id, job)
            st.session_state[rev_key] = rev + 1

    active_cal = cal or auto_cal or manual_cal
    mpp = calibration_mpp(active_cal, img_w, img_h) if active_cal else None
    total_m2 = sum(effective_box_m2(b, mpp, img_w, img_h) for b in current)
    internal_basis_m2 = sum(
        effective_box_m2(b, mpp, img_w, img_h)
        for b in current if b.get("substrate") == FLOOR_M2_OPTIONS[0]
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("Measured floor m²", f"{total_m2:g}")
    c2.metric("Internal works floor m²", f"{internal_basis_m2:g}")
    c3.metric("Areas / boxes", len(current))

    if cal:
        st.success(f"Scale calibrated from a {cal['len_m']:g} m reference line. Box m² updates automatically.")
        if st.button("Clear drawn calibration", type="secondary", key=f"pr_floor_clear_cal_{job_id}"):
            job.setdefault("floor_measurements", {})[img_path] = {
                "zones": current,
                "file": opt.get("file"),
                "page": opt.get("page"),
                "render_dpi": opt.get("render_dpi") or 150,
                "updated_at": now_stamp(),
            }
            save_job(job_id, job)
            st.session_state[rev_key] = rev + 1
            st.rerun()
    elif manual_cal:
        st.info(f"Using page scale {scale_ratio_label(load_manual_scale(job, opt.get('file'), opt.get('page')))}. You can override it with Calibrate scale in the drawing.")
    elif auto_cal:
        st.info(f"{auto_note} You can override it by clicking Calibrate scale and drawing over a known dimension.")
    else:
        st.warning("Scale is not set yet. Click Calibrate scale above, drag a known dimension line, and enter its real length in metres.")

    if stored_cal is None and img_w:
        _render_scale_selector(job, opt, job_id, img_w, img_h)

    if current:
        rows = []
        for b in current:
            rows.append({
                "Area": b.get("label") or "Measured floor area",
                "Use": b.get("substrate") or FLOOR_M2_OPTIONS[0],
                "Floor m²": effective_box_m2(b, mpp, img_w, img_h),
            })
        st.markdown("#### Floor-area schedule")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    st.caption("Floor m² pricing rows are kept separate from paintable wall surface m², so they do not create false paint-litre quantities.")
    st.markdown("</div>", unsafe_allow_html=True)
'''
replace_once(
    '\ndef _render_scale_selector(job: Dict[str, Any], opt: Dict[str, Any], job_id: str, img_w: Any, img_h: Any) -> None:\n',
    floor_page + '\n\ndef _render_scale_selector(job: Dict[str, Any], opt: Dict[str, Any], job_id: str, img_w: Any, img_h: Any) -> None:\n'
)

replace_once(
    '    job["elevation_progress"] = {\n'
    '        k: v for k, v in (job.get("elevation_progress") or {}).items()\n'
    '        if str(k) != img_path and Path(str(k)).name != name\n'
    '    }\n',
    '    job["elevation_progress"] = {\n'
    '        k: v for k, v in (job.get("elevation_progress") or {}).items()\n'
    '        if str(k) != img_path and Path(str(k)).name != name\n'
    '    }\n'
    '    job["floor_measurements"] = {\n'
    '        k: v for k, v in (job.get("floor_measurements") or {}).items()\n'
    '        if str(k) != img_path and Path(str(k)).name != name\n'
    '    }\n'
)

replace_once(
    '        "Elevation Progress": pd.DataFrame([\n',
    '        "Floor m²": pd.DataFrame([\n'
    '            {\n'
    '                "image_path": img_path,\n'
    '                "zones": json.dumps(entry.get("zones", [])),\n'
    '                "calibration": json.dumps(entry.get("calibration") or {}),\n'
    '                "updated_at": entry.get("updated_at", ""),\n'
    '            }\n'
    '            for img_path, entry in (job.get("floor_measurements", {}) or {}).items()\n'
    '        ]),\n'
    '        "Elevation Progress": pd.DataFrame([\n'
)

replace_once(
    '            "Verify & Correct",\n'
    '            "Colour Schedule",\n',
    '            "Verify & Correct",\n'
    '            "Floor m²",\n'
    '            "Colour Schedule",\n'
)

replace_once(
    '    elif menu == "Verify & Correct":\n'
    '        corrections_page(job_id)\n'
    '    elif menu == "Colour Schedule":\n',
    '    elif menu == "Verify & Correct":\n'
    '        corrections_page(job_id)\n'
    '    elif menu == "Floor m²":\n'
    '        floor_measurements_page(job_id)\n'
    '    elif menu == "Colour Schedule":\n'
)

PATH.write_text(text, encoding="utf-8")
print("PlanReader floor m² patch applied")
