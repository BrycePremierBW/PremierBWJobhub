import base64
import io
import json
import math
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

from planreader_marker_component import plan_marker_editor
from planreader_substrate_component import substrate_box_editor
from planreader_3d import (
    external_scene_data,
    render_planreader_3d_html,
)
from planrender_studio import (
    ELEVATION_FACE_LABELS,
    _face_key,
    _img_data_url,
    _substrate_for_substrate_text,
    build_studio_data,
    render_planrender_studio_html,
)

APP_NAME = "PB PlanReader"
DEFAULT_COVERAGE_M2_PER_L = 12.0
DEFAULT_CEILING_HEIGHT_M = 2.7
DEFAULT_OPENINGS_ALLOWANCE_M2 = 0.0

ROOM_KEYWORDS = [
    "lounge", "living", "family", "dining", "kitchen", "meals", "bedroom",
    "bed 1", "bed 2", "bed 3", "bed 4", "bed 5", "master", "ensuite", "bath",
    "laundry", "toilet", "wc", "study", "office", "garage", "rumpus", "play",
    "hall", "entry", "foyer", "alfresco", "porch", "verandah", "veranda",
    "patio", "store", "corridor", "passage", "sitting", "theatre", "retreat",
    "closet", "dressing", "powder", "sunroom", "bunk", "guest",
]

ROOT = Path(__file__).resolve().parent
ASSETS_DIR = ROOT / "assets"
LOCAL_DATA_DIR = ROOT / "data"
RENDER_DATA_DIR = Path(os.getenv("DATA_DIR", "/var/data"))
DATA_DIR = RENDER_DATA_DIR if RENDER_DATA_DIR.exists() and os.access(str(RENDER_DATA_DIR), os.W_OK) else LOCAL_DATA_DIR
JOBS_DIR = DATA_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _load_planreader_bridge():
    """Load the JobHub bridge by file path so the JobHub startup guards never run here."""
    try:
        import importlib.util

        bridge_path = ROOT / "jobhub" / "planreader_bridge.py"
        spec = importlib.util.spec_from_file_location("planreader_bridge", str(bridge_path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


PLANREADER_BRIDGE = _load_planreader_bridge()
PLANREADER_BRIDGE_AVAILABLE = PLANREADER_BRIDGE is not None

PAINT_KEYWORDS = [
    "paint", "painting", "painter", "coating", "coatings", "primer", "sealer", "undercoat",
    "topcoat", "dulux", "haymes", "taubmans", "resene", "wattyl", "colorbond", "colourbond",
    "colour", "color", "finish", "finishes", "render", "rendered", "cladding", "soffit", "eave",
    "ceiling", "wall", "blockwork", "plasterboard", "fc", "fibre cement", "fiber cement", "door",
    "frame", "skirting", "trim", "gloss", "enamel", "epoxy", "floor coating", "texture", "stain",
]
PAGE_TYPES = {
    "floor_plan": ["floor plan", "ground floor", "level 1", "first floor", "plan"],
    "elevation": ["elevation", "north elevation", "south elevation", "east elevation", "west elevation", "front elevation", "rear elevation"],
    "roof_plan": ["roof plan", "soffit", "reflected ceiling", "ceiling plan"],
    "finishes": ["finish", "finishes", "colour schedule", "color schedule", "materials schedule"],
    "doors_windows": ["door schedule", "window schedule", "louvre", "glazing"],
    "painting_spec": ["painting", "paint systems", "coatings", "sealer", "primer"],
    "site_plan": ["site plan", "locality", "setout", "set out"],
    "specification": ["technical specification", "general requirements", "worksection"],
}

ELEVATION_BOX_SOURCE = "Manual elevation box"
AUTO_EXTERNAL_SOURCE = "Auto external take-off"

DEFAULT_EXTERNAL_WALL_HEIGHT_M = 2.7
DEFAULT_EAVE_DEPTH_M = 0.45
DEFAULT_WALL_THICKNESS_M = 0.15

SUBSTRATE_OPTIONS = [
    "External walls / render",
    "Cladding / external lining",
    "Soffits / eaves",
    "Fascia / gutters / trim",
    "Windows / doors / frames",
    "Floors / balconies",
    "Other",
]

SUBSTRATE_LABOUR_MAP = {
    "External walls / render": ("Exterior", "External"),
    "Cladding / external lining": ("Exterior", "External"),
    "Soffits / eaves": ("Ceilings", "External"),
    "Fascia / gutters / trim": ("Woodwork", "External"),
    "Windows / doors / frames": ("Woodwork", "External"),
    "Floors / balconies": ("Floor coating", "External"),
    "Other": ("General", "External"),
}


def safe_name(value: str, fallback: str = "file") -> str:
    value = str(value or "").strip()
    value = re.sub(r"[^A-Za-z0-9_.() -]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._-/")
    return value[:120] or fallback


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except Exception:
        return "$0.00"


def litres_from_area(
    area_m2: float,
    coats: float = 2.0,
    coverage_m2_per_l: float = DEFAULT_COVERAGE_M2_PER_L,
) -> float:
    try:
        return round(float(area_m2) * float(coats) / float(coverage_m2_per_l), 2)
    except Exception:
        return 0.0


DEFAULT_WASTE_PCT = 5.0
DEFAULT_LABOUR_HOURS_PER_M2 = {
    "Walls": 0.12,
    "Ceilings": 0.10,
    "Exterior": 0.14,
    "Woodwork": 0.16,
    "Floor coating": 0.09,
    "General": 0.12,
}


def labour_hours_for(area_m2: float, labour_category: Any, waste_pct: float = DEFAULT_WASTE_PCT) -> float:
    try:
        work = float(area_m2) * (1 + max(float(waste_pct or 0.0), 0.0) / 100.0)
        rate = DEFAULT_LABOUR_HOURS_PER_M2.get(str(labour_category or ""), DEFAULT_LABOUR_HOURS_PER_M2["Walls"])
        return round(work * rate, 2)
    except Exception:
        return 0.0


def recalculate_takeoff_values(
    rows: List[Dict[str, Any]],
    coverage_m2_per_l: float = DEFAULT_COVERAGE_M2_PER_L,
    waste_pct: float = DEFAULT_WASTE_PCT,
) -> List[Dict[str, Any]]:
    """Apply coats/coverage/waste consistently to every take-off row.

    Paint litres come from the measured m² (with waste) and coat count;
    labour hours come from a per-category hours-per-m² rate (with waste).
    The measured quantity itself is left untouched.
    """
    out = []
    waste = max(float(waste_pct or 0.0), 0.0) / 100.0
    for r in rows or []:
        qty = max(float(r.get("qty_m2") or 0.0), 0.0)
        lineal = max(float(r.get("lineal_m") or 0.0), 0.0)
        coats = max(float(r.get("coats") or 0.0), 0.0)
        work = max(qty, lineal) * (1 + waste)
        row = dict(r)
        row["paint_litres"] = litres_from_area(qty * (1 + waste), coats, coverage_m2_per_l)
        row["labour_hours"] = labour_hours_for(max(qty, lineal), row.get("labour_category"), waste_pct)
        out.append(row)
    return out


def validate_measurements(job: Dict[str, Any]) -> List[str]:
    """Cross-check the different measurement signals and flag inconsistencies."""
    warnings: List[str] = []
    rooms = job.get("rooms", [])
    rooms_with_area = [
        r for r in rooms
        if _to_float(r.get("dim1_m")) and _to_float(r.get("dim2_m"))
    ]
    room_area = sum(_to_float(r["dim1_m"]) * _to_float(r["dim2_m"]) for r in rooms_with_area)
    markers = load_corrections(job.get("job_id") or "")
    footprint = external_footprint(job, markers, rooms)
    env_area = footprint["envelope_w_m"] * footprint["envelope_h_m"]
    if footprint["method"] == "vector-wall":
        if room_area > 0 and env_area > 0:
            ratio = env_area / room_area
            if ratio > 1.6 or ratio < 0.65:
                warnings.append(
                    f"Envelope from PDF wall geometry ({env_area:g} m²) disagrees with the measured rooms "
                    f"({room_area:g} m²) — check the plan scale or correct the room sizes."
                )
    elif footprint["method"] == "area-estimate":
        warnings.append(
            "No scale source found yet — external quantities are an area-based estimate, not measured. "
            "Upload a vector PDF with dimensions, or position room markers, for exact measurement."
        )
    elif footprint["method"] == "marker-envelope":
        if room_area > 0 and env_area > 0:
            ratio = env_area / room_area
            if ratio > 1.6 or ratio < 0.65:
                warnings.append(
                    f"Marker envelope ({env_area:g} m²) disagrees with the measured rooms ({room_area:g} m²) "
                    f"— move the room markers or correct their sizes."
                )
    return warnings


def build_takeoff_summary(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in rows or []:
        substrate = str(r.get("substrate") or "Other")
        int_ext = str(r.get("internal_external") or "")
        key = (int_ext, substrate)
        e = by_key.setdefault(key, {
            "internal_external": int_ext,
            "substrate": substrate,
            "qty_m2": 0.0,
            "lineal_m": 0.0,
            "count": 0.0,
            "coats": 0.0,
            "labour_hours": 0.0,
            "paint_litres": 0.0,
        })
        e["qty_m2"] += float(r.get("qty_m2") or 0.0)
        e["lineal_m"] += float(r.get("lineal_m") or 0.0)
        e["count"] += float(r.get("count") or 0.0)
        e["coats"] = max(e["coats"], float(r.get("coats") or 0.0))
        e["labour_hours"] += float(r.get("labour_hours") or 0.0)
        e["paint_litres"] += float(r.get("paint_litres") or 0.0)
    summary = []
    for e in by_key.values():
        e["qty_m2"] = round(e["qty_m2"], 2)
        e["lineal_m"] = round(e["lineal_m"], 2)
        e["count"] = round(e["count"], 1)
        e["coats"] = round(e["coats"], 1)
        e["labour_hours"] = round(e["labour_hours"], 2)
        e["paint_litres"] = round(e["paint_litres"], 2)
        summary.append(e)
    summary.sort(key=lambda e: (str(e["internal_external"]), str(e["substrate"])))
    return summary


def takeoff_report_pdf_bytes(job: Dict[str, Any], summary: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> bytes:
    """A one-page painting take-off report (summary + detail) as PDF bytes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=20, rightMargin=20, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    title = str(job.get("job_name") or job.get("project_name") or "Painting take-off")
    story = [
        Paragraph(f"<b>{safe_name(title)}</b> — Painting take-off", styles["Title"]),
        Paragraph(
            f"Job: {job.get('job_no','') or '-'} &nbsp;·&nbsp; {job.get('site_address','') or '-'} &nbsp;·&nbsp; "
            f"Generated {now_stamp()}",
            styles["Normal"],
        ),
        Spacer(1, 10),
    ]
    if summary:
        head = ["Internal/External", "Substrate", "m²", "Lineal m", "Count", "Coats", "Labour hrs", "Litres"]
        data = [[s["internal_external"], s["substrate"], s["qty_m2"], s["lineal_m"], s["count"], s["coats"], s["labour_hours"], s["paint_litres"]] for s in summary]
        total = [
            "Total", "",
            round(sum(float(s["qty_m2"]) for s in summary), 2),
            round(sum(float(s["lineal_m"]) for s in summary), 2),
            round(sum(float(s["count"]) for s in summary), 1),
            "",
            round(sum(float(s["labour_hours"]) for s in summary), 2),
            round(sum(float(s["paint_litres"]) for s in summary), 2),
        ]
        data.append(total)
        t = Table([head] + data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#eef2f7")]),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#d9e2f0")),
            ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ]))
        story.append(t)
        story.append(Spacer(1, 12))
    detail_head = ["Location", "Substrate", "Int/Ext", "m²", "Lineal m", "Coats", "Labour hrs", "Litres", "Rate", "Value ex GST"]
    detail_rows = []
    for r in rows:
        qty = float(r.get("qty_m2") or 0.0)
        lineal = float(r.get("lineal_m") or 0.0)
        rate = float(r.get("rate_ex_gst") or 0.0)
        value = (qty + lineal) * rate
        detail_rows.append([
            str(r.get("area_location") or "")[:42],
            str(r.get("substrate") or ""),
            str(r.get("internal_external") or ""),
            round(qty, 2),
            round(lineal, 2),
            float(r.get("coats") or 0.0),
            float(r.get("labour_hours") or 0.0),
            float(r.get("paint_litres") or 0.0),
            round(rate, 2),
            round(value, 2),
        ])
    if detail_rows:
        d = Table([detail_head] + detail_rows, repeatRows=1, colWidths=[150, 120, 60, 50, 55, 45, 55, 55, 50, 60])
        d.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6fa")]),
            ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ]))
        story.append(Paragraph("<b>Detail</b>", styles["Heading3"]))
        story.append(d)
    doc.build(story)
    return buf.getvalue()


COLOUR_SCHEDULE_COLUMNS = ["area_location", "surface", "colour", "finish", "product", "notes", "hex"]

DEFAULT_FINISH_BY_SURFACE = {
    "Walls": "Low Sheen",
    "Ceiling": "Flat / Matt",
    "Trim / Woodwork": "Semi Gloss",
    "Doors": "Semi Gloss",
    "Skirting": "Semi Gloss",
    "External walls": "Weathershield Low Sheen",
    "Fascia": "Gloss",
    "Gutter": "Gloss",
    "Soffit": "Low Sheen",
    "Floor coating": "Floor enamel",
    "Fence / Deck": "Exterior gloss",
}


def _surface_label_from_substrate(substrate: Any) -> str:
    low = str(substrate or "").lower()
    for key, label in [
        ("ceiling", "Ceiling"),
        ("trim", "Trim / Woodwork"),
        ("woodwork", "Trim / Woodwork"),
        ("skirt", "Skirting"),
        ("door", "Doors"),
        ("external", "External walls"),
        ("fascia", "Fascia"),
        ("gutter", "Gutter"),
        ("soffit", "Soffit"),
        ("floor", "Floor coating"),
        ("fence", "Fence / Deck"),
        ("deck", "Fence / Deck"),
        ("wall", "Walls"),
    ]:
        if key in low:
            return label
    return str(substrate or "Walls").strip() or "Walls"


def default_colour_finish(surface: Any) -> str:
    return DEFAULT_FINISH_BY_SURFACE.get(str(surface or "").strip(), "Low Sheen")


def normalise_colour_schedule(rows: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for row in rows or []:
        area = str(row.get("area_location") or row.get("room") or "").strip()
        surface = str(row.get("surface") or row.get("substrate") or "").strip() or "Walls"
        key = (area.casefold(), surface.casefold())
        if key in seen:
            continue
        seen.add(key)
        finish = str(row.get("finish") or "").strip() or default_colour_finish(surface)
        out.append({
            "area_location": area or "Whole job",
            "surface": surface,
            "colour": str(row.get("colour") or "").strip(),
            "finish": finish,
            "product": str(row.get("product") or "").strip(),
            "notes": str(row.get("notes") or "").strip(),
            "hex": str(row.get("hex") or "").strip(),
        })
    out.sort(key=lambda r: (r["area_location"].casefold(), r["surface"].casefold()))
    return out


def seed_colour_schedule(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build a colour schedule draft from the take-off rows and detected rooms."""
    rows: List[Dict[str, Any]] = []
    for r in job.get("takeoff_rows", []) or []:
        area = str(r.get("area_location") or "").strip()
        if not area:
            continue
        surface = _surface_label_from_substrate(r.get("substrate"))
        rows.append({"area_location": area, "surface": surface})
    covered: set = set()
    for r in rows:
        covered.add((str(r.get("area_location", "")).casefold(), str(r.get("surface", "")).casefold()))
    for room in job.get("rooms", []) or []:
        area = str(room.get("room") or room.get("area_location") or "").strip()
        if not area:
            continue
        if (area.casefold(), "walls") not in covered:
            rows.append({"area_location": area, "surface": "Walls"})
            covered.add((area.casefold(), "walls"))
    return normalise_colour_schedule(rows)


def resolve_colour_hex(colour: Any) -> str:
    """Best-effort hex for a common colour name; None when unknown."""
    text = str(colour or "").strip()
    if not text:
        return ""
    if text.startswith("#") and len(text) in (4, 7):
        return text
    low = text.casefold()
    for key, hexv in sorted({
        "white": "#FFFFFF",
        "off white": "#F5F3EC",
        "offwhite": "#F5F3EC",
        "natural white": "#F2EFE7",
        "antique white": "#F0E6D2",
        "oat": "#E9DFCC",
        "grecian": "#D9D5CE",
        "alabaster": "#EDEAE4",
        "snow": "#F4F6F5",
        "quarter": "#F1EDE4",
        "half": "#E8E3D8",
        "black": "#1A1A1A",
        "charcoal": "#3B3B3B",
        "graphite": "#4A4A48",
        "beige": "#E3D5B6",
        "cream": "#F4EBDD",
        "greige": "#CFC4B6",
    }.items(), key=lambda kv: len(kv[0]), reverse=True):
        if key in low:
            return hexv
    return ""


def colour_schedule_df(job: Dict[str, Any]) -> pd.DataFrame:
    rows = normalise_colour_schedule(job.get("colour_schedule", []))
    if not rows:
        rows = seed_colour_schedule(job)
    return pd.DataFrame(rows, columns=COLOUR_SCHEDULE_COLUMNS)


def colour_schedule_excel_bytes(job: Dict[str, Any]) -> bytes:
    schedule = colour_schedule_df(job)
    by_colour = schedule.groupby(["colour", "finish"], dropna=False).agg(
        Areas=("area_location", lambda v: ", ".join(sorted({str(x) for x in v})))
    ).reset_index()
    by_colour = by_colour[["colour", "finish", "Areas"]]
    return df_to_excel_bytes({
        "Colour Schedule": schedule,
        "Colours Used": by_colour,
        "Takeoff Reference": pd.DataFrame(job.get("takeoff_rows", [])),
    })


def colour_schedule_pdf_bytes(job: Dict[str, Any]) -> bytes:
    """A paint-ready colour schedule (per-room surfaces) plus a colours-used summary."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    schedule = colour_schedule_df(job)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20, rightMargin=20, topMargin=20, bottomMargin=20)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"<b>{safe_name(str(job.get('job_name') or 'Painting job'))}</b> — Colour Schedule", styles["Title"]),
        Paragraph(
            f"Job: {job.get('job_no','') or '-'} &nbsp;·&nbsp; {job.get('site_address','') or '-'} &nbsp;·&nbsp; "
            f"Generated {now_stamp()}",
            styles["Normal"],
        ),
        Spacer(1, 10),
    ]
    if schedule.empty:
        story.append(Paragraph("<i>No colour schedule lines yet.</i>", styles["Normal"]))
        doc.build(story)
        return buf.getvalue()
    head = ["Area / Room", "Surface", "Colour", "Finish", "Product / Code", "Notes"]
    data = [[
        str(r["area_location"]),
        str(r["surface"]),
        str(r["colour"] or "—"),
        str(r["finish"]),
        str(r["product"] or "—"),
        str(r["notes"] or "—"),
    ] for r in schedule.to_dict("records")]
    t = Table([head] + data, repeatRows=1, colWidths=[95, 75, 95, 70, 80, 70])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef2f7")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))
    by_colour = schedule.groupby(["colour", "finish"], dropna=False)["area_location"].apply(
        lambda v: ", ".join(sorted({str(x) for x in v}))
    ).reset_index()
    by_colour.columns = ["Colour", "Finish", "Areas / Rooms"]
    story.append(Paragraph("<b>Colours Used</b>", styles["Heading3"]))
    story.append(Table([[str(x) for x in by_colour.columns]] + by_colour.astype(str).values.tolist(), repeatRows=1))
    doc.build(story)
    return buf.getvalue()


def markup_plan_image_bytes(option: Dict[str, Any], schedule_rows: List[Dict[str, Any]], markers: List[Dict[str, Any]]) -> bytes:
    """Overlay a colour chip card onto each room marker on a plan page image."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(option["image_path"]).convert("RGB")
    draw = ImageDraw.Draw(img)
    width, height = img.size
    scale = width / 1400.0
    base = max(14, int(14 * scale))
    title_font = None
    body_font = None
    for font_path in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf"):
        try:
            title_font = ImageFont.truetype(font_path, int(base * 1.2))
            break
        except Exception:
            title_font = None
    for font_path in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        try:
            body_font = ImageFont.truetype(font_path, base)
            break
        except Exception:
            body_font = None
    if title_font is None or body_font is None:
        body_font = ImageFont.load_default()

    rows_by_area: Dict[str, List[Dict[str, Any]]] = {}
    for row in schedule_rows:
        rows_by_area.setdefault(str(row.get("area_location") or "").casefold(), []).append(row)
    colours_used: set = set()

    def draw_card(px, py, area, lines):
        pad = int(8 * scale)
        line_h = int((base + 6) * scale)
        card_w = min(width - 20, int(240 * scale))
        card_h = int((len(lines) + 2) * line_h) + pad
        x0 = px + int(14 * scale)
        y0 = py + int(14 * scale)
        if x0 + card_w > width - 8:
            x0 = px - card_w - int(14 * scale)
        if y0 + card_h > height - 8:
            y0 = py - card_h - int(14 * scale)
        x0 = max(4, min(x0, width - card_w - 4))
        y0 = max(4, min(y0, height - card_h - 4))
        fill = resolve_colour_hex(rows_by_area.get(area, [{}])[0].get("colour")) or "#EEEEEE"
        draw.rounded_rectangle([x0, y0, x0 + card_w, y0 + card_h], radius=int(6 * scale), fill="#FFFFFF", outline="#222222", width=2)
        draw.rectangle([x0, y0, x0 + card_w, y0 + int(6 * scale)], fill=fill)
        ty = y0 + pad
        draw.text((x0 + pad, ty), str(area or "").title(), fill="#111111", font=title_font or body_font)
        ty += line_h
        for line in lines:
            dot = resolve_colour_hex(line.get("colour")) or "#999999"
            draw.ellipse([x0 + pad, ty + line_h * 0.3, x0 + pad + line_h * 0.6, ty + line_h * 0.9], fill=dot, outline="#000000")
            label = f"{line.get('surface','')}: {line.get('colour') or 'TBC'}"
            if line.get("finish"):
                label += f" ({line['finish']})"
            draw.text((x0 + pad + int(line_h * 0.9), ty), label, fill="#222222", font=body_font)
            ty += line_h

    for marker in markers or []:
        mx = _to_float(marker.get("x"))
        my = _to_float(marker.get("y"))
        if not mx or not my:
            continue
        area = str(marker.get("label") or "").strip()
        lines = rows_by_area.get(area.casefold(), [])
        px = int(mx * width)
        py = int(my * height)
        draw.ellipse([px - int(5 * scale), py - int(5 * scale), px + int(5 * scale), py + int(5 * scale)], fill="#C8102E", outline="#FFFFFF", width=2)
        for line in lines:
            if line.get("colour"):
                colours_used.add((str(line["colour"]).strip(), resolve_colour_hex(line.get("colour")) or "#999999"))
        if lines:
            draw_card(px, py, area, lines)

    if colours_used:
        legend = "Colours: " + "  ·  ".join(f"{name}" for name, _ in sorted(colours_used))
        lh = base + 8
        draw.rectangle([0, height - lh - 12, width, height], fill="#FFFFFF", outline="#999999")
        draw.text((8, height - lh - 6), legend, fill="#111111", font=body_font)
        lx = 8
        for name, hexv in sorted(colours_used):
            w = len(name) * base
            draw.rectangle([lx, height - lh - 12, lx + 12, height - lh], fill=hexv, outline="#333333")
            lx += 18 + int(draw.textlength(name, font=body_font)) + 14
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def generate_colour_markup_images(job_id: str, job: Dict[str, Any]) -> List[Path]:
    """Render a colour-markup PNG for every plan page that has room markers."""
    markers = load_corrections(job_id)
    if not markers:
        return []
    schedule_rows = normalise_colour_schedule(job.get("colour_schedule", []))
    if not schedule_rows:
        schedule_rows = seed_colour_schedule(job)
    out_dir = job_dir(job_id) / "colour_schedules"
    out_dir.mkdir(parents=True, exist_ok=True)
    created: List[Path] = []
    for option in _correction_options(job):
        page_markers = [
            m for m in markers
            if str(m.get("file") or "") == option["file"] and int(m.get("page") or 1) == option["page"]
        ]
        if not page_markers:
            continue
        name = f"colour_markup_{safe_name(option['file'])}_{option['page']:03d}.png"
        path = out_dir / name
        path.write_bytes(markup_plan_image_bytes(option, schedule_rows, page_markers))
        created.append(path)
    return created


def colour_schedule_page(job_id: str):
    job = load_job(job_id)
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("Colour schedule")
    st.caption("Assign a colour (name, finish, product/code and optional hex swatch) to every room surface. The schedule, markup images and print-ready exports give painters the full colour brief.")
    existing = normalise_colour_schedule(job.get("colour_schedule", []))
    if not existing:
        existing = seed_colour_schedule(job)
    if not existing:
        st.warning("No rooms or take-off rows yet. Correct the rooms on the plan or build wall/ceiling rows first.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    df = pd.DataFrame(existing, columns=COLOUR_SCHEDULE_COLUMNS)
    try:
        hex_col = st.column_config.TextColumn("Hex swatch", placeholder="e.g. #F0E6D2")
    except TypeError:
        hex_col = st.column_config.TextColumn("Hex swatch")
    editor = st.data_editor(
        df,
        hide_index=True,
        num_rows="dynamic",
        width="stretch",
        key=f"pr_colour_schedule_{job_id}",
        column_config={
            "area_location": st.column_config.SelectboxColumn("Area / Room", options=sorted({str(r.get("room") or "") for r in job.get("rooms", [])} | {str(r.get("area_location") or "") for r in job.get("takeoff_rows", [])} | {str(v) for v in df["area_location"].dropna().unique()} | {"Whole job"})),
            "surface": st.column_config.SelectboxColumn("Surface", options=sorted(DEFAULT_FINISH_BY_SURFACE.keys()) + ["Other"]),
            "finish": st.column_config.SelectboxColumn("Finish", options=["Flat / Matt", "Low Sheen", "Satin", "Semi Gloss", "Gloss", "Floor enamel", "Weathershield Low Sheen", "Exterior gloss", "Other"]),
            "hex": hex_col,
        },
    )
    c1, c2 = st.columns(2)
    if c1.button("Save colour schedule", type="primary"):
        job["colour_schedule"] = normalise_colour_schedule(editor.to_dict("records"))
        save_job(job_id, job)
        st.success(f"Saved {len(job['colour_schedule'])} colour schedule lines.")
        st.rerun()
    if c2.button("Generate colour markup images for plan pages"):
        created = generate_colour_markup_images(job_id, job)
        if not created:
            st.warning("No plan pages with room markers found. Tap rooms in Verify & Correct first, then save colours.")
        else:
            job_files = job.get("files", [])
            existing_paths = {f.get("path") for f in job_files}
            for path in created:
                if str(path) not in existing_paths:
                    job_files.append(file_record(path, "png", "Colour markup"))
            job["files"] = job_files
            save_job(job_id, job)
            st.success(f"Generated {len(created)} colour markup image(s).")
            st.rerun()

    st.markdown("### Export")
    schedule_excel = colour_schedule_excel_bytes(job)
    schedule_pdf = colour_schedule_pdf_bytes(job)
    c3, c4 = st.columns(2)
    c3.download_button("Download colour schedule (Excel)", schedule_excel, file_name=f"{safe_name(job.get('job_name','job'))}_colour_schedule.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    c4.download_button("Download colour schedule (PDF)", schedule_pdf, file_name=f"{safe_name(job.get('job_name','job'))}_colour_schedule.pdf", mime="application/pdf")

    st.markdown("### Colour markup previews")
    markup_files = [f for f in job.get("files", []) if f.get("category") == "Colour markup"]
    if not markup_files:
        st.info("Generate colour markup images above to see the colour brief overlaid on the plan.")
    else:
        for f in markup_files:
            p = Path(f.get("path") or "")
            if p.exists():
                st.image(str(p), caption=f.get("name", ""), width="stretch")
                st.download_button("Download this markup", p.read_bytes(), file_name=p.name, mime="image/png", key=f"dl_markup_{p.name}_{int(time.time())}")

    st.markdown("</div>", unsafe_allow_html=True)


def jobhub_sync_page(job_id: str):
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("JobHub sync")
    st.caption("Push this job's take-off rows, colour schedule and colour markup straight into JobHub. PlanReader and JobHub share one database, so anything you send appears in JobHub on its next refresh — and JobHub edits to the same tables flow straight back.")
    if not PLANREADER_BRIDGE_AVAILABLE:
        st.error("The JobHub bridge module could not be loaded in this environment.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    bridge = PLANREADER_BRIDGE
    try:
        bridge.ensure_bridge_schema()
    except Exception as exc:
        st.error(f"Could not connect to the shared JobHub database: {exc}")
        st.caption("Check that DATA_DIR / DATABASE_URL point at the same storage JobHub uses.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    try:
        st.caption(bridge.connection_status())
    except Exception:
        pass

    job = load_job(job_id)
    job_no = str(job.get("job_no") or job.get("job_id") or job_id)
    job_name = str(job.get("job_name") or "")

    linked_id = bridge.link_job_by_no(job_no)
    if linked_id is None:
        st.info(f"Job `{job_no}` is not in JobHub yet. Create it now to start sharing data.")
        if st.button("Create this job in JobHub", type="primary", key=f"pr_link_create_{job_id}"):
            linked_id = bridge.create_linked_job(
                job_no,
                job_name=job_name,
                site_address=str(job.get("site_address") or ""),
                status=str(job.get("status") or "Active"),
            )
            if linked_id is None:
                st.error("Could not create the JobHub job.")
                st.markdown("</div>", unsafe_allow_html=True)
                return
            st.success(f"Created JobHub job #{linked_id} for {job_no}.")
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
        return
    st.success(f"Linked to JobHub job #{linked_id} — {job_no} {job_name}".rstrip())

    takeoff_rows = job.get("takeoff_rows", [])
    schedule = normalise_colour_schedule(job.get("colour_schedule", [])) or seed_colour_schedule(job)

    st.markdown("### Ready to send")
    c1, c2, c3 = st.columns(3)
    c1.metric("Take-off rows", len(takeoff_rows))
    c2.metric("Colour schedule lines", len(schedule))
    markup_count = len([f for f in job.get("files", []) if f.get("category") == "Colour markup"])
    c3.metric("Colour markup images", markup_count)

    col1, col2 = st.columns(2)
    if col1.button("Push take-off rows to JobHub", key=f"pr_sync_takeoff_{job_id}"):
        n = bridge.sync_takeoff_rows(linked_id, takeoff_rows)
        st.success(f"Sent {n} take-off row(s) to JobHub.")
        st.rerun()
    if col2.button("Push colour schedule to JobHub", key=f"pr_sync_schedule_{job_id}"):
        n = bridge.sync_colour_schedule(linked_id, schedule)
        st.success(f"Sent {n} colour schedule line(s) to JobHub.")
        st.rerun()

    if st.button("Upload colour markup + schedule exports to JobHub", type="primary", key=f"pr_sync_docs_{job_id}"):
        uploaded = 0
        created = generate_colour_markup_images(job_id, job)
        for path in created:
            try:
                if bridge.upsert_document_blob(
                    linked_id,
                    path.name,
                    path.read_bytes(),
                    mime_type="image/png",
                    doc_type="Colour markup",
                    notes=f"Colour markup for {job_no}",
                ):
                    uploaded += 1
            except Exception:
                pass
        xlsx_name = f"{safe_name(job_name or job_no)}_colour_schedule.xlsx"
        if bridge.upsert_document_blob(
            linked_id,
            xlsx_name,
            colour_schedule_excel_bytes(job),
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            doc_type="Colour Schedule",
            notes=f"Colour schedule export for {job_no}",
        ):
            uploaded += 1
        pdf_name = f"{safe_name(job_name or job_no)}_colour_schedule.pdf"
        if bridge.upsert_document_blob(
            linked_id,
            pdf_name,
            colour_schedule_pdf_bytes(job),
            mime_type="application/pdf",
            doc_type="Colour Schedule",
            notes=f"Colour schedule export for {job_no}",
        ):
            uploaded += 1
        st.success(f"Uploaded {uploaded} file(s) to the JobHub job.")
        st.rerun()

    st.markdown("### What JobHub has for this job")
    try:
        takeoff_now = bridge.job_takeoff_frame(linked_id)
        schedule_now = bridge.job_colour_schedule_frame(linked_id)
        docs_now = bridge.job_document_blobs_frame(linked_id)
        st.caption(
            f"Take-off rows in JobHub: {len(takeoff_now)} · "
            f"colour schedule lines: {len(schedule_now)} · "
            f"files: {len(docs_now)}"
        )
        if not docs_now.empty:
            st.dataframe(docs_now.drop(columns=["blob_data"]), width="stretch", hide_index=True)
    except Exception as exc:
        st.caption(f"Could not preview JobHub state: {exc}")

    st.markdown("</div>", unsafe_allow_html=True)


def job_id_from_name(job_no: str, job_name: str) -> str:
    base = safe_name(f"{job_no}_{job_name}", "job").lower().replace(" ", "_")
    return base or f"job_{int(time.time())}"


def job_dir(job_id: str) -> Path:
    path = JOBS_DIR / safe_name(job_id, "job")
    path.mkdir(parents=True, exist_ok=True)
    (path / "source_files").mkdir(exist_ok=True)
    (path / "converted_images").mkdir(exist_ok=True)
    (path / "exports").mkdir(exist_ok=True)
    return path


def meta_path(job_id: str) -> Path:
    return job_dir(job_id) / "job_meta.json"


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_job(job_id: str) -> Dict[str, Any]:
    return load_json(meta_path(job_id), {})


def save_job(job_id: str, data: Dict[str, Any]) -> None:
    data["job_id"] = job_id
    data["updated_at"] = now_stamp()
    save_json(meta_path(job_id), data)


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def corrections_path(job_id: str) -> Path:
    return job_dir(job_id) / "plan_corrections.json"


def load_corrections(job_id: str) -> List[Dict[str, Any]]:
    return load_json(corrections_path(job_id), [])


def save_corrections(job_id: str, markers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    seen: set = set()
    for marker in markers or []:
        label = str(marker.get("label") or "").strip()[:80]
        if not label:
            continue
        dim1 = _to_float(marker.get("dim1_m"))
        dim2 = _to_float(marker.get("dim2_m"))
        key = (label.lower(), round(dim1 or 0, 2), round(dim2 or 0, 2))
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({
            "label": label,
            "x": _to_float(marker.get("x")) or 0.5,
            "y": _to_float(marker.get("y")) or 0.5,
            "dim1_m": round(dim1, 2) if dim1 else None,
            "dim2_m": round(dim2, 2) if dim2 else None,
            "area_m2": round(dim1 * dim2, 2) if dim1 and dim2 else None,
            "file": str(marker.get("file") or ""),
            "page": int(marker.get("page") or 1),
            "source": "Manual plan marker",
            "updated_at": now_stamp(),
        })
    save_json(corrections_path(job_id), cleaned)
    return cleaned


def apply_room_corrections(
    rooms: List[Dict[str, Any]],
    markers: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge manually marked rooms into the detected room list.

    A marker with the same label overrides the detected dimensions; a marker
    that matches no detected room is appended as a new room. Markers without
    dimensions keep the detected sizes (or are skipped when nothing matches).
    """
    if not markers:
        return rooms or []
    markers_by_label: Dict[str, Dict[str, Any]] = {}
    for marker in markers:
        label = str(marker.get("label") or "").strip()
        if label:
            markers_by_label[label.lower()] = marker
    corrected: List[Dict[str, Any]] = []
    seen_labels: set = set()
    for room in rooms or []:
        label = str(room.get("room") or "").strip()
        key = label.lower()
        marker = markers_by_label.get(key)
        if marker:
            dim1 = _to_float(marker.get("dim1_m"))
            dim2 = _to_float(marker.get("dim2_m"))
            if dim1 and dim2:
                room = dict(room)
                room["dim1_m"] = round(dim1, 2)
                room["dim2_m"] = round(dim2, 2)
                room["area_m2"] = round(dim1 * dim2, 2)
                room["source"] = "Corrected via plan marker"
        corrected.append(room)
        seen_labels.add(key)
    for label, marker in markers_by_label.items():
        if label in seen_labels:
            continue
        dim1 = _to_float(marker.get("dim1_m"))
        dim2 = _to_float(marker.get("dim2_m"))
        if not dim1 or not dim2:
            continue
        corrected.append({
            "room": str(marker.get("label") or "").strip(),
            "dim1_m": round(dim1, 2),
            "dim2_m": round(dim2, 2),
            "area_m2": round(dim1 * dim2, 2),
            "source": "Marked on plan",
        })
    return corrected


def _rebuild_takeoff(job: Dict[str, Any], rooms: List[Dict[str, Any]]) -> pd.DataFrame:
    combined = {"painting_snippets": [], "area_candidates": [], "rooms": rooms}
    for analysis in job.get("analyses", []):
        combined["painting_snippets"].extend(analysis.get("painting_snippets", []))
        combined["area_candidates"].extend(analysis.get("area_candidates", []))
    df = build_takeoff_from_analysis(combined)
    rows = merge_elevation_box_rows(job, df.to_dict("records"))
    rows = merge_auto_external_rows(job, rows)
    return pd.DataFrame(rows)


def _correction_options(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    options: List[Dict[str, Any]] = []
    for analysis in job.get("analyses", []):
        for page in analysis.get("pages", []):
            image_path = page.get("image_path")
            if image_path and Path(image_path).exists():
                options.append({
                    "label": f"{analysis.get('file', '')} · page {page.get('page', 1)}",
                    "file": str(analysis.get("file", "") or ""),
                    "page": int(page.get("page", 1)),
                    "image_path": str(image_path),
                })
    return options


def list_jobs() -> List[Dict[str, Any]]:
    jobs = []
    for p in sorted(JOBS_DIR.glob("*/job_meta.json")):
        meta = load_json(p, {})
        if meta:
            jobs.append(meta)
    return sorted(jobs, key=lambda j: j.get("updated_at", ""), reverse=True)


def save_uploaded_file(job_id: str, uploaded, subfolder: str = "source_files") -> Path:
    dst_dir = job_dir(job_id) / subfolder
    dst_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_name(uploaded.name, "upload")
    out = dst_dir / filename
    if out.exists():
        stem, suffix = out.stem, out.suffix
        out = dst_dir / f"{stem}_{int(time.time())}{suffix}"
    with open(out, "wb") as f:
        f.write(uploaded.getbuffer())
    return out


def file_record(path: Path, file_type: str, category: str = "Uploaded") -> Dict[str, Any]:
    return {
        "name": path.name,
        "path": str(path),
        "file_type": file_type,
        "category": category,
        "uploaded_at": now_stamp(),
        "size_kb": round(path.stat().st_size / 1024, 1) if path.exists() else 0,
    }


def classify_page(text: str) -> str:
    low = (text or "").lower()
    scores = {}
    for key, words in PAGE_TYPES.items():
        scores[key] = sum(1 for w in words if w in low)
    best = max(scores, key=scores.get) if scores else "other"
    return best if scores.get(best, 0) else "other"


def extract_drawing_number(text: str) -> str:
    candidates = re.findall(r"\b(?:A|AR|DA|WD|SK|S|E|M|H|C)[- ]?\d{2,4}(?:\.\d+)?\b", text or "", flags=re.I)
    return candidates[0].upper().replace(" ", "-") if candidates else ""


def title_from_text(text: str, page_type: str) -> str:
    lines = [re.sub(r"\s+", " ", l).strip() for l in (text or "").splitlines()]
    lines = [l for l in lines if 4 <= len(l) <= 90]
    priority = ["ELEVATION", "FLOOR PLAN", "ROOF PLAN", "FINISH", "SCHEDULE", "PAINT", "SITE PLAN", "SECTION"]
    for word in priority:
        for l in lines[:80]:
            if word in l.upper():
                return l
    return page_type.replace("_", " ").title()


def painting_lines(text: str, limit: int = 120) -> List[str]:
    rows = []
    for raw in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) < 4:
            continue
        low = line.lower()
        if any(k in low for k in PAINT_KEYWORDS):
            rows.append(line[:260])
    # dedupe preserving order
    out = []
    seen = set()
    for r in rows:
        key = r.lower()
        if key not in seen:
            out.append(r)
            seen.add(key)
        if len(out) >= limit:
            break
    return out


def extract_area_candidates(text: str) -> List[Dict[str, Any]]:
    rows = []
    lines = [re.sub(r"\s+", " ", x).strip() for x in (text or "").splitlines()]
    area_re = re.compile(r"(?P<val>\d+(?:\.\d+)?)\s*(?:m2|m²|sqm|sq\.m|square metres|square meters)\b", re.I)
    lm_re = re.compile(r"(?P<val>\d+(?:\.\d+)?)\s*(?:lm|lineal metres|linear metres|l/m)\b", re.I)
    for i, line in enumerate(lines):
        context = " ".join(lines[max(0, i - 1): min(len(lines), i + 2)])[:300]
        for m in area_re.finditer(line):
            rows.append({"source": context, "qty": float(m.group("val")), "unit": "m²"})
        for m in lm_re.finditer(line):
            rows.append({"source": context, "qty": float(m.group("val")), "unit": "lm"})
    return rows[:300]


def _parse_dimension(raw: str) -> float | None:
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _normalise_dims(a: float, b: float) -> Tuple[float, float] | None:
    # Architectural dimensions above 50 are almost always millimetres.
    if a > 50 or b > 50:
        a, b = a / 1000, b / 1000
    if a <= 0 or b <= 0:
        return None
    return (round(a, 3), round(b, 3))


def _room_label(prefix: str, lines: List[str], idx: int) -> str:
    window = [prefix] + list(reversed(lines[max(0, idx - 3): idx]))
    for text in window:
        low = text.lower()
        best_pos, best_kw = None, None
        for kw in ROOM_KEYWORDS:
            p = low.find(kw)
            if p < 0:
                continue
            if best_pos is None or p < best_pos or (p == best_pos and len(kw) > len(best_kw or "")):
                best_pos, best_kw = p, kw
        if best_kw:
            seg = text[best_pos:]
            seg = re.split(r"[x×]", seg, maxsplit=1)[0]
            seg = re.sub(
                r"\s*(?:\d{1,2}[.,]\d+|\d{3,4})\s*(?:m|mm|metres?|meters?)?\s*$",
                "",
                seg,
                flags=re.I,
            )
            seg = re.sub(
                r"\s*(?:approx\.?|approximately|appx|dimension|dims?|size)\s*$",
                "",
                seg,
                flags=re.I,
            )
            seg = re.sub(r"\s+", " ", seg).strip(" -:,")
            if seg:
                return seg[:40].title()
    return "Room"


def extract_room_dimensions(text: str) -> List[Dict[str, Any]]:
    """Pull room dimension pairs (e.g. 'Lounge 5400 x 3200') out of plan text.

    Dimensions in metres, millimetres or comma-grouped figures are converted to
    metres and labelled with the room they belong to. Only clearly labelled rooms
    large enough to be painted spaces are returned, so schedule/sheet sizes do
    not pollute the take-off.
    """
    lines = [re.sub(r"\s+", " ", x).strip() for x in (text or "").splitlines()]
    dim_re = re.compile(
        r"(?P<d1>\d{1,5}(?:[.,]\d{1,3})?)\s*(?:m|mm|metres?|meters?)?\s*"
        r"[x×]\s*"
        r"(?P<d2>\d{1,5}(?:[.,]\d{1,3})?)\s*(?:m|mm|metres?|meters?)?",
        re.I,
    )
    results: List[Dict[str, Any]] = []
    for i, line in enumerate(lines):
        prev_end = 0
        for m in dim_re.finditer(line):
            d1, d2 = _parse_dimension(m.group("d1")), _parse_dimension(m.group("d2"))
            if d1 is None or d2 is None:
                continue
            dims = _normalise_dims(d1, d2)
            if dims is None:
                continue
            d1m, d2m = dims
            area = round(d1m * d2m, 2)
            if area < 2.0 or d1m > 50 or d2m > 50 or area > 2500:
                prev_end = m.end()
                continue
            label = _room_label(line[prev_end:m.start()].strip(" -:,"), lines, i)
            if label == "Room":
                prev_end = m.end()
                continue
            results.append({
                "room": label,
                "dim1_m": d1m,
                "dim2_m": d2m,
                "area_m2": area,
                "source": line[:250],
            })
            prev_end = m.end()
            if len(results) >= 250:
                return results
    return results


def infer_project_info(all_text: str, filenames: List[str]) -> Dict[str, str]:
    text = re.sub(r"\s+", " ", all_text or " ")
    first_lines = [l.strip() for l in (all_text or "").splitlines() if l.strip()][:100]
    project = ""
    address = ""
    job_no = ""
    for pat in [r"PROJECT\s*(?:NUMBER|NO\.?|#)?\s*[:\-]?\s*([A-Z0-9\-_.]+)", r"(?:JOB|PROJECT)\s*(?:NO\.?|NUMBER)\s*[:\-]?\s*([A-Z0-9\-_.]+)"]:
        m = re.search(pat, text, re.I)
        if m:
            job_no = m.group(1).strip()
            break
    for line in first_lines[:30]:
        up = line.upper()
        if len(line) > 8 and any(k in up for k in ["CONSTRUCTION", "BUILDING", "PROJECT", "DEVELOPMENT", "SWITCHGEAR", "SUBSTATION"]):
            project = line[:120]
            break
    address_pats = [
        r"AT\s+([^\n\r]{8,120}(?:QLD|QUEENSLAND|NSW|VIC|SA|WA|TAS|NT|ACT)[^\n\r]{0,40})",
        r"\b\d{1,5}\s+[A-Z][A-Za-z0-9 .,'\-/]+(?:ROAD|RD|STREET|ST|AVENUE|AVE|DRIVE|DR|COURT|CT|CRESCENT|CRES|PLACE|PL|LANE|LN)\b[^\n\r]{0,80}",
    ]
    for pat in address_pats:
        m = re.search(pat, all_text or "", re.I)
        if m:
            address = m.group(1).strip() if m.groups() else m.group(0).strip()
            address = re.sub(r"\s+", " ", address)[:160]
            break
    if not project and filenames:
        project = Path(filenames[0]).stem.replace("_", " ").replace("-", " ").title()
    return {"project_name": project, "site_address": address, "job_no": job_no}


def analyse_pdf(path: Path, render_pages: bool = False, dpi: int = 150) -> Dict[str, Any]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError(
            "PyMuPDF is required to read plan PDFs. Install it with: pip install PyMuPDF"
        ) from None
    doc = fitz.open(path)
    page_records = []
    all_text_parts = []
    paint_snips = []
    area_candidates = []
    room_rows = []
    converted = []
    conv_dir = path.parent.parent / "converted_images" / path.stem
    conv_dir.mkdir(parents=True, exist_ok=True)
    for idx, page in enumerate(doc):
        text = page.get_text("text") or ""
        all_text_parts.append(text)
        ptype = classify_page(text)
        rec = {
            "file": path.name,
            "page": idx + 1,
            "page_type": ptype,
            "drawing_no": extract_drawing_number(text),
            "title": title_from_text(text, ptype),
            "text_chars": len(text),
            "has_text": len(text.strip()) > 20,
        }
        page_records.append(rec)
        paint_snips.extend([{"file": path.name, "page": idx + 1, "text": x} for x in painting_lines(text, 30)])
        for c in extract_area_candidates(text):
            c.update({"file": path.name, "page": idx + 1})
            area_candidates.append(c)
        for r in extract_room_dimensions(text):
            r.update({"file": path.name, "page": idx + 1})
            room_rows.append(r)
        if render_pages:
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            img_path = conv_dir / f"{path.stem}_page_{idx+1:03d}.png"
            pix.save(str(img_path))
            converted.append(str(img_path))
            rec["image_path"] = str(img_path)
            rec["render_dpi"] = dpi
        vs = _page_vector_scale(page)
        rec["vector_scale"] = vs["meta"]
        rec["vector_wall_lines"] = vs["wall_lines"][:1500]
    doc.close()
    return {
        "file": path.name,
        "path": str(path),
        "page_count": len(page_records),
        "pages": page_records,
        "all_text": "\n".join(all_text_parts),
        "painting_snippets": paint_snips[:500],
        "area_candidates": area_candidates[:500],
        "rooms": room_rows[:250],
        "converted_images": converted,
    }


def detect_substrate_from_text(text: str) -> Tuple[str, str, str]:
    low = (text or "").lower()
    if any(k in low for k in ["ceiling", "soffit", "eave"]):
        return "Ceilings / soffits", "Ceilings", "Internal" if "ceiling" in low and "soffit" not in low else "External"
    if any(k in low for k in ["door", "frame", "jamb", "skirting", "trim"]):
        return "Woodwork / metalwork", "Woodwork", "Internal"
    if any(k in low for k in ["render", "cladding", "external", "elevation", "facade", "façade"]):
        return "External walls", "Exterior", "External"
    if any(k in low for k in ["epoxy", "floor"]):
        return "Floors", "Floor coating", "Internal"
    if any(k in low for k in ["wall", "blockwork", "plaster", "plasterboard"]):
        return "Internal walls", "Walls", "Internal"
    return "Painting item", "General", "Internal"


def substrate_labour(substrate: str) -> Tuple[str, str]:
    return SUBSTRATE_LABOUR_MAP.get(str(substrate or "").strip(), ("General", "External"))


def guess_substrate_from_label(label: str) -> str:
    low = (label or "").lower()
    if any(k in low for k in ["soffit", "eave", "ceiling"]):
        return "Soffits / eaves"
    if any(k in low for k in ["fascia", "gutter", "trim", "capping"]):
        return "Fascia / gutters / trim"
    if any(k in low for k in ["window", "door", "frame"]):
        return "Windows / doors / frames"
    if any(k in low for k in ["cladding", "lining", "timber", "fc ", "fibre", "fiber", "weatherboard"]):
        return "Cladding / external lining"
    if any(k in low for k in ["floor", "balcony", "deck", "paving"]):
        return "Floors / balconies"
    return "External walls / render"


def normalise_boxes(boxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    for b in boxes or []:
        x = _to_float(b.get("x")) or 0.0
        y = _to_float(b.get("y")) or 0.0
        w = _to_float(b.get("w")) or 0.0
        h = _to_float(b.get("h")) or 0.0
        if w <= 0 or h <= 0:
            continue
        x = max(0.0, min(100.0, x))
        y = max(0.0, min(100.0, y))
        w = max(0.0, min(100.0 - x, w))
        h = max(0.0, min(100.0 - y, h))
        qty = _to_float(b.get("qty_m2")) or 0.0
        cleaned.append({
            "id": str(b.get("id") or "").strip()[:60],
            "label": str(b.get("label") or "").strip()[:120],
            "substrate": str(b.get("substrate") or "").strip()[:120] or "External walls / render",
            "x": round(x, 4),
            "y": round(y, 4),
            "w": round(w, 4),
            "h": round(h, 4),
            "progress": normalise_progress(b.get("progress", 0)),
            "qty_m2": round(max(0.0, qty), 2),
            "manual_m2": round(max(0.0, _to_float(b.get("manual_m2")) or 0.0), 2),
        })
    return cleaned


def substrate_boxes_from_job(job: Dict[str, Any], img_path: str) -> List[Dict[str, Any]]:
    state = (job.get("elevation_progress") or {}).get(img_path, {}) or {}
    zones = list(state.get("zones", []) or [])
    boxes = []
    for z in zones:
        boxes.append({
            "id": str(z.get("id") or "").strip(),
            "label": str(z.get("label") or "").strip(),
            "substrate": str(z.get("substrate") or "").strip()
            or guess_substrate_from_label(z.get("label") or ""),
            "x": z.get("x", 0),
            "y": z.get("y", 0),
            "w": z.get("w", 0),
            "h": z.get("h", 0),
            "progress": z.get("progress", 0),
            "qty_m2": z.get("qty_m2", 0),
            "manual_m2": z.get("manual_m2", 0),
        })
    return normalise_boxes(boxes)


def save_substrate_boxes(job: Dict[str, Any], img_path: str, boxes: List[Dict[str, Any]]) -> None:
    job.setdefault("elevation_progress", {})
    state = job["elevation_progress"].setdefault(img_path, {}) or {}
    state["zones"] = normalise_boxes(boxes)
    state["updated_at"] = now_stamp()
    job["elevation_progress"][img_path] = state


def _image_pixel_size(path: Any):
    try:
        from PIL import Image as _PILImage
        with _PILImage.open(str(path)) as im:
            return im.size
    except Exception:
        return None


def normalise_calibration(cal: Any) -> Optional[Dict[str, Any]]:
    if not cal or not isinstance(cal, dict):
        return None
    x1 = _to_float(cal.get("x1"))
    y1 = _to_float(cal.get("y1"))
    x2 = _to_float(cal.get("x2"))
    y2 = _to_float(cal.get("y2"))
    len_m = _to_float(cal.get("len_m"))
    if None in (x1, y1, x2, y2) or len_m is None or len_m <= 0:
        return None
    def _clamp(v: float) -> float:
        return round(max(0.0, min(100.0, v)), 4)
    return {
        "x1": _clamp(x1),
        "y1": _clamp(y1),
        "x2": _clamp(x2),
        "y2": _clamp(y2),
        "len_m": round(len_m, 4),
    }


def calibration_mpp(cal: Any, img_w: Any, img_h: Any) -> Optional[float]:
    cal = normalise_calibration(cal)
    w = _to_float(img_w)
    h = _to_float(img_h)
    if not cal or not w or w <= 0 or not h or h <= 0:
        return None
    dx = (cal["x2"] - cal["x1"]) / 100.0 * w
    dy = (cal["y2"] - cal["y1"]) / 100.0 * h
    px = math.hypot(dx, dy)
    if px <= 1e-6:
        return None
    return cal["len_m"] / px


def measured_box_m2(box: Dict[str, Any], mpp: Optional[float], img_w: Any, img_h: Any) -> float:
    w = _to_float(box.get("w"))
    h = _to_float(box.get("h"))
    iw = _to_float(img_w)
    ih = _to_float(img_h)
    if mpp is None or not w or w <= 0 or not h or h <= 0 or not iw or iw <= 0 or not ih or ih <= 0:
        return 0.0
    return round((w / 100.0 * iw) * (h / 100.0 * ih) * mpp * mpp, 2)


def effective_box_m2(box: Dict[str, Any], mpp: Optional[float] = None, img_w: Any = None, img_h: Any = None) -> float:
    manual = _to_float(box.get("manual_m2")) or 0.0
    if manual > 0:
        return round(manual, 2)
    measured = measured_box_m2(box, mpp, img_w, img_h)
    if measured > 0:
        return measured
    return round(_to_float(box.get("qty_m2")) or 0.0, 2)


def _seg_len_pt(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.hypot(x2 - x1, y2 - y1)


def _line_angle_deg(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.degrees(math.atan2(y2 - y1, x2 - x1))


def _rotate_xy(x: float, y: float, cx: float, cy: float, angle_deg: float):
    rad = math.radians(angle_deg)
    dx = x - cx
    dy = y - cy
    return (
        cx + dx * math.cos(rad) - dy * math.sin(rad),
        cy + dx * math.sin(rad) + dy * math.cos(rad),
    )


def _dedupe_lines(lines: List[Tuple[float, float, float, float]], snap: float = 0.5, min_len_pt: float = 1.0):
    seen = set()
    out = []
    for (x1, y1, x2, y2) in lines:
        if _seg_len_pt(x1, y1, x2, y2) < min_len_pt:
            continue
        a = (round(x1 / snap) * snap, round(y1 / snap) * snap)
        b = (round(x2 / snap) * snap, round(y2 / snap) * snap)
        key = (a, b) if a <= b else (b, a)
        if key in seen:
            continue
        seen.add(key)
        out.append((a[0], a[1], b[0], b[1]))
    return out


def _page_words(page, max_words: int = 4000) -> List[Dict[str, Any]]:
    words = []
    try:
        raw = page.get_text("words")
    except Exception:
        return words
    for w in (raw or [])[:max_words]:
        if len(w) >= 5:
            words.append({"text": w[4], "bbox": [float(w[0]), float(w[1]), float(w[2]), float(w[3])]})
    return words


def extract_page_vectors(page, max_lines: int = 2000) -> Dict[str, Any]:
    """Extract line geometry and text tokens from a PDF page (PDF points).

    Uses PyMuPDF's vector operators so measurements come from the drawing's
    exact coordinates instead of rendered pixels. Returns the page size in
    PDF points, deduplicated line segments, and word tokens for
    dimension-text detection.
    """
    lines: List[Tuple[float, float, float, float]] = []
    try:
        items = page.get_drawings()
    except Exception:
        items = []
    for d in items or []:
        for it in d.get("items") or []:
            op = it[0]
            try:
                if op == "l" and len(it) >= 3:
                    p1, p2 = it[1], it[2]
                    lines.append((float(p1.x), float(p1.y), float(p2.x), float(p2.y)))
                elif op == "re" and len(it) >= 2:
                    r = it[1]
                    x0, y0, x1, y1 = float(r.x0), float(r.y0), float(r.x1), float(r.y1)
                    lines.extend([
                        (x0, y0, x1, y0), (x1, y0, x1, y1),
                        (x1, y1, x0, y1), (x0, y1, x0, y0),
                    ])
                elif op == "qu" and len(it) >= 2:
                    q = it[1]
                    for i in range(4):
                        a, b = q[i], q[(i + 1) % 4]
                        lines.append((float(a.x), float(a.y), float(b.x), float(b.y)))
                elif op == "c" and len(it) >= 4:
                    p1, p3 = it[1], it[3]
                    lines.append((float(p1.x), float(p1.y), float(p3.x), float(p3.y)))
            except (TypeError, AttributeError, IndexError, ValueError):
                continue
    dedup = _dedupe_lines(lines)
    words = _page_words(page)
    pr = page.rect
    return {
        "page_w_pt": float(pr.width),
        "page_h_pt": float(pr.height),
        "lines": dedup[:max_lines],
        "words": words,
    }


def parse_dimension_value(text: str) -> Optional[float]:
    """Parse a dimension label into a real-world length in metres.

    Handles ``3500`` / ``3,500`` (mm), ``12.5m``, ``4.8`` (metres) and
    ``2500mm``. Returns None for non-dimension text (areas, labels, notes).
    """
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    if "²" in t or "sq" in low or "m2" in low or "/" in low or "x" in low:
        return None
    m = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*mm\s*$", t, re.I)
    if m:
        v = _to_float(m.group(1).replace(",", "."))
        return round(v / 1000.0, 4) if v else None
    m = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*m\s*$", t, re.I)
    if m:
        v = _to_float(m.group(1).replace(",", "."))
        return v
    m = re.match(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*$", t)
    if m:
        v = _to_float(m.group(1).replace(",", "."))
        if v is None:
            return None
        if v >= 100:
            return round(v / 1000.0, 4)
        if 0.1 <= v < 100:
            return round(v, 4)
    return None


def detect_dimension_texts(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    dims = []
    for w in words or []:
        v = parse_dimension_value(w.get("text"))
        if v is None or v <= 0.0 or v > 60.0:
            continue
        dims.append({"value_m": v, "text": w.get("text"), "bbox": list(w.get("bbox") or [0.0, 0.0, 0.0, 0.0])})
    return dims


def _near_horizontal(line, tol: float = 2.0) -> bool:
    a = _line_angle_deg(*line) % 180
    return a <= tol or a >= 180 - tol


def detect_scale_bar(lines: List[Tuple[float, float, float, float]], dims: List[Dict[str, Any]]):
    """Find a drawn scale bar: a long horizontal line labelled with its length."""
    if not lines or not dims:
        return None
    candidates = []
    for i, (x1, y1, x2, y2) in enumerate(lines):
        if not _near_horizontal((x1, y1, x2, y2)):
            continue
        L = _seg_len_pt(x1, y1, x2, y2)
        if L < 20 or L > 400:
            continue
        yc = (y1 + y2) / 2.0
        xmin, xmax = min(x1, x2), max(x1, x2)
        for d in dims:
            bx0, by0, bx1, by1 = d["bbox"]
            bcx = (bx0 + bx1) / 2.0
            bcy = (by0 + by1) / 2.0
            if abs(bcy - yc) > 25:
                continue
            if bcx < xmin - L * 0.5 or bcx > xmax + L * 0.5:
                continue
            scale = d["value_m"] / L
            if 0.0005 <= scale <= 0.05:
                candidates.append({"scale": scale, "line_i": i, "value_m": d["value_m"], "len_pt": L})
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c["len_pt"], c["value_m"]))
    best = candidates[0]
    return {
        "m_per_pt": best["scale"],
        "source": "scale-bar",
        "line_len_pt": best["len_pt"],
        "value_m": best["value_m"],
    }


def _match_dimension_line(lines: List[Tuple[float, float, float, float]], dim: Dict[str, Any], tol: float = 16.0):
    """Find the line a dimension label belongs to (parallel + nearest + longest)."""
    bx0, by0, bx1, by1 = dim["bbox"]
    bcx = (bx0 + bx1) / 2.0
    bcy = (by0 + by1) / 2.0
    bw = max(bx1 - bx0, 1e-3)
    best = None
    for i, (x1, y1, x2, y2) in enumerate(lines):
        L = _seg_len_pt(x1, y1, x2, y2)
        if L < 6:
            continue
        xmin, xmax = min(x1, x2), max(x1, x2)
        ymin, ymax = min(y1, y2), max(y1, y2)
        a = _line_angle_deg(x1, y1, x2, y2) % 180
        if a <= 25 or a >= 155:
            if abs(bcy - (y1 + y2) / 2.0) > tol:
                continue
            overlap = min(xmax, bx1) - max(xmin, bx0)
            if overlap < 0.4 * min(bw, L):
                continue
        else:
            if abs(bcx - (x1 + x2) / 2.0) > tol:
                continue
            overlap = min(ymax, by1) - max(ymin, by0)
            if overlap < 0.4 * min((by1 - by0), L):
                continue
        if best is None or L > best[0]:
            best = (L, i)
    return best[1] if best is not None else None


def solve_vector_scale(lines, dims, page_w_pt: float = 1.0, page_h_pt: float = 1.0):
    """Solve the plan scale (metres per PDF point) from the drawing itself.

    Prefers a drawn scale bar; otherwise matches dimension labels to the
    wall/dimension lines they annotate and takes the median ratio. Returns a
    dict or None when no reliable scale can be derived.
    """
    bar = detect_scale_bar(lines, dims)
    if bar:
        return {
            "m_per_pt": round(bar["m_per_pt"], 6),
            "source": "scale-bar",
            "reliability": 1,
            "dims_used": 1,
            "scale_detail": f"Scale bar {bar['value_m']:g} m over {bar['line_len_pt']:g} pt",
        }
    scales = []
    used = set()
    for d in dims:
        i = _match_dimension_line(lines, d)
        if i is None or i in used:
            continue
        x1, y1, x2, y2 = lines[i]
        L = _seg_len_pt(x1, y1, x2, y2)
        if L <= 0:
            continue
        s = d["value_m"] / L
        if 0.0005 <= s <= 0.05:
            scales.append(s)
            used.add(i)
    if not scales:
        return None
    scales.sort()
    med = scales[len(scales) // 2]
    return {
        "m_per_pt": round(med, 6),
        "source": "dimension-text",
        "reliability": len(scales),
        "dims_used": len(scales),
        "scale_detail": f"{len(scales)} dimension labels matched (median {med:g} m/pt)",
    }


def estimate_plan_rotation(lines: List[Tuple[float, float, float, float]], min_len_pt: float = 15.0) -> float:
    """Dominant rotation of the drawing from its near-axis wall lines (degrees)."""
    bins: Dict[float, float] = {}
    for (x1, y1, x2, y2) in lines:
        L = _seg_len_pt(x1, y1, x2, y2)
        if L < min_len_pt:
            continue
        r = _line_angle_deg(x1, y1, x2, y2) % 180
        if r > 90:
            r = 180 - r
        if r > 45:
            r = 90 - r
        b = round(r * 2) / 2.0
        if abs(b) <= 6:
            bins[b] = bins.get(b, 0.0) + L
    if not bins:
        return 0.0
    best = max(bins.items(), key=lambda kv: kv[1])[0]
    return round(best, 2)


def building_wall_lines(lines, dims, min_len_pt: float = 8.0, page_w_pt: Optional[float] = None, page_h_pt: Optional[float] = None):
    """Wall-like lines: strips dimension lines and near-page-size frames."""
    excluded = set()
    for d in dims:
        i = _match_dimension_line(lines, d)
        if i is not None:
            excluded.add(i)
    max_span = 0.0
    if page_w_pt or page_h_pt:
        max_span = 0.98 * max(float(page_w_pt or 0), float(page_h_pt or 0))
    out = []
    for i, (x1, y1, x2, y2) in enumerate(lines):
        if i in excluded:
            continue
        L = _seg_len_pt(x1, y1, x2, y2)
        if L < min_len_pt:
            continue
        if max_span and L > max_span:
            continue
        out.append((x1, y1, x2, y2))
    return out


def vector_envelope_perimeter(
    wall_lines,
    m_per_pt: float,
    angle_deg: float = 0.0,
    page_w_pt: float = 1.0,
    page_h_pt: float = 1.0,
):
    """Outer envelope of the (deskewed) wall lines, converted to real metres."""
    cx = float(page_w_pt) / 2.0
    cy = float(page_h_pt) / 2.0
    xs = []
    ys = []
    for (x1, y1, x2, y2) in wall_lines or []:
        if angle_deg:
            a = _rotate_xy(x1, y1, cx, cy, -angle_deg)
            b = _rotate_xy(x2, y2, cx, cy, -angle_deg)
            xs.extend([a[0], b[0]])
            ys.extend([a[1], b[1]])
        else:
            xs.extend([x1, x2])
            ys.extend([y1, y2])
    if not xs:
        return None
    w_pt = max(xs) - min(xs)
    h_pt = max(ys) - min(ys)
    if w_pt <= 0 or h_pt <= 0:
        return None
    w_m = w_pt * m_per_pt
    h_m = h_pt * m_per_pt
    return {
        "perimeter_m": round(2 * (w_m + h_m), 2),
        "envelope_w_m": round(w_m, 2),
        "envelope_h_m": round(h_m, 2),
        "method": "vector-wall",
        "wall_line_count": len(wall_lines),
    }


def _page_vector_scale(page) -> Dict[str, Any]:
    """Analyse one PDF page: vector geometry, auto scale, rotation."""
    vec = extract_page_vectors(page)
    dims = detect_dimension_texts(vec["words"])
    scale = solve_vector_scale(vec["lines"], dims, vec["page_w_pt"], vec["page_h_pt"])
    angle = estimate_plan_rotation(vec["lines"])
    wall_lines = building_wall_lines(vec["lines"], dims, page_w_pt=vec["page_w_pt"], page_h_pt=vec["page_h_pt"])
    meta = {
        "page_w_pt": round(vec["page_w_pt"], 3),
        "page_h_pt": round(vec["page_h_pt"], 3),
        "m_per_pt": scale["m_per_pt"] if scale else None,
        "source": scale["source"] if scale else None,
        "scale_detail": scale["scale_detail"] if scale else None,
        "dims_used": scale["dims_used"] if scale else 0,
        "angle_deg": angle,
        "line_count": len(vec["lines"]),
        "wall_line_count": len(wall_lines),
    }
    return {"meta": meta, "wall_lines": [[round(c, 2) for c in l] for l in wall_lines]}


def _plan_vector_page(job: Dict[str, Any]):
    """Best plan page for vector measurement (floor plans preferred)."""
    best = None
    best_score = -1
    for a in job.get("analyses") or []:
        for p in a.get("pages") or []:
            vs = p.get("vector_scale") or {}
            if not vs.get("m_per_pt"):
                continue
            ptype = str(p.get("page_type") or "")
            ptype_bonus = 3 if "plan" in ptype else (1 if ptype else 0)
            score = ptype_bonus * 100000 + (vs.get("wall_line_count") or 0)
            if score > best_score:
                best_score = score
                best = p
    return best


def plan_auto_scale(job: Dict[str, Any], file: str, page: Any, dpi: int = 150):
    """Automatic drawing scale for one rendered page, as metres per pixel."""
    for analysis in job.get("analyses") or []:
        if str(analysis.get("file") or "") != str(file or ""):
            continue
        for p in analysis.get("pages") or []:
            if int(p.get("page") or 1) != int(page or 1):
                continue
            vs = p.get("vector_scale") or {}
            mpt = vs.get("m_per_pt")
            if not mpt:
                return None
            return {
                "m_per_px": round(float(mpt) * 72.0 / max(float(dpi), 1), 8),
                "m_per_pt": float(mpt),
                "source": vs.get("source"),
                "scale_detail": vs.get("scale_detail"),
                "angle_deg": vs.get("angle_deg") or 0.0,
            }
    return None


def _plan_page_image(job: Dict[str, Any], file: str, page: Any):
    for analysis in job.get("analyses") or []:
        if str(analysis.get("file") or "") != str(file or ""):
            continue
        for p in analysis.get("pages") or []:
            if int(p.get("page") or 1) == int(page or 1):
                return Path(str(p.get("image_path") or ""))
    return None


def _solve_plan_scale(
    markers: List[Dict[str, Any]],
    aspect_ratio: float,
    total_area_m2: float,
):
    """Find metres-per-fraction scales so the marker rectangles tile a
    footprint whose area equals the measured total room area.

    Returns ``(m_per_width_fraction, m_per_height_fraction)`` or None when no
    scale in range reproduces the measured area (markers too few/inconsistent).
    """
    xc = [_to_float(m.get("x")) for m in markers]
    yc = [_to_float(m.get("y")) for m in markers]
    d1 = [_to_float(m.get("dim1_m")) for m in markers]
    d2 = [_to_float(m.get("dim2_m")) for m in markers]
    if any(v is None or v < 0 for v in xc + yc + d1 + d2):
        return None
    if total_area_m2 <= 0:
        return None
    ar = aspect_ratio or 1.0

    def envelope_area(sh):
        sw = ar * sh
        bb_w = max(x + d / (2 * sw) for x, d in zip(xc, d1)) - min(x - d / (2 * sw) for x, d in zip(xc, d1))
        bb_h = max(y + d / (2 * sh) for y, d in zip(yc, d2)) - min(y - d / (2 * sh) for y, d in zip(yc, d2))
        if bb_w <= 0 or bb_h <= 0:
            return None
        return bb_w * sw * bb_h * sh

    lo, hi = 1e-3, 500.0
    f_lo = envelope_area(lo)
    f_hi = envelope_area(hi)
    if f_lo is None or f_hi is None:
        return None
    if not (f_lo <= total_area_m2 <= f_hi):
        return None
    for _ in range(90):
        mid = (lo + hi) / 2
        f_mid = envelope_area(mid)
        if f_mid is None:
            return None
        if f_mid < total_area_m2:
            lo = mid
        else:
            hi = mid
    sh = (lo + hi) / 2
    sw = ar * sh
    return sw, sh


def external_footprint(
    job: Dict[str, Any],
    markers: List[Dict[str, Any]],
    rooms: List[Dict[str, Any]],
    wall_thickness_m: float = DEFAULT_WALL_THICKNESS_M,
) -> Dict[str, Any]:
    """Estimate the building's external perimeter from plan measurements.

    Prefers positioned room markers (tap position + real dimensions): the
    plan's scale is solved so the marker rectangles tile a footprint matching
    the measured total floor area, and the envelope gives internal dimensions.
    Falls back to a rectangle built from total floor area with a 1.5 aspect
    ratio. Returns a dict with ``perimeter_m``, dimensions, ``method`` and a
    human-readable note.
    """
    t = max(_to_float(wall_thickness_m) or 0.0, 0.0)
    fallback = {
        "perimeter_m": 0.0,
        "envelope_w_m": 0.0,
        "envelope_h_m": 0.0,
        "total_area_m2": 0.0,
        "method": "none",
        "note": "No room measurements available for an external calculation.",
    }

    vpage = _plan_vector_page(job)
    if vpage is not None:
        vs = vpage.get("vector_scale") or {}
        wall_lines = vpage.get("vector_wall_lines") or []
        if wall_lines and vs.get("m_per_pt"):
            env = vector_envelope_perimeter(
                wall_lines,
                vs["m_per_pt"],
                vs.get("angle_deg") or 0.0,
                vs.get("page_w_pt") or 1.0,
                vs.get("page_h_pt") or 1.0,
            )
            if env and env["perimeter_m"] > 0:
                return {
                    "perimeter_m": env["perimeter_m"],
                    "envelope_w_m": env["envelope_w_m"],
                    "envelope_h_m": env["envelope_h_m"],
                    "total_area_m2": round(env["envelope_w_m"] * env["envelope_h_m"], 2),
                    "method": "vector-wall",
                    "note": (f"Measured from PDF wall geometry on page {vpage.get('page')} "
                             f"({vpage.get('title') or 'plan'}): {env['envelope_w_m']:g} x {env['envelope_h_m']:g} m "
                             f"envelope from {env['wall_line_count']} wall lines. Scale from "
                             f"{vs.get('source') or 'n/a'}."),
                }

    if not markers and not rooms:
        return fallback

    positioned = [
        m for m in markers or []
        if _to_float(m.get("x")) is not None and _to_float(m.get("y")) is not None
        and _to_float(m.get("dim1_m")) and _to_float(m.get("dim2_m"))
    ]
    if positioned:
        pages: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        for m in positioned:
            key = (str(m.get("file") or ""), int(m.get("page") or 1))
            pages.setdefault(key, []).append(m)
        best_key, best_markers = max(pages.items(), key=lambda kv: len(kv[1]))
        image = _plan_page_image(job, *best_key)
        size = _image_pixel_size(image) if image and image.exists() else None
        if size:
            total_area = sum(_to_float(m.get("dim1_m")) * _to_float(m.get("dim2_m")) for m in best_markers)
            scale = _solve_plan_scale(best_markers, size[0] / size[1], total_area)
            if scale:
                sw, sh = scale
                w_frac = (
                    max(_to_float(m.get("x")) + _to_float(m.get("dim1_m")) / (2 * sw) for m in best_markers)
                    - min(_to_float(m.get("x")) - _to_float(m.get("dim1_m")) / (2 * sw) for m in best_markers)
                )
                h_frac = (
                    max(_to_float(m.get("y")) + _to_float(m.get("dim2_m")) / (2 * sh) for m in best_markers)
                    - min(_to_float(m.get("y")) - _to_float(m.get("dim2_m")) / (2 * sh) for m in best_markers)
                )
                w_m = round(max(w_frac * sw, 0.0), 2)
                h_m = round(max(h_frac * sh, 0.0), 2)
                p_int = 2 * (w_m + h_m)
                p_ext = round(p_int + 8 * t, 2)
                return {
                    "perimeter_m": p_ext,
                    "envelope_w_m": w_m,
                    "envelope_h_m": h_m,
                    "total_area_m2": round(total_area, 2),
                    "method": "marker-envelope",
                    "note": (f"Footprint from {len(best_markers)} positioned room markers: "
                             f"{w_m:g} x {h_m:g} m envelope (+{t:g} m wall thickness) = {p_ext:g} m perimeter."),
                }

    rooms_with_area = [
        r for r in rooms or []
        if _to_float(r.get("dim1_m")) and _to_float(r.get("dim2_m"))
    ]
    if not rooms_with_area:
        return fallback
    total_area = sum(_to_float(r.get("dim1_m")) * _to_float(r.get("dim2_m")) for r in rooms_with_area)
    aspect = 1.5
    w = math.sqrt(total_area / aspect)
    h = aspect * w
    p_int = 2 * (w + h)
    p_ext = round(p_int + 8 * t, 2)
    return {
        "perimeter_m": p_ext,
        "envelope_w_m": round(w + 2 * t, 2),
        "envelope_h_m": round(h + 2 * t, 2),
        "total_area_m2": round(total_area, 2),
        "method": "area-estimate",
        "note": (f"No positioned room markers found — footprint estimated from {total_area:g} m² of rooms "
                 f"as a {aspect:g}:1 rectangle ({w:g} x {h:g} m) + wall thickness = {p_ext:g} m perimeter."),
    }


def elevation_openings_m2(job: Dict[str, Any]) -> float:
    total = 0.0
    for img_path, entry in (job.get("elevation_progress") or {}).items():
        cal = normalise_calibration(entry.get("calibration"))
        size = _image_pixel_size(img_path)
        mpp = calibration_mpp(cal, size[0] if size else None, size[1] if size else None) if cal else None
        for b in normalise_boxes(entry.get("zones", [])):
            if str(b.get("substrate") or "").strip() != "Windows / doors / frames":
                continue
            total += effective_box_m2(b, mpp, size[0] if size else None, size[1] if size else None)
    return round(total, 2)


def compute_external_takeoff_rows(
    job: Dict[str, Any],
    wall_height_m: Any = None,
    eave_depth_m: Any = None,
    wall_thickness_m: Any = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build the external painting rows automatically from the plan's room
    measurements (footprint perimeter) and the elevations (measured window and
    door openings).
    """
    settings = job.get("external_settings") or {}
    H = _to_float(wall_height_m) or _to_float(settings.get("wall_height_m")) or DEFAULT_EXTERNAL_WALL_HEIGHT_M
    E = _to_float(eave_depth_m) or _to_float(settings.get("eave_depth_m")) or DEFAULT_EAVE_DEPTH_M
    T = _to_float(wall_thickness_m) or _to_float(settings.get("wall_thickness_m")) or DEFAULT_WALL_THICKNESS_M
    markers = load_corrections(job.get("job_id") or "")
    footprint = external_footprint(job, markers, job.get("rooms") or [], T)
    P = footprint["perimeter_m"]
    openings = elevation_openings_m2(job)
    gross = round(P * H, 2)
    net = round(max(gross - openings, 0.0), 2)
    soffits = round(P * E, 2)
    note = (f"Auto external: {P:g} m perimeter x {H:g} m wall height − {openings:g} m² openings = {net:g} m² walls; "
            f"soffits {P:g} m x {E:g} m eave = {soffits:g} m².")
    rows: List[Dict[str, Any]] = []
    if P > 0:
        rows.extend([
            {
                "internal_external": "External",
                "area_location": "Whole building",
                "substrate": "External walls / render",
                "labour_category": "Exterior",
                "qty_m2": net,
                "lineal_m": 0.0,
                "count": 0,
                "coats": 2,
                "rate_ex_gst": 0.0,
                "labour_hours": 0.0,
                "paint_litres": litres_from_area(net, 2),
                "source_note": AUTO_EXTERNAL_SOURCE,
                "confidence": note,
            },
            {
                "internal_external": "External",
                "area_location": "Whole building",
                "substrate": "External soffits / eaves",
                "labour_category": "Ceilings",
                "qty_m2": soffits,
                "lineal_m": 0.0,
                "count": 0,
                "coats": 2,
                "rate_ex_gst": 0.0,
                "labour_hours": 0.0,
                "paint_litres": litres_from_area(soffits, 2),
                "source_note": AUTO_EXTERNAL_SOURCE,
                "confidence": note,
            },
            {
                "internal_external": "External",
                "area_location": "Whole building",
                "substrate": "Fascia / gutters / trim",
                "labour_category": "Woodwork",
                "qty_m2": 0.0,
                "lineal_m": round(P, 2),
                "count": 0,
                "coats": 2,
                "rate_ex_gst": 0.0,
                "labour_hours": 0.0,
                "paint_litres": 0.0,
                "source_note": AUTO_EXTERNAL_SOURCE,
                "confidence": note,
            },
        ])
        if openings > 0:
            rows.append({
                "internal_external": "External",
                "area_location": "Elevation openings",
                "substrate": "Windows / doors / frames",
                "labour_category": "Woodwork",
                "qty_m2": openings,
                "lineal_m": 0.0,
                "count": 0,
                "coats": 2,
                "rate_ex_gst": 0.0,
                "labour_hours": 0.0,
                "paint_litres": litres_from_area(openings, 2),
                "source_note": AUTO_EXTERNAL_SOURCE,
                "confidence": "Measured window/door areas from elevation boxes.",
            })
    info = {
        "perimeter_m": P,
        "envelope_w_m": footprint["envelope_w_m"],
        "envelope_h_m": footprint["envelope_h_m"],
        "total_area_m2": footprint["total_area_m2"],
        "method": footprint["method"],
        "footprint_note": footprint["note"],
        "wall_height_m": round(H, 2),
        "eave_depth_m": round(E, 2),
        "wall_thickness_m": round(T, 2),
        "openings_m2": openings,
        "gross_walls_m2": gross,
        "net_walls_m2": net,
        "soffits_m2": soffits,
        "fascia_lineal_m": round(P, 2),
    }
    return rows, info


def merge_auto_external_rows(job: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    auto, info = compute_external_takeoff_rows(job)
    kept = [r for r in rows if r.get("source_note") != AUTO_EXTERNAL_SOURCE]
    if info["perimeter_m"] > 0:
        kept.extend(auto)
    return kept


def merge_elevation_box_rows(
    job: Dict[str, Any],
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    kept = [r for r in rows if r.get("source_note") != ELEVATION_BOX_SOURCE]
    for img_path, entry in (job.get("elevation_progress") or {}).items():
        cal = normalise_calibration(entry.get("calibration"))
        size = _image_pixel_size(img_path)
        img_w = size[0] if size else None
        img_h = size[1] if size else None
        mpp = calibration_mpp(cal, img_w, img_h) if cal else None
        for b in normalise_boxes(entry.get("zones", [])):
            qty = effective_box_m2(b, mpp, img_w, img_h)
            if qty <= 0:
                continue
            labour, int_ext = substrate_labour(b.get("substrate"))
            substrate = b.get("substrate") or "External walls / render"
            location = f"{Path(img_path).stem} · {b.get('label') or substrate}"[:120]
            kept.append({
                "internal_external": int_ext,
                "area_location": location,
                "substrate": substrate,
                "labour_category": labour,
                "qty_m2": round(qty, 2),
                "lineal_m": 0.0,
                "count": 0,
                "coats": 2,
                "rate_ex_gst": 0.0,
                "labour_hours": 0.0,
                "paint_litres": litres_from_area(qty, 2),
                "source_note": ELEVATION_BOX_SOURCE,
                "confidence": "Manual elevation box",
            })
    return kept


def compute_room_takeoff_rows(
    rooms: List[Dict[str, Any]],
    ceiling_height: float = DEFAULT_CEILING_HEIGHT_M,
    openings_allowance_m2: float = DEFAULT_OPENINGS_ALLOWANCE_M2,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    allowance = max(float(openings_allowance_m2 or 0), 0.0)
    for r in rooms or []:
        room = str(r.get("room") or "Room").strip() or "Room"
        d1 = float(r.get("dim1_m") or 0)
        d2 = float(r.get("dim2_m") or 0)
        if d1 <= 0 or d2 <= 0:
            continue
        perimeter = 2 * (d1 + d2)
        area = round(d1 * d2, 2)
        wall_area = round(max(perimeter * float(ceiling_height) - allowance, 0.0), 2)
        note = f"Room dimension {room}: {d1:g} x {d2:g} m"
        rows.append({
            "internal_external": "Internal",
            "area_location": room,
            "substrate": "Internal walls",
            "labour_category": "Walls",
            "qty_m2": wall_area,
            "lineal_m": 0.0,
            "count": 0,
            "coats": 2,
            "rate_ex_gst": 0.0,
            "labour_hours": 0.0,
            "paint_litres": litres_from_area(wall_area, 2),
            "source_note": note,
            "confidence": "Computed from room dimensions",
        })
        rows.append({
            "internal_external": "Internal",
            "area_location": room,
            "substrate": "Internal ceilings",
            "labour_category": "Ceilings",
            "qty_m2": area,
            "lineal_m": 0.0,
            "count": 0,
            "coats": 2,
            "rate_ex_gst": 0.0,
            "labour_hours": 0.0,
            "paint_litres": litres_from_area(area, 2),
            "source_note": note,
            "confidence": "Computed from room dimensions",
        })
    return rows


def build_takeoff_from_analysis(analysis: Dict[str, Any]) -> pd.DataFrame:
    rows = []
    seen = set()
    rooms = analysis.get("rooms", [])
    snippets = analysis.get("painting_snippets", [])
    area_candidates = analysis.get("area_candidates", [])
    # Computed wall/ceiling areas from room dimensions come first (highest confidence).
    for r in compute_room_takeoff_rows(rooms):
        key = (r["substrate"], round(r["qty_m2"], 2), r["source_note"][:60])
        if key in seen:
            continue
        seen.add(key)
        rows.append(r)
    # Lineal quantities for trims first, then m² text quantities for painted areas.
    for cand in area_candidates:
        source = cand.get("source", "")
        low_source = source.lower()
        if cand.get("unit") == "lm":
            if any(k in low_source for k in PAINT_KEYWORDS) and any(
                t in low_source
                for t in ["skirting", "architrave", "trim", "scotia", "cornice", "picture rail", "shadow"]
            ):
                substrate, labour_cat, int_ext = detect_substrate_from_text(source)
                key = (substrate, round(float(cand.get("qty", 0)), 2), source[:60])
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "internal_external": int_ext,
                    "area_location": f"{Path(cand.get('file','')).stem} p{cand.get('page','')}",
                    "substrate": substrate,
                    "labour_category": labour_cat,
                    "qty_m2": 0.0,
                    "lineal_m": float(cand.get("qty", 0)),
                    "count": 0,
                    "coats": 2,
                    "rate_ex_gst": 0.0,
                    "labour_hours": 0.0,
                    "paint_litres": 0.0,
                    "source_note": source[:250],
                    "confidence": "Medium - lineal quantity found",
                })
            continue
        if cand.get("unit") != "m²":
            continue
        if not any(k in source.lower() for k in PAINT_KEYWORDS):
            continue
        substrate, labour_cat, int_ext = detect_substrate_from_text(source)
        key = (substrate, round(float(cand.get("qty", 0)), 2), source[:60])
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "internal_external": int_ext,
            "area_location": f"{Path(cand.get('file','')).stem} p{cand.get('page','')}",
            "substrate": substrate,
            "labour_category": labour_cat,
            "qty_m2": float(cand.get("qty", 0)),
            "lineal_m": 0.0,
            "count": 0,
            "coats": 2,
            "rate_ex_gst": 0.0,
            "labour_hours": 0.0,
            "paint_litres": litres_from_area(float(cand.get("qty", 0)), 2),
            "source_note": source[:250],
            "confidence": "Medium - text quantity found",
        })
    # Then create category rows from paint snippets if no quantities.
    for item in snippets[:150]:
        txt = item.get("text", "")
        substrate, labour_cat, int_ext = detect_substrate_from_text(txt)
        key = (substrate, labour_cat, int_ext, item.get("file"), item.get("page"))
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "internal_external": int_ext,
            "area_location": f"{Path(item.get('file','')).stem} p{item.get('page','')}",
            "substrate": substrate,
            "labour_category": labour_cat,
            "qty_m2": 0.0,
            "lineal_m": 0.0,
            "count": 0,
            "coats": 2,
            "rate_ex_gst": 0.0,
            "labour_hours": 0.0,
            "paint_litres": 0.0,
            "source_note": txt[:250],
            "confidence": "Low - item found but quantity needs measure",
        })
    # Always add missing standard painting buckets.
    standard = [
        ("Internal", "Internal walls", "Walls"),
        ("Internal", "Internal ceilings", "Ceilings"),
        ("Internal", "Doors / frames / trim", "Woodwork"),
        ("External", "External walls / render / cladding", "Exterior"),
        ("External", "External soffits / eaves", "Ceilings"),
        ("External", "Downpipes / small gloss items", "Woodwork"),
    ]
    existing_subs = {r["substrate"].lower() for r in rows}
    for int_ext, substrate, labour_cat in standard:
        if substrate.lower() not in existing_subs:
            rows.append({
                "internal_external": int_ext,
                "area_location": "To be measured",
                "substrate": substrate,
                "labour_category": labour_cat,
                "qty_m2": 0.0,
                "lineal_m": 0.0,
                "count": 0,
                "coats": 2,
                "rate_ex_gst": 0.0,
                "labour_hours": 0.0,
                "paint_litres": 0.0,
                "source_note": "Standard painting bucket added for review.",
                "confidence": "Manual quantity required",
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["internal_external", "area_location", "substrate", "source_note"]).reset_index(drop=True)
    return df


def run_optional_ai_extract(text: str) -> str:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return "OPENAI_API_KEY is not set. Manual extraction still works."
    model = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip()
    prompt = f"""
You are a commercial painting estimator. Extract the useful painting information from the plan/spec text below.
Return concise structured sections:
1. Project details
2. Drawing pages / schedules found
3. Painting substrates
4. Paint systems / colours / sheens
5. Items to include
6. Items to exclude or verify
7. Take-off rows with internal/external, area/location, substrate, qty if stated, unit, notes.
Do not invent quantities. Mark unknown quantities as TO MEASURE.

TEXT:
{text[:50000]}
"""
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "You extract painting take-off information from architectural plan/spec text."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
            },
            timeout=90,
        )
        if resp.status_code >= 400:
            return f"OpenAI error {resp.status_code}: {resp.text[:1000]}"
        data = resp.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "No AI content returned.")
    except Exception as e:
        return f"OpenAI request failed: {e}"


def normalise_progress(value: Any) -> float:
    try:
        p = float(value)
    except (TypeError, ValueError):
        p = 0.0
    if p < 0:
        p = 0.0
    if p > 100:
        p = 100.0
    return round(p, 1)


def zone_colour(progress: float) -> Tuple[int, int, int]:
    p = normalise_progress(progress)
    if p == 0:
        return (128, 128, 128)
    if p < 50:
        return (230, 140, 30)
    if p < 100:
        return (255, 180, 0)
    return (0, 150, 60)


def zone_rect_px(zone: Dict[str, Any], width: int, height: int) -> Tuple[int, int, int, int]:
    def val(key: str, default: float) -> float:
        try:
            return float(zone.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    x = round(val("x", 0.0) / 100.0 * width)
    y = round(val("y", 0.0) / 100.0 * height)
    w = round(val("w", 0.0) / 100.0 * width)
    h = round(val("h", 0.0) / 100.0 * height)
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(0, min(w, width - x))
    h = max(0, min(h, height - y))
    return (x, y, w, h)


def render_elevation_overlay(
    image_path: str,
    zones: List[Dict[str, Any]],
    out_path: str,
    overall_progress: float = 0.0,
) -> Path:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise ImportError(
            "Pillow is required to render elevation progress overlays. Install it with: pip install Pillow"
        ) from None
    img = Image.open(image_path).convert("RGB")
    width, height = img.size
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()
    for z in zones or []:
        label = str(z.get("label") or "").strip()
        x, y, w, h = zone_rect_px(z, width, height)
        colour = zone_colour(normalise_progress(z.get("progress", 0)))
        draw.rectangle([x, y, x + w, y + h], fill=colour + (120,), outline=(255, 255, 255, 220), width=2)
        if label:
            tb = draw.textbbox((x + 4, y + 4), label, font=font)
            pad = 3
            draw.rectangle([tb[0] - pad, tb[1] - pad, tb[2] + pad, tb[3] + pad], fill=(20, 20, 20, 200))
            draw.text((x + 4, y + 4), label, fill=(255, 255, 255, 255), font=font)
    header = 46
    draw.rectangle([0, 0, width, header], fill=zone_colour(normalise_progress(overall_progress)) + (210,))
    draw.text((10, 14), f"Elevation progress: {normalise_progress(overall_progress):g}%", fill=(255, 255, 255, 255), font=font)
    composed = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    composed.save(str(out))
    return out


def build_elevation_board(entries: List[Tuple[str, str, Any]], out_path: str, cols: int = 2) -> Path | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise ImportError(
            "Pillow is required to build the elevation progress board. Install it with: pip install Pillow"
        ) from None
    cell_w = 900
    caption_h = 44
    gap = 24
    margin = 24
    cells = []
    for img_path, label, progress in entries:
        try:
            im = Image.open(img_path).convert("RGB")
        except Exception:
            continue
        scale = cell_w / im.width if im.width > 0 else 1.0
        cell_h = round(im.height * scale)
        cells.append({"im": im, "label": label, "progress": normalise_progress(progress), "h": cell_h})
    if not cells:
        return None
    block_h = max(c["h"] for c in cells) + caption_h
    ncols = min(cols, len(cells))
    nrows = (len(cells) + ncols - 1) // ncols
    canvas_w = margin * 2 + ncols * cell_w + (ncols - 1) * gap
    canvas_h = margin * 2 + nrows * block_h + (nrows - 1) * gap
    canvas = Image.new("RGB", (canvas_w, canvas_h), (245, 245, 245))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    for i, c in enumerate(cells):
        col = i % ncols
        row = i // ncols
        x0 = margin + col * (cell_w + gap)
        y0 = margin + row * (block_h + gap)
        im = c["im"].resize((cell_w, c["h"]), Image.Resampling.LANCZOS)
        canvas.paste(im, (x0, y0))
        bar = zone_colour(c["progress"])
        draw.rectangle([x0, y0 + c["h"], x0 + cell_w, y0 + c["h"] + caption_h], fill=bar)
        label = str(c["label"] or "Elevation")[:80]
        draw.text((x0 + 8, y0 + c["h"] + 15), f"{label} - {c['progress']:g}%", fill=(255, 255, 255, 255), font=font)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out))
    return out


def elevation_image_options(job: Dict[str, Any]) -> List[Dict[str, str]]:
    seen = set()
    out = []
    for a in job.get("analyses", []):
        for p in a.get("pages", []):
            if p.get("page_type") != "elevation":
                continue
            ip = p.get("image_path") or ""
            if not ip or ip in seen or not Path(ip).exists():
                continue
            seen.add(ip)
            out.append({
                "label": f"{a.get('file','')} - {p.get('title') or 'Elevation'} (p{p.get('page')})",
                "image_path": ip,
                "file": a.get("file", ""),
                "page": p.get("page", 1),
                "render_dpi": p.get("render_dpi") or 150,
            })
    for f in job.get("files", []):
        if f.get("category") != "Drawing image":
            continue
        ip = f.get("path") or ""
        if not ip or ip in seen or not Path(ip).exists():
            continue
        seen.add(ip)
        out.append({"label": f.get("name", "Elevation image"), "image_path": ip})
    return out


def df_to_excel_bytes(sheets: Dict[str, pd.DataFrame]) -> bytes:
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for name, df in sheets.items():
            safe_sheet = re.sub(r"[^A-Za-z0-9 _-]", "", name)[:31] or "Sheet"
            df.to_excel(writer, index=False, sheet_name=safe_sheet)
    return out.getvalue()


def file_download_button(path: Path, label: str = "Download"):
    if path.exists():
        st.download_button(label, path.read_bytes(), file_name=path.name, mime="application/octet-stream", key=f"dl_{path}_{time.time()}")


def render_logo():
    logo = ASSETS_DIR / "PB_Logo_Main_PNG.png"
    if logo.exists():
        try:
            b64 = base64.b64encode(logo.read_bytes()).decode()
            st.sidebar.markdown(f"<div class='side-logo'><img src='data:image/png;base64,{b64}' /></div>", unsafe_allow_html=True)
        except Exception:
            pass


def app_css():
    st.markdown(
        """
<style>
:root { --pb-bg:#f4f0ea; --pb-card:#ffffff; --pb-ink:#171717; --pb-muted:#666; --pb-line:#e3ddd4; --pb-accent:#b5a38d; }
.stApp { background: var(--pb-bg); }
.block-container { padding-top: 1.4rem; max-width: 1450px; }
[data-testid="stSidebar"] { background:#111; color:#fff; }
[data-testid="stSidebar"] * { color:#fff; }
[data-testid="stSidebar"] select, [data-testid="stSidebar"] option, [data-testid="stSidebar"] input { color:#111 !important; }
.side-logo { background:#fff; border-radius:16px; padding:12px; margin: 8px 0 18px 0; text-align:center; }
.side-logo img { max-width: 100%; max-height: 95px; object-fit:contain; }
.pb-card { background:rgba(255,255,255,.93); border:1px solid var(--pb-line); border-radius:18px; padding:18px; margin:10px 0; box-shadow:0 8px 24px rgba(0,0,0,.04); }
.pb-card h3 { margin-top:0; }
.metric-row { display:flex; gap:12px; flex-wrap:wrap; }
.metric-box { flex:1 1 150px; background:#fff; border:1px solid var(--pb-line); border-radius:15px; padding:14px; }
.metric-box .big { font-size:28px; font-weight:800; color:#111; }
.metric-box .label { color:#666; font-size:13px; }
.status-good { background:#e8f5ed; color:#0a6b31; border:1px solid #bce3c9; border-radius:999px; padding:3px 10px; font-weight:700; }
.status-warn { background:#fff6df; color:#8a5a00; border:1px solid #f4daa0; border-radius:999px; padding:3px 10px; font-weight:700; }
.status-bad { background:#ffe9e6; color:#9e1f13; border:1px solid #efbeb8; border-radius:999px; padding:3px 10px; font-weight:700; }
.small-muted { color:#666; font-size:13px; }
</style>
        """,
        unsafe_allow_html=True,
    )


def create_or_select_job() -> str:
    jobs = list_jobs()
    st.sidebar.markdown("### Job")
    mode = st.sidebar.radio("Job mode", ["Open existing", "Create new"], label_visibility="collapsed")
    if mode == "Create new" or not jobs:
        with st.sidebar.form("new_job_form"):
            job_no = st.text_input("Job no", value=f"PB-{datetime.now().strftime('%y%m%d')}")
            job_name = st.text_input("Job name", value="New plan import")
            builder = st.text_input("Builder / client", value="")
            address = st.text_input("Site address", value="")
            submitted = st.form_submit_button("Create / open job")
        if submitted:
            jid = job_id_from_name(job_no, job_name)
            meta = load_job(jid) or {}
            meta.update({"job_no": job_no, "job_name": job_name, "builder": builder, "site_address": address, "created_at": meta.get("created_at") or now_stamp()})
            save_job(jid, meta)
            st.session_state["selected_job_id"] = jid
            st.rerun()
    options = {f"{j.get('job_no','')} - {j.get('job_name','')}": j.get("job_id") for j in jobs}
    current = st.session_state.get("selected_job_id")
    labels = list(options.keys())
    default_index = 0
    if current:
        for i, lbl in enumerate(labels):
            if options[lbl] == current:
                default_index = i
    selected_label = st.sidebar.selectbox("Select job", labels, index=default_index if labels else 0)
    jid = options.get(selected_label) if selected_label else None
    if jid:
        st.session_state["selected_job_id"] = jid
        return jid
    return st.session_state.get("selected_job_id", "")


def process_uploads_page(job_id: str):
    job = load_job(job_id)
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("Upload plans / specs")
    st.caption("Upload multiple PDFs/images. This app extracts text, classifies drawing pages, renders pages to images, and builds a draft painting take-off.")
    uploads = st.file_uploader(
        "Upload plan/spec files",
        type=["pdf", "png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
    )
    col1, col2, col3 = st.columns(3)
    with col1:
        render_pdf_pages = st.checkbox("Convert PDF pages to PNG", value=True)
    with col2:
        dpi = st.select_slider("Image quality", options=[100, 150, 200, 250], value=150)
    with col3:
        run_ai = st.checkbox("Run optional AI summary", value=False, help="Only runs if OPENAI_API_KEY is set.")
    if st.button("Import files and gather plan information", type="primary", disabled=not uploads):
        files = job.get("files", [])
        analyses = job.get("analyses", [])
        all_text = []
        progress = st.progress(0)
        for i, up in enumerate(uploads or []):
            saved = save_uploaded_file(job_id, up, "source_files")
            ext = saved.suffix.lower()
            files.append(file_record(saved, ext.lstrip("."), "Plan/spec import"))
            if ext == ".pdf":
                with st.spinner(f"Reading {saved.name}..."):
                    result = analyse_pdf(saved, render_pages=render_pdf_pages, dpi=int(dpi))
                    analyses.append({k: v for k, v in result.items() if k != "all_text"})
                    all_text.append(result.get("all_text", ""))
                    for img_path in result.get("converted_images", []):
                        files.append(file_record(Path(img_path), "png", "Converted drawing image"))
            elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
                # Keep images as mapper-ready drawings.
                img_dst = job_dir(job_id) / "converted_images" / saved.name
                if saved != img_dst:
                    shutil.copyfile(saved, img_dst)
                files.append(file_record(img_dst, ext.lstrip("."), "Drawing image"))
            progress.progress((i + 1) / max(len(uploads), 1))
        combined_text = "\n".join(all_text)
        inferred = infer_project_info(combined_text, [u.name for u in uploads])
        for k, v in inferred.items():
            if v and not job.get(k):
                job[k] = v
        job["files"] = files
        job["analyses"] = analyses
        job["last_combined_text"] = combined_text[:200000]
        # Build take-off rows from all analyses.
        combined_analysis = {"painting_snippets": [], "area_candidates": [], "rooms": []}
        for a in analyses:
            combined_analysis["painting_snippets"].extend(a.get("painting_snippets", []))
            combined_analysis["area_candidates"].extend(a.get("area_candidates", []))
            combined_analysis["rooms"].extend(a.get("rooms", []))
        combined_analysis["rooms"] = apply_room_corrections(
            combined_analysis["rooms"], load_corrections(job_id)
        )
        df = build_takeoff_from_analysis(combined_analysis)
        job["takeoff_rows"] = merge_elevation_box_rows(job, df.to_dict("records"))
        job["rooms"] = combined_analysis["rooms"]
        opts = elevation_image_options(job)
        if opts:
            try:
                board = build_elevation_board(
                    [(o["image_path"], o["label"], 0) for o in opts],
                    str(job_dir(job_id) / "rendered_progress" / "elevation_board.png"),
                )
                if board:
                    job["elevation_board"] = str(board)
            except ImportError:
                pass
        if run_ai:
            job["ai_summary"] = run_optional_ai_extract(combined_text)
        save_job(job_id, job)
        st.success("Import complete. Plan information, page list, converted images and draft take-off were saved.")
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def overview_page(job_id: str):
    job = load_job(job_id)
    analyses = job.get("analyses", [])
    files = job.get("files", [])
    pages = []
    snippets = []
    areas = []
    for a in analyses:
        pages.extend(a.get("pages", []))
        snippets.extend(a.get("painting_snippets", []))
        areas.extend(a.get("area_candidates", []))
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.title("PB PlanReader")
    st.caption("Clean Render package for gathering painting information from uploaded plans/specs.")
    st.markdown("<div class='metric-row'>", unsafe_allow_html=True)
    for label, value in [
        ("Files", len(files)),
        ("PDF pages read", len(pages)),
        ("Painting lines found", len(snippets)),
        ("Area/lineal quantities found", len(areas)),
        ("Rooms detected", len(job.get("rooms", []))),
        ("Elevations tracked", len(job.get("elevation_progress", {}))),
        ("Take-off rows", len(job.get("takeoff_rows", []))),
    ]:
        st.markdown(f"<div class='metric-box'><div class='big'>{value}</div><div class='label'>{label}</div></div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("Job details found")
    c1, c2 = st.columns(2)
    with c1:
        job_no = st.text_input("Job no", value=job.get("job_no", ""))
        job_name = st.text_input("Job name / project", value=job.get("job_name") or job.get("project_name", ""))
    with c2:
        builder = st.text_input("Builder / client", value=job.get("builder", ""))
        address = st.text_input("Site address", value=job.get("site_address", ""))
    if st.button("Save job details"):
        job.update({"job_no": job_no, "job_name": job_name, "builder": builder, "site_address": address})
        save_job(job_id, job)
        st.success("Saved.")
    st.markdown("</div>", unsafe_allow_html=True)

    if pages:
        st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
        st.subheader("Drawing / page register gathered from PDFs")
        df = pd.DataFrame(pages)
        st.dataframe(df, width="stretch", height=320)
        st.markdown("</div>", unsafe_allow_html=True)
    if snippets:
        st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
        st.subheader("Painting / finish lines found")
        st.dataframe(pd.DataFrame(snippets).head(200), width="stretch", height=320)
        st.markdown("</div>", unsafe_allow_html=True)
    if job.get("ai_summary"):
        st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
        st.subheader("Optional AI summary")
        st.write(job.get("ai_summary"))
        st.markdown("</div>", unsafe_allow_html=True)


def takeoff_page(job_id: str):
    job = load_job(job_id)
    rows = job.get("takeoff_rows", [])
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("Painting take-off draft")
    st.caption("This is a working take-off table. Quantities found in the PDF text are brought in; missing quantities stay at 0 for manual measurement/review.")
    if not rows:
        st.warning("No take-off rows yet. Upload plans/specs first.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    df = pd.DataFrame(rows)
    required_cols = [
        "internal_external", "area_location", "substrate", "labour_category", "qty_m2", "lineal_m", "count",
        "coats", "rate_ex_gst", "labour_hours", "paint_litres", "source_note", "confidence"
    ]
    for col in required_cols:
        if col not in df.columns:
            df[col] = 0.0 if col in ["qty_m2", "lineal_m", "count", "coats", "rate_ex_gst", "labour_hours", "paint_litres"] else ""
    edited = st.data_editor(
        df[required_cols],
        width="stretch",
        num_rows="dynamic",
        height=520,
        column_config={
            "qty_m2": st.column_config.NumberColumn("m²", min_value=0.0, step=1.0),
            "lineal_m": st.column_config.NumberColumn("Lineal m", min_value=0.0, step=1.0),
            "count": st.column_config.NumberColumn("Count", min_value=0.0, step=1.0),
            "coats": st.column_config.NumberColumn("Coats", min_value=0.0, step=1.0),
            "rate_ex_gst": st.column_config.NumberColumn("Rate ex GST", min_value=0.0, step=1.0),
            "labour_hours": st.column_config.NumberColumn("Labour hrs", min_value=0.0, step=1.0),
            "paint_litres": st.column_config.NumberColumn("Paint litres", min_value=0.0, step=1.0),
        },
        key=f"takeoff_editor_{job_id}",
    )
    st.markdown("#### Room dimension take-off")
    rooms = job.get("rooms", [])
    st.caption("Rooms detected from dimension text on the plans. Edit sizes, set ceiling height and door/window opening allowance, then rebuild wall/ceiling rows. Computed rows replace any existing wall/ceiling rows for the same room.")
    room_df = pd.DataFrame(rooms) if rooms else pd.DataFrame(columns=["room", "dim1_m", "dim2_m"])
    room_cols = ["room", "dim1_m", "dim2_m"] + ([c for c in ["area_m2", "page"] if c in room_df.columns])
    for col in room_cols:
        if col not in room_df.columns:
            room_df[col] = 0.0 if col in ["dim1_m", "dim2_m", "area_m2", "page"] else ""
    edited_rooms = st.data_editor(
        room_df[room_cols],
        width="stretch",
        num_rows="dynamic",
        height=220,
        column_config={
            "room": st.column_config.TextColumn("Room"),
            "dim1_m": st.column_config.NumberColumn("Dim 1 (m)", min_value=0.0, step=0.1),
            "dim2_m": st.column_config.NumberColumn("Dim 2 (m)", min_value=0.0, step=0.1),
            "area_m2": st.column_config.NumberColumn("Area m²", min_value=0.0, step=0.1, disabled=True),
            "page": st.column_config.NumberColumn("Page", min_value=0, step=1, disabled=True),
        },
        key=f"rooms_editor_{job_id}",
    )
    c_h, c_o, c_cov, c_waste = st.columns(4)
    ceiling_height = c_h.number_input("Ceiling height (m)", min_value=2.1, max_value=4.5, value=DEFAULT_CEILING_HEIGHT_M, step=0.1, key=f"pr_ceiling_{job_id}")
    opening_allowance = c_o.number_input("Door/window opening allowance per room (m²)", min_value=0.0, max_value=20.0, value=DEFAULT_OPENINGS_ALLOWANCE_M2, step=0.5, key=f"pr_openings_{job_id}")
    coverage = c_cov.number_input("Paint coverage (m²/L)", min_value=6.0, max_value=20.0, value=DEFAULT_COVERAGE_M2_PER_L, step=0.5, key=f"pr_coverage_{job_id}")
    waste_pct = c_waste.number_input("Waste %", min_value=0.0, max_value=25.0, value=DEFAULT_WASTE_PCT, step=1.0, key=f"pr_waste_{job_id}")
    if st.button("Build wall / ceiling rows from rooms", type="secondary"):
        room_records = edited_rooms.to_dict("records")
        computed = compute_room_takeoff_rows(room_records, ceiling_height=ceiling_height, openings_allowance_m2=opening_allowance)
        computed_map = {(r["substrate"], str(r["area_location"])): r for r in computed}
        current = edited.to_dict("records")
        keep = []
        for r in current:
            key = (r.get("substrate"), str(r.get("area_location", "")))
            if key in computed_map:
                continue
            keep.append(r)
        keep.extend(computed_map.values())
        job["takeoff_rows"] = keep
        job["rooms"] = room_records
        save_job(job_id, job)
        st.success(f"Rebuilt {len(computed)} wall/ceiling rows from {len(room_records)} rooms.")
        st.rerun()
    st.markdown("#### External take-off (auto)")
    ext_rows, ext_info = compute_external_takeoff_rows(job)
    st.caption(ext_info["footprint_note"])
    method_label = {
        "vector-wall": "PDF wall geometry",
        "marker-envelope": "Room markers",
        "area-estimate": "Area estimate",
        "none": "No measurements",
    }.get(ext_info["method"], ext_info["method"])
    st.caption(f"Measurement method: **{method_label}**.")
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Building perimeter", f"{ext_info['perimeter_m']:g} m")
    e2.metric("Wall height", f"{ext_info['wall_height_m']:g} m")
    e3.metric("Openings (measured)", f"{ext_info['openings_m2']:g} m²")
    e4.metric("Gross external walls", f"{ext_info['gross_walls_m2']:g} m²")
    h_col, e_col, t_col = st.columns(3)
    wall_h = h_col.number_input("External wall height (m)", min_value=1.5, max_value=6.0, value=ext_info["wall_height_m"], step=0.1, key=f"pr_ext_h_{job_id}")
    eave_d = e_col.number_input("Eave depth (m)", min_value=0.0, max_value=2.0, value=ext_info["eave_depth_m"], step=0.05, key=f"pr_ext_eave_{job_id}")
    wall_t = t_col.number_input("Wall thickness allowance (m)", min_value=0.0, max_value=1.0, value=ext_info["wall_thickness_m"], step=0.05, key=f"pr_ext_t_{job_id}")
    if st.button("Generate external rows from plan + elevations", type="secondary"):
        ext_rows, ext_info = compute_external_takeoff_rows(job, wall_height_m=wall_h, eave_depth_m=eave_d, wall_thickness_m=wall_t)
        job["external_settings"] = {"wall_height_m": wall_h, "eave_depth_m": eave_d, "wall_thickness_m": wall_t}
        current = edited.to_dict("records")
        keep = [r for r in current if r.get("source_note") != AUTO_EXTERNAL_SOURCE]
        if ext_info["perimeter_m"] > 0:
            keep.extend(ext_rows)
        job["takeoff_rows"] = keep
        save_job(job_id, job)
        st.success(f"External rows generated: {ext_info['net_walls_m2']:g} m² walls, {ext_info['soffits_m2']:g} m² soffits, {ext_info['fascia_lineal_m']:g} m fascia.")
        st.rerun()
    if ext_info["perimeter_m"] > 0:
        st.caption(f"Net walls **{ext_info['net_walls_m2']:g} m²** (gross {ext_info['gross_walls_m2']:g} − {ext_info['openings_m2']:g} openings) · soffits **{ext_info['soffits_m2']:g} m²** · fascia **{ext_info['fascia_lineal_m']:g} m**.")
    else:
        st.warning("No room measurements yet — correct the rooms on the plan (Verify & Correct) or build wall/ceiling rows above to enable the automatic external take-off.")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Recalculate litres + labour", type="secondary"):
            recalculated = recalculate_takeoff_values(edited.to_dict("records"), coverage_m2_per_l=coverage, waste_pct=waste_pct)
            job["takeoff_rows"] = recalculated
            save_job(job_id, job)
            st.success(f"Paint litres and labour hours recalculated (coverage {coverage:g} m²/L, {waste_pct:g}% waste).")
            st.rerun()
    with col2:
        if st.button("Save take-off", type="primary"):
            job["takeoff_rows"] = edited.to_dict("records")
            save_job(job_id, job)
            st.success("Take-off saved.")
    with col3:
        edited["value_ex_gst"] = pd.to_numeric(edited["qty_m2"], errors="coerce").fillna(0) * pd.to_numeric(edited["rate_ex_gst"], errors="coerce").fillna(0)
        excel = df_to_excel_bytes({"Summary": pd.DataFrame(build_takeoff_summary(edited.to_dict("records"))), "Takeoff": edited, "Files": pd.DataFrame(job.get("files", []))})
        st.download_button("Download Excel", excel, file_name=f"{safe_name(job.get('job_name','takeoff'))}_takeoff.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    edited["value_ex_gst"] = pd.to_numeric(edited["qty_m2"], errors="coerce").fillna(0) * pd.to_numeric(edited["rate_ex_gst"], errors="coerce").fillna(0)
    st.markdown("### Totals")
    a, b, c, d = st.columns(4)
    a.metric("Internal m²", round(edited.loc[edited["internal_external"].astype(str).str.lower().str.contains("internal"), "qty_m2"].sum(), 2))
    b.metric("External m²", round(edited.loc[edited["internal_external"].astype(str).str.lower().str.contains("external"), "qty_m2"].sum(), 2))
    c.metric("Paint litres", round(pd.to_numeric(edited["paint_litres"], errors="coerce").fillna(0).sum(), 2))
    d.metric("Value ex GST", money(edited["value_ex_gst"].sum()))

    warnings = validate_measurements(job)
    if warnings:
        for w in warnings:
            st.warning(w)
    else:
        st.caption("Measurement checks passed — room areas and building envelope agree.")

    report_pdf = takeoff_report_pdf_bytes(job, build_takeoff_summary(edited.to_dict("records")), edited.to_dict("records"))
    st.download_button(
        "Download estimate report (PDF)",
        report_pdf,
        file_name=f"{safe_name(job.get('job_name','takeoff'))}_estimate_report.pdf",
        mime="application/pdf",
        type="secondary",
    )
    st.markdown("</div>", unsafe_allow_html=True)


def _cycle_plan_page(box_key: str, labels: List[str], delta: int) -> None:
    labels = list(labels)
    current = st.session_state.get(box_key)
    if current not in labels:
        current = labels[0]
    st.session_state[box_key] = labels[(labels.index(current) + delta) % len(labels)]


def corrections_page(job_id: str):
    job = load_job(job_id)
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("Verify & correct rooms on the plan")
    st.caption("Tap each room on the rendered plan to place a marker, give it a name and type its two dimensions in metres. Corrections override the auto-detected rooms and rebuild the take-off, so the app learns from your review as you go.")
    options = _correction_options(job)
    if not options:
        st.warning("No converted plan pages yet. Upload a PDF and tick 'Convert PDF pages to PNG' first.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    labels = [option["label"] for option in options]
    box_key = f"pr_plan_page_{job_id}"
    if box_key not in st.session_state or st.session_state.get(box_key) not in labels:
        st.session_state[box_key] = labels[0]
    nav = st.columns([1, 6, 1])
    nav[0].button(
        "◀ Prev",
        key=f"{box_key}_prev",
        width="stretch",
        on_click=_cycle_plan_page,
        args=(box_key, labels, -1),
    )
    selected = nav[1].selectbox("Plan page", labels, key=box_key, label_visibility="collapsed")
    nav[2].button(
        "Next ▶",
        key=f"{box_key}_next",
        width="stretch",
        on_click=_cycle_plan_page,
        args=(box_key, labels, 1),
    )
    st.caption(f"Plan page {labels.index(selected) + 1} of {len(labels)}")
    option = next((o for o in options if o["label"] == selected), options[0])
    image_path = Path(option["image_path"])
    if not image_path.exists():
        st.warning("Rendered image is missing. Re-upload the plan to regenerate it.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    file_name = option["file"]
    page_no = option["page"]
    saved_markers = [
        marker for marker in load_corrections(job_id)
        if marker.get("file") == file_name and int(marker.get("page") or 1) == page_no
    ]
    hints = [
        room for room in job.get("rooms", [])
        if str(room.get("file") or "") == file_name and int(room.get("page") or 0) == page_no
    ]
    initial = [
        {key: marker.get(key) for key in ("label", "x", "y", "dim1_m", "dim2_m")}
        for marker in saved_markers
    ]
    returned = plan_marker_editor(
        image_path.read_bytes(),
        markers=initial,
        hints=hints,
        key=f"pr_markers_{job_id}_{safe_name(file_name)}_{page_no}",
        height=780,
    )
    if returned:
        tagged = []
        for marker in returned:
            tagged.append(dict(marker))
            tagged[-1]["file"] = file_name
            tagged[-1]["page"] = page_no
        save_corrections(job_id, tagged)
        all_markers = load_corrections(job_id)
        job["rooms"] = apply_room_corrections(job.get("rooms", []), all_markers)
        job["takeoff_rows"] = _rebuild_takeoff(job, job["rooms"]).to_dict("records")
        save_job(job_id, job)
        st.success("Corrections saved. Rooms and take-off rows updated.")
    st.caption(f"Saved corrections for this page: {len(saved_markers)}. Every edit is saved automatically when you tap Save markers.")
    st.markdown("</div>", unsafe_allow_html=True)


def progress_page(job_id: str):
    job = load_job(job_id)
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("Elevation progress tracker")
    st.caption("Drag a box onto the elevation to mark a painted/unpainted area, tag it with a substrate, set its progress %, and optionally add an m² quantity that flows into the take-off. Positions are stored as exact percentages and drive the progress board.")
    options = elevation_image_options(job)
    if not options:
        st.warning("No elevation drawing images yet. Upload PDF plans and tick 'Convert PDF pages to PNG' (elevation pages are rendered automatically), or upload elevation images directly.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    labels = [o["label"] for o in options]
    box_key = f"pr_elevation_{job_id}"
    if box_key not in st.session_state or st.session_state.get(box_key) not in labels:
        st.session_state[box_key] = labels[0]
    nav = st.columns([1, 6, 1])
    nav[0].button(
        "◀ Prev",
        key=f"{box_key}_prev",
        width="stretch",
        on_click=_cycle_plan_page,
        args=(box_key, labels, -1),
    )
    selected = nav[1].selectbox("Elevation", labels, key=box_key, label_visibility="collapsed")
    nav[2].button(
        "Next ▶",
        key=f"{box_key}_next",
        width="stretch",
        on_click=_cycle_plan_page,
        args=(box_key, labels, 1),
    )
    st.caption(f"Elevation {labels.index(selected) + 1} of {len(labels)}")
    opt = next((o for o in options if o["label"] == selected), options[0])
    img_path = opt["image_path"]
    img_file = Path(img_path)
    if not img_file.exists():
        st.warning("Elevation image is missing. Re-upload the plan to regenerate it.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    state = job.get("elevation_progress", {}).get(img_path, {}) or {}
    stored = substrate_boxes_from_job(job, img_path)
    stored_cal = normalise_calibration(state.get("calibration"))
    overall = float(state.get("progress", 0.0))
    rev_key = f"pr_box_rev_{job_id}_{safe_name(img_path)}"
    rev = int(st.session_state.get(rev_key, 0))
    size = _image_pixel_size(img_path)
    img_w = size[0] if size else None
    img_h = size[1] if size else None

    auto_cal = None
    auto_scale_note = ""
    if stored_cal is None and opt.get("file") and opt.get("page") and img_w:
        auto = plan_auto_scale(job, opt["file"], opt["page"], dpi=opt.get("render_dpi") or 150)
        if auto and auto["m_per_px"]:
            auto_cal = {
                "x1": 0.0, "y1": 0.0, "x2": 100.0, "y2": 0.0,
                "len_m": round(img_w * auto["m_per_px"], 4),
            }
            auto_scale_note = (f"Scale auto-detected from the PDF ({auto.get('source') or 'vector scale'}) — "
                               f"areas are measured automatically.")

    returned = substrate_box_editor(
        img_file.read_bytes(),
        boxes=stored,
        substrates=SUBSTRATE_OPTIONS,
        calibration=stored_cal or auto_cal,
        revision=rev,
        key=f"pr_boxes_{job_id}_{safe_name(img_path)}",
        height=860,
    )
    current = stored
    cal = stored_cal
    auto_cal_n = normalise_calibration(auto_cal)
    if returned is not None:
        payload = returned if isinstance(returned, dict) else {}
        next_boxes = normalise_boxes(payload.get("boxes"))
        next_cal = normalise_calibration(payload.get("calibration"))
        if next_cal == auto_cal_n:
            next_cal = None
        if next_boxes != stored or next_cal != stored_cal:
            current = next_boxes
            cal = next_cal
            job.setdefault("elevation_progress", {})
            job["elevation_progress"][img_path] = {
                "progress": normalise_progress(overall),
                "zones": current,
                "calibration": cal,
                "updated_at": now_stamp(),
            }
            job["takeoff_rows"] = merge_elevation_box_rows(job, job.get("takeoff_rows", []))
            save_job(job_id, job)
            st.session_state[rev_key] = rev + 1

    mpp = calibration_mpp(cal or auto_cal, img_w, img_h) if (cal or auto_cal) else None
    m2_total = sum(effective_box_m2(b, mpp, img_w, img_h) for b in current)
    st.caption(f"**{len(current)} box(es)** drawn · **{m2_total:g} m²** in the take-off (only boxes with an m² value flow in).")

    if cal:
        st.caption(f"Scale calibrated — {cal['len_m']:g} m reference line, so box areas are **measured from the drawing** automatically.")
        if st.button("Clear calibration", type="secondary"):
            job.setdefault("elevation_progress", {})
            job["elevation_progress"][img_path] = {
                "progress": normalise_progress(overall),
                "zones": current,
                "updated_at": now_stamp(),
            }
            save_job(job_id, job)
            st.session_state[rev_key] = rev + 1
            st.rerun()
    elif auto_cal:
        st.caption(f"{auto_scale_note} Draw a box and its m² will be measured without manual calibration. Use **Calibrate scale** to override with a drawn reference line.")
    else:
        st.caption("Not calibrated — draw a box, then use **Calibrate scale** above to set the drawing scale so m² are measured from the drawing (or type an m² manually).")

    c1, c2, c3 = st.columns(3)
    overall_slider = c1.slider("Overall elevation progress %", 0, 100, int(overall), step=1, key=f"pr_overall_{job_id}")

    def _persist(boxes: List[Dict[str, Any]]) -> None:
        job.setdefault("elevation_progress", {})
        job["elevation_progress"][img_path] = {
            "progress": normalise_progress(overall_slider),
            "zones": boxes,
            "calibration": cal,
            "updated_at": now_stamp(),
        }
        save_job(job_id, job)

    if c2.button("Set all boxes to overall %", type="secondary"):
        boxes = [dict(b, progress=normalise_progress(overall_slider)) for b in current]
        _persist(boxes)
        job["takeoff_rows"] = merge_elevation_box_rows(job, job.get("takeoff_rows", []))
        save_job(job_id, job)
        st.session_state[rev_key] = int(st.session_state.get(rev_key, 0)) + 1
        st.rerun()
    if c3.button("Save progress state", type="secondary"):
        _persist(current)
        st.success("Elevation progress saved.")

    st.markdown("#### Renders")
    render_dir = job_dir(job_id) / "rendered_progress"
    overlay_path = render_dir / f"{safe_name(Path(img_path).stem, 'elevation')}_overlay.png"
    if st.button("Render elevation overlay", type="primary"):
        try:
            render_elevation_overlay(img_path, current, str(overlay_path), overall_slider)
            job.setdefault("elevation_progress", {})[img_path] = {
                "progress": normalise_progress(overall_slider),
                "zones": current,
                "calibration": cal,
                "updated_at": now_stamp(),
            }
            save_job(job_id, job)
            st.success(f"Overlay rendered to {overlay_path.name}")
        except ImportError as e:
            st.error(str(e))
    if overlay_path.exists():
        st.image(str(overlay_path), caption=overlay_path.name, width="stretch")
        file_download_button(overlay_path, "Download overlay PNG")

    board_path = render_dir / "elevation_board.png"
    if st.button("Build elevation progress board", type="secondary"):
        progress_state = job.get("elevation_progress", {})
        entries = []
        for o in options:
            entry = progress_state.get(o["image_path"], {})
            entries.append((o["image_path"], o["label"], entry.get("progress", 0)))
        try:
            board = build_elevation_board(entries, str(board_path))
            if board:
                job["elevation_board"] = str(board)
                save_job(job_id, job)
                st.success("Elevation progress board built.")
            else:
                st.warning("No elevation images could be opened to build the board.")
        except ImportError as e:
            st.error(str(e))
    if board_path.exists():
        st.image(str(board_path), caption=board_path.name, width="stretch")
        file_download_button(board_path, "Download board PNG")
    st.markdown("</div>", unsafe_allow_html=True)


def _delete_plan_page(job_id: str, job: Dict[str, Any], img_path) -> Dict[str, Any]:
    """Remove a converted plan/elevation page and everything that references it."""
    img_path = str(img_path)
    name = Path(img_path).name
    job["files"] = [
        f for f in job.get("files", [])
        if str(f.get("path") or "") != img_path and str(f.get("name") or "") != name
    ]
    kept_analyses: List[Dict[str, Any]] = []
    for a in job.get("analyses", []):
        pages = [p for p in a.get("pages", []) if str(p.get("image_path") or "") != img_path]
        a["pages"] = pages
        if pages:
            kept_analyses.append(a)
    job["analyses"] = kept_analyses
    job["rooms"] = [r for r in job.get("rooms", []) if str(r.get("file") or "") != name]
    job["elevation_progress"] = {
        k: v for k, v in (job.get("elevation_progress") or {}).items()
        if str(k) != img_path and Path(str(k)).name != name
    }
    markers = [m for m in load_corrections(job_id) if str(m.get("file") or "") != name]
    save_corrections(job_id, markers)
    p = Path(img_path)
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass
    save_job(job_id, job)
    return job


def images_page(job_id: str):
    job = load_job(job_id)
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("Converted plan/elevation images")
    st.caption("Previews of every converted page. Deleting a page removes it from this job, the 3D studio, rooms, corrections and elevation progress.")
    imgs = [Path(f.get("path", "")) for f in job.get("files", []) if f.get("category") in ["Converted drawing image", "Drawing image"]]
    imgs = [p for p in imgs if p.exists()]
    if not imgs:
        st.warning("No converted drawing images yet. Upload PDFs and tick 'Convert PDF pages to PNG'.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    by_path = {str(p): p for p in imgs}
    meta: Dict[str, Dict[str, Any]] = {}
    for a in job.get("analyses", []):
        for pg in a.get("pages", []):
            ip = str(pg.get("image_path") or "")
            if ip in by_path:
                meta[ip] = {
                    "page": pg.get("page"),
                    "page_type": pg.get("page_type"),
                    "title": pg.get("title"),
                }
    allow_delete = st.checkbox("Enable page deletion", value=False, key="cv_enable_delete")
    st.caption(f"{len(imgs)} page(s) shown (preview of every page).")
    cols = st.columns(3)
    for i, img_path in enumerate(imgs[:240]):
        with cols[i % 3]:
            st.image(str(img_path), width="stretch")
            m = meta.get(str(img_path), {})
            cap = img_path.name
            if m.get("page_type") or m.get("title"):
                cap = f"{cap} · {m.get('page_type') or m.get('title')}"
            st.caption(cap)
            file_download_button(img_path, "Download image")
            if allow_delete:
                if st.button("Delete page", key=f"cv_del_{i}_{img_path.name}", type="secondary"):
                    _delete_plan_page(job_id, job, img_path)
                    st.success(f"Deleted {img_path.name}")
                    st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def files_page(job_id: str):
    job = load_job(job_id)
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("File manager")
    files = job.get("files", [])
    if not files:
        st.info("No files uploaded.")
        st.markdown("</div>", unsafe_allow_html=True)
        return
    df = pd.DataFrame(files)
    st.dataframe(df[[c for c in ["name", "category", "file_type", "size_kb", "uploaded_at", "path"] if c in df.columns]], width="stretch", height=340)
    st.markdown("### Remove files attached to the wrong job")
    choices = {f"{i+1}. {f.get('name')} - {f.get('category')}": i for i, f in enumerate(files)}
    selected = st.multiselect("Select files to remove from this job", list(choices.keys()))
    delete_physical = st.checkbox("Also delete physical file from storage", value=False)
    if st.button("Remove selected files", disabled=not selected):
        remove_idx = {choices[x] for x in selected}
        kept = []
        for i, rec in enumerate(files):
            if i in remove_idx:
                p = Path(rec.get("path", ""))
                if delete_physical and p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
            else:
                kept.append(rec)
        job["files"] = kept
        save_job(job_id, job)
        st.success("Selected file records removed.")
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def export_page(job_id: str):
    job = load_job(job_id)
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("Export plan import pack")
    pages, snippets, areas = [], [], []
    for a in job.get("analyses", []):
        pages.extend(a.get("pages", []))
        snippets.extend(a.get("painting_snippets", []))
        areas.extend(a.get("area_candidates", []))
    sheets = {
        "Job Details": pd.DataFrame([job]),
        "Files": pd.DataFrame(job.get("files", [])),
        "Drawing Pages": pd.DataFrame(pages),
        "Painting Lines Found": pd.DataFrame(snippets),
        "Quantities Found": pd.DataFrame(areas),
        "Rooms Detected": pd.DataFrame(job.get("rooms", [])),
        "Elevation Progress": pd.DataFrame([
            {
                "image_path": img_path,
                "progress": entry.get("progress", 0),
                "zones": json.dumps(entry.get("zones", [])),
                "calibration": json.dumps(entry.get("calibration") or {}),
                "updated_at": entry.get("updated_at", ""),
            }
            for img_path, entry in (job.get("elevation_progress", {}) or {}).items()
        ]),
        "Takeoff Draft": pd.DataFrame(job.get("takeoff_rows", [])),
    }
    excel = df_to_excel_bytes({k: v for k, v in sheets.items() if not v.empty})
    st.download_button("Download complete Excel import pack", excel, file_name=f"{safe_name(job.get('job_name','planreader'))}_planreader_export.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.markdown("</div>", unsafe_allow_html=True)


def settings_page(job_id: str):
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("Storage / reset")
    st.write(f"Data folder: `{DATA_DIR}`")
    st.write(f"Current job folder: `{job_dir(job_id)}`")
    if st.checkbox("Show danger controls"):
        if st.button("Delete this job and all imported files", type="secondary"):
            shutil.rmtree(job_dir(job_id), ignore_errors=True)
            st.session_state.pop("selected_job_id", None)
            st.success("Job deleted.")
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


def _studio_project_label(job: Dict[str, Any]) -> str:
    parts = [str(job.get("job_no") or "").strip()]
    name = str(job.get("name") or "").strip()
    if name:
        parts.append(name)
    if not any(parts):
        parts = ["Unnamed project"]
    return " – ".join(p for p in parts if p)


def _blank_elevation_data_url(face_w_m: float, face_h_m: float) -> Optional[str]:
    """Synthesise a blank, plan-scaled elevation canvas (no photo required)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    if face_w_m <= 0 or face_h_m <= 0:
        return None
    scale = 900 / max(face_w_m, 1.0)
    img_w = max(400, int(round(face_w_m * scale)))
    img_h = max(240, int(round(face_h_m * scale)))
    image = Image.new("RGB", (img_w, img_h), (238, 240, 244))
    draw = ImageDraw.Draw(image)
    ground = int(img_h * 0.97)
    draw.line([(0, ground), (img_w, ground)], fill=(176, 182, 190), width=3)
    draw.rectangle([1, 1, img_w - 1, img_h - 1], outline=(120, 128, 140), width=2)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _studio_elevation_entry(
    face: str,
    data_url: str,
    img_w: int,
    img_h: int,
    mpp: float,
    zones: List[Dict[str, Any]],
    w_m: Optional[float] = None,
    h_m: Optional[float] = None,
) -> Dict[str, Any]:
    return {
        "key": face,
        "label": ELEVATION_FACE_LABELS.get(face, face.title()),
        "dataUrl": data_url,
        "m_per_px": round(float(mpp or 0), 6),
        "w_px": int(img_w or 0),
        "h_px": int(img_h or 0),
        "w_m": round(float(w_m), 2) if w_m else None,
        "h_m": round(float(h_m), 2) if h_m else None,
        "zones": zones or [],
    }


def _studio_elevations(
    job: Dict[str, Any],
    envelope: Dict[str, Any],
    wall_height: Optional[float] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set = set()

    def _face_w(face: str) -> Optional[float]:
        return _to_float(
            envelope.get("envelope_w_m")
            if face in ("front", "rear")
            else envelope.get("envelope_h_m")
        )

    def _push(face, img_file, img_w, img_h, mpp, zones, w_m=None, h_m=None):
        data_url = _img_data_url(img_file)
        if not data_url:
            return
        seen.add(face)
        out.append(
            _studio_elevation_entry(
                face, data_url, img_w or 0, img_h or 0, mpp, zones,
                w_m=w_m if w_m is not None else mpp * (img_w or 0),
                h_m=h_m if h_m is not None else mpp * (img_h or 0),
            )
        )

    # 1) user-calibrated elevations (Progress Tracking page)
    for img_path, entry in (job.get("elevation_progress") or {}).items():
        face = _face_key(img_path)
        if not face or face in seen:
            continue
        img_file = Path(img_path)
        if not img_file.exists():
            continue
        size = _image_pixel_size(img_file)
        img_w = size[0] if size else None
        img_h = size[1] if size else None
        mpp = None
        if img_w:
            cal = normalise_calibration(entry.get("calibration"))
            if cal:
                mpp = calibration_mpp(cal, img_w, img_h or img_w)
            if not mpp:
                fw = _face_w(face)
                if fw:
                    mpp = round(fw / img_w, 6)
        if not mpp:
            mpp = 0.05
        zones = []
        for z in substrate_boxes_from_job(job, img_path):
            code = _substrate_for_substrate_text(z.get("substrate")) or _substrate_for_substrate_text(z.get("label"))
            zones.append({
                "x": z.get("x", 0),
                "y": z.get("y", 0),
                "w": z.get("w", 0),
                "h": z.get("h", 0),
                "substrate": code or "RBL",
            })
        _push(face, img_file, img_w, img_h, mpp, zones)

    # 2) auto-discover elevation drawings from the imported plan PDFs
    for analysis in job.get("analyses") or []:
        fname = str(analysis.get("file") or "")
        for page in analysis.get("pages") or []:
            img_path = page.get("image_path")
            if not img_path:
                continue
            face = _face_key(str(img_path)) or _face_key(str(page.get("title") or "")) or _face_key(fname)
            if not face or face in seen:
                continue
            ptype = str(page.get("page_type") or "")
            title = str(page.get("title") or "")
            if ptype != "elevation" and "elev" not in title.lower() and "elev" not in fname.lower():
                continue
            img_file = Path(img_path)
            if not img_file.exists():
                continue
            size = _image_pixel_size(img_file)
            img_w = size[0] if size else None
            img_h = size[1] if size else None
            mpp = None
            auto = plan_auto_scale(job, fname, page.get("page") or 1, dpi=page.get("render_dpi") or 150)
            if auto and auto.get("m_per_px"):
                mpp = auto["m_per_px"]
            elif img_w:
                fw = _face_w(face)
                if fw:
                    mpp = round(fw / img_w, 6)
            if not mpp:
                mpp = 0.05
            _push(face, img_file, img_w, img_h, mpp, [])

    # 3) elevation drawings uploaded as files
    for f in job.get("files") or []:
        name = str(f.get("name") or "")
        img_path = f.get("path") or name
        face = _face_key(str(img_path)) or _face_key(name)
        if not face or face in seen:
            continue
        category = str(f.get("category") or "")
        if category not in ("Elevation", "Drawing image") and "elev" not in name.lower():
            continue
        img_file = Path(str(img_path))
        if not img_file.exists():
            continue
        size = _image_pixel_size(img_file)
        img_w = size[0] if size else None
        img_h = size[1] if size else None
        mpp = None
        if img_w:
            fw = _face_w(face)
            if fw:
                mpp = round(fw / img_w, 6)
        if not mpp:
            mpp = 0.05
        _push(face, img_file, img_w, img_h, mpp, [])

    # 4) no elevation drawings at all: synthesise faces from the measured plan
    if not out:
        ew = _face_w("front") or 30.0
        ed = _face_w("left") or 9.0
        eh = _to_float(wall_height) or _to_float((job.get("external_settings") or {}).get("wall_height_m")) or 2.7
        for face, fw, fh in (("front", ew, eh), ("rear", ew, eh), ("left", ed, eh), ("right", ed, eh)):
            if face in seen:
                continue
            data_url = _blank_elevation_data_url(fw, fh)
            if not data_url:
                continue
            scale = 900 / max(fw, 1.0)
            img_w = max(400, int(round(fw * scale)))
            img_h = max(240, int(round(fh * scale)))
            mpp = round(fw / img_w, 6)
            seen.add(face)
            out.append(
                _studio_elevation_entry(face, data_url, img_w, img_h, mpp, [], w_m=fw, h_m=fh)
            )
    return out


def _studio_drawings(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    names: List[str] = []
    for img_path in (job.get("elevation_progress") or {}):
        name = Path(img_path).name
        if name and name not in names:
            names.append(name)
    for f in (job.get("files") or []):
        name = str(f.get("name") or "") or Path(str(f.get("path") or "")).name
        if name and name not in names:
            names.append(name)
    return [{"name": n} for n in names] or [{"name": "Elevations – Block B"}]


def _studio_seed_areas(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    areas: List[Dict[str, Any]] = []
    try:
        rows, _ = compute_external_takeoff_rows(job)
    except Exception:
        rows = []
    for r in rows:
        if r.get("internal_external") != "External":
            continue
        code = _substrate_for_substrate_text(r.get("substrate"))
        if not code:
            continue
        qty = _to_float(r.get("qty_m2"))
        if qty <= 0 and not _to_float(r.get("lineal_m")):
            continue
        areas.append({
            "id": "SEED-%02d" % (len(areas) + 1),
            "unit": None,
            "unit_label": "Whole building",
            "drawing": "Elevations – Block B",
            "elevation": ELEVATION_FACE_LABELS["front"],
            "face": "front",
            "substrate": code,
            "area": round(qty, 2),
            "status": "Paint Included",
            "progress": 0,
            "notes": r.get("source_note") or r.get("confidence") or "",
            "manual": False,
        })
    return areas


def three_d_render_page(job_id):
    """PB PlanRender Takeoff Studio — premium dark take-off workspace."""
    job = load_job(job_id)
    markers = load_corrections(job_id)
    rooms = apply_room_corrections(job.get("rooms", []), markers)
    wall_thickness = float(
        (job.get("external_settings") or {}).get("wall_thickness_m")
        or DEFAULT_WALL_THICKNESS_M
    )
    envelope = external_footprint(job, markers, rooms, wall_thickness)
    external_info = {}
    try:
        _, external_info = compute_external_takeoff_rows(job)
    except Exception:
        external_info = {}
    project_label = _studio_project_label(job)
    studio_data = build_studio_data(
        job=job,
        project_label=project_label,
        project_id=job_id,
        envelope=envelope,
        external_info=external_info,
        elevations=_studio_elevations(
            job,
            envelope,
            wall_height=(job.get("external_settings") or {}).get("wall_height_m"),
        ),
        drawings=_studio_drawings(job),
        seed_areas=_studio_seed_areas(job),
    )
    st.markdown("### PB PlanRender Takeoff Studio")
    st.caption(
        f"Project: **{project_label}** · the 3D model is scaled to the job's "
        f"external envelope ({envelope.get('envelope_w_m', 0):g} × "
        f"{envelope.get('envelope_h_m', 0):g} m) so drawn measurements match "
        f"the plan. Measurements auto-save in this browser and export to CSV."
    )
    studio_html = render_planrender_studio_html(studio_data)
    try:
        st.iframe(studio_html, height=1000)
    except (AttributeError, TypeError):
        st.components.v1.html(studio_html, height=1000, scrolling=False)


def main():
    st.set_page_config(page_title=APP_NAME, page_icon="🎨", layout="wide")
    app_css()
    render_logo()
    st.sidebar.title("PB PlanReader")
    st.sidebar.caption("Clean plan import + painting take-off package")
    try:
        commit = str(os.getenv("RENDER_GIT_COMMIT", "") or "").strip()
        st.sidebar.caption(f"Build {commit[:8] if commit else 'dev'} · PlanReader")
    except Exception:
        pass
    job_id = create_or_select_job()
    if not job_id:
        st.info("Create a job in the sidebar to start.")
        return
    menu = st.sidebar.radio(
        "Menu",
        [
            "Upload Plans",
            "Extracted Info",
            "Verify & Correct",
            "Colour Schedule",
            "3D Render",
            "JobHub Sync",
            "Take-off Draft",
            "Progress Tracking",
            "Converted Images",
            "File Manager",
            "Export",
            "Settings",
        ],
    )
    if menu == "Upload Plans":
        process_uploads_page(job_id)
        overview_page(job_id)
    elif menu == "Extracted Info":
        overview_page(job_id)
    elif menu == "Verify & Correct":
        corrections_page(job_id)
    elif menu == "Colour Schedule":
        colour_schedule_page(job_id)
    elif menu == "3D Render":
        three_d_render_page(job_id)
    elif menu == "JobHub Sync":
        jobhub_sync_page(job_id)
    elif menu == "Take-off Draft":
        takeoff_page(job_id)
    elif menu == "Progress Tracking":
        progress_page(job_id)
    elif menu == "Converted Images":
        images_page(job_id)
    elif menu == "File Manager":
        files_page(job_id)
    elif menu == "Export":
        export_page(job_id)
    elif menu == "Settings":
        settings_page(job_id)


if __name__ == "__main__":
    main()
