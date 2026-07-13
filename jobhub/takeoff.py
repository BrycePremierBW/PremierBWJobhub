"""Painting take-off, labour, paint, auditing and progress calculations.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *


TAKEOFF_LABOUR_CATEGORIES = ["Walls", "Ceilings", "Woodwork", "Features", "Exterior", "Prep / Other"]

TAKEOFF_AREA_TYPES = ["Internal", "External"]

TAKEOFF_FINISH_TYPES = ["Standard Paint", "Gloss / Enamel", "Primer / Sealer", "Texture / Membrane", "Other"]

TAKEOFF_COVERAGE_M2_PER_LITRE = 12.0

GLOSS_FRAME_LITRES_PER_ITEM = 0.10

GLOSS_DOOR_LITRES_PER_ITEM = 0.50

GLOSS_SKIRTING_LITRES_PER_100LM = 1.00

TAKEOFF_SUBSTRATES = [
    "Internal Plasterboard Walls",
    "Internal Plasterboard Ceilings",
    "Set Plaster / Bulkheads",
    "Timber Doors",
    "Grooved Doors",
    "Door Frames / Jambs",
    "Skirting / Architraves",
    "Feature Wall",
    "Dark Colour Areas",
    "External Render",
    "Hebel / AAC Panels",
    "Weatherboards",
    "FC Cladding",
    "Brick / Masonry",
    "Eaves / Soffits",
    "External Timberwork",
    "Metalwork / Handrails",
    "Fence / Screens",
    "Other",
]

TAKEOFF_FLAGS = [
    "High ceilings",
    "Grooved doors",
    "Multiple colours",
    "Dark colour",
    "Feature colour",
    "Texture coating",
    "Difficult access",
    "EWP / scaffold required",
    "Patch / prep heavy",
    "External weather exposure",
]

DEFAULT_TAKEOFF_PRODUCTIVITY = {
    ("Internal", "Walls"): 9.0,
    ("Internal", "Ceilings"): 8.0,
    ("Internal", "Woodwork"): 3.0,
    ("Internal", "Features"): 6.0,
    ("Internal", "Prep / Other"): 6.0,
    ("External", "Walls"): 7.0,
    ("External", "Ceilings"): 5.0,
    ("External", "Woodwork"): 3.0,
    ("External", "Features"): 5.0,
    ("External", "Exterior"): 6.0,
    ("External", "Prep / Other"): 5.0,
}

def takeoff_default_productivity(area_type, labour_category, substrate=""):
    area_type = str(area_type or "Internal")
    labour_category = str(labour_category or "Walls")
    substrate = str(substrate or "").lower()
    if "grooved" in substrate or "door" in substrate or "timber" in substrate:
        return 3.0
    if "ceiling" in substrate or "soffit" in substrate or "eave" in substrate:
        return 8.0 if area_type == "Internal" else 5.0
    if "feature" in substrate or "dark" in substrate:
        return 5.5
    return DEFAULT_TAKEOFF_PRODUCTIVITY.get((area_type, labour_category), 7.0)

def takeoff_line_hours(m2, coats, productivity_m2_per_hour):
    try:
        m2 = float(m2 or 0)
        coats = float(coats or 0)
        productivity = float(productivity_m2_per_hour or 0)
    except Exception:
        return 0.0
    if productivity <= 0:
        return 0.0
    # Productivity is m2 per labour hour per coat. Two coats doubles the hours.
    return round((m2 * coats) / productivity, 2)

def takeoff_line_paint_litres(substrate, labour_category, m2, coats, finish_type="Standard Paint", element_count=0, lineal_metres=0):
    """Calculate a basic paint allowance from each take-off line.

    General paint uses 12m² coverage per litre per coat.
    Gloss/enamel woodwork can use simple item allowances:
      - 100ml per window frame, door frame, jamb, architrave or similar item
      - 500ml per door
      - 1 litre per 100 lineal metres of skirting
    If item counts/lineal metres are not provided, it falls back to the 12m²/L rule.
    """
    try:
        m2 = float(m2 or 0)
        coats = float(coats or 0)
        element_count = float(element_count or 0)
        lineal_metres = float(lineal_metres or 0)
    except Exception:
        return 0.0

    substrate_text = str(substrate or "").lower()
    category_text = str(labour_category or "").lower()
    finish_text = str(finish_type or "").lower()
    is_gloss = any(x in finish_text for x in ["gloss", "enamel", "woodwork"]) or category_text == "woodwork"

    if is_gloss:
        if "skirting" in substrate_text and lineal_metres > 0:
            return round((lineal_metres / 100.0) * GLOSS_SKIRTING_LITRES_PER_100LM, 2)
        if ("door" in substrate_text and not any(x in substrate_text for x in ["frame", "jamb"])) and element_count > 0:
            return round(element_count * GLOSS_DOOR_LITRES_PER_ITEM, 2)
        if any(x in substrate_text for x in ["frame", "jamb", "architrave", "window"]) and element_count > 0:
            return round(element_count * GLOSS_FRAME_LITRES_PER_ITEM, 2)

    if TAKEOFF_COVERAGE_M2_PER_LITRE <= 0:
        return 0.0
    return round((m2 * coats) / TAKEOFF_COVERAGE_M2_PER_LITRE, 2)

def takeoff_paint_summary_from_lines(lines_df):
    if lines_df is None or lines_df.empty:
        return {
            "total_paint_litres": 0.0,
            "standard_paint_litres": 0.0,
            "gloss_paint_litres": 0.0,
            "by_finish": pd.DataFrame(),
            "by_substrate": pd.DataFrame(),
        }
    df = lines_df.copy()
    for col in ["m2", "Coats", "Item Count", "Lineal Metres", "Paint Litres"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if "Paint Litres" not in df.columns:
        df["Paint Litres"] = df.apply(lambda r: takeoff_line_paint_litres(
            r.get("Substrate", ""), r.get("Labour Category", ""), r.get("m2", 0), r.get("Coats", 0),
            r.get("Finish Type", "Standard Paint"), r.get("Item Count", 0), r.get("Lineal Metres", 0)
        ), axis=1)
    finish_col = "Finish Type" if "Finish Type" in df.columns else None
    gloss_mask = df[finish_col].astype(str).str.lower().str.contains("gloss|enamel", regex=True, na=False) if finish_col else df["Labour Category"].astype(str).str.lower().eq("woodwork")
    standard_l = float(df.loc[~gloss_mask, "Paint Litres"].sum()) if not df.empty else 0.0
    gloss_l = float(df.loc[gloss_mask, "Paint Litres"].sum()) if not df.empty else 0.0
    by_finish = pd.DataFrame()
    by_substrate = pd.DataFrame()
    if finish_col:
        by_finish = df.groupby(["Finish Type"], dropna=False).agg({"Paint Litres": "sum", "m2": "sum"}).reset_index()
    if "Substrate" in df.columns:
        by_substrate = df.groupby(["Area", "Substrate"], dropna=False).agg({"Paint Litres": "sum", "m2": "sum"}).reset_index()
    return {
        "total_paint_litres": round(float(df["Paint Litres"].sum()), 2),
        "standard_paint_litres": round(standard_l, 2),
        "gloss_paint_litres": round(gloss_l, 2),
        "by_finish": by_finish,
        "by_substrate": by_substrate,
    }

def next_takeoff_no(job_id):
    job_no = get_job_no_for_id(job_id)
    existing = df_query("SELECT COUNT(*) AS c FROM painting_takeoff_packages WHERE job_id = ?", (job_id,))
    next_num = int(existing.iloc[0]["c"] or 0) + 1 if not existing.empty else 1
    return f"TO-{safe_file_name(job_no)}-{next_num:02d}"

def create_takeoff_package(job_id, method="Manual", source_documents="", assumptions="", ai_notes="", notes=""):
    takeoff_no = next_takeoff_no(job_id)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    execute("""
        INSERT INTO painting_takeoff_packages
        (job_id, takeoff_no, takeoff_date, status, source_documents, generated_method, assumptions,
         ai_notes, created_by, created_at, updated_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        takeoff_no,
        str(date.today()),
        "Draft",
        source_documents,
        method,
        assumptions,
        ai_notes,
        current_username(),
        now,
        now,
        notes,
    ))
    created = df_query("SELECT id FROM painting_takeoff_packages WHERE job_id = ? AND takeoff_no = ? ORDER BY id DESC LIMIT 1", (job_id, takeoff_no))
    if created.empty:
        raise ValueError("Could not create take-off package.")
    return int(created.iloc[0]["id"])

def recalc_takeoff_package(package_id):
    lines = df_query("""
        SELECT area_type, substrate, labour_category, m2, coats, productivity_m2_per_hour, labour_hours,
               COALESCE(finish_type, 'Standard Paint') AS finish_type,
               COALESCE(element_count, 0) AS element_count,
               COALESCE(lineal_metres, 0) AS lineal_metres,
               COALESCE(paint_litres, 0) AS paint_litres
        FROM painting_takeoff_lines
        WHERE package_id = ?
    """, (package_id,))

    if lines.empty:
        vals = dict(
            interior=0, exterior=0, walls=0, ceilings=0, woodwork=0, features=0,
            exterior_hours=0, total=0, total_paint=0, standard_paint=0, gloss_paint=0
        )
    else:
        for col in ["m2", "coats", "labour_hours", "element_count", "lineal_metres", "paint_litres"]:
            lines[col] = pd.to_numeric(lines[col], errors="coerce").fillna(0)
        # Backfill paint litres for older lines that pre-date the paint calculator.
        lines["calc_paint_litres"] = lines.apply(lambda r: takeoff_line_paint_litres(
            r.get("substrate", ""), r.get("labour_category", ""), r.get("m2", 0), r.get("coats", 0),
            r.get("finish_type", "Standard Paint"), r.get("element_count", 0), r.get("lineal_metres", 0)
        ), axis=1)
        lines["paint_litres_final"] = lines.apply(lambda r: float(r["paint_litres"]) if float(r["paint_litres"] or 0) > 0 else float(r["calc_paint_litres"]), axis=1)
        gloss_mask = lines["finish_type"].astype(str).str.lower().str.contains("gloss|enamel", regex=True, na=False) | lines["labour_category"].astype(str).str.lower().eq("woodwork")
        vals = {
            "interior": float(lines[lines["area_type"].astype(str).str.lower() == "internal"]["m2"].sum()),
            "exterior": float(lines[lines["area_type"].astype(str).str.lower() == "external"]["m2"].sum()),
            "walls": float(lines[lines["labour_category"].astype(str).str.lower() == "walls"]["labour_hours"].sum()),
            "ceilings": float(lines[lines["labour_category"].astype(str).str.lower() == "ceilings"]["labour_hours"].sum()),
            "woodwork": float(lines[lines["labour_category"].astype(str).str.lower() == "woodwork"]["labour_hours"].sum()),
            "features": float(lines[lines["labour_category"].astype(str).str.lower() == "features"]["labour_hours"].sum()),
            "exterior_hours": float(lines[lines["area_type"].astype(str).str.lower() == "external"]["labour_hours"].sum()),
            "total": float(lines["labour_hours"].sum()),
            "total_paint": float(lines["paint_litres_final"].sum()),
            "standard_paint": float(lines.loc[~gloss_mask, "paint_litres_final"].sum()),
            "gloss_paint": float(lines.loc[gloss_mask, "paint_litres_final"].sum()),
        }

    execute("""
        UPDATE painting_takeoff_packages
        SET interior_total_m2 = ?, exterior_total_m2 = ?, wall_labour_hours = ?, ceiling_labour_hours = ?,
            woodwork_labour_hours = ?, feature_labour_hours = ?, exterior_labour_hours = ?, total_labour_hours = ?,
            total_paint_litres = ?, standard_paint_litres = ?, gloss_paint_litres = ?, updated_at = ?
        WHERE id = ?
    """, (
        round(vals["interior"], 2),
        round(vals["exterior"], 2),
        round(vals["walls"], 2),
        round(vals["ceilings"], 2),
        round(vals["woodwork"], 2),
        round(vals["features"], 2),
        round(vals["exterior_hours"], 2),
        round(vals["total"], 2),
        round(vals["total_paint"], 2),
        round(vals["standard_paint"], 2),
        round(vals["gloss_paint"], 2),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        package_id,
    ))

def add_takeoff_line(package_id, area_type, location_area, substrate, labour_category, m2, coats, productivity, flags, notes, finish_type="Standard Paint", element_count=0, lineal_metres=0):
    labour_hours = takeoff_line_hours(m2, coats, productivity)
    paint_litres = takeoff_line_paint_litres(substrate, labour_category, m2, coats, finish_type, element_count, lineal_metres)
    execute("""
        INSERT INTO painting_takeoff_lines
        (package_id, area_type, location_area, substrate, labour_category, m2, coats,
         productivity_m2_per_hour, labour_hours, finish_type, element_count, lineal_metres, paint_litres, flags, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        package_id,
        area_type,
        location_area,
        substrate,
        labour_category,
        float(m2 or 0),
        float(coats or 0),
        float(productivity or 0),
        labour_hours,
        finish_type,
        float(element_count or 0),
        float(lineal_metres or 0),
        paint_litres,
        flags,
        notes,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))
    recalc_takeoff_package(package_id)

def _csv_takeoff_value(row, *names, default=""):
    for name in names:
        if name in row.index:
            val = row.get(name)
            if pd.notna(val) and str(val).strip() != "":
                return val
    return default

def _csv_takeoff_float(row, *names, default=0.0):
    val = _csv_takeoff_value(row, *names, default=default)
    try:
        if isinstance(val, str):
            val = val.replace("$", "").replace(",", "").strip()
        return float(val or 0)
    except Exception:
        return float(default or 0)

def labour_category_from_import(text):
    t = str(text or "").lower()
    if "ceil" in t:
        return "Ceilings"
    if any(x in t for x in ["wood", "door", "frame", "jamb", "skirting", "stair", "handrail", "gloss", "garage door"]):
        return "Woodwork"
    if any(x in t for x in ["feature", "dark", "multiple"]):
        return "Features"
    if any(x in t for x in ["external", "exterior", "cladding", "render", "soffit", "eave", "downpipe", "fence"]):
        return "Exterior"
    if "allowance" in t or "completion" in t or "touch" in t:
        return "Features"
    return "Walls"

def area_type_from_import(row):
    text = " ".join([
        str(_csv_takeoff_value(row, "area_type", "Area Type", default="")),
        str(_csv_takeoff_value(row, "group", "Group", default="")),
        str(_csv_takeoff_value(row, "area", "Area", "location_area", "Location / Area", default="")),
        str(_csv_takeoff_value(row, "category", "Category", default="")),
        str(_csv_takeoff_value(row, "substrate", "Substrate", default="")),
    ]).lower()
    if any(x in text for x in ["external", "exterior", "cladding", "render", "soffit", "eave", "downpipe", "fence", "garage door"]):
        return "External"
    return "Internal"

def import_takeoff_csv_to_package(job_id, csv_file, source_name="CSV Import", notes=""):
    """Import a JobHub painting take-off/progress CSV into real take-off lines.

    Supports the King Street import pack columns plus normal JobHub-style columns.
    Once imported, the progress/billing model is generated immediately so the 3D model is visible.
    """
    data = csv_file.getvalue()
    df = pd.read_csv(BytesIO(data))
    if df.empty:
        raise ValueError("The CSV has no rows to import.")

    package_id = create_takeoff_package(
        job_id,
        method="CSV Import",
        source_documents=getattr(csv_file, "name", source_name),
        assumptions="Imported from structured take-off/progress CSV.",
        notes=notes,
    )

    imported = 0
    for _, row in df.iterrows():
        group = str(_csv_takeoff_value(row, "group", "Group", "area_type", "Area Type", default=""))
        unit = str(_csv_takeoff_value(row, "unit", "Unit", default="")).strip()
        level = str(_csv_takeoff_value(row, "level", "Level", default="")).strip()
        area = str(_csv_takeoff_value(row, "area", "Area", "location_area", "Location / Area", default="Section")).strip()
        substrate = str(_csv_takeoff_value(row, "substrate", "Substrate", default="Painting substrate")).strip()
        category_raw = str(_csv_takeoff_value(row, "category", "Category", "labour_category", "Labour Category", default="")).strip()
        labour_category = labour_category_from_import(" ".join([category_raw, substrate, area, group]))
        area_type = area_type_from_import(row)
        m2 = _csv_takeoff_float(row, "m2", "M2", "Total m2", "total_m2", "Area m2", default=0.0)
        coats = _csv_takeoff_float(row, "coats", "Coats", default=2.0)
        lineal_metres = _csv_takeoff_float(row, "lm", "LM", "lineal_metres", "Lineal Metres", default=0.0)
        doors = _csv_takeoff_float(row, "doors", "Doors", default=0.0)
        frames = _csv_takeoff_float(row, "frames", "Frames", "windows", "Windows", default=0.0)
        element_count = doors if doors > 0 and "door" in substrate.lower() and not any(x in substrate.lower() for x in ["frame", "jamb"]) else frames
        paint_type = str(_csv_takeoff_value(row, "paint_type", "Paint Type", "finish_type", "Finish Type", default="")).strip()
        if not paint_type:
            paint_type = "Gloss / Enamel" if labour_category == "Woodwork" else "Standard Paint"
        labour_hours = _csv_takeoff_float(row, "labour_hours", "Labour Hours", default=0.0)
        productivity = round((m2 * coats) / labour_hours, 2) if labour_hours > 0 else 8.0
        paint_litres = _csv_takeoff_float(row, "paint_litres", "Paint Litres", default=0.0)
        if paint_litres <= 0:
            paint_litres = takeoff_line_paint_litres(substrate, labour_category, m2, coats, paint_type, element_count, lineal_metres)
        location_parts = [x for x in [unit, level, area] if x]
        location_area = " - ".join(location_parts) if location_parts else area
        status = str(_csv_takeoff_value(row, "status", "Status", default="")).strip()
        note = str(_csv_takeoff_value(row, "notes", "Notes", default="")).strip()
        source_section = str(_csv_takeoff_value(row, "section_id", "Section ID", default="")).strip()
        flags = ", ".join([x for x in [category_raw, status, source_section] if x])

        execute("""
            INSERT INTO painting_takeoff_lines
            (package_id, area_type, location_area, substrate, labour_category, m2, coats,
             productivity_m2_per_hour, labour_hours, finish_type, element_count, lineal_metres, paint_litres, flags, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            package_id,
            area_type,
            location_area,
            substrate,
            labour_category,
            float(m2 or 0),
            float(coats or 0),
            float(productivity or 0),
            float(labour_hours or 0),
            paint_type,
            float(element_count or 0),
            float(lineal_metres or 0),
            float(paint_litres or 0),
            flags,
            note,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))
        imported += 1

    recalc_takeoff_package(package_id)
    ensure_progress_sections_for_package(package_id, reset_values=True)
    return package_id, imported

def delete_takeoff_line_safely(line_id):
    """Delete a take-off line and any progress/model sections generated from it."""
    line_df = df_query("SELECT package_id FROM painting_takeoff_lines WHERE id = ?", (line_id,))
    if line_df.empty:
        return 0, None

    package_id = int(line_df.iloc[0]["package_id"])
    linked_df = df_query("SELECT COUNT(*) AS c FROM painting_progress_sections WHERE takeoff_line_id = ?", (line_id,))
    linked_count = int(linked_df.iloc[0]["c"] or 0) if not linked_df.empty else 0

    # Delete dependent visual/progress rows first. This fixes PostgreSQL ForeignKeyViolation errors.
    try:
        execute("DELETE FROM building_model_surfaces WHERE takeoff_line_id = ?", (line_id,))
    except Exception:
        pass
    execute("DELETE FROM painting_progress_sections WHERE takeoff_line_id = ?", (line_id,))
    execute("DELETE FROM painting_takeoff_lines WHERE id = ?", (line_id,))
    recalc_takeoff_package(package_id)
    return linked_count, package_id

def takeoff_source_documents(job_id):
    return df_query("""
        SELECT id, document_type AS "Document Type", file_name AS "File Name", file_path, created_at AS "Created At", notes AS "Notes"
        FROM job_documents
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))

def extract_text_from_pdf_file(file_path, max_pages=25, max_chars=45000):
    text_parts = []
    try:
        reader = PdfReader(file_path)
        for page_index, page in enumerate(reader.pages[:max_pages]):
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            if page_text.strip():
                text_parts.append(f"\n--- PAGE {page_index + 1} ---\n{page_text}")
            if sum(len(x) for x in text_parts) >= max_chars:
                break
    except Exception as e:
        return "", f"Could not read PDF text from {os.path.basename(file_path)}: {e}"
    extracted = "\n".join(text_parts).strip()
    return extracted[:max_chars], None

def collect_takeoff_context_from_documents(job_id, selected_doc_ids=None):
    docs = takeoff_source_documents(job_id)
    if selected_doc_ids:
        selected_doc_ids = {int(x) for x in selected_doc_ids}
        docs = docs[docs["id"].astype(int).isin(selected_doc_ids)]

    context_parts = []
    warnings = []
    used_names = []
    for _, doc in docs.iterrows():
        file_path = str(doc.get("file_path") or "")
        file_name = str(doc.get("File Name") or os.path.basename(file_path))
        ext = os.path.splitext(file_name)[1].lower()
        if ext != ".pdf":
            warnings.append(f"Skipped {file_name}: only PDF text extraction is available inside the app for now.")
            continue
        if not os.path.exists(file_path):
            warnings.append(f"Skipped {file_name}: file is missing from storage.")
            continue
        extracted, err = extract_text_from_pdf_file(file_path)
        if err:
            warnings.append(err)
        elif extracted:
            context_parts.append(f"DOCUMENT: {file_name}\nTYPE: {doc.get('Document Type', '')}\n{extracted}")
            used_names.append(file_name)
        else:
            warnings.append(f"No readable text found in {file_name}. This may be a scanned/image plan.")

    return "\n\n".join(context_parts)[:60000], used_names, warnings

def parse_ai_takeoff_json(ai_text):
    raw = str(ai_text or "").strip()
    if not raw:
        return None, "AI returned an empty response."
    candidates = [raw]
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        candidates.insert(0, fence.group(1))
    brace = re.search(r"(\{.*\})", raw, flags=re.DOTALL)
    if brace:
        candidates.append(brace.group(1))
    last_error = "Could not find valid JSON in the AI response."
    for candidate in candidates:
        try:
            return json.loads(candidate), None
        except Exception as e:
            last_error = str(e)
    return None, last_error

def generate_ai_takeoff_lines(job_id, selected_doc_ids=None, extra_scope_notes=""):
    context_text, used_names, warnings = collect_takeoff_context_from_documents(job_id, selected_doc_ids)
    if not context_text.strip():
        return None, "No readable PDF text was found in the selected documents. Upload text-based plans/specs or add lines manually.", warnings

    job = get_job_details_for_pdf(job_id) or {}
    system_text = """
You are a professional painting estimator for Premier Brushworks. Create a painting take-off draft from the provided plans/specs text.
Only use areas, dimensions, room schedules, wall types, finishes, door/window schedules or explicit scope details that are present in the context. Do not invent quantities.
If exact m2 cannot be calculated from the text, make a conservative line with m2 0 and explain what measurement is missing in notes.
Return only valid JSON. No markdown. No commentary outside JSON.
"""
    prompt = f"""
Prepare a draft painting take-off for this job.

JOB:
{job.get('job_no','')} - {job.get('job_name','')}
Address: {job.get('site_address','')}
Builder/Client: {job.get('builder_client','')}

EXTRA SCOPE NOTES FROM USER:
{extra_scope_notes}

Return JSON in this exact shape:
{{
  "assumptions": "short assumptions and measurement limitations",
  "ai_notes": "important warnings such as high ceilings, grooved doors, dark colours, multiple colours, EWP/scaffold, texture coating",
  "lines": [
    {{
      "area_type": "Internal or External",
      "location_area": "room/elevation/level/area",
      "substrate": "substrate to be painted",
      "labour_category": "Walls, Ceilings, Woodwork, Features, Exterior or Prep / Other",
      "m2": 0,
      "coats": 2,
      "productivity_m2_per_hour": 8,
      "finish_type": "Standard Paint or Gloss / Enamel",
      "element_count": 0,
      "lineal_metres": 0,
      "flags": "comma separated flags",
      "notes": "brief scope/measurement notes"
    }}
  ]
}}

Important: Include separate lines for internal walls, ceilings, woodwork/doors, feature/dark colour areas, and external substrates when the information is available.
For paint requirements, standard paint is calculated later at 12m² per litre per coat. For gloss/enamel lines, include element_count where the plans/schedules show door quantities, window frame quantities, door frame/jamb quantities, architrave quantities or similar. For skirting lines, include lineal_metres where available. Do not invent counts or lineal metres.
"""
    answer, err = jobhub_ai_answer(prompt, context_text)
    if err:
        return None, err, warnings
    data, parse_err = parse_ai_takeoff_json(answer)
    if parse_err:
        return {"raw_ai_response": answer, "assumptions": "AI response could not be parsed into lines.", "ai_notes": parse_err, "lines": []}, None, warnings
    data["_used_names"] = used_names
    return data, None, warnings

def save_ai_takeoff_package(job_id, ai_data, selected_doc_names=None):
    selected_doc_names = selected_doc_names or ai_data.get("_used_names") or []
    assumptions = str(ai_data.get("assumptions", "") or "")
    ai_notes = str(ai_data.get("ai_notes", "") or "")
    if ai_data.get("raw_ai_response"):
        ai_notes = (ai_notes + "\n\nRAW AI RESPONSE:\n" + str(ai_data.get("raw_ai_response")))[:12000]
    package_id = create_takeoff_package(
        job_id,
        method="AI Draft from uploaded plans/specs",
        source_documents="; ".join(selected_doc_names),
        assumptions=assumptions,
        ai_notes=ai_notes,
        notes="Generated as editable draft. Review against drawings before pricing.",
    )
    for line in ai_data.get("lines", []) or []:
        area_type = str(line.get("area_type", "Internal") or "Internal")
        if area_type not in TAKEOFF_AREA_TYPES:
            area_type = "External" if "ext" in area_type.lower() else "Internal"
        category = str(line.get("labour_category", "Walls") or "Walls")
        if category not in TAKEOFF_LABOUR_CATEGORIES:
            category = "Exterior" if area_type == "External" else "Walls"
        substrate = str(line.get("substrate", "") or "Other")
        productivity = float(line.get("productivity_m2_per_hour") or takeoff_default_productivity(area_type, category, substrate))
        finish_type = str(line.get("finish_type") or ("Gloss / Enamel" if category == "Woodwork" else "Standard Paint"))
        if finish_type not in TAKEOFF_FINISH_TYPES:
            finish_type = "Gloss / Enamel" if "gloss" in finish_type.lower() or category == "Woodwork" else "Standard Paint"
        add_takeoff_line(
            package_id,
            area_type,
            str(line.get("location_area", "") or ""),
            substrate,
            category,
            float(line.get("m2") or 0),
            float(line.get("coats") or 2),
            productivity,
            str(line.get("flags", "") or ""),
            str(line.get("notes", "") or ""),
            finish_type=finish_type,
            element_count=float(line.get("element_count") or 0),
            lineal_metres=float(line.get("lineal_metres") or 0),
        )
    recalc_takeoff_package(package_id)
    return package_id

def takeoff_summary_data(package_id):
    pkg = df_query("SELECT * FROM painting_takeoff_packages WHERE id = ?", (package_id,))
    lines = df_query("""
        SELECT id AS "ID", area_type AS "Area", location_area AS "Location / Area", substrate AS "Substrate",
               labour_category AS "Labour Category", m2 AS "m2", coats AS "Coats",
               productivity_m2_per_hour AS "m2 / Labour Hr / Coat", labour_hours AS "Labour Hours",
               COALESCE(finish_type, 'Standard Paint') AS "Finish Type",
               COALESCE(element_count, 0) AS "Item Count",
               COALESCE(lineal_metres, 0) AS "Lineal Metres",
               COALESCE(paint_litres, 0) AS "Paint Litres",
               flags AS "Flags", notes AS "Notes"
        FROM painting_takeoff_lines
        WHERE package_id = ?
        ORDER BY area_type, labour_category, location_area, id
    """, (package_id,))
    if not lines.empty:
        for col in ["m2", "Coats", "m2 / Labour Hr / Coat", "Labour Hours", "Item Count", "Lineal Metres", "Paint Litres"]:
            lines[col] = pd.to_numeric(lines[col], errors="coerce").fillna(0)
        # Older records may have 0L saved. Recalculate display litres from the rule when needed.
        lines["Paint Litres"] = lines.apply(lambda r: float(r["Paint Litres"]) if float(r["Paint Litres"] or 0) > 0 else takeoff_line_paint_litres(
            r.get("Substrate", ""), r.get("Labour Category", ""), r.get("m2", 0), r.get("Coats", 0),
            r.get("Finish Type", "Standard Paint"), r.get("Item Count", 0), r.get("Lineal Metres", 0)
        ), axis=1)
    return pkg, lines

def takeoff_export_excel(package_id):
    pkg, lines = takeoff_summary_data(package_id)
    if pkg.empty:
        raise ValueError("Take-off package not found.")
    pkg_row = pkg.iloc[0]
    job = df_query("""
        SELECT j.job_no AS "Job No", j.job_name AS "Job Name", COALESCE(bc.name, '') AS "Builder / Client", j.site_address AS "Site Address"
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.id = ?
    """, (int(pkg_row["job_id"]),))

    lines_export = lines.drop(columns=["ID"]) if not lines.empty else pd.DataFrame(columns=[
        "Area", "Location / Area", "Substrate", "Labour Category", "m2", "Coats", "m2 / Labour Hr / Coat",
        "Labour Hours", "Finish Type", "Item Count", "Lineal Metres", "Paint Litres", "Flags", "Notes"
    ])

    by_area = pd.DataFrame()
    by_labour = pd.DataFrame()
    by_paint_finish = pd.DataFrame()
    by_paint_substrate = pd.DataFrame()
    if not lines_export.empty:
        by_area = lines_export.groupby(["Area", "Substrate"], dropna=False).agg({"m2": "sum", "Labour Hours": "sum", "Paint Litres": "sum"}).reset_index()
        by_labour = lines_export.groupby(["Area", "Labour Category"], dropna=False).agg({"m2": "sum", "Labour Hours": "sum", "Paint Litres": "sum"}).reset_index()
        by_paint_finish = lines_export.groupby(["Finish Type"], dropna=False).agg({"Paint Litres": "sum", "m2": "sum", "Item Count": "sum", "Lineal Metres": "sum"}).reset_index()
        by_paint_substrate = lines_export.groupby(["Area", "Substrate", "Finish Type"], dropna=False).agg({"Paint Litres": "sum", "m2": "sum", "Item Count": "sum", "Lineal Metres": "sum"}).reset_index()

    summary = pd.DataFrame([
        {"Metric": "Internal m2", "Value": float(pkg_row.get("interior_total_m2") or 0)},
        {"Metric": "External m2", "Value": float(pkg_row.get("exterior_total_m2") or 0)},
        {"Metric": "Wall Labour Hours", "Value": float(pkg_row.get("wall_labour_hours") or 0)},
        {"Metric": "Ceiling Labour Hours", "Value": float(pkg_row.get("ceiling_labour_hours") or 0)},
        {"Metric": "Woodwork Labour Hours", "Value": float(pkg_row.get("woodwork_labour_hours") or 0)},
        {"Metric": "Feature Labour Hours", "Value": float(pkg_row.get("feature_labour_hours") or 0)},
        {"Metric": "Exterior Labour Hours", "Value": float(pkg_row.get("exterior_labour_hours") or 0)},
        {"Metric": "Total Labour Hours", "Value": float(pkg_row.get("total_labour_hours") or 0)},
        {"Metric": "Total Paint Required Litres", "Value": float(pkg_row.get("total_paint_litres") or lines_export.get("Paint Litres", pd.Series(dtype=float)).sum() if not lines_export.empty else 0)},
        {"Metric": "Standard Paint Litres", "Value": float(pkg_row.get("standard_paint_litres") or 0)},
        {"Metric": "Gloss / Enamel Litres", "Value": float(pkg_row.get("gloss_paint_litres") or 0)},
    ])

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if not job.empty:
            job.to_excel(writer, index=False, sheet_name="Job")
        pd.DataFrame([pkg_row.to_dict()]).to_excel(writer, index=False, sheet_name="Takeoff Package")
        summary.to_excel(writer, index=False, sheet_name="Summary")
        by_area.to_excel(writer, index=False, sheet_name="Substrate Totals")
        by_labour.to_excel(writer, index=False, sheet_name="Labour Breakdown")
        by_paint_finish.to_excel(writer, index=False, sheet_name="Paint by Finish")
        by_paint_substrate.to_excel(writer, index=False, sheet_name="Paint by Substrate")
        lines_export.to_excel(writer, index=False, sheet_name="Takeoff Lines")
        for ws in writer.book.worksheets:
            for column_cells in ws.columns:
                max_len = 0
                col_letter = column_cells[0].column_letter
                for cell in column_cells:
                    value = "" if cell.value is None else str(cell.value)
                    max_len = max(max_len, len(value))
                ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 52)
    output.seek(0)
    return output.getvalue()

def app_float(value, default=0.0):
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)

def get_adjusted_contract_value(job_id):
    job_df = df_query("SELECT contract_value FROM jobs WHERE id = ?", (job_id,))
    contract_value = app_float(job_df.iloc[0]["contract_value"] if not job_df.empty else 0)
    try:
        variations_df = df_query("""
            SELECT COALESCE(SUM(COALESCE(amount_ex_gst, 0)), 0) AS total
            FROM job_variations
            WHERE job_id = ? AND LOWER(COALESCE(status, '')) = 'approved'
        """, (job_id,))
        approved_variations = app_float(variations_df.iloc[0]["total"] if not variations_df.empty else 0)
    except Exception:
        approved_variations = 0.0
    return contract_value + approved_variations

def get_billed_amount_for_job(job_id):
    try:
        billed_df = df_query("""
            SELECT COALESCE(SUM(COALESCE(amount_ex_gst, 0)), 0) AS total
            FROM invoice_claims
            WHERE job_id = ?
              AND LOWER(COALESCE(status, '')) NOT IN ('draft', 'void', 'cancelled', 'rejected')
        """, (job_id,))
        return app_float(billed_df.iloc[0]["total"] if not billed_df.empty else 0)
    except Exception:
        return 0.0

def latest_takeoff_package_for_job(job_id):
    packages = df_query("""
        SELECT id
        FROM painting_takeoff_packages
        WHERE job_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (job_id,))
    if packages.empty:
        return None
    return int(packages.iloc[0]["id"])

def takeoff_packages_for_job(job_id):
    """Return take-off packages for a job using the labels expected by mapper pages."""
    packages = df_query("""
        SELECT
            id,
            COALESCE(NULLIF(takeoff_no, ''), '') AS package_name,
            COALESCE(NULLIF(status, ''), 'Draft') AS status,
            takeoff_no,
            takeoff_date,
            generated_method,
            interior_total_m2,
            exterior_total_m2,
            total_labour_hours,
            total_paint_litres,
            updated_at
        FROM painting_takeoff_packages
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))
    if not packages.empty:
        packages["package_name"] = packages.apply(
            lambda r: str(r.get("package_name") or f"Take-off {int(r.get('id') or 0)}"),
            axis=1,
        )
        packages["status"] = packages["status"].fillna("Draft").replace("", "Draft")
    return packages

def run_twenty_point_takeoff_check(package_id, save_result=True):
    pkg, lines = takeoff_summary_data(package_id)
    if pkg.empty:
        return pd.DataFrame()

    p = pkg.iloc[0]
    lines_df = lines.copy()
    if not lines_df.empty:
        for col in ["m2", "Coats", "m2 / Labour Hr / Coat", "Labour Hours"]:
            lines_df[col] = pd.to_numeric(lines_df[col], errors="coerce").fillna(0)

    checks = []

    def add_check(no, name, passed, severity="Warning", notes=""):
        checks.append({
            "No": no,
            "Check": name,
            "Result": "Pass" if passed else severity,
            "Notes": notes if notes else ("OK" if passed else "Needs review"),
        })

    total_lines = len(lines_df)
    total_m2 = float(lines_df["m2"].sum()) if not lines_df.empty else 0.0
    total_hours = float(lines_df["Labour Hours"].sum()) if not lines_df.empty else 0.0
    internal_m2 = float(lines_df[lines_df["Area"].astype(str).str.lower() == "internal"]["m2"].sum()) if not lines_df.empty else 0.0
    external_m2 = float(lines_df[lines_df["Area"].astype(str).str.lower() == "external"]["m2"].sum()) if not lines_df.empty else 0.0
    categories = set(lines_df["Labour Category"].astype(str).str.lower()) if not lines_df.empty else set()
    substrates = set(lines_df["Substrate"].astype(str).str.lower()) if not lines_df.empty else set()
    flags_text = " ".join(lines_df["Flags"].fillna("").astype(str).tolist()).lower() if not lines_df.empty else ""
    notes_text = " ".join(lines_df["Notes"].fillna("").astype(str).tolist()).lower() if not lines_df.empty else ""
    source_docs = str(p.get("source_documents") or "")
    assumptions = str(p.get("assumptions") or "")
    package_status = str(p.get("status") or "Draft")

    add_check(1, "Take-off has lines", total_lines > 0, "Critical", f"{total_lines} line(s) found.")
    add_check(2, "Total m² greater than zero", total_m2 > 0, "Critical", f"Total measured area: {total_m2:,.2f}m².")
    add_check(3, "Every line has Internal / External", lines_df.empty or lines_df["Area"].astype(str).str.strip().ne("").all(), "Critical")
    add_check(4, "Every line has a location / area", lines_df.empty or lines_df["Location / Area"].astype(str).str.strip().ne("").all(), "Warning")
    add_check(5, "Every line has a substrate", lines_df.empty or lines_df["Substrate"].astype(str).str.strip().ne("").all(), "Critical")
    add_check(6, "Every line has a labour category", lines_df.empty or lines_df["Labour Category"].astype(str).str.strip().ne("").all(), "Critical")
    add_check(7, "Every line has positive coats", lines_df.empty or (lines_df["Coats"] > 0).all(), "Critical")
    add_check(8, "Every line has positive productivity", lines_df.empty or (lines_df["m2 / Labour Hr / Coat"] > 0).all(), "Critical")
    add_check(9, "Calculated labour hours are present", total_hours > 0, "Critical", f"Total labour hours: {total_hours:,.2f}.")
    add_check(10, "Internal totals calculated", internal_m2 > 0, "Warning", f"Internal total: {internal_m2:,.2f}m².")
    add_check(11, "External totals considered", external_m2 > 0 or "external" in assumptions.lower() or "external" in notes_text, "Warning", f"External total: {external_m2:,.2f}m².")
    add_check(12, "Wall labour category reviewed", "walls" in categories, "Warning")
    add_check(13, "Ceiling labour category reviewed", "ceilings" in categories or "ceiling" in substrates or "ceiling" in assumptions.lower(), "Warning")
    add_check(14, "Woodwork / doors reviewed", "woodwork" in categories or "door" in " ".join(substrates) or "timber" in " ".join(substrates), "Warning")
    add_check(15, "Feature / dark colours reviewed", "features" in categories or "feature" in flags_text or "dark" in flags_text or "feature" in assumptions.lower() or "dark" in assumptions.lower(), "Warning")
    add_check(16, "High ceilings flagged where required", "high" in flags_text or "ceiling" not in assumptions.lower() or "height" not in assumptions.lower(), "Warning")
    add_check(17, "Grooved doors / detailed doors flagged where required", "grooved" in flags_text or "grooved" not in notes_text, "Warning")
    add_check(18, "Difficult access / EWP reviewed for exterior", external_m2 == 0 or "access" in flags_text or "ewp" in flags_text or "scaffold" in flags_text or "access" in assumptions.lower(), "Warning")
    add_check(19, "Source documents recorded", bool(source_docs.strip()) or str(p.get("generated_method") or "").lower() == "manual", "Warning", source_docs or "Manual take-off or no source documents recorded.")
    add_check(20, "Package has been reviewed before issue", package_status.lower() in ["reviewed", "issued"], "Warning", f"Current status: {package_status}.")

    audit_df = pd.DataFrame(checks)
    pass_count = int((audit_df["Result"] == "Pass").sum()) if not audit_df.empty else 0
    score = round((pass_count / 20) * 100, 1)
    if save_result:
        notes = audit_df.to_json(orient="records")[:12000]
        try:
            execute("""
                UPDATE painting_takeoff_packages
                SET audit_score = ?, audit_notes = ?, audit_at = ?, updated_at = ?
                WHERE id = ?
            """, (score, notes, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), package_id))
        except Exception:
            pass
    return audit_df

def render_takeoff_audit_panel(package_id, key_prefix="takeoff_audit"):
    st.markdown("### 20-Point Take-off Check")
    st.caption("This runs twenty practical estimating checks against the take-off before you rely on it for pricing, progress claims or labour planning.")
    audit_df = run_twenty_point_takeoff_check(package_id, save_result=True)
    if audit_df.empty:
        st.info("No audit available for this take-off package.")
        return
    pass_count = int((audit_df["Result"] == "Pass").sum())
    warning_count = int((audit_df["Result"] == "Warning").sum())
    critical_count = int((audit_df["Result"] == "Critical").sum())
    score = round((pass_count / 20) * 100, 1)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Audit Score", f"{score:.1f}%")
    c2.metric("Passed", pass_count)
    c3.metric("Warnings", warning_count)
    c4.metric("Critical", critical_count)
    st.dataframe(audit_df, width="stretch", hide_index=True)

def progress_package_options(job_id):
    packages = df_query("""
        SELECT id, takeoff_no, status, generated_method, interior_total_m2, exterior_total_m2, total_labour_hours
        FROM painting_takeoff_packages
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))
    if packages.empty:
        return {}
    return {
        f"{row['takeoff_no']} - {row['status']} - {float(row['interior_total_m2'] or 0):,.0f}m² internal / {float(row['exterior_total_m2'] or 0):,.0f}m² external": int(row["id"])
        for _, row in packages.iterrows()
    }

def ensure_progress_sections_for_package(package_id, reset_values=False):
    pkg = df_query("SELECT * FROM painting_takeoff_packages WHERE id = ?", (package_id,))
    if pkg.empty:
        raise ValueError("Take-off package not found.")
    job_id = int(pkg.iloc[0]["job_id"])
    job_no = get_job_no_for_id(job_id)
    lines = df_query("""
        SELECT id, area_type, location_area, substrate, labour_category, m2, labour_hours
        FROM painting_takeoff_lines
        WHERE package_id = ?
        ORDER BY area_type, labour_category, location_area, id
    """, (package_id,))
    if lines.empty:
        return 0

    adjusted_contract = get_adjusted_contract_value(job_id)
    lines_calc = lines.copy()
    lines_calc["m2"] = pd.to_numeric(lines_calc["m2"], errors="coerce").fillna(0)
    lines_calc["labour_hours"] = pd.to_numeric(lines_calc["labour_hours"], errors="coerce").fillna(0)
    basis_total = float(lines_calc["labour_hours"].sum()) or float(lines_calc["m2"].sum()) or 0.0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    created_count = 0

    for _, line in lines_calc.iterrows():
        line_id = int(line["id"])
        basis = app_float(line.get("labour_hours")) if app_float(line.get("labour_hours")) > 0 else app_float(line.get("m2"))
        allocated = round((adjusted_contract * basis / basis_total), 2) if basis_total > 0 and adjusted_contract > 0 else 0.0
        section_code = f"{safe_file_name(job_no)}-S{line_id}"
        existing = df_query("""
            SELECT id, allocated_value_ex_gst
            FROM painting_progress_sections
            WHERE takeoff_line_id = ?
            ORDER BY id
            LIMIT 1
        """, (line_id,))
        if existing.empty:
            execute("""
                INSERT INTO painting_progress_sections
                (job_id, package_id, takeoff_line_id, section_code, area_type, location_area, substrate,
                 labour_category, total_m2, allocated_value_ex_gst, completed_m2, completed_percent,
                 status, notes, updated_by, updated_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id, package_id, line_id, section_code,
                str(line.get("area_type") or ""), str(line.get("location_area") or ""), str(line.get("substrate") or ""),
                str(line.get("labour_category") or ""), app_float(line.get("m2")), allocated, 0.0, 0.0,
                "Not Started", "", current_username(), now, now,
            ))
            created_count += 1
        else:
            progress_id = int(existing.iloc[0]["id"])
            current_allocated = app_float(existing.iloc[0].get("allocated_value_ex_gst"))
            use_allocated = allocated if reset_values or current_allocated <= 0 else current_allocated
            execute("""
                UPDATE painting_progress_sections
                SET job_id = ?, package_id = ?, section_code = ?, area_type = ?, location_area = ?, substrate = ?,
                    labour_category = ?, total_m2 = ?, allocated_value_ex_gst = ?, updated_at = ?
                WHERE id = ?
            """, (
                job_id, package_id, section_code,
                str(line.get("area_type") or ""), str(line.get("location_area") or ""), str(line.get("substrate") or ""),
                str(line.get("labour_category") or ""), app_float(line.get("m2")), use_allocated, now, progress_id,
            ))
    return created_count

def progress_sections_df(job_id, package_id=None):
    params = [job_id]
    where = "WHERE ps.job_id = ?"
    if package_id:
        where += " AND ps.package_id = ?"
        params.append(package_id)
    df = df_query(f"""
        SELECT ps.id AS "ID",
               ps.package_id AS "Package ID",
               ps.takeoff_line_id AS "Takeoff Line ID",
               ps.section_code AS "Section Code",
               ps.area_type AS "Area",
               ps.location_area AS "Location / Area",
               ps.substrate AS "Substrate",
               ps.labour_category AS "Labour Category",
               ps.total_m2 AS "Total m2",
               ps.completed_m2 AS "Completed m2",
               (ps.total_m2 - ps.completed_m2) AS "Remaining m2",
               ps.completed_percent AS "Completed %",
               ps.allocated_value_ex_gst AS "Section Value Ex GST",
               (ps.allocated_value_ex_gst * ps.completed_percent / 100.0) AS "Billable Value Ex GST",
               tl.labour_hours AS "Total Labour Hours",
               tl.paint_litres AS "Total Paint Litres",
               tl.finish_type AS "Paint / Finish Type",
               tl.coats AS "Coats",
               tl.productivity_m2_per_hour AS "Productivity m2/hr",
               tl.element_count AS "Door/Frame/Window Count",
               tl.lineal_metres AS "Lineal Metres",
               tl.flags AS "Flags",
               ps.status AS "Status",
               ps.notes AS "Notes",
               ps.updated_by AS "Updated By",
               ps.updated_at AS "Updated At"
        FROM painting_progress_sections ps
        LEFT JOIN painting_takeoff_lines tl ON tl.id = ps.takeoff_line_id
        {where}
        ORDER BY ps.area_type, ps.location_area, ps.substrate, ps.id
    """, tuple(params))
    if df.empty:
        return df
    numeric_cols = [
        "Total m2", "Completed m2", "Remaining m2", "Completed %",
        "Section Value Ex GST", "Billable Value Ex GST", "Total Labour Hours", "Total Paint Litres",
        "Coats", "Productivity m2/hr", "Door/Frame/Window Count", "Lineal Metres"
    ]
    for col in numeric_cols:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["Remaining m2"] = (df["Total m2"] - df["Completed m2"]).clip(lower=0)
    df["Completed Labour Hours"] = df.apply(lambda r: (app_float(r["Total Labour Hours"]) * app_float(r["Completed m2"]) / app_float(r["Total m2"])) if app_float(r["Total m2"]) > 0 else 0, axis=1)
    df["Remaining Labour Hours"] = (df["Total Labour Hours"] - df["Completed Labour Hours"]).clip(lower=0)
    df["Completed Paint Litres"] = df.apply(lambda r: (app_float(r["Total Paint Litres"]) * app_float(r["Completed m2"]) / app_float(r["Total m2"])) if app_float(r["Total m2"]) > 0 else 0, axis=1)
    df["Remaining Paint Litres"] = (df["Total Paint Litres"] - df["Completed Paint Litres"]).clip(lower=0)
    df["Remaining Value Ex GST"] = (df["Section Value Ex GST"] - df["Billable Value Ex GST"]).clip(lower=0)
    return df

def progress_model_summary(job_id, package_id=None):
    sections = progress_sections_df(job_id, package_id)
    if sections.empty:
        return {
            "total_m2": 0.0, "completed_m2": 0.0, "remaining_m2": 0.0, "completed_percent": 0.0,
            "total_value": 0.0, "billable_value": 0.0, "billed_value": get_billed_amount_for_job(job_id),
            "remaining_value": 0.0, "claim_available": 0.0,
        }, sections

    for col in ["Total m2", "Completed m2", "Remaining m2", "Completed %", "Section Value Ex GST", "Billable Value Ex GST"]:
        sections[col] = pd.to_numeric(sections[col], errors="coerce").fillna(0)
    total_m2 = float(sections["Total m2"].sum())
    completed_m2 = float(sections["Completed m2"].sum())
    completed_percent = round((completed_m2 / total_m2) * 100, 2) if total_m2 > 0 else 0.0
    total_value = float(sections["Section Value Ex GST"].sum())
    billable_value = float(sections["Billable Value Ex GST"].sum())
    billed_value = get_billed_amount_for_job(job_id)
    return {
        "total_m2": total_m2,
        "completed_m2": completed_m2,
        "remaining_m2": max(total_m2 - completed_m2, 0.0),
        "completed_percent": completed_percent,
        "total_value": total_value,
        "billable_value": billable_value,
        "billed_value": billed_value,
        "remaining_value": max(total_value - billable_value, 0.0),
        "claim_available": billable_value - billed_value,
    }, sections

def update_progress_section(section_id, completed_m2, allocated_value, status, notes):
    row_df = df_query("SELECT total_m2 FROM painting_progress_sections WHERE id = ?", (section_id,))
    if row_df.empty:
        raise ValueError("Progress section not found.")
    total_m2 = app_float(row_df.iloc[0]["total_m2"])
    completed_m2 = max(min(app_float(completed_m2), total_m2), 0.0) if total_m2 > 0 else max(app_float(completed_m2), 0.0)
    completed_percent = round((completed_m2 / total_m2) * 100, 2) if total_m2 > 0 else 0.0
    if completed_percent <= 0:
        status = "Not Started"
    elif completed_percent >= 99.99:
        status = "Complete"
    elif not status or status == "Not Started":
        status = "In Progress"
    execute("""
        UPDATE painting_progress_sections
        SET completed_m2 = ?, completed_percent = ?, allocated_value_ex_gst = ?, status = ?, notes = ?,
            updated_by = ?, updated_at = ?
        WHERE id = ?
    """, (
        completed_m2, completed_percent, app_float(allocated_value), status, notes,
        current_username(), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), section_id,
    ))

def progress_export_excel(job_id, package_id=None):
    summary, sections = progress_model_summary(job_id, package_id)
    job = df_query("""
        SELECT j.job_no AS "Job No", j.job_name AS "Job Name", COALESCE(bc.name, '') AS "Builder / Client",
               j.site_address AS "Site Address", j.contract_value AS "Contract Value Ex GST"
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.id = ?
    """, (job_id,))
    summary_df = pd.DataFrame([{"Metric": k.replace("_", " ").title(), "Value": v} for k, v in summary.items()])
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if not job.empty:
            job.to_excel(writer, index=False, sheet_name="Job")
        summary_df.to_excel(writer, index=False, sheet_name="Progress Summary")
        sections.to_excel(writer, index=False, sheet_name="Progress Sections")
        if not sections.empty:
            by_substrate = sections.groupby(["Area", "Substrate"], dropna=False).agg({"Total m2": "sum", "Completed m2": "sum", "Billable Value Ex GST": "sum"}).reset_index()
            by_substrate.to_excel(writer, index=False, sheet_name="By Substrate")
        for ws in writer.book.worksheets:
            for column_cells in ws.columns:
                max_len = 0
                col_letter = column_cells[0].column_letter
                for cell in column_cells:
                    value = "" if cell.value is None else str(cell.value)
                    max_len = max(max_len, len(value))
                ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 52)
    output.seek(0)
    return output.getvalue()

def progress_row_colour(status, selected=False):
    status_text = str(status or "").lower()
    if selected:
        return "background-color: #dbeafe; font-weight: 700;"
    if "complete" in status_text:
        return "background-color: #dcfce7;"
    if "progress" in status_text:
        return "background-color: #fef9c3;"
    if "hold" in status_text or "review" in status_text:
        return "background-color: #ffedd5;"
    return "background-color: #ffffff;"

def style_progress_rows(df):
    def apply_row(row):
        selected = str(row.get("Selected", "")).strip() in ["✅", "True", "true", "1", "Yes"]
        style = progress_row_colour(row.get("Status"), selected=selected)
        return [style for _ in row]
    return df.style.apply(apply_row, axis=1)

def progress_section_label(row):
    return (
        f"{row.get('Section Code', '')} | {row.get('Area', '')} | {row.get('Location / Area', '')} | "
        f"{row.get('Substrate', '')} | {app_float(row.get('Total m2')):,.1f}m² | "
        f"{app_float(row.get('Section Value Ex GST')):,.0f} ex GST | {app_float(row.get('Completed %')):.1f}%"
    )

def progress_selection_summary(selected_df):
    if selected_df is None or selected_df.empty:
        return {
            "selected_m2": 0.0, "completed_m2": 0.0, "remaining_m2": 0.0,
            "selected_value": 0.0, "current_billable": 0.0, "available_if_complete": 0.0,
            "labour_hours": 0.0, "remaining_labour_hours": 0.0,
            "paint_litres": 0.0, "remaining_paint_litres": 0.0,
        }
    return {
        "selected_m2": float(selected_df["Total m2"].sum()),
        "completed_m2": float(selected_df["Completed m2"].sum()),
        "remaining_m2": float(selected_df["Remaining m2"].sum()),
        "selected_value": float(selected_df["Section Value Ex GST"].sum()),
        "current_billable": float(selected_df["Billable Value Ex GST"].sum()),
        "available_if_complete": float(selected_df["Remaining Value Ex GST"].sum()),
        "labour_hours": float(selected_df["Total Labour Hours"].sum()),
        "remaining_labour_hours": float(selected_df["Remaining Labour Hours"].sum()),
        "paint_litres": float(selected_df["Total Paint Litres"].sum()),
        "remaining_paint_litres": float(selected_df["Remaining Paint Litres"].sum()),
    }

def render_progress_visual_cards(display_sections, selected_ids, key_prefix="progress_cards"):
    if display_sections.empty:
        return
    st.markdown("### Visual Job Model")
    st.caption("Completed items stay green, in-progress items stay yellow and selected items are highlighted blue.")
    cards = display_sections.copy().head(120)
    chunks = [cards.iloc[i:i + 3] for i in range(0, len(cards), 3)]
    for chunk_index, chunk in enumerate(chunks):
        cols = st.columns(3)
        for col, (_, row) in zip(cols, chunk.iterrows()):
            status = str(row.get("Status") or "Not Started")
            selected = int(row.get("ID")) in selected_ids
            border = "#2563eb" if selected else ("#16a34a" if "complete" in status.lower() else "#f59e0b" if "progress" in status.lower() else "#d1d5db")
            bg = "#eff6ff" if selected else ("#dcfce7" if "complete" in status.lower() else "#fef9c3" if "progress" in status.lower() else "#ffffff")
            col.markdown(f"""
                <div style="background:{bg}; border:2px solid {border}; border-radius:14px; padding:12px; margin-bottom:10px; min-height:150px;">
                    <div style="font-weight:800; font-size:0.92rem; color:#111827;">{row.get('Location / Area', '')}</div>
                    <div style="font-size:0.8rem; color:#374151; margin-top:4px;">{row.get('Area', '')} • {row.get('Substrate', '')}</div>
                    <div style="font-size:0.8rem; color:#374151;">{row.get('Labour Category', '')}</div>
                    <div style="font-size:1.05rem; font-weight:800; color:#111827; margin-top:8px;">{app_float(row.get('Completed %')):.1f}% complete</div>
                    <div style="font-size:0.78rem; color:#374151; margin-top:4px;">{app_float(row.get('Total m2')):,.1f}m² • {pb_money(row.get('Section Value Ex GST'))}</div>
                    <div style="font-size:0.75rem; color:#6b7280; margin-top:4px;">{status}</div>
                </div>
            """, unsafe_allow_html=True)

