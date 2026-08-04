from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import pandas as pd
import streamlit as st

from jobhub.job_pack_matching import expand_nested_job_pack_uploads, match_job_pack_to_jobs

from .common import AppContext, _clean, _float, _int, job_options
from .estimating import recalc_estimate
from .ui import header, rerun_success


MAX_PACK_BYTES = 150 * 1024 * 1024
MAX_FILES = 300
MAX_EXTRACTED_BYTES = 350 * 1024 * 1024


def _header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _column(frame: pd.DataFrame, *aliases: str, default: Any = "") -> pd.Series:
    lookup = {_header(column): column for column in frame.columns}
    source = next((lookup[_header(alias)] for alias in aliases if _header(alias) in lookup), None)
    return frame[source] if source else pd.Series([default] * len(frame))


def _normalise_frame(frame: pd.DataFrame, mapping: dict[str, tuple[str, ...]]) -> pd.DataFrame:
    result = pd.DataFrame()
    for target, aliases in mapping.items():
        result[target] = _column(frame, *aliases)
    return result.fillna("")


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name and not path.is_absolute() and ".." not in path.parts and not re.match(r"^[A-Za-z]:", name))


def _read_csv(archive: zipfile.ZipFile, filename: str) -> pd.DataFrame:
    match = next((name for name in archive.namelist() if PurePosixPath(name).name.casefold() == filename.casefold()), None)
    if not match:
        return pd.DataFrame()
    return pd.read_csv(io.BytesIO(archive.read(match))).fillna("")


def _uploaded_bytes(upload: Any) -> bytes:
    data = upload.getvalue() if hasattr(upload, "getvalue") else upload.read()
    data = bytes(data or b"")
    if not data:
        raise ValueError("The uploaded ZIP is empty.")
    if len(data) > MAX_PACK_BYTES:
        raise ValueError("The Job Pack is larger than 150 MB.")
    return data


def parse_job_pack(upload: Any) -> dict[str, Any]:
    content = _uploaded_bytes(upload)
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("The upload is not a valid ZIP file.") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError("The Job Pack contains too many files.")
        if sum(max(0, int(info.file_size or 0)) for info in infos) > MAX_EXTRACTED_BYTES:
            raise ValueError("The uncompressed Job Pack is too large.")
        unsafe = [info.filename for info in infos if not _safe_member(info.filename)]
        if unsafe:
            raise ValueError("The Job Pack contains an unsafe file path.")
        manifest_name = next((name for name in archive.namelist() if PurePosixPath(name).name.casefold() == "job_manifest.json"), None)
        if not manifest_name:
            raise ValueError("job_manifest.json is missing.")
        manifest = json.loads(archive.read(manifest_name).decode("utf-8-sig"))
        job = dict(manifest.get("job") or {})
        builder = dict(manifest.get("builder_client") or {})
        estimate = dict(manifest.get("estimate") or {})
        summary = {
            "pack_id": _clean(manifest.get("pack_id")) or Path(str(getattr(upload, "name", "Job Pack"))).stem,
            "revision": _clean(manifest.get("revision")) or "1",
            "job_no": _clean(job.get("job_no")),
            "job_name": _clean(job.get("job_name")),
            "site_address": _clean(job.get("site_address")),
            "builder_client": _clean(builder.get("name")),
            "contract_value_ex_gst": _float(job.get("contract_value_ex_gst")),
        }
        documents = []
        data_names = {
            "job_manifest.json", "takeoff_lines.csv", "labour_budget.csv",
            "material_allowances.csv", "colour_schedule.csv", "purchase_orders.csv",
            "job_stages.csv", "readme.txt",
        }
        for info in infos:
            base = PurePosixPath(info.filename).name.casefold()
            if info.is_dir() or base in data_names or base.startswith("place_"):
                continue
            documents.append((info.filename, archive.read(info)))
        return {
            "name": str(getattr(upload, "name", "Job Pack.zip")),
            "content": content,
            "manifest": manifest,
            "summary": summary,
            "job": job,
            "builder": builder,
            "estimate": estimate,
            "preferences": dict(manifest.get("import_preferences") or {}),
            "lines": _read_csv(archive, "takeoff_lines.csv"),
            "materials": _read_csv(archive, "material_allowances.csv"),
            "colours": _read_csv(archive, "colour_schedule.csv"),
            "purchase_orders": _read_csv(archive, "purchase_orders.csv"),
            "stages": _read_csv(archive, "job_stages.csv"),
            "documents": documents,
        }


def build_job_pack_template() -> bytes:
    manifest = {
        "pack_version": "2.0-lean",
        "pack_id": "PB-JOBNO-JOB-PACK",
        "revision": "1",
        "job": {
            "job_no": "PB00000", "job_name": "Example Project", "site_address": "",
            "status": "Quoted", "leading_hand": "", "start_date": "", "end_date": "",
            "contract_value_ex_gst": 0, "notes": "",
        },
        "builder_client": {"type": "Builder", "name": "Example Builder Pty Ltd"},
        "estimate": {
            "estimate_no": "PB00000-TO-01", "estimate_date": date.today().isoformat(),
            "revision": "1", "status": "Draft", "labour_hours": 0,
            "labour_rate": 125, "material_allowance": 0,
        },
        "production": {"painter_day_hours": 8, "painter_day_value_ex_gst": 1000},
        "import_preferences": {
            "update_job_record": True, "create_estimate": True, "import_materials": True,
            "attach_documents": True, "import_stages": True,
        },
    }
    lines = pd.DataFrame([{
        "Section": "Internal", "Item Description": "Walls - plasterboard", "Qty": 100,
        "Unit": "m2", "Unit Rate": 0, "Estimated Labour Hours": 12,
        "Material Allowance": 450, "Substrate": "Plasterboard",
        "Location": "Ground floor", "Coating System": "1 sealer + 2 finish coats",
        "Colour / Finish": "To colour schedule", "Notes": "Replace this example.",
    }])
    materials = pd.DataFrame([{
        "Product Code / Ref": "CUSTOM", "Product / Material Name": "Interior low sheen",
        "Supplier": "Haymes", "Unit": "15L", "Unit Price Ex GST": 150,
        "Colour / Finish": "To colour schedule", "Qty Required": 3,
        "Location": "Ground floor walls", "Notes": "Preliminary allowance",
    }])
    purchase_orders = pd.DataFrame([{
        "PO Number": "PO-EXAMPLE", "Description": "Example purchase order",
        "Amount Ex GST": 0, "Status": "Active", "Received Date": "", "Notes": "",
    }])
    stages = pd.DataFrame([{
        "Stage Name": "Internal", "Order": 1, "Job %": 100, "PO Number": "PO-EXAMPLE",
        "Status": "Planned", "Start Date": "", "End Date": "", "Budget Hours": 0, "Notes": "",
    }])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("job_manifest.json", json.dumps(manifest, indent=2))
        archive.writestr("takeoff_lines.csv", lines.to_csv(index=False))
        archive.writestr("material_allowances.csv", materials.to_csv(index=False))
        archive.writestr("purchase_orders.csv", purchase_orders.to_csv(index=False))
        archive.writestr("job_stages.csv", stages.to_csv(index=False))
        archive.writestr("original_plans/PLACE_PLANS_HERE.txt", "Place plans here.")
        archive.writestr("README.txt", "Premier Brushworks Job Pack template. Increase revision when reissuing a pack.")
    return output.getvalue()


def _ensure_import_schema(ctx: AppContext) -> None:
    pk = "SERIAL PRIMARY KEY" if ctx.db.postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ctx.db.execute(f"""
        CREATE TABLE IF NOT EXISTS takeoff_pack_imports (
            id {pk}, job_id INTEGER NOT NULL, pack_id TEXT, revision TEXT,
            source_file TEXT, imported_at TEXT, imported_by TEXT,
            line_count INTEGER DEFAULT 0, material_count INTEGER DEFAULT 0,
            document_count INTEGER DEFAULT 0, stage_count INTEGER DEFAULT 0,
            purchase_order_count INTEGER DEFAULT 0,
            UNIQUE(job_id,pack_id,revision)
        )
    """)


def _builder_id(ctx: AppContext, builder: dict[str, Any]) -> int | None:
    name = _clean(builder.get("name"))
    if not name:
        return None
    existing = _int(ctx.db.scalar("SELECT id FROM builders_clients WHERE LOWER(TRIM(name))=LOWER(TRIM(?)) LIMIT 1", (name,), 0))
    if existing:
        return existing
    return ctx.db.insert_id(
        """
        INSERT INTO builders_clients(type,name,contact_name,phone,email,address,qbcc,abn,terms,notes)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        tuple(_clean(builder.get(key)) for key in ("type", "name", "contact_name", "phone", "email", "address", "qbcc", "abn", "terms", "notes")),
    )


def _target_job(ctx: AppContext, pack: dict[str, Any], target_job_id: int | None, create_new: bool) -> int:
    job = pack["job"]
    builder_id = _builder_id(ctx, pack["builder"])
    if create_new:
        job_no = _clean(job.get("job_no"))
        job_name = _clean(job.get("job_name"))
        if not job_no or not job_name:
            raise ValueError("A new job requires job_no and job_name in the manifest.")
        return ctx.db.insert_id(
            """
            INSERT INTO jobs
            (job_no,job_name,builder_client_id,site_address,status,leading_hand,start_date,end_date,contract_value,notes,row_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,1)
            """,
            (
                job_no, job_name, builder_id, _clean(job.get("site_address")),
                _clean(job.get("status")) or "Quoted", _clean(job.get("leading_hand")),
                _clean(job.get("start_date")), _clean(job.get("end_date")),
                _float(job.get("contract_value_ex_gst")), _clean(job.get("notes")),
            ),
        )
    if not target_job_id:
        raise ValueError("Choose a target job.")
    if pack["preferences"].get("update_job_record", True):
        ctx.db.execute(
            """
            UPDATE jobs SET job_name=COALESCE(NULLIF(?,''),job_name),builder_client_id=COALESCE(?,builder_client_id),
                site_address=COALESCE(NULLIF(?,''),site_address),status=COALESCE(NULLIF(?,''),status),
                leading_hand=COALESCE(NULLIF(?,''),leading_hand),start_date=COALESCE(NULLIF(?,''),start_date),
                end_date=COALESCE(NULLIF(?,''),end_date),contract_value=CASE WHEN ?>0 THEN ? ELSE contract_value END,
                notes=COALESCE(NULLIF(?,''),notes),row_version=COALESCE(row_version,1)+1 WHERE id=?
            """,
            (
                _clean(job.get("job_name")), builder_id, _clean(job.get("site_address")),
                _clean(job.get("status")), _clean(job.get("leading_hand")), _clean(job.get("start_date")),
                _clean(job.get("end_date")), _float(job.get("contract_value_ex_gst")),
                _float(job.get("contract_value_ex_gst")), _clean(job.get("notes")), int(target_job_id),
            ),
        )
    return int(target_job_id)


def _import_estimate(ctx: AppContext, job_id: int, pack: dict[str, Any]) -> tuple[int, int]:
    if not pack["preferences"].get("create_estimate", True):
        return 0, 0
    estimate = pack["estimate"]
    estimate_id = ctx.db.insert_id(
        """
        INSERT INTO estimate_working_sheets
        (job_id,estimate_no,estimate_date,revision,status,labour_hours,labour_rate,material_allowance,
         total_ex_gst,gst_amount,total_inc_gst,notes,archived,updated_at)
        VALUES (?,?,?,?,?,?,?,?,0,0,0,?,0,?)
        """,
        (
            job_id, _clean(estimate.get("estimate_no")) or f"JOB-{job_id}-PACK",
            _clean(estimate.get("estimate_date")) or date.today().isoformat(),
            _clean(estimate.get("revision")) or pack["summary"]["revision"],
            _clean(estimate.get("status")) or "Draft", _float(estimate.get("labour_hours")),
            _float(estimate.get("labour_rate")) or 125.0, _float(estimate.get("material_allowance")),
            _clean(estimate.get("notes")) or "Imported from Premier Brushworks Job Pack.",
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    source = pack["lines"]
    if source.empty:
        recalc_estimate(ctx, estimate_id)
        return estimate_id, 0
    lines = _normalise_frame(source, {
        "section": ("Section",), "description": ("Item Description", "Description", "Item"),
        "qty": ("Qty", "Quantity"), "unit": ("Unit",), "rate": ("Unit Rate", "Rate"),
        "labour": ("Estimated Labour Hours", "Labour Hours"), "materials": ("Material Allowance",),
        "substrate": ("Substrate",), "location": ("Location", "Work Location"),
        "system": ("Coating System",), "colour": ("Colour / Finish", "Colour", "Finish"),
        "notes": ("Notes",),
    })
    rows = []
    for _, row in lines.iterrows():
        description = _clean(row["description"])
        if not description:
            continue
        qty = _float(row["qty"]); rate = _float(row["rate"])
        rows.append((estimate_id, _clean(row["section"]), description, qty, _clean(row["unit"]), rate, round(qty * rate, 2), _float(row["labour"]), _float(row["materials"]), _clean(row["substrate"]), _clean(row["location"]), _clean(row["system"]), _clean(row["colour"]), pack["name"], _clean(row["notes"])))
    ctx.db.execute_many(
        """
        INSERT INTO estimate_line_items
        (estimate_id,section,item_description,qty,unit,unit_rate,line_total,estimated_labour_hours,
         material_allowance,substrate,work_location,coating_system,colour_finish,source_pack,notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    recalc_estimate(ctx, estimate_id)
    return estimate_id, len(rows)


def _import_materials(ctx: AppContext, job_id: int, pack: dict[str, Any]) -> int:
    if not pack["preferences"].get("import_materials", True) or pack["materials"].empty:
        return 0
    source = _normalise_frame(pack["materials"], {
        "code": ("Product Code / Ref", "Product Code", "Code"),
        "name": ("Product / Material Name", "Product Name", "Material"),
        "supplier": ("Supplier",), "unit": ("Unit",), "price": ("Unit Price Ex GST", "Price"),
        "colour": ("Colour / Finish", "Colour"), "qty": ("Qty Required", "Quantity", "Qty"),
        "notes": ("Notes",),
    })
    rows = []
    for _, row in source.iterrows():
        name = _clean(row["name"])
        if name:
            rows.append((job_id, None, _float(row["qty"]), 0, _clean(row["supplier"]), _clean(row["notes"]), _clean(row["code"]), name, _clean(row["supplier"]), _clean(row["unit"]), _float(row["price"]), _clean(row["colour"])))
    ctx.db.execute_many(
        """
        INSERT INTO material_entries
        (job_id,product_id,qty_required,qty_received,supplier,notes,custom_product_code,
         custom_product_name,custom_supplier,custom_unit,custom_unit_price,custom_colour)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    return len(rows)


def _import_pos_and_stages(ctx: AppContext, job_id: int, pack: dict[str, Any]) -> tuple[int, int]:
    po_map: dict[str, int] = {}
    po_count = 0
    if not pack["purchase_orders"].empty:
        source = _normalise_frame(pack["purchase_orders"], {
            "number": ("PO Number", "PO"), "description": ("Description",),
            "amount": ("Amount Ex GST", "Amount"), "status": ("Status",),
            "received": ("Received Date",), "notes": ("Notes",),
        })
        for _, row in source.iterrows():
            number = _clean(row["number"])
            if not number:
                continue
            existing = _int(ctx.db.scalar("SELECT id FROM job_purchase_orders WHERE job_id=? AND po_number=? LIMIT 1", (job_id, number), 0))
            if existing:
                po_id = existing
            else:
                po_id = ctx.db.insert_id(
                    """
                    INSERT INTO job_purchase_orders
                    (job_id,po_number,description,amount_ex_gst,status,received_date,notes,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (job_id, number, _clean(row["description"]), _float(row["amount"]), _clean(row["status"]) or "Active", _clean(row["received"]), _clean(row["notes"]), datetime.now().isoformat(), datetime.now().isoformat()),
                )
                po_count += 1
            po_map[number.casefold()] = po_id
    stage_count = 0
    if pack["preferences"].get("import_stages", True) and not pack["stages"].empty:
        source = _normalise_frame(pack["stages"], {
            "name": ("Stage Name", "Stage"), "order": ("Order", "Sequence"),
            "percent": ("Job %", "Job Percent"), "po": ("PO Number", "PO"),
            "status": ("Status",), "start": ("Start Date",), "end": ("End Date",),
            "hours": ("Budget Hours",), "notes": ("Notes",),
        })
        for _, row in source.iterrows():
            name = _clean(row["name"])
            if not name:
                continue
            existing = _int(ctx.db.scalar("SELECT id FROM job_stages WHERE job_id=? AND LOWER(TRIM(stage_name))=LOWER(TRIM(?)) LIMIT 1", (job_id, name), 0))
            values = (po_map.get(_clean(row["po"]).casefold()), name, max(1, _int(row["order"])), _float(row["percent"]), _clean(row["status"]) or "Planned", _clean(row["start"]), _clean(row["end"]), _float(row["hours"]), _clean(row["notes"]), datetime.now().isoformat())
            if existing:
                ctx.db.execute("UPDATE job_stages SET purchase_order_id=?,stage_name=?,sequence_order=?,job_percent=?,status=?,start_date=?,end_date=?,budget_hours=?,notes=?,updated_at=? WHERE id=?", (*values, existing))
            else:
                ctx.db.execute("INSERT INTO job_stages(job_id,purchase_order_id,stage_name,sequence_order,job_percent,status,start_date,end_date,budget_hours,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (job_id, *values[:-1], values[-1], values[-1]))
                stage_count += 1
    return po_count, stage_count


def _save_documents(ctx: AppContext, job_id: int, pack: dict[str, Any]) -> int:
    if not pack["preferences"].get("attach_documents", True):
        return 0
    job = ctx.db.query("SELECT job_no FROM jobs WHERE id=?", (job_id,))
    job_no = _clean(job.iloc[0].get("job_no")) if not job.empty else str(job_id)
    root = ctx.job_files_dir.resolve()
    folder = (root / re.sub(r"[^A-Za-z0-9._ -]", "_", job_no).strip(" .") / "Imported Job Packs" / re.sub(r"[^A-Za-z0-9._ -]", "_", pack["summary"]["pack_id"]).strip(" .")).resolve()
    if root not in folder.parents:
        raise ValueError("Unsafe Job Pack storage path.")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / Path(pack["name"]).name).write_bytes(pack["content"])
    count = 0
    for member_name, content in pack["documents"]:
        relative = PurePosixPath(member_name)
        target = (folder / Path(*relative.parts)).resolve()
        if folder not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        ctx.db.execute("INSERT INTO job_documents(job_id,document_type,file_name,file_path,created_at,notes,mime_type) VALUES (?,?,?,?,?,?,?)", (job_id, relative.parts[0] if len(relative.parts) > 1 else "Job Pack", target.name, str(target), datetime.now().isoformat(timespec="seconds"), f"Imported from {pack['name']}", "application/octet-stream"))
        count += 1
    return count


def import_job_pack(ctx: AppContext, pack: dict[str, Any], target_job_id: int | None, create_new: bool = False) -> dict[str, Any]:
    _ensure_import_schema(ctx)
    existing = ctx.db.query("SELECT id FROM takeoff_pack_imports WHERE job_id=? AND pack_id=? AND revision=?", (target_job_id or 0, pack["summary"]["pack_id"], pack["summary"]["revision"])) if target_job_id else pd.DataFrame()
    if not existing.empty:
        raise ValueError("This exact Job Pack revision has already been imported into the selected job.")
    job_id = _target_job(ctx, pack, target_job_id, create_new)
    estimate_id, line_count = _import_estimate(ctx, job_id, pack)
    material_count = _import_materials(ctx, job_id, pack)
    po_count, stage_count = _import_pos_and_stages(ctx, job_id, pack)
    document_count = _save_documents(ctx, job_id, pack)
    ctx.db.execute(
        """
        INSERT INTO takeoff_pack_imports
        (job_id,pack_id,revision,source_file,imported_at,imported_by,line_count,material_count,
         document_count,stage_count,purchase_order_count)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (job_id, pack["summary"]["pack_id"], pack["summary"]["revision"], pack["name"], datetime.now().isoformat(timespec="seconds"), ctx.user.get("username", ""), line_count, material_count, document_count, stage_count, po_count),
    )
    ctx.audit("import", "takeoff_pack_imports", job_id, f"{pack['summary']['pack_id']} rev {pack['summary']['revision']}")
    return {"job_id": job_id, "estimate_id": estimate_id, "lines": line_count, "materials": material_count, "documents": document_count, "stages": stage_count, "purchase_orders": po_count}


def job_pack_import_page(ctx: AppContext) -> None:
    header("Job Pack Import", "Create or update jobs from prepared ZIP packs without re-entering job, stage or PO details.")
    st.download_button("Download Job Pack Template", build_job_pack_template(), "PB_JobHub_Job_Pack_Template.zip", "application/zip")
    uploads = st.file_uploader("Choose Job Pack ZIPs", type=["zip"], accept_multiple_files=True, key="lean_job_pack_upload")
    if not uploads:
        st.info("Upload one or more Job Pack ZIPs. Nothing is written until you confirm an import.")
        return
    uploads = expand_nested_job_pack_uploads(uploads)
    packs = []
    errors = []
    for upload in uploads:
        try:
            packs.append(parse_job_pack(upload))
        except Exception as exc:
            errors.append(f"{getattr(upload, 'name', 'ZIP')}: {exc}")
    for error in errors:
        st.error(error)
    if not packs:
        return
    jobs = ctx.db.query("""
        SELECT j.id,j.job_no,j.job_name,j.site_address,j.contract_value,
               COALESCE(b.name,'') AS builder_client
        FROM jobs j LEFT JOIN builders_clients b ON b.id=j.builder_client_id ORDER BY j.job_no
    """)
    job_records = jobs.to_dict("records") if not jobs.empty else []
    review = []
    for pack in packs:
        match = match_job_pack_to_jobs(pack["summary"], job_records)
        pack["match"] = match
        review.append({
            "ZIP": pack["name"], "Pack Job": pack["summary"]["job_no"],
            "Project": pack["summary"]["job_name"], "Builder": pack["summary"]["builder_client"],
            "Matched Job": match["match"]["label"] if match["status"] == "matched" else "",
            "Status": "Ready" if match["status"] == "matched" else ("Needs review" if match["status"] == "ambiguous" else "No match"),
        })
    st.dataframe(pd.DataFrame(review), hide_index=True, use_container_width=True)
    if len(packs) > 1:
        ready = [pack for pack in packs if pack["match"]["status"] == "matched"]
        if ready and st.button(f"Import all {len(ready)} automatically matched packs", type="primary", key="takeoff_job_pack_bulk_one_click_import"):
            results = []
            for pack in ready:
                result = import_job_pack(ctx, pack, int(pack["match"]["match"]["job_id"]))
                results.append(f"{pack['summary']['job_no']}: {result['lines']} lines, {result['stages']} stages")
            rerun_success("Imported Job Packs — " + "; ".join(results))
        if len(ready) < len(packs):
            st.warning("Unmatched packs were not imported. Upload them one at a time to choose a job or create a new job.")
        return

    pack = packs[0]
    st.subheader(f"{pack['summary']['job_no']} — {pack['summary']['job_name']}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Take-off lines", len(pack["lines"]))
    c2.metric("Materials", len(pack["materials"]))
    c3.metric("Stages", len(pack["stages"]))
    c4.metric("Documents", len(pack["documents"]))
    live_options = job_options(ctx, include_archived=True)
    match = pack["match"]
    default_action = "Update matched job" if match["status"] == "matched" else "Create a new job"
    actions = ["Update matched job", "Choose an existing job", "Create a new job"] if live_options else ["Create a new job"]
    action = st.radio("Import action", actions, index=actions.index(default_action) if default_action in actions else 0, horizontal=True)
    target_id = None
    if action == "Update matched job":
        if match["status"] != "matched":
            st.error("No reliable automatic match is available.")
            return
        st.success("Matched to " + match["match"]["label"] + " — " + ", ".join(match["match"]["reasons"]))
        target_id = int(match["match"]["job_id"])
    elif action == "Choose an existing job":
        label = st.selectbox("Existing job", list(live_options), key="takeoff_pack_target_job")
        target_id = live_options[label]
    if st.button("Import Job Pack", type="primary", key="takeoff_job_pack_one_click_import"):
        result = import_job_pack(ctx, pack, target_id, create_new=action == "Create a new job")
        rerun_success(
            f"Job Pack imported: {result['lines']} take-off lines, {result['materials']} materials, "
            f"{result['stages']} stages, {result['purchase_orders']} POs and {result['documents']} documents."
        )
