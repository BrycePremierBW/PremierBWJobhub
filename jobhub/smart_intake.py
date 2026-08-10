"""Smart document intake for plans, scope documents and colour schedules.

Any uploaded plan (PDF), scope / specification document (PDF/TXT) or colour
schedule (CSV/XLSX) is parsed into the same internal package shape used by the
Job Pack importer (``parse_takeoff_job_pack`` in ``pb_jobhub_app.py``). Labour
hours and paint quantities are estimated from the extracted text, then either a
new job is created from the package or the package is attached to (and merged
with) an existing job.

Everything in this module is pure (no Streamlit calls) so it can be unit tested
and reused by the main app without wiring up the UI.
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import date
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, Dict, List, Tuple

from jobhub_time import jobhub_today

import pandas as pd

from jobhub.takeoff import (
    takeoff_default_productivity,
    takeoff_line_hours,
    takeoff_line_paint_litres,
)
from pb_planreader_app import (
    compute_room_takeoff_rows,
    detect_substrate_from_text,
    extract_area_candidates,
    extract_room_dimensions,
    infer_project_info,
    litres_from_area,
)

INTAKE_PACK_VERSION = "3.0"
INTAKE_LABOUR_RATE = 60.0
INTAKE_CEILING_HEIGHT_M = 2.7
INTAKE_COVERAGE_M2_PER_LITRE = 12.0
INTAKE_DEFAULT_COATS = 2
INTAKE_EACH_LABOUR_HOURS = 0.75
INTAKE_LM_LABOUR_METRES_PER_HOUR = 25.0
INTAKE_MAX_PDF_BYTES = 25 * 1024 * 1024
INTAKE_MAX_TABLE_ROWS = 10_000

PLAN_KEYWORDS = [
    "floor plan", "plan", "layout", "drawing", "elevation", "ground floor",
    "first floor", "site plan", "architectural", "working drawing",
]
SCOPE_KEYWORDS = [
    "scope", "specification", "spec", "worksection", "painting spec",
    "paint specification", "brief", "tender", "proposal", "drawing notes",
]
COLOUR_KEYWORDS = [
    "colour schedule", "color schedule", "finish schedule", "paint schedule",
    "materials schedule", "colour", "color",
]

_PAINT_SNIP_KEYWORDS = [
    "paint", "painting", "painter", "coating", "primer", "sealer", "undercoat",
    "topcoat", "ceiling", "wall", "door", "frame", "skirting", "trim", "gloss",
    "enamel", "render", "cladding", "soffit", "eave", "epoxy", "colour", "color",
]

_TRIM_KEYWORDS = [
    "skirting", "architrave", "trim", "scotia", "cornice", "picture rail", "shadow",
]

_AREA_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?:m2|m\^2|m²|sqm|sq\.?\s?m|square\s*(?:metres|meters|metre|meter))\b",
    re.I,
)
_LM_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?:lm\b|lineal\s*(?:metres|meters|m)\b|linear\s*(?:metres|meters|m)\b)",
    re.I,
)
_ITEM_COUNT_RE = re.compile(
    r"(?P<qty>\d+)\s+(?:[A-Za-z][A-Za-z\-]*\s+){0,3}(?P<unit>doors|door frames|door jambs|window frames|windows|"
    r"frames|jambs|architraves|skirting boards)\b",
    re.I,
)
_LITRES_RE = re.compile(
    r"(?P<qty>\d+(?:\.\d+)?)\s*(?:litres|liters|litre|liter)\b",
    re.I,
)
_HOURS_RE = re.compile(
    r"(?P<hours>\d+(?:\.\d+)?)\s*(?:labour\s*)?(?:hours|hrs?)\b",
    re.I,
)
_COATS_RE = re.compile(r"(?P<coats>\d)\s*coats?\b", re.I)
_COLOUR_RE = re.compile(
    r"(?:colour|color)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9 .'/\-]{0,40})",
    re.I,
)
_PRODUCT_BRAND_RE = re.compile(
    r"(dulux|haymes|taubmans|wattyl|resene|cape|solashield|elite|expression|wash.?and?.?wear|livos|porter|sherwin)",
    re.I,
)
_PB_JOB_NO_RE = re.compile(r"\b(PB\d{3,6})\b", re.I)

_TAKEOFF_LINE_COLUMNS = [
    "Stage Name", "Section", "Item Description", "Qty", "Unit", "Unit Rate",
    "Line Total", "Estimated Labour Hours", "Material Allowance", "Substrate",
    "Location", "Coating System", "Colour / Finish", "Notes",
]
_MATERIAL_COLUMNS = [
    "Product Code / Ref", "Product / Material Name", "Supplier", "Unit",
    "Unit Price Ex GST", "Colour / Finish", "Qty Required", "Location",
    "Substrate", "Coating System", "Notes", "Line Cost Ex GST",
]
_COLOUR_COLUMNS = [
    "Location", "Substrate", "Product / Material Name", "Colour / Finish",
    "Coating System", "Notes",
]
_LABOUR_COLUMNS = ["Item Description", "Estimated Labour Hours", "Labour Rate", "Notes"]
_PO_COLUMNS = ["PO Number", "Description", "Amount Ex GST", "Status", "Received Date", "Notes"]
_STAGE_COLUMNS = [
    "Stage Name", "Order", "Job %", "PO Number", "Status", "Start Date",
    "End Date", "Budget Hours", "Notes",
]

_DOCUMENT_TYPE_FOLDER = {
    "plan": "original_plans",
    "scope": "specifications",
    "colour_schedule": "colour_schedules",
    "other": "documents",
}
_DOCUMENT_TYPE_LABEL = {
    "plan": "Plans / Drawings",
    "scope": "Specification / Scope",
    "colour_schedule": "Colour / Finish Schedule",
    "other": "Take-off Pack Document",
}


def _header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _safe_member(name: Any) -> str:
    name = str(name or "document").replace("\\", "/")
    base = name.rsplit("/", 1)[-1]
    base = re.sub(r"[^A-Za-z0-9_.() -]+", "_", base)
    return base or "document"


def _txt(value: Any, default: str = "") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, float):
        text = f"{value:g}"
    else:
        text = str(value)
    return re.sub(r"\s+", " ", text).strip() or default


def _num(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _decode_text_bytes(file_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return file_bytes.decode(encoding)
        except Exception:
            continue
    return file_bytes.decode("utf-8", errors="replace")


def _job_no_from_filename(filename: Any) -> str:
    match = _PB_JOB_NO_RE.search(str(filename or ""))
    return match.group(1).upper() if match else ""


def _job_no_from_text(text: str) -> str:
    for pattern in [
        r"(?:JOB|PROJECT)\s*(?:NUMBER|NO\.?|#)\s*[:#\-]?\s*(PB\d{3,6})",
        r"(?:JOB|PROJECT)\s*(?:NUMBER|NO\.?|#)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9._\-]{2,20})",
        r"\b(PB\d{3,6})\b",
    ]:
        match = re.search(pattern, str(text or ""), re.I)
        if match:
            value = match.group(1).strip()
            if len(value) >= 3:
                return value.upper()
    return ""


def classify_intake_document(filename: Any) -> str:
    """Classify an uploaded file as plan, scope or colour schedule.

    Returns one of ``"plan"``, ``"scope"`` or ``"colour_schedule"``. Spreadsheet
    files are always colour schedules; anything else falls back to ``"scope"``.
    """
    lower = str(filename or "").lower()
    ext = PurePosixPath(lower).suffix
    if ext in {".csv", ".xlsx", ".xls"}:
        return "colour_schedule"
    if any(keyword in lower for keyword in PLAN_KEYWORDS):
        return "plan"
    if any(keyword in lower for keyword in COLOUR_KEYWORDS):
        return "colour_schedule"
    if any(keyword in lower for keyword in SCOPE_KEYWORDS):
        return "scope"
    if ext in {".png", ".jpg", ".jpeg", ".webp"}:
        return "plan"
    return "scope"


def extract_intake_pdf_text(file_bytes: bytes, max_pages: int = 50, max_chars: int = 500_000) -> str:
    """Extract text from a plan / scope PDF using pypdf."""
    if len(file_bytes or b"") > INTAKE_MAX_PDF_BYTES:
        raise ValueError("The PDF is larger than the 25 MB upload limit.")
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(file_bytes))
    page_parts: List[str] = []
    total = 0
    for page in reader.pages[:max_pages]:
        text = page.extract_text() or ""
        page_parts.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(page_parts)


def _estimate_line_hours(
    qty: float,
    unit: str,
    coats: float,
    substrate: str,
    labour_category: str,
    area_type: str,
) -> float:
    if unit == "m²":
        productivity = takeoff_default_productivity(area_type, labour_category, substrate)
        return takeoff_line_hours(qty, coats, productivity)
    if unit == "lm":
        if INTAKE_LM_LABOUR_METRES_PER_HOUR <= 0:
            return 0.0
        return round(float(qty) / INTAKE_LM_LABOUR_METRES_PER_HOUR, 2)
    if unit == "each":
        return round(float(qty) * INTAKE_EACH_LABOUR_HOURS, 2)
    return 0.0


def _estimate_line_paint_litres(
    qty: float,
    unit: str,
    coats: float,
    substrate: str,
    labour_category: str,
    finish_type: str = "Standard Paint",
) -> float:
    if unit == "m²":
        return takeoff_line_paint_litres(substrate, labour_category, qty, coats, finish_type)
    if unit == "lm":
        return takeoff_line_paint_litres(
            substrate, labour_category, 0, coats, finish_type,
            element_count=0, lineal_metres=qty,
        )
    if unit == "each":
        return takeoff_line_paint_litres(
            substrate, labour_category, 0, coats, finish_type,
            element_count=qty, lineal_metres=0,
        )
    return 0.0


def _line_row(
    description: str,
    qty: float,
    unit: str,
    hours: float,
    substrate: str,
    location: str,
    coating_system: str,
    colour_finish: str,
    notes: str,
) -> Dict[str, Any]:
    return {
        "Stage Name": "",
        "Section": "Take-off",
        "Item Description": description,
        "Qty": round(float(qty), 2),
        "Unit": unit,
        "Unit Rate": 0.0,
        "Line Total": 0.0,
        "Estimated Labour Hours": round(float(hours), 2),
        "Material Allowance": 0.0,
        "Substrate": substrate,
        "Location": location,
        "Coating System": coating_system,
        "Colour / Finish": colour_finish,
        "Notes": notes,
    }


def _material_row(
    name: str,
    qty: float,
    colour: str,
    location: str,
    substrate: str,
    system: str,
    notes: str,
    unit: str = "litre",
    code: str = "COLOUR-SCHEDULE",
) -> Dict[str, Any]:
    return {
        "Product Code / Ref": code,
        "Product / Material Name": name,
        "Supplier": "",
        "Unit": unit,
        "Unit Price Ex GST": 0.0,
        "Colour / Finish": colour,
        "Qty Required": round(float(qty), 2),
        "Location": location,
        "Substrate": substrate,
        "Coating System": system,
        "Notes": notes,
        "Line Cost Ex GST": 0.0,
    }


def _accum_line(rows: List[Dict[str, Any]], seen: set, row: Dict[str, Any]) -> None:
    key = (
        _header(row.get("Item Description")),
        _header(row.get("Unit")),
        _header(row.get("Location")),
        _header(row.get("Colour / Finish")),
    )
    if key in seen:
        return
    seen.add(key)
    rows.append(row)


def _accum_material(
    rows: List[Dict[str, Any]],
    seen: set,
    name: str,
    qty: float,
    colour: str,
    location: str,
    substrate: str,
    system: str,
    notes: str = "",
) -> None:
    if qty <= 0 or not name:
        return
    key = (_header(name), _header(colour), _header(location), _header(substrate), _header(system))
    for row in rows:
        match_key = (
            _header(row.get("Product / Material Name")),
            _header(row.get("Colour / Finish")),
            _header(row.get("Location")),
            _header(row.get("Substrate")),
            _header(row.get("Coating System")),
        )
        if match_key == key:
            row["Qty Required"] = round(_num(row.get("Qty Required")) + qty, 2)
            return
    seen.add(key)
    rows.append(_material_row(name, qty, colour, location, substrate, system, notes))


def _detect_substrate_window(line_text: str, window_text: str) -> Tuple[str, str, str]:
    """Detect a scope line's substrate from its own wording first.

    The wider ``window_text`` (neighbouring lines) is only used when the line
    itself carries no substrate keyword, so a following item such as
    "2. Ceilings - paint 85 m2" cannot re-classify a wall line above it.
    """
    line_result = detect_substrate_from_text(line_text)
    if line_result[0] != "Painting item":
        return line_result
    return detect_substrate_from_text(window_text)


def _scope_description(context: str, fallback: str) -> str:
    for raw in context.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line and len(line) >= 4:
            return line[:100]
    return fallback[:100]


def _product_from_context(context: str) -> str:
    match = _PRODUCT_BRAND_RE.search(context)
    if match:
        return "Paint (stated qty)"
    return "Paint (stated qty)"


def _colour_from_line(line: str) -> str:
    match = _COLOUR_RE.search(line)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip(" -")[:60]
    return ""


def parse_scope_text(text: str, source_name: str = "") -> Dict[str, Any]:
    """Parse a scope / specification document into intake rows.

    Every line is scanned for area (m2), lineal (lm), item count and paint
    quantity patterns. Labour hours and paint litres are estimated for each
    take-off row so a scope upload immediately produces a working estimate.
    """
    text = str(text or "")
    hints = infer_project_info(text, [source_name] if source_name else [])
    job_hints = {
        "job_no": _job_no_from_text(text) or _job_no_from_filename(source_name),
        "job_name": _txt(hints.get("project_name")),
        "site_address": _txt(hints.get("site_address")),
        "builder_client": "",
        "job_notes": "",
    }
    raw = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()]
    lines: List[Dict[str, Any]] = []
    materials: List[Dict[str, Any]] = []
    colours: List[Dict[str, Any]] = []
    seen_lines: set = set()
    seen_materials: set = set()
    seen_colours: set = set()

    for i, raw_line in enumerate(raw):
        if len(raw_line) < 4:
            continue
        low = raw_line.lower()
        context = " ".join(raw[max(0, i - 1): min(len(raw), i + 2)])[:300]
        hours_match = _HOURS_RE.search(low)
        explicit_hours = _num(hours_match.group("hours")) if hours_match else 0.0
        coats_match = _COATS_RE.search(low)
        coats = int(coats_match.group("coats")) if coats_match else INTAKE_DEFAULT_COATS

        for match in _AREA_RE.finditer(low):
            qty = _num(match.group("qty"))
            if qty <= 0:
                continue
            substrate, labour_cat, area_type = _detect_substrate_window(low, context)
            hours = explicit_hours or _estimate_line_hours(qty, "m²", coats, substrate, labour_cat, area_type)
            litres = _estimate_line_paint_litres(qty, "m²", coats, substrate, labour_cat)
            description = _scope_description(context, f"{substrate} {qty:g} m2")
            _accum_line(lines, seen_lines, _line_row(
                description, qty, "m²", hours, substrate, "", "", _colour_from_line(raw_line),
                context[:250],
            ))
            _accum_material(materials, seen_materials, f"Paint (calculated - {substrate})", litres, "", "", substrate, "", context[:250])

        for match in _LM_RE.finditer(low):
            qty = _num(match.group("qty"))
            if qty <= 0:
                continue
            if not any(keyword in context.lower() for keyword in _TRIM_KEYWORDS):
                continue
            substrate, labour_cat, area_type = _detect_substrate_window(low, context)
            if substrate == "Painting item":
                substrate, labour_cat = "Skirting / architraves", "Woodwork"
            hours = explicit_hours or _estimate_line_hours(qty, "lm", coats, substrate, labour_cat, area_type)
            litres = _estimate_line_paint_litres(qty, "lm", coats, substrate, labour_cat, "Gloss / Enamel")
            description = _scope_description(context, f"{substrate} {qty:g} lm")
            _accum_line(lines, seen_lines, _line_row(
                description, qty, "lm", hours, substrate, "", "", _colour_from_line(raw_line),
                context[:250],
            ))
            _accum_material(materials, seen_materials, f"Paint (calculated - {substrate})", litres, "", "", substrate, "", context[:250])

        for match in _ITEM_COUNT_RE.finditer(low):
            qty = _num(match.group("qty"))
            if qty <= 0:
                continue
            substrate, labour_cat, area_type = "Timber doors", "Woodwork", "Internal"
            hours = explicit_hours or _estimate_line_hours(qty, "each", coats, substrate, labour_cat, area_type)
            litres = _estimate_line_paint_litres(qty, "each", coats, substrate, labour_cat, "Gloss / Enamel")
            description = _scope_description(context, f"{substrate} x {qty:g}")
            _accum_line(lines, seen_lines, _line_row(
                description, qty, "each", hours, substrate, "", "", _colour_from_line(raw_line),
                context[:250],
            ))
            _accum_material(materials, seen_materials, f"Paint (calculated - {substrate})", litres, "", "", substrate, "", context[:250])

        for match in _LITRES_RE.finditer(low):
            qty = _num(match.group("qty"))
            if qty <= 0:
                continue
            name = _product_from_context(context)
            colour = _colour_from_line(raw_line)
            _accum_material(materials, seen_materials, name, qty, colour, "", "", "", context[:250])

    for raw_line in raw:
        low = raw_line.lower()
        if not any(keyword in low for keyword in _PAINT_SNIP_KEYWORDS):
            continue
        colour = _colour_from_line(raw_line)
        if not colour:
            continue
        substrate, _, _ = detect_substrate_from_text(low)
        key = (_header(colour), _header(substrate), _header(raw_line))
        if key in seen_colours:
            continue
        seen_colours.add(key)
        colours.append({
            "Location": "",
            "Substrate": substrate,
            "Product / Material Name": "Colour schedule",
            "Colour / Finish": colour,
            "Coating System": "",
            "Notes": raw_line[:250],
        })

    return {
        "document_type": "scope",
        "file_name": source_name,
        "job_hints": job_hints,
        "lines": lines,
        "materials": materials,
        "colours": colours,
        "raw_text": text,
    }


def parse_plan_text(text: str, source_name: str = "", ceiling_height: float = INTAKE_CEILING_HEIGHT_M) -> Dict[str, Any]:
    """Parse extracted plan PDF text into intake rows.

    Room dimensions are converted to wall and ceiling areas (reusing PlanReader)
    and labour hours / paint litres are estimated for every measured row.
    """
    text = str(text or "")
    hints = infer_project_info(text, [source_name] if source_name else [])
    job_hints = {
        "job_no": _job_no_from_text(text) or _job_no_from_filename(source_name),
        "job_name": _txt(hints.get("project_name")),
        "site_address": _txt(hints.get("site_address")),
        "builder_client": "",
        "job_notes": "",
    }
    lines: List[Dict[str, Any]] = []
    materials: List[Dict[str, Any]] = []
    seen_lines: set = set()
    seen_materials: set = set()

    rooms = extract_room_dimensions(text)
    for row in compute_room_takeoff_rows(rooms, ceiling_height):
        qty = _num(row.get("qty_m2"))
        if qty <= 0:
            continue
        substrate = _txt(row.get("substrate"), "Internal walls")
        labour_cat = _txt(row.get("labour_category"), "Walls")
        area_type = _txt(row.get("internal_external"), "Internal")
        coats = _num(row.get("coats")) or INTAKE_DEFAULT_COATS
        location = _txt(row.get("area_location"))
        hours = _estimate_line_hours(qty, "m²", coats, substrate, labour_cat, area_type)
        litres = _num(row.get("paint_litres")) or _estimate_line_paint_litres(qty, "m²", coats, substrate, labour_cat)
        _accum_line(lines, seen_lines, _line_row(
            f"{substrate} - {location}", qty, "m²", hours, substrate, location, "", "",
            _txt(row.get("source_note")),
        ))
        _accum_material(materials, seen_materials, f"Paint (calculated - {substrate})", litres, "", location, substrate, "", _txt(row.get("source_note")))

    for candidate in extract_area_candidates(text):
        qty = _num(candidate.get("qty"))
        unit = _txt(candidate.get("unit"))
        context = _txt(candidate.get("source"))
        if qty <= 0 or not context:
            continue
        if unit != "m²":
            continue
        if not any(keyword in context.lower() for keyword in _PAINT_SNIP_KEYWORDS):
            continue
        substrate, labour_cat, area_type = detect_substrate_from_text(context)
        hours = _estimate_line_hours(qty, "m²", INTAKE_DEFAULT_COATS, substrate, labour_cat, area_type)
        litres = _estimate_line_paint_litres(qty, "m²", INTAKE_DEFAULT_COATS, substrate, labour_cat)
        _accum_line(lines, seen_lines, _line_row(
            f"{substrate} - {context[:60]}", qty, "m²", hours, substrate, "", "", "", context[:250],
        ))
        _accum_material(materials, seen_materials, f"Paint (calculated - {substrate})", litres, "", "", substrate, "", context[:250])

    for candidate in extract_area_candidates(text):
        qty = _num(candidate.get("qty"))
        unit = _txt(candidate.get("unit"))
        context = _txt(candidate.get("source"))
        if qty <= 0 or unit != "lm" or not context:
            continue
        low = context.lower()
        if not any(keyword in low for keyword in _TRIM_KEYWORDS):
            continue
        substrate, labour_cat, area_type = detect_substrate_from_text(context)
        if substrate == "Painting item":
            substrate, labour_cat = "Skirting / architraves", "Woodwork"
        hours = _estimate_line_hours(qty, "lm", INTAKE_DEFAULT_COATS, substrate, labour_cat, area_type)
        litres = _estimate_line_paint_litres(qty, "lm", INTAKE_DEFAULT_COATS, substrate, labour_cat, "Gloss / Enamel")
        _accum_line(lines, seen_lines, _line_row(
            f"{substrate} - {context[:60]}", qty, "lm", hours, substrate, "", "", "", context[:250],
        ))
        _accum_material(materials, seen_materials, f"Paint (calculated - {substrate})", litres, "", "", substrate, "", context[:250])

    return {
        "document_type": "plan",
        "file_name": source_name,
        "job_hints": job_hints,
        "lines": lines,
        "materials": materials,
        "colours": [],
        "raw_text": text,
    }


def parse_colour_schedule_bytes(file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
    """Parse a colour / finish schedule CSV or Excel file into intake rows.

    Rows with a product and quantity become material quantities; rows with a
    colour become colour-schedule entries; rows that also carry an area (m2)
    quantity generate a take-off line with estimated labour hours.
    """
    lower = str(filename or "").lower()
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        df = pd.read_excel(BytesIO(file_bytes))
    else:
        df = pd.read_csv(BytesIO(file_bytes), dtype=str)
    df = df.dropna(how="all")
    if df.empty:
        raise ValueError("The colour schedule table is empty.")

    aliases = {
        "location": "Location", "area": "Location", "room": "Location",
        "substrate": "Substrate", "surface": "Substrate",
        "productmaterialname": "Product / Material Name", "productname": "Product / Material Name",
        "product": "Product / Material Name", "material": "Product / Material Name",
        "paint": "Product / Material Name", "description": "Product / Material Name",
        "colourfinish": "Colour / Finish", "colorfinish": "Colour / Finish",
        "colour": "Colour / Finish", "color": "Colour / Finish",
        "coatingsystem": "Coating System", "paintsystem": "Coating System",
        "finish": "Coating System", "sheen": "Coating System",
        "qty": "Qty", "quantity": "Qty", "litres": "Qty", "liters": "Qty",
        "unit": "Unit", "notes": "Notes",
    }
    mapped: Dict[str, Any] = {}
    for column in df.columns:
        key = _header(column)
        if key in aliases:
            mapped.setdefault(aliases[key], column)

    def col(standard: str) -> Any:
        return mapped.get(standard, standard)

    lines: List[Dict[str, Any]] = []
    materials: List[Dict[str, Any]] = []
    colours: List[Dict[str, Any]] = []
    seen_lines: set = set()
    seen_materials: set = set()
    seen_colours: set = set()

    for _, row in df.iterrows():
        location = _txt(row.get(col("Location")))
        substrate = _txt(row.get(col("Substrate")))
        product = _txt(row.get(col("Product / Material Name")))
        colour = _txt(row.get(col("Colour / Finish")))
        system = _txt(row.get(col("Coating System")))
        notes = _txt(row.get(col("Notes")))
        qty = _num(row.get(col("Qty")))
        unit = _txt(row.get(col("Unit")))

        if colour or system or location:
            key = (_header(location), _header(substrate), _header(product), _header(colour), _header(system))
            if key not in seen_colours:
                seen_colours.add(key)
                colours.append({
                    "Location": location,
                    "Substrate": substrate,
                    "Product / Material Name": product or "Colour schedule",
                    "Colour / Finish": colour,
                    "Coating System": system,
                    "Notes": notes,
                })

        if product and qty > 0:
            key = (_header(product), _header(colour), _header(location), _header(substrate), _header(system))
            if key in seen_materials:
                continue
            seen_materials.add(key)
            materials.append(_material_row(
                product, qty, colour, location, substrate, system, notes,
                unit=unit or "litre", code="COLOUR-SCHEDULE",
            ))

        unit_key = _header(unit)
        if qty > 0 and unit_key in {"m2", "m2m", "m2m2", "sqm", "sqm2", "m"}:
            if not substrate:
                substrate = "Internal walls"
            labour_cat, area_type = "Walls", "Internal"
            if any(keyword in substrate.lower() for keyword in ["ceiling", "soffit", "eave"]):
                labour_cat, area_type = "Ceilings", "Internal"
            elif any(keyword in substrate.lower() for keyword in ["render", "cladding", "external"]):
                labour_cat, area_type = "Exterior", "External"
            hours = _estimate_line_hours(qty, "m²", INTAKE_DEFAULT_COATS, substrate, labour_cat, area_type)
            description = f"{colour or 'Paint'} - {location or 'Colour schedule area'}"
            _accum_line(lines, seen_lines, _line_row(
                description, qty, "m²", hours, substrate, location, system, colour, notes,
            ))
            litres = _estimate_line_paint_litres(qty, "m²", INTAKE_DEFAULT_COATS, substrate, labour_cat)
            _accum_material(materials, seen_materials, f"Paint (calculated - {substrate})", litres, colour, location, substrate, system, notes)

    job_hints = {
        "job_no": _job_no_from_filename(filename),
        "job_name": "",
        "site_address": "",
        "builder_client": "",
        "job_notes": "",
    }
    return {
        "document_type": "colour_schedule",
        "file_name": filename,
        "job_hints": job_hints,
        "lines": lines,
        "materials": materials,
        "colours": colours,
        "raw_text": "",
    }


def parse_intake_upload(file_bytes: bytes, filename: str = "") -> Dict[str, Any]:
    """Parse a single uploaded document into intake parts.

    The document type is classified from its name, then the file is parsed into
    lines (with labour hours), materials and colours. The original bytes are
    retained so the source document can be attached to the job later.
    """
    if not file_bytes:
        raise ValueError("The uploaded file is empty.")
    doc_type = classify_intake_document(filename)
    lower = str(filename or "").lower()

    if doc_type == "colour_schedule":
        if not (lower.endswith(".csv") or lower.endswith(".xlsx") or lower.endswith(".xls")):
            raise ValueError(f"{filename} looks like a colour schedule but must be a CSV or Excel file.")
        parts = parse_colour_schedule_bytes(file_bytes, filename)
    elif doc_type == "plan":
        if lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
            raise ValueError(
                f"Plan image {filename} cannot be read directly. Upload a plan PDF with selectable text, "
                "or attach the image as a scope document."
            )
        if lower.endswith(".pdf"):
            text = extract_intake_pdf_text(file_bytes)
        else:
            text = _decode_text_bytes(file_bytes)
        parts = parse_plan_text(text, filename)
        parts["raw_text"] = text
    else:
        if lower.endswith(".pdf"):
            text = extract_intake_pdf_text(file_bytes)
        elif lower.endswith((".txt", ".md", ".rtf")):
            text = _decode_text_bytes(file_bytes)
        elif lower.endswith((".csv", ".xlsx", ".xls")):
            parts = parse_colour_schedule_bytes(file_bytes, filename)
            parts["raw_bytes"] = file_bytes
            return parts
        else:
            raise ValueError(f"Unsupported scope document type: {filename}")
        parts = parse_scope_text(text, filename)
        parts["raw_text"] = text

    parts["raw_bytes"] = file_bytes
    parts["document_type"] = doc_type
    parts["file_name"] = filename
    return parts


def _accum_line_rows(target: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> None:
    index: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (
            _header(row.get("Item Description")),
            _header(row.get("Unit")),
            _header(row.get("Location")),
            _header(row.get("Colour / Finish")),
        )
        if key in index:
            existing = index[key]
            existing["Qty"] = max(_num(existing.get("Qty")), _num(row.get("Qty")))
            existing["Estimated Labour Hours"] = max(
                _num(existing.get("Estimated Labour Hours")), _num(row.get("Estimated Labour Hours"))
            )
            existing["Material Allowance"] = _num(existing.get("Material Allowance")) + _num(row.get("Material Allowance"))
            continue
        index[key] = row
        target.append(row)


def _accum_material_rows(target: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> None:
    index: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (
            _header(row.get("Product / Material Name")),
            _header(row.get("Colour / Finish")),
            _header(row.get("Location")),
            _header(row.get("Substrate")),
            _header(row.get("Coating System")),
        )
        if key in index:
            index[key]["Qty Required"] = _num(index[key].get("Qty Required")) + _num(row.get("Qty Required"))
            continue
        index[key] = row
        target.append(row)


def _accum_colour_rows(target: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> None:
    seen: set = set()
    for row in rows:
        key = (
            _header(row.get("Location")),
            _header(row.get("Substrate")),
            _header(row.get("Product / Material Name")),
            _header(row.get("Colour / Finish")),
            _header(row.get("Coating System")),
        )
        if key in seen:
            continue
        seen.add(key)
        target.append(row)


def merge_intake_parts(parts_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge multiple parsed documents into a single intake package.

    Matching take-off lines are kept at their larger quantity / hours so a plan
    and a scope stating the same area are not double counted. Material rows with
    the same product, colour and location are accumulated. The source bytes of
    every document are retained for attachment.
    """
    merged: Dict[str, Any] = {
        "document_type": "merged",
        "file_name": "; ".join(str(p.get("file_name") or "") for p in parts_list),
        "job_hints": {},
        "lines": [],
        "materials": [],
        "colours": [],
        "source_files": [],
        "raw_text": "",
    }
    counts = {
        "parts": len(parts_list),
        "lines_before": 0, "materials_before": 0, "colours_before": 0,
    }
    for part in parts_list:
        counts["lines_before"] += len(part.get("lines") or [])
        counts["materials_before"] += len(part.get("materials") or [])
        counts["colours_before"] += len(part.get("colours") or [])
        for key, value in (part.get("job_hints") or {}).items():
            if not merged["job_hints"].get(key) and value:
                merged["job_hints"][key] = value
        _accum_line_rows(merged["lines"], part.get("lines") or [])
        _accum_material_rows(merged["materials"], part.get("materials") or [])
        _accum_colour_rows(merged["colours"], part.get("colours") or [])
        file_name = part.get("file_name") or ""
        doc_type = part.get("document_type") or "other"
        raw_bytes = part.get("raw_bytes") or b""
        if file_name:
            merged["source_files"].append({
                "file_name": file_name,
                "bytes": raw_bytes,
                "document_type": doc_type,
            })
        raw_text = part.get("raw_text") or ""
        if raw_text:
            merged["raw_text"] = "\n".join(filter(None, [merged["raw_text"], raw_text]))

    counts["lines_after"] = len(merged["lines"])
    counts["materials_after"] = len(merged["materials"])
    counts["colours_after"] = len(merged["colours"])
    merged["merge_summary"] = counts
    return merged


def _lines_dataframe(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_TAKEOFF_LINE_COLUMNS)
    df = pd.DataFrame(rows, columns=_TAKEOFF_LINE_COLUMNS)
    for column in ["Qty", "Unit Rate", "Line Total", "Estimated Labour Hours", "Material Allowance"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    for column in ["Item Description", "Unit", "Substrate", "Location", "Coating System", "Colour / Finish", "Notes", "Section", "Stage Name"]:
        df[column] = df[column].fillna("")
    return df.head(INTAKE_MAX_TABLE_ROWS)


def _labour_dataframe(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    work = _lines_dataframe(rows)
    if work.empty:
        return pd.DataFrame(columns=_LABOUR_COLUMNS)
    grouped = work.groupby("Item Description", dropna=False)["Estimated Labour Hours"].sum().reset_index()
    grouped["Labour Rate"] = 0.0
    grouped["Notes"] = ""
    return grouped[_LABOUR_COLUMNS]


def _materials_dataframe(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_MATERIAL_COLUMNS)
    df = pd.DataFrame(rows, columns=_MATERIAL_COLUMNS)
    for column in ["Unit Price Ex GST", "Qty Required", "Line Cost Ex GST"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    for column in df.columns:
        if column not in ["Unit Price Ex GST", "Qty Required", "Line Cost Ex GST"]:
            df[column] = df[column].fillna("")
    df["Line Cost Ex GST"] = (df["Unit Price Ex GST"] * df["Qty Required"]).round(2)
    return df.head(INTAKE_MAX_TABLE_ROWS)


def _colours_dataframe(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=_COLOUR_COLUMNS)
    df = pd.DataFrame(rows, columns=_COLOUR_COLUMNS).fillna("")
    return df.head(INTAKE_MAX_TABLE_ROWS)


def _empty_dataframe(columns: List[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=columns)


def build_intake_zip_bytes(merged: Dict[str, Any]) -> Tuple[bytes, List[str]]:
    """Build an in-memory Job Pack ZIP from merged intake parts.

    The ZIP contains the manifest, the standard take-off CSVs and the original
    source documents (placed in the conventional pack folders so the importer
    classifies them correctly). This lets the normal Job Pack importer create a
    brand new job from smart intake data.
    """
    hints = merged.get("job_hints") or {}
    lines_df = _lines_dataframe(merged.get("lines") or [])
    labour_df = _labour_dataframe(merged.get("lines") or [])
    materials_df = _materials_dataframe(merged.get("materials") or [])
    colours_df = _colours_dataframe(merged.get("colours") or [])
    labour_hours = float(lines_df["Estimated Labour Hours"].sum()) if not lines_df.empty else 0.0
    material_allowance = float(lines_df["Material Allowance"].sum()) if not lines_df.empty else 0.0

    pack_id = _txt(hints.get("job_no")) or "PB-SMART-INTAKE"
    source_stem = _txt(merged.get("file_name"), "smart-intake")[:60]
    if source_stem and ";" not in source_stem:
        pack_id = source_stem.replace(" ", "-")
    pack_id = re.sub(r"[^A-Za-z0-9_.\-]+", "-", pack_id)[:80] or "PB-SMART-INTAKE"

    manifest = {
        "pack_id": pack_id,
        "revision": "1",
        "pack_version": INTAKE_PACK_VERSION,
        "ready_to_import": True,
        "job": {
            "job_no": _txt(hints.get("job_no")),
            "job_name": _txt(hints.get("job_name")),
            "site_address": _txt(hints.get("site_address")),
            "status": "Not Started",
            "notes": _txt(hints.get("job_notes")),
            "restrict_material_products": False,
            "allowed_material_suppliers": [],
        },
        "builder_client": {
            "type": "Builder / Client",
            "name": _txt(hints.get("builder_client")),
        },
        "estimate": {
            "estimate_no": "",
            "estimate_date": str(jobhub_today()),
            "status": "Draft",
            "labour_hours": round(labour_hours, 2),
            "material_allowance": round(material_allowance, 2),
            "gst_percent": 10,
            "target_gp_percent": 0,
            "contingency_percent": 0,
            "notes": "Created by Smart Document Intake.",
        },
        "import_preferences": {
            "update_job_record": True,
            "create_estimate": True,
            "update_budget": True,
            "import_materials": True,
            "attach_documents": True,
            "import_stages": True,
            "use_imported_line_pricing": False,
            "create_missing_builder": True,
            "fill_blank_builder_details": True,
        },
    }

    member_names: List[str] = []
    documents: List[Dict[str, Any]] = []
    for source_file in merged.get("source_files") or []:
        file_name = str(source_file.get("file_name") or "document")
        doc_type = str(source_file.get("document_type") or "other")
        folder = _DOCUMENT_TYPE_FOLDER.get(doc_type, "documents")
        member = f"{folder}/{_safe_member(file_name)}"
        member_names.append(member)
        documents.append({
            "member": member,
            "file_name": file_name,
            "document_type": _DOCUMENT_TYPE_LABEL.get(doc_type, "Take-off Pack Document"),
            "size_bytes": len(source_file.get("bytes") or b""),
        })

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("job_manifest.json", json.dumps(manifest, default=str, sort_keys=True))
        if not lines_df.empty:
            zf.writestr("takeoff_lines.csv", lines_df.to_csv(index=False))
        if not labour_df.empty:
            zf.writestr("labour_budget.csv", labour_df.to_csv(index=False))
        if not materials_df.empty:
            zf.writestr("material_allowances.csv", materials_df.to_csv(index=False))
        if not colours_df.empty:
            zf.writestr("colour_schedule.csv", colours_df.to_csv(index=False))
        for source_file in merged.get("source_files") or []:
            member = _safe_member(source_file.get("file_name") or "document")
            folder = _DOCUMENT_TYPE_FOLDER.get(str(source_file.get("document_type") or "other"), "documents")
            zf.writestr(f"{folder}/{member}", source_file.get("bytes") or b"")
    buffer.seek(0)
    return buffer.getvalue(), member_names


def parts_to_intake_package(
    parts_list: List[Dict[str, Any]],
    source_name: str = "smart_intake.zip",
) -> Dict[str, Any]:
    """Turn one or more parsed intake parts into a Job Pack import package.

    The returned dictionary mirrors ``parse_takeoff_job_pack`` so the standard
    ``import_takeoff_job_pack`` (create-new) and a dedicated attach / merge
    helper can both consume it.
    """
    merged = merge_intake_parts(parts_list)
    hints = merged.get("job_hints") or {}
    lines_df = _lines_dataframe(merged.get("lines") or [])
    labour_df = _labour_dataframe(merged.get("lines") or [])
    materials_df = _materials_dataframe(merged.get("materials") or [])
    colours_df = _colours_dataframe(merged.get("colours") or [])
    zip_bytes, member_names = build_intake_zip_bytes(merged)

    labour_hours = float(lines_df["Estimated Labour Hours"].sum()) if not lines_df.empty else 0.0
    material_allowance = float(lines_df["Material Allowance"].sum()) if not lines_df.empty else 0.0
    line_pricing_total = float(lines_df["Line Total"].sum()) if not lines_df.empty else 0.0
    pack_id = _txt(hints.get("job_no")) or "PB-SMART-INTAKE"

    summary = {
        "pack_id": pack_id,
        "revision": "1",
        "pack_version": INTAKE_PACK_VERSION,
        "job_no": _txt(hints.get("job_no")),
        "job_name": _txt(hints.get("job_name")),
        "site_address": _txt(hints.get("site_address")),
        "builder_client": _txt(hints.get("builder_client")),
        "builder_client_type": "Builder / Client",
        "status": "Not Started",
        "contract_value_ex_gst": 0.0,
        "job_notes": _txt(hints.get("job_notes")),
        "restrict_material_products": False,
        "restrict_material_products_supplied": False,
        "allowed_material_suppliers": [],
        "estimate_no": "",
        "estimate_date": str(jobhub_today()),
        "estimate_status": "Draft",
        "labour_hours": round(labour_hours, 2),
        "labour_rate": INTAKE_LABOUR_RATE,
        "material_allowance": round(material_allowance, 2),
        "access_equipment_allowance": 0.0,
        "subcontractor_allowance": 0.0,
        "sundries_allowance": 0.0,
        "target_gp_percent": 0.0,
        "contingency_percent": 0.0,
        "gst_percent": 10.0,
        "pricing_method": "Production Target Included",
        "notes": "Created by Smart Document Intake.",
        "line_pricing_total": round(line_pricing_total, 2),
        "ready_to_import": True,
        "import_preferences": {
            "update_job_record": True,
            "create_estimate": True,
            "update_budget": True,
            "import_materials": True,
            "attach_documents": True,
            "import_stages": True,
            "use_imported_line_pricing": False,
            "create_missing_builder": True,
            "fill_blank_builder_details": True,
        },
    }

    return {
        "source_bytes": zip_bytes,
        "source_name": source_name,
        "member_names": member_names,
        "manifest": {
            "pack_id": pack_id,
            "revision": "1",
            "pack_version": INTAKE_PACK_VERSION,
            "ready_to_import": True,
        },
        "summary": summary,
        "lines": lines_df,
        "labour": labour_df,
        "materials": materials_df,
        "colours": colours_df,
        "purchase_orders": _empty_dataframe(_PO_COLUMNS),
        "stages": _empty_dataframe(_STAGE_COLUMNS),
        "documents": _documents_for_parts(merged),
        "merge_summary": merged.get("merge_summary") or {},
        "job_hints": hints,
        "source_files": merged.get("source_files") or [],
    }


def _documents_for_parts(merged: Dict[str, Any]) -> List[Dict[str, Any]]:
    documents: List[Dict[str, Any]] = []
    for source_file in merged.get("source_files") or []:
        file_name = str(source_file.get("file_name") or "document")
        doc_type = str(source_file.get("document_type") or "other")
        folder = _DOCUMENT_TYPE_FOLDER.get(doc_type, "documents")
        member = f"{folder}/{_safe_member(file_name)}"
        documents.append({
            "member": member,
            "file_name": file_name,
            "document_type": _DOCUMENT_TYPE_LABEL.get(doc_type, "Take-off Pack Document"),
            "mime_type": "application/octet-stream",
            "size_bytes": len(source_file.get("bytes") or b""),
        })
    return documents
