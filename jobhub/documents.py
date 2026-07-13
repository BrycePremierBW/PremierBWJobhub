"""PDF imports, job documents and printable form generation.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *


PDF_CHECKLIST_ITEMS = {
    "access": ("Access Equipment", [
        "4ft Step Ladder",
        "6ft Step Ladder",
        "8ft Step Ladder",
        "10ft Step Ladder",
        "3m Extension Ladder",
        "4.8m Extension Ladder",
        "6m Extension Ladder",
        "Door Stackers",
        "600mm Trestles",
        "900mm Trestles",
        "4m Planks",
        "5m Planks",
        "6m Planks",
    ]),
    "prep": ("Preparation Equipment", [
        "Mirka Dustless Sander",
        "Mirka Extractor",
        "Pole Sander",
        "Pressure Cleaner",
        "PowerShot",
        "Saw Stools",
        "Paper Machine",
        "Mixing Paddle",
        "Broom",
        "Dustpan",
        "Brush",
    ]),
    "painting": ("Painting Equipment", [
        "Graco Sprayguns",
        "Fine Finish Tips",
        "Standard Spray Tips",
        "Roller Frames 270mm",
        "Mini Roller Frames",
        "Roller Sleeves 270mm",
        "Mini Roller Sleeves",
        "Brushes",
        "Paint Trays",
        "Paint Pots",
    ]),
    "poles": ("Extension Poles", [
        "600mm Pole",
        "1200mm Pole",
        "1800mm Pole",
        "2400mm Pole",
        "Adjustable Pole",
    ]),
    "dewalt": ("DeWalt Electrical Tools", [
        "Impact Driver",
        "Hammer Drill",
        "Blower",
        "Sheet Sander",
        "Orbital Sander",
        "Grinder",
        "Work Light",
        "Bluetooth Speaker",
        "Battery Charger",
        "5Ah Battery",
        "Extension Leads",
        "RCD",
    ]),
    "cons": ("Consumables", [
        "Green Tape",
        "Yellow Tape",
        "Plastic Masking Film",
        "Black Plastic",
        "Canvas Drop Sheets",
        "Floor Protection Paper",
        "Gap Filler",
        "Plaster Filler",
        "Timber Filler",
        "Putty",
        "Bog",
        "Sugar Soap",
        "Mixing Sticks",
        "Sandpaper 80G",
        "Sandpaper 120G",
        "Sandpaper 180G",
        "Sandpaper 240G",
    ]),
}

def clean_pdf_value(value):
    if value is None:
        return ""
    text = str(value)
    if text in ["/Off", "Off", "None", "nan"]:
        return ""
    if text.startswith("/"):
        text = text[1:]
    return text.strip()

def pdf_field_value(fields, name):
    field = fields.get(name)
    if not field:
        return ""
    return clean_pdf_value(field.get("/V", ""))

def qty_to_float(value):
    text = clean_pdf_value(value)
    if not text:
        return 0.0
    match = re.search(r"[-+]?\d*\.?\d+", text)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0

def is_pdf_tick(value):
    text = clean_pdf_value(value).lower()
    return bool(text and text not in ["off", "false", "0", "no"])

def parse_master_checklist_pdf(uploaded_file):
    reader = PdfReader(uploaded_file)
    fields = reader.get_fields() or {}

    job_info = {
        "job_number": pdf_field_value(fields, "p1_job_0"),
        "job_name": pdf_field_value(fields, "p1_job_1"),
        "site_address": pdf_field_value(fields, "p1_job_2"),
        "client_builder": pdf_field_value(fields, "p1_job_3"),
        "leading_hand": pdf_field_value(fields, "p1_team_0"),
        "crew_members": pdf_field_value(fields, "p1_team_1"),
        "team_extra": pdf_field_value(fields, "p1_team_extra"),
    }

    equipment_rows = []

    for prefix, (category, item_names) in PDF_CHECKLIST_ITEMS.items():
        for idx, item_name in enumerate(item_names):
            req = pdf_field_value(fields, f"{prefix}_{idx}_req")
            loaded = pdf_field_value(fields, f"{prefix}_{idx}_loaded")
            returned = pdf_field_value(fields, f"{prefix}_{idx}_returned")
            tick = pdf_field_value(fields, f"{prefix}_{idx}_tick")
            missing = pdf_field_value(fields, f"{prefix}_{idx}_missing")

            has_anything = any([req, loaded, returned, is_pdf_tick(tick), missing])
            if not has_anything:
                continue

            equipment_rows.append({
                "Category": category,
                "Equipment Item": item_name,
                "Qty Required Raw": req,
                "Qty Loaded Raw": loaded,
                "Qty Returned Raw": returned,
                "Qty Required": qty_to_float(req),
                "Qty Loaded": qty_to_float(loaded),
                "Qty Returned": qty_to_float(returned),
                "Ticked": "Yes" if is_pdf_tick(tick) else "",
                "Missing / Damaged": missing,
            })

    material_rows = []
    for idx in range(5):
        product = pdf_field_value(fields, f"paintreg_{idx}_product")
        colour = pdf_field_value(fields, f"paintreg_{idx}_colour")
        qty_req = pdf_field_value(fields, f"paintreg_{idx}_qty_req")
        qty_loaded = pdf_field_value(fields, f"paintreg_{idx}_qty_loaded")

        if any([product, colour, qty_req, qty_loaded]):
            material_rows.append({
                "Product": product,
                "Colour": colour,
                "Qty Required": qty_req,
                "Qty Loaded": qty_loaded,
            })

    return job_info, pd.DataFrame(equipment_rows), pd.DataFrame(material_rows)

def find_or_create_builder_client(cur, name):
    name = clean_pdf_value(name)
    if not name:
        return None
    cur.execute("SELECT id FROM builders_clients WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""
        INSERT INTO builders_clients
        (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("Client / Builder", name, "", "", "", "", "", "", "", "Created from imported PDF checklist"))

    cur.execute("SELECT id FROM builders_clients WHERE name = ?", (name,))
    row = cur.fetchone()
    return row[0] if row else None

def find_or_create_checklist_item(cur, category, item_name):
    cur.execute("SELECT id FROM equipment_checklist_items WHERE item_name = ?", (item_name,))
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute("""
        INSERT INTO equipment_checklist_items
        (category, item_name, default_qty, notes)
        VALUES (?, ?, ?, ?)
    """, (category, item_name, 0, "Created from imported PDF checklist"))

    cur.execute("SELECT id FROM equipment_checklist_items WHERE item_name = ?", (item_name,))
    row = cur.fetchone()
    return row[0] if row else None

def import_master_checklist_to_job(job_id, job_info, equipment_df, materials_df, source_file, update_job=True, replace_imported_materials=True):
    conn = connect()
    cur = conn.cursor()

    imported_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if update_job:
        update_fields = []
        params = []

        if job_info.get("job_number"):
            update_fields.append("job_no = ?")
            params.append(job_info["job_number"])

        if job_info.get("job_name"):
            update_fields.append("job_name = ?")
            params.append(job_info["job_name"])

        if job_info.get("site_address"):
            update_fields.append("site_address = ?")
            params.append(job_info["site_address"])

        if job_info.get("leading_hand"):
            update_fields.append("leading_hand = ?")
            params.append(job_info["leading_hand"])

        if job_info.get("client_builder"):
            builder_id = find_or_create_builder_client(cur, job_info["client_builder"])
            if builder_id:
                update_fields.append("builder_client_id = ?")
                params.append(builder_id)

        crew_notes = []
        if job_info.get("crew_members"):
            crew_notes.append(f"Crew Members from checklist: {job_info['crew_members']}")
        if job_info.get("team_extra"):
            crew_notes.append(f"Team Notes from checklist: {job_info['team_extra']}")

        if crew_notes:
            cur.execute("SELECT notes FROM jobs WHERE id = ?", (job_id,))
            current_notes_row = cur.fetchone()
            current_notes = current_notes_row[0] if current_notes_row and current_notes_row[0] else ""
            new_notes = (current_notes + "\n" if current_notes else "") + "\n".join(crew_notes)
            update_fields.append("notes = ?")
            params.append(new_notes)

        if update_fields:
            params.append(job_id)
            cur.execute(f"UPDATE jobs SET {', '.join(update_fields)} WHERE id = ?", params)

    imported_equipment_count = 0

    for _, row in equipment_df.iterrows():
        category = str(row.get("Category", "")).strip()
        item_name = str(row.get("Equipment Item", "")).strip()
        if not item_name:
            continue

        item_id = find_or_create_checklist_item(cur, category, item_name)

        qty_required = float(row.get("Qty Required", 0) or 0)
        qty_loaded = float(row.get("Qty Loaded", 0) or 0)
        qty_returned = float(row.get("Qty Returned", 0) or 0)

        raw_req = str(row.get("Qty Required Raw", "") or "").strip()
        raw_loaded = str(row.get("Qty Loaded Raw", "") or "").strip()
        raw_returned = str(row.get("Qty Returned Raw", "") or "").strip()
        missing = str(row.get("Missing / Damaged", "") or "").strip()
        ticked = str(row.get("Ticked", "") or "").strip()

        notes_parts = []
        if raw_req and qty_required == 0:
            notes_parts.append(f"Original required qty: {raw_req}")
        if raw_loaded and qty_loaded == 0:
            notes_parts.append(f"Original loaded qty: {raw_loaded}")
        if raw_returned and qty_returned == 0:
            notes_parts.append(f"Original returned qty: {raw_returned}")
        if missing:
            notes_parts.append(f"Missing/damaged: {missing}")
        if ticked:
            notes_parts.append("Checklist ticked")
        notes_parts.append(f"Imported from {source_file} at {imported_at}")
        notes = " | ".join(notes_parts)

        is_required = 1 if (qty_required > 0 or raw_req) else 0
        is_packed = 1 if (qty_loaded > 0 or raw_loaded or ticked) else 0
        is_returned = 1 if (qty_returned > 0 or raw_returned) else 0

        cur.execute("""
            SELECT id FROM equipment_checklist_records
            WHERE job_id = ? AND checklist_item_id = ?
            ORDER BY id ASC
        """, (job_id, item_id))
        existing = cur.fetchall()

        if existing:
            keep_id = existing[0][0]
            cur.execute("""
                UPDATE equipment_checklist_records
                SET qty_required = ?, qty_taken = ?, qty_returned = ?,
                    is_required = ?, is_packed = ?, is_returned = ?,
                    date_out = ?, date_in = ?, taken_by = ?, returned_by = ?,
                    condition_out = ?, condition_in = ?, notes = ?
                WHERE id = ?
            """, (
                qty_required, qty_loaded, qty_returned,
                is_required, is_packed, is_returned,
                imported_at.split(" ")[0], "", "", "",
                "", missing, notes, keep_id
            ))

            for duplicate in existing[1:]:
                cur.execute("DELETE FROM equipment_checklist_records WHERE id = ?", (duplicate[0],))
        else:
            cur.execute("""
                INSERT INTO equipment_checklist_records
                (job_id, checklist_item_id, qty_required, qty_taken, qty_returned,
                 is_required, is_packed, is_returned, date_out, date_in, taken_by, returned_by,
                 condition_out, condition_in, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id, item_id, qty_required, qty_loaded, qty_returned,
                is_required, is_packed, is_returned,
                imported_at.split(" ")[0], "", "", "",
                "", missing, notes
            ))

        imported_equipment_count += 1

    imported_material_count = 0

    if replace_imported_materials:
        cur.execute("DELETE FROM imported_material_entries WHERE job_id = ?", (job_id,))

    for _, row in materials_df.iterrows():
        product = str(row.get("Product", "") or "").strip()
        colour = str(row.get("Colour", "") or "").strip()
        qty_required = str(row.get("Qty Required", "") or "").strip()
        qty_loaded = str(row.get("Qty Loaded", "") or "").strip()

        if not any([product, colour, qty_required, qty_loaded]):
            continue

        cur.execute("""
            INSERT INTO imported_material_entries
            (job_id, product, colour, qty_required, qty_loaded, source_file, imported_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (job_id, product, colour, qty_required, qty_loaded, source_file, imported_at, "Imported from PDF master checklist"))

        imported_material_count += 1

    conn.commit()
    conn.close()

    return imported_equipment_count, imported_material_count

def safe_file_name(name):
    name = str(name or "file").strip()
    name = re.sub(r"[^a-zA-Z0-9._-]", "_", name)
    return name[:120]

def get_job_details_for_pdf(job_id):
    df = df_query("""
        SELECT j.id,
               j.job_no,
               j.job_name,
               j.site_address,
               j.leading_hand,
               COALESCE(bc.name, '') AS builder_client
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE j.id = ?
    """, (job_id,))

    if df.empty:
        return None

    return df.iloc[0].to_dict()

def fill_pdf_template(template_path, output_path, field_values):
    reader = PdfReader(template_path)
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    if "/AcroForm" in reader.trailer["/Root"]:
        writer._root_object.update({
            NameObject("/AcroForm"): reader.trailer["/Root"]["/AcroForm"]
        })

    try:
        writer.set_need_appearances_writer(True)
    except Exception:
        try:
            if "/AcroForm" not in writer._root_object:
                writer._root_object[NameObject("/AcroForm")] = DictionaryObject()
            writer._root_object["/AcroForm"].update({
                NameObject("/NeedAppearances"): BooleanObject(True)
            })
        except Exception:
            pass

    for page in writer.pages:
        writer.update_page_form_field_values(page, field_values)

    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path

def attach_document_to_job(job_id, document_type, file_path, notes="Generated from JobHub"):
    execute("""
        INSERT INTO job_documents
        (job_id, document_type, file_name, file_path, created_at, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        document_type,
        os.path.basename(file_path),
        file_path,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        notes,
    ))

def save_uploaded_job_document(job_id, uploaded_file, document_type, notes=""):
    """Save uploaded plans/specs/docs into the selected job folder and register them in job_documents."""
    job = get_job_details_for_pdf(job_id)
    if not job:
        raise ValueError("Job not found.")

    job_no = str(job.get("job_no") or f"job_{job_id}")
    job_folder = get_job_folder(job_no)
    documents_folder = os.path.join(job_folder, "documents")
    os.makedirs(documents_folder, exist_ok=True)

    original_name = safe_file_name(uploaded_file.name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stored_file_name = f"{timestamp}_{original_name}"
    file_path = os.path.join(documents_folder, stored_file_name)

    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    attach_document_to_job(
        job_id,
        document_type,
        file_path,
        notes=notes or f"Uploaded as {document_type} by {current_username()}",
    )

    return file_path

def parse_page_selection(page_text, max_pages):
    """Parse page text like '1,3,5-7'. Returns zero-based page indexes."""
    if max_pages <= 0:
        return []
    text_value = str(page_text or "").strip()
    if not text_value:
        return list(range(max_pages))
    selected = set()
    for part in text_value.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            try:
                start = int(start_text.strip())
                end = int(end_text.strip())
            except Exception:
                continue
            if end < start:
                start, end = end, start
            for page_no in range(start, end + 1):
                if 1 <= page_no <= max_pages:
                    selected.add(page_no - 1)
        else:
            try:
                page_no = int(part)
            except Exception:
                continue
            if 1 <= page_no <= max_pages:
                selected.add(page_no - 1)
    return sorted(selected)

def get_pdf_page_count_from_bytes(pdf_bytes):
    """Count PDF pages with PyMuPDF if available, otherwise pypdf."""
    try:
        import fitz  # PyMuPDF
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return int(doc.page_count)
    except Exception:
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            return len(reader.pages)
        except Exception:
            return 0

def save_converted_drawing_image(job_id, source_pdf_name, page_index, image_bytes, image_format, view_name, notes=""):
    """Save a rendered PDF page image and attach it as a drawing mapper document."""
    job = get_job_details_for_pdf(job_id)
    if not job:
        raise ValueError("Job not found.")
    job_no = str(job.get("job_no") or f"job_{job_id}")
    job_folder = get_job_folder(job_no)
    documents_folder = os.path.join(job_folder, "documents")
    os.makedirs(documents_folder, exist_ok=True)

    stem = os.path.splitext(safe_file_name(source_pdf_name))[0]
    ext = "jpg" if str(image_format).upper() in ["JPG", "JPEG"] else "png"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stored_file_name = f"{timestamp}_{stem}_page_{page_index + 1:03d}.{ext}"
    file_path = os.path.join(documents_folder, stored_file_name)
    with open(file_path, "wb") as f:
        f.write(image_bytes)
    attach_document_to_job(
        job_id,
        f"Drawing Mapper - {view_name}",
        file_path,
        notes=notes or f"Converted from PDF page {page_index + 1} for plan/elevation mapping.",
    )
    return file_path

def convert_pdf_bytes_to_drawing_images(job_id, pdf_bytes, pdf_name, page_indexes, view_name, dpi=220, image_format="PNG"):
    """Render selected PDF pages to high quality PNG/JPEG images for the mapper."""
    try:
        import fitz  # PyMuPDF
    except Exception as exc:
        raise RuntimeError("PDF to image conversion needs PyMuPDF. Add 'PyMuPDF' to requirements.txt, commit, and redeploy Render.") from exc

    output_paths = []
    fmt = "jpeg" if str(image_format).upper() in ["JPG", "JPEG"] else "png"
    matrix_scale = max(float(dpi), 72.0) / 72.0
    mat = fitz.Matrix(matrix_scale, matrix_scale)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        if not page_indexes:
            page_indexes = list(range(doc.page_count))
        for page_index in page_indexes:
            if page_index < 0 or page_index >= doc.page_count:
                continue
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            if fmt == "jpeg":
                # Re-save through Pillow to control JPEG output and keep file size sensible.
                img = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
                img_buffer = BytesIO()
                img.save(img_buffer, format="JPEG", quality=92, optimize=True)
                image_bytes = img_buffer.getvalue()
                saved_format = "JPEG"
            else:
                image_bytes = pix.tobytes("png")
                saved_format = "PNG"
            output_paths.append(save_converted_drawing_image(
                job_id,
                pdf_name,
                page_index,
                image_bytes,
                saved_format,
                view_name,
                notes=f"High quality {saved_format} rendered from {pdf_name}, PDF page {page_index + 1}, {dpi} DPI.",
            ))
    return output_paths

def maybe_convert_uploaded_pdf_to_mapper_images(job_id, uploaded_pdfs, view_name, page_selection_text, dpi, image_format, key_prefix):
    """UI helper used by the actual plan/elevation mapper."""
    if not uploaded_pdfs:
        st.error("Choose at least one PDF plan/elevation file first.")
        return 0
    total_saved = 0
    for uploaded_pdf in uploaded_pdfs:
        try:
            pdf_bytes = uploaded_pdf.getvalue()
            page_count = get_pdf_page_count_from_bytes(pdf_bytes)
            if page_count <= 0:
                st.error(f"Could not read pages from {uploaded_pdf.name}.")
                continue
            page_indexes = parse_page_selection(page_selection_text, page_count)
            if not page_indexes:
                st.error(f"No valid pages selected for {uploaded_pdf.name}. This PDF has {page_count} page(s).")
                continue
            if len(page_indexes) > 25:
                st.warning(f"{uploaded_pdf.name}: converting the first 25 selected pages only to keep Render responsive.")
                page_indexes = page_indexes[:25]
            saved_paths = convert_pdf_bytes_to_drawing_images(
                job_id,
                pdf_bytes,
                uploaded_pdf.name,
                page_indexes,
                view_name,
                dpi=int(dpi),
                image_format=image_format,
            )
            total_saved += len(saved_paths)
            st.success(f"Converted {len(saved_paths)} page(s) from {uploaded_pdf.name} into mapper-ready image(s).")
        except Exception as e:
            st.error(f"Could not convert {uploaded_pdf.name}: {e}")
    return total_saved

def delete_job_document(document_id):
    """Delete a job document record, remove linked mapper zones, and remove the saved file if it still exists."""
    doc_df = df_query("SELECT file_path FROM job_documents WHERE id = ?", (document_id,))
    if not doc_df.empty:
        file_path = str(doc_df.iloc[0]["file_path"] or "")
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

    # Drawing mapper zones can reference job_documents. Remove these first so PostgreSQL
    # does not block deletion when a PDF/image was attached to the wrong job.
    try:
        execute("DELETE FROM drawing_progress_zones WHERE document_id = ?", (document_id,))
    except Exception:
        pass

    execute("DELETE FROM job_documents WHERE id = ?", (document_id,))

def move_job_document_to_job(document_id, new_job_id):
    """Move a document record to another job folder. The existing file path is retained."""
    execute("UPDATE job_documents SET job_id = ? WHERE id = ?", (new_job_id, document_id))
    try:
        execute("UPDATE drawing_progress_zones SET job_id = ? WHERE document_id = ?", (new_job_id, document_id))
    except Exception:
        pass

def job_document_id_for_path(file_path):
    doc_df = df_query("SELECT id FROM job_documents WHERE file_path = ? ORDER BY id DESC LIMIT 1", (file_path,))
    if doc_df.empty:
        return None
    return int(doc_df.iloc[0]["id"])

def download_mime_for_file(file_name):
    ext = os.path.splitext(str(file_name or ""))[1].lower()
    if ext == ".pdf":
        return "application/pdf"
    if ext in [".jpg", ".jpeg"]:
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext in [".xlsx", ".xlsm"]:
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext == ".xls":
        return "application/vnd.ms-excel"
    if ext == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if ext == ".doc":
        return "application/msword"
    if ext == ".csv":
        return "text/csv"
    return "application/octet-stream"

PDF_IMPORT_GROUPS = {
    "Job Setup / Tender": [
        "Architectural Plans",
        "Specifications",
        "Colour Schedule",
        "Scope of Works",
        "Quote / Estimate",
        "Purchase Order",
        "Contract / Work Order",
    ],
    "Site Operations": [
        "Paint & Materials Order",
        "Equipment Checklist",
        "Timesheet / Day Labour",
        "Wage / Payroll Support",
        "Staff Schedule / Roster",
        "Safety / SWMS",
    ],
    "Claims / Completion": [
        "Progress Claim / Invoice",
        "Variation",
        "Completion / Sign-off",
        "Defects / QA",
        "Builder Correspondence",
        "Other PDF",
    ],
}

PDF_IMPORT_NOTES = {
    "Architectural Plans": "Plans used for estimating, take-off, progress model and site reference.",
    "Specifications": "Specification document used for scope, products, systems and inclusions/exclusions.",
    "Colour Schedule": "Colour/finish schedule used for site setup, ordering and quality checks.",
    "Scope of Works": "Scope document used for quote, site team instructions and variations.",
    "Quote / Estimate": "Quote, estimate or working document linked to this job.",
    "Purchase Order": "Builder/client purchase order or approval document.",
    "Contract / Work Order": "Contract, work order or acceptance document.",
    "Paint & Materials Order": "Paint/material order PDF linked to this job.",
    "Equipment Checklist": "Equipment/checklist PDF linked to this job.",
    "Timesheet / Day Labour": "Timesheet or day labour PDF linked to this job.",
    "Wage / Payroll Support": "Wage or payroll support PDF linked to this job.",
    "Staff Schedule / Roster": "Roster or staff schedule PDF linked to this job.",
    "Safety / SWMS": "Safety, SWMS or WHS PDF linked to this job.",
    "Progress Claim / Invoice": "Progress claim, invoice or billing PDF linked to this job.",
    "Variation": "Variation PDF linked to this job.",
    "Completion / Sign-off": "Completion, handover or sign-off PDF linked to this job.",
    "Defects / QA": "Defects, QA, ITP or touch-up PDF linked to this job.",
    "Builder Correspondence": "Builder/client correspondence saved as a PDF.",
    "Other PDF": "Other PDF linked to this job.",
}

def pdf_import_categories_for_context(context="all"):
    """Return sensible PDF import categories for a page/context."""
    context = str(context or "all").lower()
    if context in ["takeoff", "estimating", "plans"]:
        return [
            "Architectural Plans", "Specifications", "Colour Schedule", "Scope of Works",
            "Quote / Estimate", "Purchase Order", "Contract / Work Order",
        ]
    if context in ["progress", "billing", "claims"]:
        return [
            "Progress Claim / Invoice", "Variation", "Purchase Order", "Completion / Sign-off",
            "Defects / QA", "Builder Correspondence", "Architectural Plans",
        ]
    if context in ["site", "operations"]:
        return [
            "Paint & Materials Order", "Equipment Checklist", "Timesheet / Day Labour",
            "Staff Schedule / Roster", "Safety / SWMS", "Variation", "Completion / Sign-off",
        ]
    if context in ["timesheets"]:
        return ["Timesheet / Day Labour", "Staff Schedule / Roster", "Safety / SWMS", "Other PDF"]
    if context in ["wages"]:
        return ["Wage / Payroll Support", "Timesheet / Day Labour", "Staff Schedule / Roster", "Other PDF"]
    if context in ["equipment"]:
        return ["Equipment Checklist", "Safety / SWMS", "Paint & Materials Order", "Other PDF"]
    if context in ["materials"]:
        return ["Paint & Materials Order", "Purchase Order", "Colour Schedule", "Specifications", "Other PDF"]
    if context in ["variations"]:
        return ["Variation", "Builder Correspondence", "Scope of Works", "Photos / Evidence PDF", "Other PDF"]
    if context in ["completion"]:
        return ["Completion / Sign-off", "Defects / QA", "Progress Claim / Invoice", "Other PDF"]
    # Default = everything sensible, grouped in a stable order.
    categories = []
    for group_items in PDF_IMPORT_GROUPS.values():
        categories.extend(group_items)
    return categories

def guess_drawing_view_from_filename_global(file_name, fallback_view="Auto-detect from file name"):
    """Guess mapper view from a plan/elevation image or PDF file name."""
    if fallback_view and fallback_view != "Auto-detect from file name":
        return fallback_view
    name = str(file_name or "").lower().replace("_", " ").replace("-", " ")
    checks = [
        (["front", "north elevation", "north elev"], "Front Elevation"),
        (["rear", "back", "south elevation", "south elev"], "Rear Elevation"),
        (["left", "west elevation", "west elev"], "Left Elevation"),
        (["right", "east elevation", "east elev"], "Right Elevation"),
        (["ground", "gf", "floor plan", "floorplan", "level 0"], "Ground Floor Plan"),
        (["level 1", "lvl 1", "first floor", "l1"], "Level 1 Plan"),
        (["level 2", "lvl 2", "second floor", "l2"], "Level 2 Plan"),
        (["roof", "soffit", "eaves"], "Roof / Soffit Plan"),
        (["internal", "room", "rooms"], "Internal Areas"),
        (["site plan", "site"], "Site Plan"),
    ]
    for keywords, label in checks:
        if any(keyword in name for keyword in keywords):
            return label
    return "Other"

def classify_uploaded_job_file(file_name):
    """Auto-classify uploaded job files so plan sets go into the correct JobHub sections."""
    name = str(file_name or "").lower().replace("_", " ").replace("-", " ")
    ext = os.path.splitext(name)[1].lower()
    if any(x in name for x in ["colour", "color", "finish", "finishes", "schedule"]):
        return "Colour Schedule"
    if any(x in name for x in ["spec", "specification", "architectural specification"]):
        return "Specifications"
    if any(x in name for x in ["scope", "sow", "work scope"]):
        return "Scope of Works"
    if any(x in name for x in ["quote", "estimate", "pricing", "tender"]):
        return "Quote / Estimate"
    if any(x in name for x in ["po", "purchase order", "work order", "contract"]):
        return "Purchase Order"
    if any(x in name for x in ["claim", "invoice"]):
        return "Progress Claim / Invoice"
    if any(x in name for x in ["variation", "var"]):
        return "Variation"
    if any(x in name for x in ["defect", "qa", "itp", "punch", "touch up"]):
        return "Defects / QA"
    if any(x in name for x in ["swms", "safety", "whs", "jsa", "risk"]):
        return "Safety / SWMS"
    if any(x in name for x in ["elevation", "drawing", "architectural", "plans", "plan", "roof", "floor"]):
        return "Architectural Plans"
    if ext in [".png", ".jpg", ".jpeg", ".webp"]:
        return f"Drawing Mapper - {guess_drawing_view_from_filename_global(file_name)}"
    if ext == ".pdf":
        return "Other PDF"
    return "Other"

def render_remove_or_move_wrong_job_documents(job_id, key_prefix="wrong_job_docs"):
    """Allow admins/managers to remove or move PDFs/images uploaded to the wrong job."""
    st.markdown("### Remove / move files attached to the wrong job")
    docs = df_query("""
        SELECT id, document_type AS type, file_name, file_path, created_at, notes
        FROM job_documents
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))
    if docs.empty:
        st.info("No documents are attached to this job yet.")
        return

    options = {
        f"{int(r['id'])} | {r['type']} | {r['file_name']} | {r['created_at']}": int(r["id"])
        for _, r in docs.iterrows()
    }
    selected = st.multiselect(
        "Select wrong file(s)",
        list(options.keys()),
        key=f"{key_prefix}_selected_{job_id}",
    )
    selected_ids = [options[label] for label in selected]
    action = st.radio(
        "Action",
        ["Delete selected from this job", "Move selected to another job"],
        horizontal=True,
        key=f"{key_prefix}_action_{job_id}",
    )
    target_job_id = None
    if action == "Move selected to another job":
        job_options = get_job_options()
        target_options = {label: int(value) for label, value in job_options.items() if int(value) != int(job_id)}
        if target_options:
            target_label = st.selectbox("Move to job", list(target_options.keys()), key=f"{key_prefix}_target_{job_id}")
            target_job_id = target_options[target_label]
        else:
            st.warning("No other jobs exist to move the selected files to.")

    confirm_text = st.text_input(
        "Type CONFIRM to apply",
        key=f"{key_prefix}_confirm_{job_id}",
        placeholder="CONFIRM",
    )
    if st.button("Apply document fix", key=f"{key_prefix}_apply_{job_id}", disabled=not selected_ids):
        if confirm_text.strip().upper() != "CONFIRM":
            st.error("Type CONFIRM first.")
            return
        if action == "Delete selected from this job":
            for doc_id in selected_ids:
                delete_job_document(doc_id)
            st.success(f"Deleted {len(selected_ids)} selected file(s) from this job.")
            refresh()
        else:
            if not target_job_id:
                st.error("Choose a destination job first.")
                return
            for doc_id in selected_ids:
                move_job_document_to_job(doc_id, target_job_id)
            st.success(f"Moved {len(selected_ids)} selected file(s) to the selected job.")
            refresh()

def render_smart_plan_set_import(job_id, key_prefix="smart_plan_set", expanded=False):
    """One-stop plan set import: categorise files, convert plan PDFs, and optionally run AI take-off."""
    with st.expander("Smart plan set import - upload new plans and place info in the right spots", expanded=expanded):
        st.caption("Upload a whole plan/spec/colour/scope set. JobHub auto-saves each file as Plans, Specs, Colour Schedule, Scope, etc. Plan PDFs can also be converted to mapper images.")
        uploaded_files = st.file_uploader(
            "Upload new plan/spec/scope/colour file set",
            type=["pdf", "doc", "docx", "xls", "xlsx", "csv", "jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key=f"{key_prefix}_files_{job_id}",
        )
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        auto_convert = c1.checkbox("Convert plan PDFs to mapper images", value=True, key=f"{key_prefix}_convert_{job_id}")
        page_selection = c2.text_input("Plan PDF pages", value="", placeholder="blank = all, or 1,3,5-7", key=f"{key_prefix}_pages_{job_id}")
        dpi = c3.selectbox("Image quality", [150, 200, 220, 300], index=2, key=f"{key_prefix}_dpi_{job_id}")
        image_format = c4.selectbox("Image output", ["PNG", "JPEG"], index=0, key=f"{key_prefix}_format_{job_id}")
        run_ai_after_import = st.checkbox(
            "After upload, run AI take-off and place results into Painting Take-off + Progress/Billing",
            value=False,
            key=f"{key_prefix}_run_ai_{job_id}",
        )
        extra_notes = st.text_area(
            "Import / scope notes",
            value="New plan set imported. Review all AI/manual take-off data against the drawings before issuing price or claim.",
            key=f"{key_prefix}_notes_{job_id}",
        )
        ai_confirmed = True
        if run_ai_after_import:
            ai_ready, ai_msg = ai_backend_ready()
            if not ai_ready:
                st.warning("AI take-off is unavailable: " + ai_msg)
            ai_confirmed = bool(confirm_ai_api_spend("Confirm: use OpenAI/Ollama AI for plan set extraction", key=f"{key_prefix}_ai_confirm_{job_id}"))

        if st.button("Import new plan set", key=f"{key_prefix}_button_{job_id}", use_container_width=True):
            if not uploaded_files:
                st.error("Choose at least one file first.")
                return
            saved_doc_ids = []
            saved_messages = []
            converted_count = 0
            for uploaded in uploaded_files:
                try:
                    doc_type = classify_uploaded_job_file(uploaded.name)
                    file_path = save_uploaded_job_document(job_id, uploaded, doc_type, notes=extra_notes)
                    doc_id = job_document_id_for_path(file_path)
                    if doc_id:
                        saved_doc_ids.append(doc_id)
                    saved_messages.append(f"{uploaded.name} → {doc_type}")

                    name_lower = str(uploaded.name).lower()
                    is_pdf = name_lower.endswith(".pdf")
                    looks_like_plan = doc_type == "Architectural Plans" or any(x in name_lower for x in ["plan", "drawing", "elevation", "architectural"])
                    if auto_convert and is_pdf and looks_like_plan:
                        pdf_bytes = uploaded.getvalue()
                        page_count = get_pdf_page_count_from_bytes(pdf_bytes)
                        page_indexes = parse_page_selection(page_selection, page_count)
                        if len(page_indexes) > 25:
                            st.warning(f"{uploaded.name}: converting first 25 selected pages only.")
                            page_indexes = page_indexes[:25]
                        view_name = guess_drawing_view_from_filename_global(uploaded.name)
                        saved_images = convert_pdf_bytes_to_drawing_images(
                            job_id,
                            pdf_bytes,
                            uploaded.name,
                            page_indexes,
                            view_name,
                            dpi=int(dpi),
                            image_format=image_format,
                        )
                        converted_count += len(saved_images)
                except Exception as e:
                    st.error(f"Could not import {uploaded.name}: {e}")

            if saved_messages:
                st.success(f"Imported {len(saved_messages)} file(s) into the selected job.")
                with st.expander("Where the files were placed", expanded=True):
                    for msg in saved_messages:
                        st.write(msg)
                if converted_count:
                    st.success(f"Created {converted_count} mapper-ready plan/elevation image(s). Open the Plan / Elevation Mapper and select the drawing background.")

            if run_ai_after_import and saved_doc_ids:
                ai_ready, ai_msg = ai_backend_ready()
                if not ai_ready:
                    st.warning("Files were imported, but AI take-off was not run: " + ai_msg)
                elif not ai_confirmed:
                    st.warning("Files were imported, but AI take-off was not run because AI spend was not confirmed.")
                else:
                    with st.spinner("Reading imported plan set and creating take-off/progress model..."):
                        ai_data, err, warnings = generate_ai_takeoff_lines(job_id, selected_doc_ids=saved_doc_ids, extra_scope_notes=extra_notes)
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
                        st.success("AI take-off created and placed into Painting Take-off + Progress/Billing. Review every line before using it for price or claim.")
            refresh()

def render_quick_pdf_import_buttons(job_id, categories=None, title="Quick PDF Import Buttons", key_prefix="quick_pdf_import", expanded=False):
    """Render multiple small PDF-only uploaders and save PDFs straight into the selected job folder."""
    if not job_id:
        st.info("Select a job before importing PDFs.")
        return

    categories = categories or pdf_import_categories_for_context("all")
    # De-duplicate while keeping order.
    seen = set()
    categories = [c for c in categories if not (c in seen or seen.add(c))]

    with st.expander(title, expanded=expanded):
        st.caption("Use these buttons when you only need to attach a PDF to the job. Files save into the selected Job Folder and appear in Plans / Docs and reports.")
        note_default = "Imported using JobHub PDF import buttons."
        import_notes = st.text_input(
            "Optional import notes",
            value="",
            placeholder=note_default,
            key=f"{key_prefix}_notes_{job_id}",
        )
        cols_per_row = 3
        for start in range(0, len(categories), cols_per_row):
            row_categories = categories[start:start + cols_per_row]
            cols = st.columns(cols_per_row)
            for idx, category in enumerate(row_categories):
                with cols[idx]:
                    st.markdown(f"**{category}**")
                    st.caption(PDF_IMPORT_NOTES.get(category, "PDF linked to this job."))
                    uploader_key = f"{key_prefix}_{safe_file_name(category).lower()}_{job_id}_{start}_{idx}"
                    uploaded_pdfs = st.file_uploader(
                        f"Import {category} PDF",
                        type=["pdf"],
                        accept_multiple_files=True,
                        key=uploader_key,
                    )
                    if uploaded_pdfs:
                        if st.button(f"Save {category} PDF(s)", key=f"{uploader_key}_save", use_container_width=True):
                            saved = 0
                            for uploaded_pdf in uploaded_pdfs:
                                try:
                                    notes = import_notes.strip() or PDF_IMPORT_NOTES.get(category, note_default)
                                    save_uploaded_job_document(job_id, uploaded_pdf, category, notes=notes)
                                    saved += 1
                                except Exception as e:
                                    st.error(f"Could not save {uploaded_pdf.name}: {e}")
                            if saved:
                                st.success(f"Saved {saved} {category} PDF{'s' if saved != 1 else ''} to this job.")
                                refresh()

def render_context_pdf_import_for_selected_job(context="all", title="Import PDFs", key_prefix="context_pdf_import"):
    """Page-level helper: choose a job then show PDF import buttons for the relevant context."""
    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first, then import PDFs.")
        return None
    selected_job = st.selectbox("Select Job for PDF Import", list(job_options.keys()), key=f"{key_prefix}_job_select")
    job_id = int(job_options[selected_job])
    render_quick_pdf_import_buttons(
        job_id,
        categories=pdf_import_categories_for_context(context),
        title=title,
        key_prefix=f"{key_prefix}_{context}",
        expanded=True,
    )
    return job_id

def pdf_import_centre_page(default_job_id=None):
    pb_page_header(
        "PDF Import Centre",
        "One place to import plans, specs, scopes, forms, claims, timesheets, schedules and completion PDFs into the correct job folder.",
        "Document Control"
    )

    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first, then import PDFs.")
        return

    labels = list(job_options.keys())
    index = 0
    if default_job_id:
        for i, label in enumerate(labels):
            if int(job_options[label]) == int(default_job_id):
                index = i
                break
    selected_job = st.selectbox("Select Job", labels, index=index, key=f"pdf_import_centre_job_{default_job_id or 'main'}")
    job_id = int(job_options[selected_job])

    render_smart_plan_set_import(job_id, key_prefix=f"pdf_centre_smart_import_{job_id}", expanded=True)

    st.markdown("### Import PDF by area")
    tab_setup, tab_site, tab_claims, tab_all = st.tabs([
        "Job Setup / Tender",
        "Site Operations",
        "Claims / Completion",
        "All PDF Buttons",
    ])
    with tab_setup:
        render_quick_pdf_import_buttons(
            job_id,
            categories=PDF_IMPORT_GROUPS["Job Setup / Tender"],
            title="Import setup, tender and estimating PDFs",
            key_prefix=f"pdf_centre_setup_{job_id}",
            expanded=True,
        )
    with tab_site:
        render_quick_pdf_import_buttons(
            job_id,
            categories=PDF_IMPORT_GROUPS["Site Operations"],
            title="Import site operation PDFs",
            key_prefix=f"pdf_centre_site_{job_id}",
            expanded=True,
        )
    with tab_claims:
        render_quick_pdf_import_buttons(
            job_id,
            categories=PDF_IMPORT_GROUPS["Claims / Completion"],
            title="Import claims, variations and completion PDFs",
            key_prefix=f"pdf_centre_claims_{job_id}",
            expanded=True,
        )
    with tab_all:
        render_quick_pdf_import_buttons(
            job_id,
            categories=pdf_import_categories_for_context("all"),
            title="All sensible PDF import buttons",
            key_prefix=f"pdf_centre_all_{job_id}",
            expanded=True,
        )

    st.divider()
    st.markdown("### PDFs/files already attached to this job")
    attached = df_query("""
        SELECT id, document_type AS 'Type', file_name AS 'File Name', created_at AS 'Uploaded', notes AS 'Notes'
        FROM job_documents
        WHERE job_id = ?
        ORDER BY id DESC
    """, (job_id,))
    if attached.empty:
        st.info("No files have been attached to this job yet.")
    else:
        st.dataframe(attached[["Type", "File Name", "Uploaded", "Notes"]], width="stretch", hide_index=True)
        with st.expander("Remove or move files attached to the wrong job", expanded=False):
            render_remove_or_move_wrong_job_documents(job_id, key_prefix=f"pdf_centre_wrong_docs_{job_id}")

def render_job_documents_panel(job_id, allow_upload=True, allow_delete=True, key_prefix="job_docs"):
    st.markdown("### Plans / Specs / Job Documents")

    if allow_upload:
        render_smart_plan_set_import(job_id, key_prefix=f"{key_prefix}_smart_import_{job_id}", expanded=False)

    if allow_upload:
        render_quick_pdf_import_buttons(
            job_id,
            categories=pdf_import_categories_for_context("all"),
            title="PDF Import Buttons",
            key_prefix=f"{key_prefix}_quick_pdf",
            expanded=False,
        )

        with st.expander("General upload - PDF, Word, Excel, CSV or images", expanded=True):
            doc_type = st.selectbox(
                "Document Type",
                [
                    "Architectural Plans",
                    "Specifications",
                    "Colour Schedule",
                    "Scope of Works",
                    "Quote / Estimate",
                    "Purchase Order",
                    "Variation",
                    "Completion / Sign-off",
                    "Safety / SWMS",
                    "Other",
                ],
                key=f"{key_prefix}_document_type_{job_id}",
            )
            doc_notes = st.text_area("Document Notes", key=f"{key_prefix}_document_notes_{job_id}")
            uploaded_documents = st.file_uploader(
                "Upload one or more files",
                type=["pdf", "doc", "docx", "xls", "xlsx", "csv", "jpg", "jpeg", "png", "webp"],
                accept_multiple_files=True,
                key=f"{key_prefix}_file_uploader_{job_id}",
            )

            if st.button("Upload Document(s) to This Job", key=f"{key_prefix}_upload_button_{job_id}"):
                if not uploaded_documents:
                    st.error("Choose at least one file to upload.")
                else:
                    saved_count = 0
                    for uploaded_file in uploaded_documents:
                        try:
                            save_uploaded_job_document(job_id, uploaded_file, doc_type, notes=doc_notes)
                            saved_count += 1
                        except Exception as e:
                            st.error(f"Could not upload {uploaded_file.name}: {e}")
                    if saved_count:
                        st.success(f"Uploaded {saved_count} document(s) to this job folder.")
                        refresh()

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
        st.info("No plans, specs or documents have been attached to this job yet.")
        return

    st.markdown("### Attached Documents")
    for _, doc in documents_df.iterrows():
        doc_id = int(doc["id"])
        file_name = str(doc["File Name"] or "")
        file_path = str(doc["file_path"] or "")
        document_type = str(doc["Document Type"] or "Document")
        notes = str(doc["Notes"] or "")

        with st.container(border=True):
            st.markdown(f"**{document_type}**")
            st.write(file_name)
            st.caption(f"Created: {doc['Created At']}")
            if notes:
                st.caption(notes)

            cols = st.columns([1, 1]) if allow_delete else st.columns([1])
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    cols[0].download_button(
                        label="Download",
                        data=f,
                        file_name=file_name,
                        mime=download_mime_for_file(file_name),
                        key=f"{key_prefix}_download_{doc_id}",
                    )
            else:
                cols[0].warning("File path not found on disk.")

            if allow_delete:
                delete_confirm = st.checkbox("Delete this document", key=f"{key_prefix}_delete_confirm_{doc_id}")
                if cols[1].button("Delete", key=f"{key_prefix}_delete_button_{doc_id}"):
                    if not delete_confirm:
                        st.error("Tick the delete checkbox first.")
                    else:
                        delete_job_document(doc_id)
                        st.success("Document deleted.")
                        refresh()

def generate_equipment_checklist_pdf(job_id):
    job = get_job_details_for_pdf(job_id)

    if not job:
        raise ValueError("Job not found.")

    job_no = str(job.get("job_no") or f"job_{job_id}")
    job_folder = get_job_folder(job_no)

    output_path = os.path.join(
        job_folder,
        f"{safe_file_name(job_no)}_equipment_checklist_fillable.pdf"
    )

    fields = {
        "p1_job_0": job.get("job_no", ""),
        "p1_job_1": job.get("job_name", ""),
        "p1_job_2": job.get("site_address", ""),
        "p1_job_3": job.get("builder_client", ""),
        "p1_team_0": job.get("leading_hand", ""),
        "p1_team_1": "",
        "p1_team_extra": "",
    }

    try:
        equipment_df = df_query("""
            SELECT i.item_name,
                   COALESCE(r.qty_required, 0) AS qty_required,
                   COALESCE(r.qty_taken, 0) AS qty_taken,
                   COALESCE(r.qty_returned, 0) AS qty_returned,
                   COALESCE(r.condition_in, '') AS condition_in,
                   COALESCE(r.notes, '') AS notes
            FROM equipment_checklist_items i
            LEFT JOIN equipment_checklist_records r
                ON r.checklist_item_id = i.id
               AND r.job_id = ?
        """, (job_id,))

        record_map = {
            str(row["item_name"]).strip().lower(): row
            for _, row in equipment_df.iterrows()
        }

        for prefix, (_, item_names) in PDF_CHECKLIST_ITEMS.items():
            for idx, item_name in enumerate(item_names):
                row = record_map.get(item_name.strip().lower())
                if row is None:
                    continue

                qty_required = float(row["qty_required"] or 0)
                qty_taken = float(row["qty_taken"] or 0)
                qty_returned = float(row["qty_returned"] or 0)

                fields[f"{prefix}_{idx}_req"] = "" if qty_required == 0 else str(qty_required)
                fields[f"{prefix}_{idx}_loaded"] = "" if qty_taken == 0 else str(qty_taken)
                fields[f"{prefix}_{idx}_returned"] = "" if qty_returned == 0 else str(qty_returned)
                fields[f"{prefix}_{idx}_missing"] = str(row["condition_in"] or row["notes"] or "")

    except Exception:
        pass

    fill_pdf_template(EQUIPMENT_TEMPLATE_PDF, output_path, fields)
    attach_document_to_job(job_id, "Equipment Checklist", output_path)

    return output_path

def generate_paint_order_pdf(job_id):
    job = get_job_details_for_pdf(job_id)

    if not job:
        raise ValueError("Job not found.")

    job_no = str(job.get("job_no") or f"job_{job_id}")
    job_folder = get_job_folder(job_no)

    output_path = os.path.join(
        job_folder,
        f"{safe_file_name(job_no)}_paint_materials_order_fillable.pdf"
    )

    fields = {
        "Project": f"{job.get('job_no', '')} - {job.get('job_name', '')}".strip(" -"),
        "Builder__Client": job.get("builder_client", ""),
        "Site_Address": job.get("site_address", ""),
        "Required_Delivery_Date": "",
        "Ordered_By": "",
    }

    material_rows = []

    try:
        imported_df = df_query("""
            SELECT product,
                   colour,
                   qty_required,
                   qty_loaded
            FROM imported_material_entries
            WHERE job_id = ?
            ORDER BY id
            LIMIT 10
        """, (job_id,))

        for _, row in imported_df.iterrows():
            material_rows.append({
                "product": row["product"],
                "colour": row["colour"],
                "qty_required": row["qty_required"],
                "qty_received": row["qty_loaded"],
            })
    except Exception:
        pass

    if not material_rows:
        try:
            entries_df = df_query("""
                SELECT COALESCE(NULLIF(m.custom_product_name, ''), p.product_name, '') AS product,
                       COALESCE(NULLIF(m.custom_colour, ''), '') AS colour,
                       m.qty_required,
                       m.qty_received
                FROM material_entries m
                LEFT JOIN products p ON p.id = m.product_id
                WHERE m.job_id = ?
                ORDER BY m.id
                LIMIT 10
            """, (job_id,))

            for _, row in entries_df.iterrows():
                material_rows.append({
                    "product": row["product"],
                    "colour": row["colour"],
                    "qty_required": row["qty_required"],
                    "qty_received": row["qty_received"],
                })
        except Exception:
            pass

    for idx, row in enumerate(material_rows[:10]):
        fields[f"product_{idx}"] = str(row.get("product", "") or "")
        fields[f"colour_{idx}"] = str(row.get("colour", "") or "")
        fields[f"qtyreq_{idx}"] = str(row.get("qty_required", "") or "")
        fields[f"qtyrec_{idx}"] = str(row.get("qty_received", "") or "")

    fill_pdf_template(PAINT_ORDER_TEMPLATE_PDF, output_path, fields)
    attach_document_to_job(job_id, "Paint & Materials Order Form", output_path)

    return output_path

def generate_variation_form_pdf(job_id, requested_by="", description="", reason="", notes=""):
    job = get_job_details_for_pdf(job_id)

    if not job:
        raise ValueError("Job not found.")

    if not os.path.exists(VARIATION_TEMPLATE_PDF):
        raise FileNotFoundError(
            "Variation template PDF not found. Add 'PB Variation Form fillable.pdf' to the templates folder."
        )

    job_no = str(job.get("job_no") or f"job_{job_id}")
    job_folder = get_job_folder(job_no)

    count_df = df_query("""
        SELECT COUNT(*) AS c
        FROM job_variations
        WHERE job_id = ?
    """, (job_id,))

    next_no = int(count_df.iloc[0]["c"]) + 1 if not count_df.empty else 1
    variation_no = f"VAR-{next_no:03d}"

    output_path = os.path.join(
        job_folder,
        f"{safe_file_name(job_no)}_{variation_no}_variation_form_fillable.pdf"
    )

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_text = str(date.today())

    execute("""
        INSERT INTO job_variations
        (
            job_id,
            variation_no,
            description,
            reason,
            amount_ex_gst,
            status,
            sent_date,
            approved_date,
            approved_by,
            notes,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        variation_no,
        description,
        reason,
        0,
        "Draft",
        "",
        "",
        "",
        f"Employee/request form generated by {requested_by}. {notes}",
        created_at,
    ))

    fields = {
        "Variation_No": variation_no,
        "Job_No": job.get("job_no", ""),
        "Job_Name": job.get("job_name", ""),
        "Project": f"{job.get('job_no', '')} - {job.get('job_name', '')}".strip(" -"),
        "Builder_Client": job.get("builder_client", ""),
        "Site_Address": job.get("site_address", ""),
        "Requested_By": requested_by,
        "Date": today_text,
        "Description": description,
        "Reason": reason,
        "Notes": notes,
        "Amount_Ex_GST": "",
        "Approved_By": "",
        "Approved_Date": "",
    }

    fill_pdf_template(VARIATION_TEMPLATE_PDF, output_path, fields)

    attach_document_to_job(
        job_id,
        "Variation Form",
        output_path,
        notes=f"Generated by employee/user: {requested_by}"
    )

    return output_path, variation_no

