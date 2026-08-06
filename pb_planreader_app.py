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
    return pd.DataFrame(merge_elevation_box_rows(job, df.to_dict("records")))


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
    c_h, c_o, c_cov = st.columns(3)
    ceiling_height = c_h.number_input("Ceiling height (m)", min_value=2.1, max_value=4.5, value=DEFAULT_CEILING_HEIGHT_M, step=0.1, key=f"pr_ceiling_{job_id}")
    opening_allowance = c_o.number_input("Door/window opening allowance per room (m²)", min_value=0.0, max_value=20.0, value=DEFAULT_OPENINGS_ALLOWANCE_M2, step=0.5, key=f"pr_openings_{job_id}")
    coverage = c_cov.number_input("Paint coverage (m²/L)", min_value=6.0, max_value=20.0, value=DEFAULT_COVERAGE_M2_PER_L, step=0.5, key=f"pr_coverage_{job_id}")
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
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Recalculate paint litres", type="secondary"):
            edited["paint_litres"] = edited.apply(lambda r: litres_from_area(r.get("qty_m2", 0), r.get("coats", 2), coverage), axis=1)
            job["takeoff_rows"] = edited.to_dict("records")
            save_job(job_id, job)
            st.success("Paint litres recalculated.")
            st.rerun()
    with col2:
        if st.button("Save take-off", type="primary"):
            job["takeoff_rows"] = edited.to_dict("records")
            save_job(job_id, job)
            st.success("Take-off saved.")
    with col3:
        edited["value_ex_gst"] = pd.to_numeric(edited["qty_m2"], errors="coerce").fillna(0) * pd.to_numeric(edited["rate_ex_gst"], errors="coerce").fillna(0)
        excel = df_to_excel_bytes({"Takeoff": edited, "Files": pd.DataFrame(job.get("files", []))})
        st.download_button("Download Excel", excel, file_name=f"{safe_name(job.get('job_name','takeoff'))}_takeoff.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    edited["value_ex_gst"] = pd.to_numeric(edited["qty_m2"], errors="coerce").fillna(0) * pd.to_numeric(edited["rate_ex_gst"], errors="coerce").fillna(0)
    st.markdown("### Totals")
    a, b, c, d = st.columns(4)
    a.metric("Internal m²", round(edited.loc[edited["internal_external"].astype(str).str.lower().str.contains("internal"), "qty_m2"].sum(), 2))
    b.metric("External m²", round(edited.loc[edited["internal_external"].astype(str).str.lower().str.contains("external"), "qty_m2"].sum(), 2))
    c.metric("Paint litres", round(pd.to_numeric(edited["paint_litres"], errors="coerce").fillna(0).sum(), 2))
    d.metric("Value ex GST", money(edited["value_ex_gst"].sum()))
    st.markdown("</div>", unsafe_allow_html=True)


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
    selected = st.selectbox("Plan page", labels, index=0)
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
    selected = st.selectbox("Elevation", labels, index=0)
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

    returned = substrate_box_editor(
        img_file.read_bytes(),
        boxes=stored,
        substrates=SUBSTRATE_OPTIONS,
        calibration=stored_cal,
        revision=rev,
        key=f"pr_boxes_{job_id}_{safe_name(img_path)}",
        height=860,
    )
    current = stored
    cal = stored_cal
    if returned is not None:
        payload = returned if isinstance(returned, dict) else {}
        next_boxes = normalise_boxes(payload.get("boxes"))
        next_cal = normalise_calibration(payload.get("calibration"))
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

    size = _image_pixel_size(img_path)
    img_w = size[0] if size else None
    img_h = size[1] if size else None
    mpp = calibration_mpp(cal, img_w, img_h) if cal else None
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


def images_page(job_id: str):
    job = load_job(job_id)
    st.markdown("<div class='pb-card'>", unsafe_allow_html=True)
    st.subheader("Converted plan/elevation images")
    st.caption("These are rendered directly from the PDF pages and can be used as clean backgrounds for the mapper.")
    imgs = [Path(f.get("path", "")) for f in job.get("files", []) if f.get("category") in ["Converted drawing image", "Drawing image"]]
    imgs = [p for p in imgs if p.exists()]
    if not imgs:
        st.warning("No converted drawing images yet. Upload PDFs and tick 'Convert PDF pages to PNG'.")
    else:
        cols = st.columns(3)
        for i, img_path in enumerate(imgs[:120]):
            with cols[i % 3]:
                st.image(str(img_path), caption=img_path.name, width="stretch")
                file_download_button(img_path, "Download image")
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
