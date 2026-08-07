"""PB PlanRender Takeoff Studio.

A premium, dark-themed painting take-off application rendered as a single
self-contained HTML page (Three.js viewport + measurement workflow). The
module is standalone: it only builds a JSON payload and an HTML string, so
PlanReader can load it without any JobHub guard cascade.

The studio ships:

* a dark charcoal CAD-style interface (blue action / green completed /
  orange remaining / red destructive)
* a top navigation bar (3D Model / Elevations / Reports / Export + help and
  settings), a project control bar and a left sidebar with the substrate
  legend, measurement tools, view options and saved model views
* a central photorealistic 3D model of a row of five attached three-storey
  townhouses (scaled to the job's external envelope so measured square
  metres match the plan)
* surface selection with substrate classification, click-and-drag Draw Box
  measurement with blue dashed outlines and floating area labels, a
  right-hand Selected Area editor, progress/completed-remaining tracking, a
  bottom summary bar, CSV / image / JSON export, undo-redo, audit history
  and automatic local saving
"""

from __future__ import annotations

import csv
import io
import json
import math
from typing import Any, Dict, List, Optional

STUDIO_VERSION = "v1.0"

SUBSTRATES: List[Dict[str, str]] = [
    {"code": "EC1", "name": "Lineaboard Cladding", "hex": "#C9B89A"},
    {"code": "EC2", "name": "Textureboard Cladding", "hex": "#B7A98E"},
    {"code": "EC3", "name": "Easylap Cladding", "hex": "#A99B83"},
    {"code": "RBL", "name": "Rendered Block", "hex": "#D9CFC0"},
    {"code": "SOF", "name": "Soffits / Eaves", "hex": "#E8E3D8"},
    {"code": "EC5", "name": "Timber Look Cladding", "hex": "#9C6B3F"},
    {"code": "BA2 / SCR", "name": "Aluminium Screens", "hex": "#6B7280"},
    {"code": "BA1", "name": "Glass Balustrade", "hex": "#93C5FD"},
    {"code": "SHD", "name": "Sunhoods", "hex": "#4B5563"},
    {"code": "BC / EG / PPT", "name": "Cappings and Gutters", "hex": "#64748B"},
    {"code": "RS", "name": "Roof Sheet", "hex": "#5B6570"},
    {"code": "DP", "name": "Downpipes", "hex": "#374151"},
    {"code": "GD", "name": "Garage Doors", "hex": "#334155"},
]
SUBSTRATE_CODE_TO_HEX = {s["code"]: s["hex"] for s in SUBSTRATES}

STATUSES = [
    "Paint Included",
    "Paint Excluded",
    "Provisional",
    "Variation",
    "Completed",
    "Not Started",
    "Requires Site Verification",
]

ELEVATION_FACE_LABELS = {
    "front": "Front – King Street",
    "rear": "Rear – Hamilton Street",
    "left": "Left – North",
    "right": "Right – South",
}

UNIT_COUNT = 5
CSV_COLUMNS = [
    "Area ID",
    "Building or unit",
    "Drawing",
    "Elevation",
    "Substrate",
    "Area",
    "Status",
    "Percentage completed",
    "Completed square metres",
    "Remaining square metres",
    "Notes",
]

STUDIO_TIP = (
    "Use Draw Box to create custom measurement areas. Include all soffits "
    "and eaves. Areas are approximate. Verify on site for final takeoff."
)


def _face_key(path: Any) -> Optional[str]:
    """Map an elevation image path to a building face."""
    value = str(path or "").lower()
    if "front" in value or "north" in value:
        return "front"
    if "rear" in value or "south" in value or "back" in value:
        return "rear"
    if "left" in value or "west" in value:
        return "left"
    if "right" in value or "east" in value:
        return "right"
    return None


def _substrate_for_substrate_text(substrate: Any) -> Optional[str]:
    s = str(substrate or "").lower()
    if "window" in s or "glazing" in s:
        return None
    if "soffit" in s or "eave" in s:
        return "SOF"
    if "render" in s or "block" in s or "wall" in s:
        return "RBL"
    if "fascia" in s or "gutter" in s or "trim" in s or "capping" in s or "cap" in s:
        return "BC / EG / PPT"
    if "roof" in s:
        return "RS"
    if "cladding" in s:
        return "EC1"
    if "screen" in s:
        return "BA2 / SCR"
    if "balustrade" in s or "glass" in s:
        return "BA1"
    if "sunhood" in s:
        return "SHD"
    if "garage" in s or "door" in s:
        return "GD"
    if "downpipe" in s:
        return "DP"
    return None


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def next_area_id(existing: List[Dict[str, Any]]) -> str:
    """Next free sequential area id (A-001, A-002, ...)."""
    used = set()
    for area in existing or []:
        used.add(str(area.get("id") or "").upper())
    index = 1
    while "A-%03d" % index in used:
        index += 1
    return "A-%03d" % index


def export_areas_csv(areas: List[Dict[str, Any]], drawing: str = "Drawing") -> str:
    """Build the standard take-off CSV (UTF-8, CRLF) from area records."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for area in areas or []:
        total = max(_f(area.get("area")), 0.0)
        progress = min(max(_f(area.get("progress")), 0.0), 100.0)
        completed = round(total * progress / 100.0, 2)
        remaining = round(max(total - completed, 0.0), 2)
        writer.writerow([
            area.get("id") or "",
            area.get("unit_label") or area.get("unit") or "",
            area.get("drawing") or drawing,
            area.get("elevation") or "",
            area.get("substrate") or "",
            f"{total:g}" if total else "0",
            area.get("status") or "Paint Included",
            f"{progress:g}",
            f"{completed:g}",
            f"{remaining:g}",
            (area.get("notes") or "").replace("\r", " ").replace("\n", " "),
        ])
    return buffer.getvalue()


def totals(areas: List[Dict[str, Any]]) -> Dict[str, float]:
    """Overall totals: total / completed / remaining square metres."""
    total = 0.0
    completed = 0.0
    for area in areas or []:
        if str(area.get("status")) == "Paint Excluded":
            continue
        value = max(_f(area.get("area")), 0.0)
        progress = min(max(_f(area.get("progress")), 0.0), 100.0)
        total += value
        completed += value * progress / 100.0
    return {
        "total": round(total, 2),
        "completed": round(completed, 2),
        "remaining": round(max(total - completed, 0.0), 2),
    }


def build_studio_data(
    job: Optional[Dict[str, Any]] = None,
    project_label: str = "23-060 – 122–126 King Street, Buderim",
    project_id: str = "sample-king-street",
    envelope: Optional[Dict[str, Any]] = None,
    external_info: Optional[Dict[str, Any]] = None,
    elevations: Optional[List[Dict[str, Any]]] = None,
    drawings: Optional[List[Dict[str, Any]]] = None,
    seed_areas: Optional[List[Dict[str, Any]]] = None,
    operator: str = "Estimator",
) -> Dict[str, Any]:
    """Assemble the full studio payload for the HTML app."""
    job = job or {}
    envelope = envelope or {}
    external_info = external_info or {}

    width = _f(envelope.get("envelope_w_m"))
    depth = _f(envelope.get("envelope_h_m"))
    if width <= 0 or depth <= 0:
        width = 30.0
        depth = 9.0
    wall_height = _f(external_info.get("wall_height_m"))
    if wall_height <= 0:
        wall_height = _f((job.get("external_settings") or {}).get("wall_height_m"))
    if wall_height <= 0:
        wall_height = 9.0

    elevations = elevations or []
    drawings = drawings or []
    seed_areas = seed_areas or []

    units = [{"label": "Unit %d" % (i + 1)} for i in range(UNIT_COUNT)]

    return {
        "appName": "PB PlanRender Takeoff Studio",
        "version": STUDIO_VERSION,
        "tip": STUDIO_TIP,
        "operator": operator,
        "project": {"id": project_id, "label": project_label},
        "projects": [{"id": project_id, "label": project_label}],
    "drawings": [{"name": (d.get("name") if isinstance(d, dict) else d)} for d in drawings]
    or [{"name": "Elevations – Block B"}],
        "viewOptions": [
            "3D Model",
            "Drawing",
            "Elevation Overlay",
            "Takeoff View",
            "Progress View",
        ],
        "envelope": {
            "w": round(width, 2),
            "d": round(depth, 2),
            "h": round(wall_height, 2),
            "method": str(envelope.get("method") or "none"),
            "note": str(envelope.get("note") or ""),
        },
        "units": units,
        "elevationLabels": ELEVATION_FACE_LABELS,
        "elevations": elevations,
        "areas": seed_areas,
        "substrates": SUBSTRATES,
        "statuses": STATUSES,
        "totals": totals(seed_areas),
    }


def _img_data_url(path: Any, max_width: int = 900, quality: int = 80) -> Optional[str]:
    """Downscale and base64-encode an image for embedding in the studio page."""
    try:
        from PIL import Image

        image = Image.open(str(path)).convert("RGB")
        if image.width > max_width:
            scale = max_width / float(image.width)
            image = image.resize(
                (max_width, max(1, int(image.height * scale))), Image.LANCZOS
            )
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality)
        return "data:image/jpeg;base64," + base64_encode(buffer.getvalue())
    except Exception:
        return None


def base64_encode(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode("ascii")


def _render_template() -> str:
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PB PlanRender Takeoff Studio</title>
<style>
  :root{
    --bg0:#0e1116; --bg1:#141920; --bg2:#1a2029; --panel:rgba(18,23,30,.92);
    --line:rgba(255,255,255,.08); --line-2:rgba(255,255,255,.14);
    --text:#d4dae3; --muted:#8b95a5; --blue:#3b82f6; --blue-hi:#2563eb;
    --green:#22c55e; --orange:#f97316; --red:#ef4444;
    --radius:6px; --font:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
  }
  *{box-sizing:border-box; margin:0; padding:0;}
  html,body{height:100%; background:var(--bg0); color:var(--text);
    font-family:var(--font); font-size:13px; overflow:hidden; -webkit-font-smoothing:antialiased;}
  button{font-family:var(--font);}
  .screen{position:fixed; top:0; left:0; right:0; bottom:0;}
  .hidden{display:none !important;}

  /* ---------- top navigation ---------- */
  #topbar{position:fixed; top:0; left:0; right:0; height:52px; z-index:30;
    background:linear-gradient(180deg,#161b23,#10141b); border-bottom:1px solid var(--line);
    display:flex; align-items:center; padding:0 14px; gap:10px;}
  .logo{display:flex; align-items:center; gap:10px; margin-right:6px;}
  .logo-mark{width:30px; height:30px; border-radius:8px; background:linear-gradient(135deg,#3b82f6,#7c3aed);
    display:flex; align-items:center; justify-content:center; color:#fff; font-weight:800; font-size:15px;}
  .logo-name{font-size:15px; font-weight:700; color:#eef2f7; letter-spacing:.2px; white-space:nowrap;}
  .logo-name small{color:var(--muted); font-weight:500; font-size:11px; display:block; line-height:1; margin-top:2px;}
  #nav{display:flex; gap:4px; margin-left:auto; align-items:center;}
  .nav-btn{background:transparent; color:var(--muted); border:1px solid transparent; border-radius:var(--radius);
    padding:7px 14px; cursor:pointer; font-size:13px; font-weight:600;}
  .nav-btn:hover{color:var(--text); background:var(--bg2);}
  .nav-btn.active{color:#fff; background:var(--blue-hi); border-color:var(--blue-hi);}
  .icon-btn{background:transparent; color:var(--muted); border:1px solid transparent; border-radius:var(--radius);
    padding:7px 9px; cursor:pointer; font-size:15px; line-height:1;}
  .icon-btn:hover{color:var(--text); background:var(--bg2);}

  /* ---------- project control bar ---------- */
  #projectbar{position:fixed; top:52px; left:0; right:0; height:46px; z-index:29;
    background:var(--bg1); border-bottom:1px solid var(--line);
    display:flex; align-items:center; gap:10px; padding:0 14px;}
  .field{display:flex; align-items:center; gap:6px;}
  .field label{color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.5px;}
  select{background:var(--bg2); color:var(--text); border:1px solid var(--line-2); border-radius:var(--radius);
    padding:5px 8px; font-size:13px; max-width:300px; outline:none;}
  select:focus{border-color:var(--blue);}

  /* ---------- layout ---------- */
  #viewport{position:fixed; top:98px; left:0; right:0; bottom:34px;}
  #footer{position:fixed; left:0; right:0; bottom:0; height:34px; z-index:25;
    background:var(--bg1); border-top:1px solid var(--line);
    display:flex; align-items:center; justify-content:space-between; padding:0 14px;
    font-size:11px; color:var(--muted);}

  /* ---------- left sidebar ---------- */
  #sidebar{position:fixed; top:108px; left:12px; bottom:44px; width:236px; z-index:20;
    background:var(--panel); border:1px solid var(--line); border-radius:10px;
    display:flex; flex-direction:column; overflow:hidden; box-shadow:0 10px 30px rgba(0,0,0,.35);}
  .sb-section{border-bottom:1px solid var(--line);}
  .sb-head{display:flex; align-items:center; justify-content:space-between; cursor:pointer;
    padding:9px 12px; font-size:11px; text-transform:uppercase; letter-spacing:.6px; color:var(--muted); font-weight:700;}
  .sb-head:hover{color:var(--text);}
  .sb-head .chev{transition:transform .15s;}
  .sb-section.closed .sb-body{display:none;}
  .sb-section.closed .chev{transform:rotate(-90deg);}
  .sb-body{padding:4px 10px 10px;}
  .swatch-row{display:flex; align-items:center; gap:8px; padding:3px 2px; cursor:pointer; border-radius:4px;}
  .swatch-row:hover{background:rgba(255,255,255,.05);}
  .swatch-row.active{background:rgba(59,130,246,.18); outline:1px solid rgba(59,130,246,.5);}
  .dot{width:13px; height:13px; border-radius:3px; border:1px solid rgba(255,255,255,.2); flex:0 0 auto;}
  .swatch-code{font-weight:700; font-size:11px; color:#eef2f7; min-width:66px;}
  .swatch-name{color:var(--muted); font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
  .tool-grid{display:grid; grid-template-columns:1fr 1fr; gap:6px;}
  .tool{background:var(--bg2); color:var(--text); border:1px solid var(--line-2); border-radius:var(--radius);
    padding:7px 4px; cursor:pointer; font-size:12px; text-align:center;}
  .tool:hover{border-color:var(--muted);}
  .tool.active{background:var(--blue-hi); border-color:var(--blue-hi); color:#fff;}
  .radio{display:flex; gap:6px;}
  .radio label{flex:1; text-align:center; background:var(--bg2); border:1px solid var(--line-2); border-radius:var(--radius);
    padding:6px 2px; cursor:pointer; font-size:12px;}
  .radio input{display:none;}
  .radio label.on{background:var(--blue-hi); border-color:var(--blue-hi); color:#fff;}
  .switch{display:flex; align-items:center; gap:8px; cursor:pointer; padding:6px 2px; font-size:12px;}
  .switch input{display:none;}
  .switch .track{width:34px; height:18px; border-radius:10px; background:var(--bg2); border:1px solid var(--line-2); position:relative; transition:background .15s;}
  .switch .track::after{content:''; position:absolute; top:2px; left:2px; width:12px; height:12px; border-radius:50%; background:var(--muted); transition:left .15s,background .15s;}
  .switch input:checked + .track{background:var(--blue-hi); border-color:var(--blue-hi);}
  .switch input:checked + .track::after{left:18px; background:#fff;}
  .view-grid{display:grid; grid-template-columns:1fr; gap:6px;}
  .view-btn{background:var(--bg2); border:1px solid var(--line-2); border-radius:var(--radius); color:var(--text);
    padding:6px 8px; cursor:pointer; font-size:12px; text-align:left;}
  .view-btn:hover{border-color:var(--muted);}
  .view-btn.active{background:rgba(59,130,246,.2); border-color:var(--blue); color:#fff;}

  /* ---------- right selected-area panel ---------- */
  #panel{position:fixed; top:108px; right:12px; bottom:44px; width:320px; z-index:20;
    background:var(--panel); border:1px solid var(--line); border-radius:10px; display:none;
    flex-direction:column; overflow:hidden; box-shadow:0 10px 30px rgba(0,0,0,.35);}
  #panel.open{display:flex;}
  .panel-head{display:flex; align-items:center; justify-content:space-between; padding:12px 14px;
    border-bottom:1px solid var(--line);}
  .panel-head h3{font-size:14px; color:#eef2f7;}
  .panel-head .close{background:transparent; border:0; color:var(--muted); cursor:pointer; font-size:17px; line-height:1;}
  .panel-head .close:hover{color:var(--red);}
  .panel-body{flex:1; overflow:auto; padding:12px 14px; display:flex; flex-direction:column; gap:11px;}
  .row label{display:block; font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:var(--muted); margin-bottom:4px;}
  .row input, .row select, .row textarea{width:100%; background:var(--bg2); color:var(--text);
    border:1px solid var(--line-2); border-radius:var(--radius); padding:6px 8px; font-size:13px; outline:none;}
  .row input:focus, .row select:focus, .row textarea:focus{border-color:var(--blue);}
  .row textarea{min-height:58px; resize:vertical;}
  .num-badge{display:flex; align-items:center; gap:6px;}
  .num-badge input{flex:1;}
  .num-badge .m2{color:var(--muted); font-size:12px;}
  .progress-readout{display:flex; justify-content:space-between; font-size:12px; padding-top:4px;}
  .progress-readout .done{color:var(--green); font-weight:600;}
  .progress-readout .left{color:var(--orange); font-weight:600;}
  .history{font-size:11px; color:var(--muted); border-top:1px dashed var(--line-2); padding-top:8px;}
  .history div{padding:2px 0;}
  .actions{display:flex; gap:8px; margin-top:2px;}
  .btn{border:0; border-radius:var(--radius); padding:8px 12px; font-size:13px; font-weight:600; cursor:pointer;}
  .btn.primary{background:var(--blue-hi); color:#fff;}
  .btn.primary:hover{background:#1d4ed8;}
  .btn.danger{background:rgba(239,68,68,.15); color:var(--red); border:1px solid rgba(239,68,68,.5);}
  .btn.danger:hover{background:var(--red); color:#fff;}
  .btn.ghost{background:transparent; color:var(--muted); border:1px solid var(--line-2);}
  .btn.ghost:hover{color:var(--text); border-color:var(--muted);}

  /* ---------- bottom bar ---------- */
  #bottombar{position:fixed; left:50%; transform:translateX(-50%); bottom:40px; z-index:22;
    background:var(--panel); border:1px solid var(--line-2); border-radius:10px;
    padding:9px 16px; display:flex; align-items:center; gap:22px; box-shadow:0 10px 26px rgba(0,0,0,.4);
    white-space:nowrap; max-width:90vw; overflow:hidden;}
  .mode{font-size:12px; color:var(--muted);}
  .mode b{color:var(--blue);}
  .mode .instr{display:block; color:var(--muted); font-size:11px; font-weight:400;}
  .total{font-size:12px;}
  .total b{font-weight:700;}
  .total .t{color:#fff;}
  .total .g{color:var(--green);}
  .total .o{color:var(--orange);}

  /* ---------- export controls ---------- */
  #exportbar{position:fixed; right:14px; bottom:42px; z-index:22; display:flex; gap:8px;}
  .exp-btn{background:var(--bg2); color:var(--text); border:1px solid var(--line-2); border-radius:var(--radius);
    padding:8px 14px; font-size:12px; font-weight:600; cursor:pointer;}
  .exp-btn:hover{border-color:var(--blue); color:#fff;}
  .exp-btn.csv{background:rgba(34,197,94,.15); border-color:rgba(34,197,94,.5); color:var(--green);}
  .exp-btn.csv:hover{background:var(--green); color:#0e1116;}
  .exp-btn.img{background:rgba(59,130,246,.15); border-color:rgba(59,130,246,.5); color:var(--blue);}
  .exp-btn.img:hover{background:var(--blue); color:#fff;}

  /* ---------- elevations screen ---------- */
  #elevations{position:fixed; top:98px; left:0; right:0; bottom:34px; overflow:auto; padding:16px; background:var(--bg0);}
  .elev-grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(420px,1fr)); gap:16px;}
  .elev-card{background:var(--bg1); border:1px solid var(--line); border-radius:10px; overflow:hidden;}
  .elev-card h4{padding:10px 12px; font-size:13px; color:#eef2f7; border-bottom:1px solid var(--line);}
  .elev-meta{padding:6px 12px; font-size:11px; color:var(--muted); border-bottom:1px solid var(--line);}
  .elev-canvas{width:100%; display:block; cursor:crosshair; background:#10151c; touch-action:none;}

  /* ---------- reports / export screens ---------- */
  #reports, #export{position:fixed; top:98px; left:0; right:0; bottom:34px; overflow:auto; padding:20px; background:var(--bg0);}
  .report-block{background:var(--bg1); border:1px solid var(--line); border-radius:10px; padding:16px; margin-bottom:16px; max-width:1100px;}
  .report-block h3{font-size:14px; color:#eef2f7; margin-bottom:10px;}
  table{border-collapse:collapse; width:100%; font-size:12px;}
  th,td{text-align:left; padding:6px 9px; border-bottom:1px solid var(--line);}
  th{color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.5px;}
  td.num{text-align:right; font-variant-numeric:tabular-nums;}
  .pill{display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px; font-weight:600;}
  .pill.g{background:rgba(34,197,94,.16); color:var(--green);}
  .pill.o{background:rgba(249,115,22,.16); color:var(--orange);}
  .pill.b{background:rgba(59,130,246,.16); color:var(--blue);}
  .kpi-row{display:flex; gap:14px; flex-wrap:wrap; margin-bottom:14px;}
  .kpi{background:var(--bg1); border:1px solid var(--line); border-radius:10px; padding:14px 18px; min-width:160px;}
  .kpi .v{font-size:22px; font-weight:800;}
  .kpi .l{font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.6px;}
  .kpi .v.g{color:var(--green);} .kpi .v.o{color:var(--orange);} .kpi .v.w{color:#fff;}
  .export-card{display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:12px;}
  .export-card .card{background:var(--bg1); border:1px solid var(--line); border-radius:10px; padding:16px;}
  .export-card .card h4{font-size:13px; margin-bottom:6px; color:#eef2f7;}
  .export-card .card p{font-size:12px; color:var(--muted); margin-bottom:10px; line-height:1.4;}
  .empty{color:var(--muted); padding:26px; text-align:center; font-size:13px;}

  /* ---------- modals ---------- */
  .modal-bg{position:fixed; inset:0; background:rgba(4,6,10,.6); z-index:60; display:none; align-items:center; justify-content:center;}
  .modal-bg.open{display:flex;}
  .modal{background:var(--bg1); border:1px solid var(--line-2); border-radius:12px; width:520px; max-width:94vw;
    max-height:80vh; overflow:auto; padding:20px; box-shadow:0 20px 60px rgba(0,0,0,.6);}
  .modal h3{font-size:15px; margin-bottom:10px; color:#eef2f7;}
  .modal p{font-size:12.5px; color:var(--muted); line-height:1.5; margin-bottom:8px;}
  .modal input[type=text]{width:100%; background:var(--bg2); color:var(--text); border:1px solid var(--line-2);
    border-radius:var(--radius); padding:7px 9px; font-size:13px; outline:none; margin-bottom:10px;}
  .modal .row{margin-bottom:10px;}
  .modal-bg .btn{margin-top:4px;}
  #confirmModal .modal{width:440px;}
  #confirmModal .modal .actions{display:flex; gap:8px; margin-top:12px;}
  .toast{position:fixed; top:110px; left:50%; transform:translateX(-50%); z-index:80;
    background:var(--bg2); border:1px solid var(--blue); color:var(--text); border-radius:8px;
    padding:9px 16px; font-size:12.5px; box-shadow:0 8px 24px rgba(0,0,0,.5); opacity:0; transition:opacity .2s; pointer-events:none;}
  .toast.show{opacity:1;}
  #glError{position:fixed; inset:0; z-index:90; background:var(--bg0); color:var(--muted);
    display:none; align-items:center; justify-content:center; font-size:14px; text-align:center; padding:40px;}
</style>
</head>
<body>
<div id="topbar">
  <div class="logo">
    <div class="logo-mark">PB</div>
    <div class="logo-name">PB PlanRender Takeoff Studio<small id="sub-version"></small></div>
  </div>
  <nav id="nav">
    <button class="nav-btn active" data-screen="3d">3D Model</button>
    <button class="nav-btn" data-screen="elev">Elevations</button>
    <button class="nav-btn" data-screen="report">Reports</button>
    <button class="nav-btn" data-screen="export">Export</button>
    <span style="width:6px"></span>
    <button class="icon-btn" id="btnHelp" title="Help">?</button>
    <button class="icon-btn" id="btnSettings" title="Settings">&#9881;</button>
  </nav>
</div>

<div id="projectbar">
  <div class="field"><label>Project</label><select id="selProject"></select></div>
  <div class="field"><label>Drawing</label><select id="selDrawing"></select></div>
  <div class="field"><label>View</label><select id="selView"></select></div>
</div>

<div id="viewport"><div id="glError">The 3D view failed to initialise (CDN unreachable?).</div></div>

<aside id="sidebar">
  <div class="sb-section" data-sec="legend">
    <div class="sb-head">Substrate legend <span class="chev">&#9662;</span></div>
    <div class="sb-body" id="legend"></div>
  </div>
  <div class="sb-section" data-sec="tools">
    <div class="sb-head">Measurement tools <span class="chev">&#9662;</span></div>
    <div class="sb-body">
      <div class="tool-grid" id="tools">
        <button class="tool active" data-tool="select">Select</button>
        <button class="tool" data-tool="box">Draw Box</button>
        <button class="tool" data-tool="delete">Delete</button>
        <button class="tool" data-tool="undo">Undo</button>
        <button class="tool" data-tool="redo">Redo</button>
        <button class="tool" data-tool="clear">Clear All</button>
      </div>
    </div>
  </div>
  <div class="sb-section" data-sec="views">
    <div class="sb-head">View options <span class="chev">&#9662;</span></div>
    <div class="sb-body">
      <div class="radio" id="viewMode">
        <label class="on"><input type="radio" name="vmode" value="realistic" checked>Realistic</label>
        <label><input type="radio" name="vmode" value="xray">X-Ray / Transparent</label>
      </div>
      <div style="height:8px"></div>
      <label class="switch"><input type="checkbox" id="showSoffits" checked><span class="track"></span>Show Soffits</label>
    </div>
  </div>
  <div class="sb-section" data-sec="views">
    <div class="sb-head">Model views <span class="chev">&#9662;</span></div>
    <div class="sb-body">
      <div class="view-grid" id="modelViews"></div>
    </div>
  </div>
</aside>

<aside id="panel">
  <div class="panel-head"><h3>Selected Area</h3><button class="close" id="panelClose">&times;</button></div>
  <div class="panel-body">
    <div class="row"><label>Area ID</label><input id="fId" readonly></div>
    <div class="row"><label>Substrate</label><select id="fSubstrate"></select></div>
    <div class="row"><label>Building or unit</label><select id="fUnit"></select></div>
    <div class="row"><label>Elevation</label><select id="fElevation"></select></div>
    <div class="row"><label>Area (m&sup2;)</label>
      <div class="num-badge"><input id="fArea" type="number" min="0" step="0.01"><span class="m2">m&sup2;</span></div>
    </div>
    <div class="row"><label>Status</label><select id="fStatus"></select></div>
    <div class="row"><label>Progress</label><input id="fProgress" type="range" min="0" max="100" step="1" value="0">
      <div class="progress-readout">
        <span class="done" id="pDone">Completed: 0.00 m&sup2;</span>
        <span class="left" id="pLeft">Remaining: 0.00 m&sup2;</span>
      </div>
    </div>
    <div class="row"><label>Notes</label><textarea id="fNotes" placeholder="Access restrictions, scaffold required, multiple colours, surface repairs, builder clarification, excluded from scope..."></textarea></div>
    <div class="row"><label>History</label><div class="history" id="fHistory"></div></div>
    <div class="actions">
      <button class="btn ghost" id="btnCopy" title="Duplicate this measurement onto every identical townhouse">Copy to townhouses</button>
    </div>
    <div class="actions">
      <button class="btn primary" id="btnUpdate">Update Area</button>
      <button class="btn danger" id="btnDelete">Delete Area</button>
    </div>
  </div>
</aside>

<div id="bottombar">
  <div class="mode" id="modeHint"><b>Select Mode</b><span class="instr">Click a surface to select or create a measurement.</span></div>
  <div class="total"><b class="t">Total Areas: <span id="tTotal">0.00</span> m&sup2;</b></div>
  <div class="total"><b class="g">Completed: <span id="tDone">0.00</span> m&sup2;</b> <span id="tDonePct"></span></div>
  <div class="total"><b class="o">Remaining: <span id="tLeft">0.00</span> m&sup2;</b> <span id="tLeftPct"></span></div>
</div>
<div id="exportbar">
  <button class="exp-btn csv" id="btnCsv">Export CSV</button>
  <button class="exp-btn img" id="btnImg">Download Image</button>
</div>

<div id="elevations" class="screen hidden"><div class="elev-grid" id="elevGrid"></div></div>

<div id="reports" class="screen hidden"></div>
<div id="export" class="screen hidden"></div>

<div id="footer">
  <span>Tip: Use <b>Draw Box</b> to create custom measurement areas. Include all soffits and eaves. Areas are approximate. Verify on site for final takeoff.</span>
  <span id="version">PB PlanRender Takeoff Studio v1.0</span>
</div>

<div class="modal-bg" id="helpModal">
  <div class="modal">
    <h3>Help &amp; workflow</h3>
    <p><b>3D Model</b> — orbit, pan and zoom the model. Click a surface to measure it or assign a substrate. Use <b>Draw Box</b> to drag a measurement anywhere.</p>
    <p><b>Elevations</b> — draw directly over the elevation drawings; each box becomes a measured area.</p>
    <p><b>Reports</b> — substrate, townhouse and elevation breakdowns with progress totals.</p>
    <p><b>Export</b> — CSV, JSON and image exports for the take-off package and JobHub.</p>
    <p><b>Substrate legend</b> — click a category to highlight that substrate across the model. Right-click a selected area clears the highlight.</p>
    <p><b>Measurements save automatically</b> in this browser. Manual area corrections are never overwritten without confirmation.</p>
    <button class="btn primary" id="btnHelpClose">Got it</button>
  </div>
</div>
<div class="modal-bg" id="settingsModal">
  <div class="modal">
    <h3>Settings</h3>
    <div class="row"><label>Estimator name (recorded in audit history)</label><input type="text" id="fOperator"></div>
    <div class="row"><label>Project / drawing data scope</label><p>Measurements are stored per project and drawing in this browser. Export CSV or JSON to share.</p></div>
    <button class="btn ghost" id="btnResetData">Reset local measurements</button>
    <button class="btn primary" id="btnSettingsClose">Done</button>
  </div>
</div>

<div class="modal-bg" id="confirmModal">
  <div class="modal">
    <h3 id="confirmTitle">Confirm</h3>
    <p id="confirmMsg"></p>
    <div class="actions">
      <button class="btn danger" id="confirmOk">Confirm</button>
      <button class="btn ghost" id="confirmCancel">Cancel</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script src="https://cdn.jsdelivr.net/npm/three@0.124.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.124.0/examples/js/controls/OrbitControls.js"></script>
<script>
"use strict";
const STUDIO = __STUDIO_JSON__;

/* ================= helpers ================= */
const $ = function(sel){ return document.querySelector(sel); };
const $$ = function(sel){ return Array.prototype.slice.call(document.querySelectorAll(sel)); };
const hex = function(c){ return parseInt((c||'#888888').replace('#',''), 16); };
const fmt = function(v){ return (Math.round(v*100)/100).toFixed(2); };
const esc = function(s){ return String(s==null?'':s).replace(/[&<>"']/g, function(m){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]; }); };
let toastTimer = null;
function toast(msg){
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(function(){ t.classList.remove('show'); }, 2200);
}
let confirmCb = null;
function askConfirm(msg, cb){
  $('#confirmMsg').textContent = msg; confirmCb = cb; $('#confirmModal').classList.add('open');
}

/* ================= state ================= */
let projectId = STUDIO.project.id;
let drawing = (STUDIO.drawings[0]||{}).name || 'Drawing';
let tool = 'select';
let areas = [];
let undoStack = [];
let redoStack = [];
let selectedId = null;
let viewMode = 'realistic';
let showSoffits = true;
let activeScreen = '3d';
let highlightSubstrate = null;

const storeKey = function(){ return 'prts:' + projectId + ':' + drawing; };
function autosave(){
  localStorage.setItem(storeKey(), JSON.stringify(areas));
  pushUndo();
}
function pushUndo(){
  undoStack.push(JSON.stringify(areas));
  if (undoStack.length > 60) undoStack.shift();
  redoStack = [];
}
function loadAreas(){
  try { areas = JSON.parse(localStorage.getItem(storeKey()) || '[]') || []; }
  catch(e){ areas = []; }
  if (!areas.length && STUDIO.areas && STUDIO.areas.length){
    areas = JSON.parse(JSON.stringify(STUDIO.areas));
    areas.forEach(function(a){ if(!a.id) a.id = nextId(); });
  }
  areas.forEach(function(a){ if(!a.history) a.history = []; });
}
function nextId(){
  const used = {};
  areas.forEach(function(a){ used[String(a.id).toUpperCase()] = true; });
  let n = 1; while (used['A-' + String(1000+n).slice(1)]) n++;
  return 'A-' + String(1000+n).slice(1);
}
function nowStamp(){
  const d = new Date(); function p(x){ return String(x).padStart(2,'0'); }
  return d.getFullYear() + '-' + p(d.getMonth()+1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
}
function audit(a, what){
  a.history = a.history || [];
  a.history.push({ when: nowStamp(), who: operatorName(), what: what });
  if (a.history.length > 40) a.history = a.history.slice(-40);
}
function operatorName(){
  return localStorage.getItem('prts:operator') || STUDIO.operator || 'Estimator';
}

/* ================= three.js setup ================= */
if (!window.THREE){ $('#glError').style.display = 'flex'; $('#glError').textContent = 'Three.js failed to load from the CDN. Check your internet connection and reload.'; }
const container = document.getElementById('viewport');
let scene, camera, renderer, controls, rafId;
let modelGroup, soffitGroup, areaGroup, decoGroup, labelGroup;
const regions = [];
let origOpaque = [];

try {
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x87a7c9);
  scene.fog = new THREE.Fog(0x87a7c9, 160, 520);

  camera = new THREE.PerspectiveCamera(45, container.clientWidth/container.clientHeight, 0.1, 1200);
  const rendererEl = container.querySelector('#glError') ? container : container;
  renderer = new THREE.WebGLRenderer({ antialias:true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(container.clientWidth, container.clientHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputEncoding = THREE.sRGBEncoding;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  container.appendChild(renderer.domElement);

  controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.07;
  controls.maxPolarAngle = Math.PI/2.02;

  modelGroup = new THREE.Group(); scene.add(modelGroup);
  soffitGroup = new THREE.Group(); scene.add(soffitGroup);
  areaGroup = new THREE.Group(); scene.add(areaGroup);
  decoGroup = new THREE.Group(); scene.add(decoGroup);
  labelGroup = new THREE.Group(); scene.add(labelGroup);
} catch(err){
  $('#glError').style.display = 'flex';
}

/* ================= geometry constants ================= */
const UCOUNT = 5;
const ENV = STUDIO.envelope || { w:30, d:9, h:9 };
let UW = Math.max((ENV.w||30)/UCOUNT, 2);
let DEPTH = Math.max(ENV.d||9, 3);
let HEIGHT = Math.max(ENV.h||9, 2.4);
const EAVE = 0.45;
const STORY = HEIGHT/3;
const TOTALW = UW*UCOUNT + 0.05*(UCOUNT-1);
const halfW = TOTALW/2, halfD = DEPTH/2;

function unitX(i){ return -halfW + UW*i + 0.025*i + UW/2; }

/* ================= materials / meshes ================= */
function mat(hexc, opts){
  opts = opts || {};
  return new THREE.MeshStandardMaterial({ color: hexc,
    roughness: opts.rough!=null?opts.rough:0.6, metalness: opts.metal!=null?opts.metal:0.05 });
}
function box(w,h,d,x,y,z, m, parent, cast){
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w,h,d), m);
  mesh.position.set(x,y,z);
  mesh.castShadow = cast!==false; mesh.receiveShadow = cast!==false;
  (parent||modelGroup).add(mesh);
  return mesh;
}
function region(w,h,d,x,y,z, sub, info, parent){
  const mesh = box(w,h,d,x,y,z, mat(hex(sub.hex)), parent||modelGroup, true);
  mesh.userData.region = true;
  mesh.userData.sub = sub.code;
  mesh.userData.areaInfo = info || {};
  regions.push(mesh);
  return mesh;
}
function spriteLabel(text, bg, x, y, z, scale, parent){
  const c = document.createElement('canvas'); c.width=512; c.height=128;
  const ctx = c.getContext('2d');
  ctx.fillStyle = bg || 'rgba(37,99,235,.94)';
  const r=14; ctx.beginPath(); ctx.moveTo(r,0); ctx.arcTo(512,0,512,128,0); ctx.arcTo(512,128,0,128,0); ctx.arcTo(0,128,0,0,0); ctx.arcTo(0,0,512,0,r); ctx.closePath(); ctx.fill();
  ctx.fillStyle = '#ffffff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.font = 'bold 52px system-ui, sans-serif'; ctx.fillText(text.split('\n')[0], 256, 48);
  if (text.indexOf('\n') > -1){ ctx.font = '500 40px system-ui, sans-serif'; ctx.fillText(text.split('\n')[1], 256, 102); }
  const tex = new THREE.CanvasTexture(c); tex.minFilter = THREE.LinearFilter;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map:tex, transparent:true, depthWrite:false }));
  sp.scale.set(scale||3.2, (scale||3.2)*0.25, 1);
  sp.position.set(x,y,z); (parent||labelGroup).add(sp); return sp;
}

/* ================= model build ================= */
function buildModel(){
  const roofRise = Math.min(2.4, STORY*0.85);
  for (let i=0; i<UCOUNT; i++){
    const x = unitX(i);
    const leftX = x - UW/2, rightX = x + UW/2;

    // ground slab
    box(UW, 0.18, DEPTH, x, 0.09, 0, mat(0x9aa1ab, {rough:0.9}), modelGroup);

    // party walls (rendered block) + rear wall + side walls
    region(UW, HEIGHT, 0.12, x, HEIGHT/2, -halfD, sub('RBL'), faceInfo(i,'rear','RBL',UW,HEIGHT));
    region(0.12, HEIGHT, DEPTH, leftX, HEIGHT/2, 0, sub('RBL'), faceInfo(i,'left','RBL',DEPTH,HEIGHT));
    region(0.12, HEIGHT, DEPTH, rightX, HEIGHT/2, 0, sub('RBL'), faceInfo(i,'right','RBL',DEPTH,HEIGHT));

    // front ground floor wall (rendered block) with garage door + entry
    const wallF = region(UW, STORY, 0.12, x, STORY/2, halfD, sub('RBL'), faceInfo(i,'front','RBL',UW,STORY));
    const gdW = Math.min(UW*0.5, 2.8), gdH = Math.min(STORY*0.85, 2.3);
    const gdx = x - UW/2 + 0.2 + gdW/2;
    const gd = region(gdW, gdH, 0.09, gdx, gdH/2, halfD+0.02, sub('GD'), faceInfo(i,'front','GD',gdW,gdH));
    // entry door + window decor (not measurable)
    box(0.9, 2.05, 0.09, x + UW/2 - 0.75, 1.05, halfD+0.02, mat(0x1f2937, {rough:0.2, metal:0.4}), modelGroup);

    // two upper storeys: recessed balcony with timber cladding + balustrade + screens + sunhood
    for (let L=1; L<=2; L++){
      const yBot = L*STORY;
      const bw = UW - 1.2;
      const recess = 1.15;
      // recessed timber-look cladding wall
      region(bw, STORY, 0.1, x, yBot + STORY/2, halfD - recess, sub('EC5'), faceInfo(i,'front','EC5',bw,STORY));
      // balcony floor slab
      box(bw, 0.1, recess, x, yBot, halfD - recess/2, mat(0x8b8f98, {rough:0.8}), modelGroup);
      // glass balustrade
      region(bw, 0.95, 0.05, x, yBot + 0.48, halfD, sub('BA1'), faceInfo(i,'front','BA1',bw,0.95));
      // aluminium screens (vertical fins)
      const fins = 4;
      for (let k=0; k<fins; k++){
        const fx = x - bw/2 + (bw/(fins+1))*(k+1);
        region(0.05, STORY-0.4, 0.06, fx, yBot + (STORY-0.4)/2 + 0.2, halfD - 0.05, sub('BA2 / SCR'), faceInfo(i,'front','BA2 / SCR', 0.05, STORY-0.4));
      }
      // sunhood above
      region(UW + 0.25, 0.09, 0.8, x, yBot + STORY + 0.02, halfD - 0.35, sub('SHD'), faceInfo(i,'front','SHD',UW+0.25,0.8));
    }

    // parapet strip above level 3
    box(UW, 0.55, 0.12, x, HEIGHT - 0.27, halfD, mat(hex(sub('RBL').hex)), modelGroup);

    // soffits (under eave) — toggle via Show Soffits
    const soff = box(UW + EAVE*2, 0.07, DEPTH + EAVE*2, x, HEIGHT - 0.02, 0, mat(hex(sub('SOF').hex), {rough:0.85}), soffitGroup);
    soff.userData.region = true; soff.userData.sub = 'SOF';
    soff.userData.areaInfo = faceInfo(i,'soffit','SOF', UW + EAVE*2, DEPTH + EAVE*2);
    regions.push(soff);

    // gutters / cappings on front and rear eave edges
    [halfD + EAVE/2, -halfD - EAVE/2].forEach(function(gz){
      const g = box(UW + EAVE, 0.16, 0.16, x, HEIGHT + 0.02, gz, mat(hex(sub('BC / EG / PPT').hex), {rough:0.4, metal:0.35}), soffitGroup);
      g.userData.region = true; g.userData.sub = 'BC / EG / PPT';
      g.userData.areaInfo = faceInfo(i, gz>0?'front':'rear', 'BC / EG / PPT', UW + EAVE, 0.16);
      regions.push(g);
    });

    // downpipes at front corners
    [[leftX+0.05, halfD-0.05],[rightX-0.05, halfD-0.05]].forEach(function(corner){
      const dp = box(0.07, HEIGHT, 0.07, corner[0], HEIGHT/2, corner[1], mat(hex(sub('DP').hex), {rough:0.5, metal:0.3}), modelGroup);
      dp.userData.region = true; dp.userData.sub = 'DP';
      dp.userData.areaInfo = faceInfo(i,'front','DP', 0.07, HEIGHT);
      regions.push(dp);
    });

    // gabled roof (two slopes + gable ends + ridge) in roof group (not soffit)
    const roofGroup = new THREE.Group(); modelGroup.add(roofGroup);
    const slope = Math.sqrt((DEPTH/2 + EAVE)*(DEPTH/2 + EAVE) + roofRise*roofRise);
    const angle = Math.atan2(roofRise, DEPTH/2 + EAVE);
    const rsMat = mat(hex(sub('RS').hex), {rough:0.75, metal:0.25});
    const frontSlope = new THREE.Mesh(new THREE.PlaneGeometry(UW + EAVE*2 + 0.2, slope), rsMat);
    frontSlope.rotation.x = -angle;
    frontSlope.position.set(x, HEIGHT + roofRise/2, 0); frontSlope.castShadow = true;
    frontSlope.userData.region = true; frontSlope.userData.sub = 'RS';
    frontSlope.userData.areaInfo = faceInfo(i,'roof','RS', slope, UW + EAVE*2 + 0.2);
    roofGroup.add(frontSlope); regions.push(frontSlope);
    const rearSlope = frontSlope.clone();
    rearSlope.rotation.x = angle;
    rearSlope.userData.areaInfo = faceInfo(i,'roof','RS', slope, UW + EAVE*2 + 0.2);
    roofGroup.add(rearSlope); regions.push(rearSlope);
    // ridge capping
    const ridge = box(UW + EAVE*2 + 0.2, 0.12, 0.28, x, HEIGHT + roofRise, 0, mat(0x40454d, {rough:0.6, metal:0.3}), roofGroup);
    ridge.userData.region = true; ridge.userData.sub = 'BC / EG / PPT';
    ridge.userData.areaInfo = faceInfo(i,'roof','BC / EG / PPT', 0.28, UW + EAVE*2 + 0.2);
    regions.push(ridge);
  }
  buildDeco();
}
function faceInfo(unit, face, sub, w, h){
  let nx=0, ny=0, nz=0, cx=0, cy=0, cz=0;
  const x = unitX(unit);
  if (face==='front'){ nx=0; nz=1; cx=x; cy=HEIGHT/2; cz=halfD; }
  else if (face==='rear'){ nx=0; nz=-1; cx=x; cy=HEIGHT/2; cz=-halfD; }
  else if (face==='left'){ nx=-1; cx=x-UW/2; cy=HEIGHT/2; cz=0; }
  else if (face==='right'){ nx=1; cx=x+UW/2; cy=HEIGHT/2; cz=0; }
  else if (face==='soffit'){ ny=-1; cx=x; cy=HEIGHT; cz=0; }
  else if (face==='roof'){ ny=1; cx=x; cy=HEIGHT + Math.min(2.4, STORY*0.85)/2; cz=0; }
  return { unit:unit, face:face, substrate:sub, w:w, h:h, nx:nx, ny:ny, nz:nz, cx:cx, cy:cy, cz:cz };
}
function sub(code){
  for (let i=0;i<STUDIO.substrates.length;i++) if (STUDIO.substrates[i].code===code) return STUDIO.substrates[i];
  return { code:code, name:code, hex:'#888888' };
}
function subName(code){ return sub(code).name; }
function subLabel(code){ return code + ' – ' + subName(code); }

function buildDeco(){
  // ground
  const g = new THREE.Mesh(new THREE.PlaneGeometry(TOTALW+60, 120), new THREE.MeshStandardMaterial({ color:0x6f8f5a, roughness:1 }));
  g.rotation.x = -Math.PI/2; g.position.y = -0.01; g.receiveShadow = true; decoGroup.add(g);
  // footpath
  const path = new THREE.Mesh(new THREE.PlaneGeometry(TOTALW+10, 2.6), new THREE.MeshStandardMaterial({ color:0x8b8b90, roughness:0.9 }));
  path.rotation.x = -Math.PI/2; path.position.set(0, 0.01, halfD + EAVE + 2.4); path.receiveShadow = true; decoGroup.add(path);
  // street
  const st = new THREE.Mesh(new THREE.PlaneGeometry(TOTALW+14, 7), new THREE.MeshStandardMaterial({ color:0x565b63, roughness:0.95 }));
  st.rotation.x = -Math.PI/2; st.position.set(0, 0.0, halfD + EAVE + 7.4); decoGroup.add(st);
  // front fencing (timber paling fence)
  const fence = box(TOTALW+0.2, 1.1, 0.08, 0, 0.55, halfD + EAVE + 0.9, mat(0x7a5b3e, {rough:0.9}), decoGroup, false);
  fence.rotation.y = 0;
  for (let i=-Math.floor(TOTALW/1.6); i<=Math.ceil(TOTALW/1.6); i++){
    box(0.06, 1.1, 0.08, i*1.6, 0.55, halfD + EAVE + 0.9, mat(0x8b6a47, {rough:0.9}), decoGroup, false);
  }
  // landscaping: shrubs + trees
  for (let i=0;i<6;i++){
    const sx = -halfW + 4 + i*(TOTALW-8)/5;
    const bush = new THREE.Mesh(new THREE.SphereGeometry(0.55, 10, 8), new THREE.MeshStandardMaterial({ color:0x3f6b3a, roughness:1 }));
    bush.position.set(sx, 0.5, halfD + EAVE + 3.6); bush.castShadow = true; decoGroup.add(bush);
  }
  for (let i=0;i<4;i++){
    const tx = -halfW + 6 + i*(TOTALW-12)/3;
    const trunk = box(0.18, 2.2, 0.18, tx, 1.1, halfD + EAVE + 6.2, mat(0x5b4128, {rough:1}), decoGroup, false);
    const crown = new THREE.Mesh(new THREE.SphereGeometry(1.15, 12, 9), new THREE.MeshStandardMaterial({ color:0x4f7a45, roughness:1 }));
    crown.position.set(tx, 3.1, halfD + EAVE + 6.2); crown.castShadow = true; decoGroup.add(crown);
  }
}

/* ================= lights / sky ================= */
function buildLights(){
  scene.add(new THREE.HemisphereLight(0xd7e6ff, 0x7d735f, 0.85));
  const sun = new THREE.DirectionalLight(0xfff2d6, 1.55);
  sun.position.set(TOTALW*0.8, 90, TOTALW*1.1);
  sun.castShadow = true; sun.shadow.mapSize.width = 2048; sun.shadow.mapSize.height = 2048;
  const sh = TOTALW*1.5;
  sun.shadow.camera.left = -sh; sun.shadow.camera.right = sh;
  sun.shadow.camera.top = sh; sun.shadow.camera.bottom = -sh;
  sun.shadow.camera.far = 400; sun.shadow.bias = -0.0005;
  scene.add(sun); scene.add(sun.target);
  const fill = new THREE.DirectionalLight(0xbfd4ff, 0.3);
  fill.position.set(-TOTALW, 40, -TOTALW); scene.add(fill);
}

/* ================= camera views ================= */
const radius = Math.max(TOTALW, DEPTH*2)/2 + HEIGHT*1.4;
function setView(v){
  const t = { x:0, y:HEIGHT*0.55, z:0 };
  if (v==='front'){ camera.position.set(0, HEIGHT*0.75, radius*1.35); }
  else if (v==='rear'){ camera.position.set(0, HEIGHT*0.75, -radius*1.35); }
  else if (v==='left'){ camera.position.set(-radius*1.5, HEIGHT*0.75, 0); }
  else if (v==='right'){ camera.position.set(radius*1.5, HEIGHT*0.75, 0); }
  else if (v==='aerial'){ camera.position.set(0, radius*2.6, 0.01); t.y = 0; }
  controls.target.set(t.x, t.y, t.z);
  controls.update();
}
const VIEWS = [
  { id:'front', label:'Front – King Street' },
  { id:'rear',  label:'Rear – Hamilton Street' },
  { id:'left',  label:'Left – North' },
  { id:'right', label:'Right – South' },
  { id:'aerial',label:'Aerial Top' }
];

/* ================= measurements (3D) ================= */
let drawState = null;
const previewGroup = new THREE.Group(); scene.add(previewGroup);
function fillMat(a){
  return new THREE.MeshBasicMaterial({ color:hex(sub(a.substrate).hex), transparent:true, opacity:0.32, depthWrite:false });
}
function planeBasis(n){
  const up = new THREE.Vector3(0,1,0);
  let u;
  if (Math.abs(n.y) > 0.99){ u = new THREE.Vector3(0,0, n.y>0?1:-1); }
  else { u = up.clone().addScaledVector(n, -up.dot(n)).normalize(); }
  if (u.lengthSq() < 1e-8) u.set(0,0,1);
  const right = new THREE.Vector3().crossVectors(n, u).normalize();
  return { right:right, up:u };
}
function addAreaMesh(a){
  if (!a.geom || a.geom.kind==='2d') return;
  const g = a.geom;
  const n = new THREE.Vector3(g.nx, g.ny, g.nz);
  const b = planeBasis(n);
  const c = new THREE.Vector3(g.cx, g.cy, g.cz);
  const w = Math.max(g.w, 0.1), h = Math.max(g.h, 0.1);
  const halfH = h/2;

  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, 0.025, h), fillMat(a));
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0), n);
  mesh.quaternion.copy(q);
  mesh.position.copy(c);
  mesh.renderOrder = 3;
  mesh.userData.areaId = a.id;
  mesh.userData.areaMesh = true;
  areaGroup.add(mesh);
  a._mesh = mesh;

  const corners = [
    c.clone().addScaledVector(b.right, -w/2).addScaledVector(b.up, -h/2),
    c.clone().addScaledVector(b.right,  w/2).addScaledVector(b.up, -h/2),
    c.clone().addScaledVector(b.right,  w/2).addScaledVector(b.up,  h/2),
    c.clone().addScaledVector(b.right, -w/2).addScaledVector(b.up,  h/2),
    c.clone().addScaledVector(b.right, -w/2).addScaledVector(b.up, -h/2)
  ];
  const line = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(corners),
    new THREE.LineDashedMaterial({ color:0x3b82f6, dashSize:0.16, gapSize:0.11, transparent:true, opacity:0.95 })
  );
  line.computeLineDistances(); line.renderOrder = 4;
  areaGroup.add(line);
  a._line = line;

  const labelPos = c.clone().addScaledVector(n, 0.25).addScaledVector(b.up, halfH + 0.7);
  const label = spriteLabel(a.substrate + '\n' + fmt(a.area) + ' m\u00b2', 'rgba(37,99,235,.94)', labelPos.x, labelPos.y, labelPos.z, 3.4);
  label.renderOrder = 5;
  a._label = label;
}
function removeAreaMesh(a){
  if (a._mesh){ areaGroup.remove(a._mesh); a._mesh.geometry.dispose(); }
  if (a._line){ areaGroup.remove(a._line); a._line.geometry.dispose(); }
  if (a._label){ labelGroup.remove(a._label); a._label.material.map.dispose(); }
}
function refreshAreaMeshes(){
  while (areaGroup.children.length){ const c = areaGroup.children.pop(); c.geometry && c.geometry.dispose(); }
  while (labelGroup.children.length){ const c = labelGroup.children.pop(); c.material && c.material.map && c.material.map.dispose(); }
  areas.forEach(function(a){ if (a.geom && a.geom.kind!=='2d') addAreaMesh(a); });
}
function highlightAreas(ids){
  areas.forEach(function(a){
    if (a._mesh) a._mesh.material.opacity = ids.indexOf(a.id) > -1 ? 0.6 : 0.32;
    if (a._line) a._line.material.color.set(ids.indexOf(a.id) > -1 ? 0x22d3ee : 0x3b82f6);
  });
}
function highlightSubstrateAreas(code){
  const ids = [];
  areas.forEach(function(a){ if (a.substrate===code) ids.push(a.id); });
  highlightAreas(ids);
  return ids;
}

/* ================= picking ================= */
const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();
function pickObject(event, fromArea){
  const rect = renderer.domElement.getBoundingClientRect();
  ndc.x = ((event.clientX - rect.left)/rect.width)*2 - 1;
  ndc.y = -((event.clientY - rect.top)/rect.height)*2 + 1;
  raycaster.setFromCamera(ndc, camera);
  const targets = [];
  areaGroup.children.forEach(function(c){ if (c.userData && c.userData.areaMesh) targets.push(c); });
  targets.push.apply(targets, regions);
  const hits = raycaster.intersectObjects(targets, false);
  return hits.length ? hits[0] : null;
}

/* ================= selection ================= */
let selectedArea = null;
function deselect(){
  selectedArea = null; selectedId = null;
  $('#panel').classList.remove('open');
  highlightAreas([]);
  if (highlightSubstrate){ highlightSubstrateAreas(highlightSubstrate); }
}
function selectArea(a){
  selectedArea = a; selectedId = a.id;
  highlightAreas([a.id]);
  $('#panel').classList.add('open');
  fillPanel(a);
}
function fillPanel(a){
  $('#fId').value = a.id || '';
  $('#fSubstrate').value = a.substrate || '';
  $('#fUnit').value = String(a.unit != null ? a.unit : 0);
  $('#fElevation').value = a.elevation || STUDIO.elevationLabels.front;
  $('#fArea').value = a.area != null ? a.area : '';
  $('#fStatus').value = a.status || 'Paint Included';
  $('#fProgress').value = a.progress || 0;
  $('#fNotes').value = a.notes || '';
  updateProgressReadout();
  const hist = $('#fHistory'); hist.innerHTML = '';
  (a.history||[]).slice().reverse().forEach(function(e){
    const d = document.createElement('div');
    d.textContent = (e.when||'') + ' · ' + (e.who||'') + ' — ' + (e.what||'');
    hist.appendChild(d);
  });
}
function updateProgressReadout(){
  const v = parseFloat($('#fArea').value || '0') || 0;
  const p = parseFloat($('#fProgress').value || '0') || 0;
  const done = v*p/100, left = Math.max(v-done, 0);
  $('#pDone').textContent = 'Completed: ' + fmt(done) + ' m\u00b2';
  $('#pLeft').textContent = 'Remaining: ' + fmt(left) + ' m\u00b2';
}

/* ================= create / commit areas ================= */
function createAreaFromRegion(reg, faceOverride){
  const info = reg.userData.areaInfo || {};
  const unit = info.unit != null ? info.unit : 0;
  const face = faceOverride || info.face || 'front';
  const nx = info.nx||0, ny=info.ny||0, nz=info.nz||0;
  const a = {
    id: nextId(),
    unit: unit,
    unit_label: STUDIO.units[unit] ? STUDIO.units[unit].label : ('Unit ' + (unit+1)),
    drawing: drawing,
    elevation: STUDIO.elevationLabels[face] || STUDIO.elevationLabels.front,
    face: face,
    substrate: reg.userData.sub || 'RBL',
    area: Math.max((info.w||0)*(info.h||0), 0.05),
    status: 'Paint Included',
    progress: 0,
    notes: '',
    manual: false,
    geom: { face:face, unit:unit, nx:nx, ny:ny, nz:nz, cx:info.cx||0, cy:info.cy||0, cz:info.cz||0, w:info.w||0, h:info.h||0 }
  };
  audit(a, 'Created');
  areas.push(a);
  addAreaMesh(a);
  autosave();
  return a;
}
function commitDrawBox(){
  if (!drawState) return;
  const st = drawState;
  const w = Math.max(st.w, 0.1), h = Math.max(st.h, 0.1);
  if (w*h < 0.04){ cancelDraw(); return; }
  const a = {
    id: nextId(),
    unit: st.unit,
    unit_label: STUDIO.units[st.unit] ? STUDIO.units[st.unit].label : ('Unit ' + (st.unit+1)),
    drawing: drawing,
    elevation: STUDIO.elevationLabels[st.face] || STUDIO.elevationLabels.front,
    face: st.face,
    substrate: st.substrate,
    area: Math.round(w*h*100)/100,
    status: 'Paint Included',
    progress: 0,
    notes: '',
    manual: false,
    geom: { face:st.face, unit:st.unit, nx:st.nx, ny:st.ny, nz:st.nz, cx:st.cx, cy:st.cy, cz:st.cz, w:w, h:h }
  };
  audit(a, 'Drawn box (' + fmt(a.area) + ' m\u00b2)');
  areas.push(a);
  addAreaMesh(a);
  cancelDraw();
  autosave();
  selectArea(a);
}
function cancelDraw(){
  drawState = null;
  controls.enabled = true;
  while (previewGroup.children.length){ const c = previewGroup.children.pop(); c.geometry && c.geometry.dispose(); }
}

/* ================= pointer interaction ================= */
let down = null, downPos = null;
function onPointerDown(e){
  if (!renderer) return;
  down = { x:e.clientX, y:e.clientY };
  downPos = { x:e.clientX, y:e.clientY };
  if (e.button !== 0) return;
  if (tool !== 'box') return;
  const hit = pickObject(e);
  if (!hit){ down = null; return; }
  const info = (hit.object.userData.region && hit.object.userData.areaInfo) || {};
  const face = info.face || 'front';
  const n = new THREE.Vector3(info.nx||0, info.ny||0, info.nz||0);
  if (n.lengthSq() < 1e-6) n.set(0,0,1);
  n.normalize();
  const anchor = hit.point.clone();
  controls.enabled = false;
  drawState = { anchor:anchor, normal:n, face:face, unit:info.unit!=null?info.unit:0,
    substrate: hit.object.userData.sub || 'RBL' };
}
function onPointerMove(e){
  if (!renderer) return;
  if (downPos && Math.abs(e.clientX-downPos.x)+Math.abs(e.clientY-downPos.y) > 5){
    down = null; // it's a drag/pan
  }
  if (tool==='box' && drawState){
    const plane = new THREE.Plane(drawState.normal, -drawState.normal.dot(drawState.anchor));
    raycaster.setFromCamera(ndcFromEvent(e), camera);
    const hit = new THREE.Vector3();
    if (raycaster.ray.intersectPlane(plane, hit)){
      const b = planeBasis(drawState.normal);
      const dRight = hit.clone().sub(drawState.anchor).dot(b.right);
      const dUp = hit.clone().sub(drawState.anchor).dot(b.up);
      const w = Math.abs(dRight), h = Math.abs(dUp);
      const cx = drawState.anchor.x + b.right.x*(dRight/2) + b.up.x*(dUp/2);
      const cy = drawState.anchor.y + b.right.y*(dRight/2) + b.up.y*(dUp/2);
      const cz = drawState.anchor.z + b.right.z*(dRight/2) + b.up.z*(dUp/2);
      drawState.w = w; drawState.h = h;
      drawState.cx = cx; drawState.cy = cy; drawState.cz = cz;
      updatePreview();
    }
    return;
  }
  if (tool==='select' || tool==='delete'){
    const hit = pickObject(e, true);
    renderer.domElement.style.cursor = hit ? 'pointer' : 'grab';
  }
}
function ndcFromEvent(e){
  const rect = renderer.domElement.getBoundingClientRect();
  return new THREE.Vector2(((e.clientX-rect.left)/rect.width)*2-1, -((e.clientY-rect.top)/rect.height)*2+1);
}
function updatePreview(){
  while (previewGroup.children.length){ const c = previewGroup.children.pop(); c.geometry && c.geometry.dispose(); }
  if (!drawState || drawState.w==null) return;
  const n = drawState.normal;
  const b = planeBasis(n);
  const c = new THREE.Vector3(drawState.cx, drawState.cy, drawState.cz);
  const w = drawState.w, h = drawState.h;
  const corners = [
    c.clone().addScaledVector(b.right,-w/2).addScaledVector(b.up,-h/2),
    c.clone().addScaledVector(b.right, w/2).addScaledVector(b.up,-h/2),
    c.clone().addScaledVector(b.right, w/2).addScaledVector(b.up, h/2),
    c.clone().addScaledVector(b.right,-w/2).addScaledVector(b.up, h/2),
    c.clone().addScaledVector(b.right,-w/2).addScaledVector(b.up,-h/2)
  ];
  const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(corners),
    new THREE.LineDashedMaterial({ color:0x22d3ee, dashSize:0.16, gapSize:0.11, transparent:true, opacity:1 }));
  line.computeLineDistances();
  previewGroup.add(line);
  const fill = new THREE.Mesh(new THREE.BoxGeometry(w,0.02,h), new THREE.MeshBasicMaterial({ color:0x3b82f6, transparent:true, opacity:0.2, depthWrite:false }));
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0), n);
  fill.quaternion.copy(q); fill.position.copy(c);
  previewGroup.add(fill);
  const lp = c.clone().addScaledVector(n,0.2).addScaledVector(b.up, h/2+0.6);
  previewGroup.add(spriteLabel(subLabel(drawState.substrate) + '\n' + fmt(w*h) + ' m\u00b2', 'rgba(34,211,238,.95)', lp.x, lp.y, lp.z, 2.8));
}
function onPointerUp(e){
  if (!renderer) return;
  if (tool==='box' && drawState){ commitDrawBox(); return; }
  if (down && Math.abs(e.clientX-down.x)+Math.abs(e.clientY-down.y) < 6){    const hit = pickObject(e, true);
    if (hit){
      const obj = hit.object;
      if (obj.userData.areaId){
        const area = areas.filter(function(x){ return x.id===obj.userData.areaId; })[0];
        if (area){ selectArea(area); }
      } else if (obj.userData.region){
        if (tool==='delete'){
          const area = areas.filter(function(x){ return x.geom && x.geom.kind!=='2d' && x.geom.face===obj.userData.areaInfo.face && x.geom.unit===obj.userData.areaInfo.unit && Math.abs(x.geom.w-obj.userData.areaInfo.w)<0.01 && Math.abs(x.geom.h-obj.userData.areaInfo.h)<0.01; })[0];
          if (area){ deleteArea(area); }
        } else {
          const area = createAreaFromRegion(obj);
          selectArea(area);
        }
      }
    }
  }
  down = null;
}

/* ================= panel actions ================= */
function readPanel(){
  if (!selectedArea) return;
  const oldArea = parseFloat(selectedArea.area) || 0;
  const oldSub = selectedArea.substrate;
  const a = selectedArea;
  const newSub = $('#fSubstrate').value;
  const newArea = parseFloat($('#fArea').value) || 0;
  const newStatus = $('#fStatus').value;
  const newProgress = parseFloat($('#fProgress').value) || 0;
  const newUnit = parseInt($('#fUnit').value, 10) || 0;
  if (a.manual && newArea !== oldArea){ askConfirm('This area was manually corrected. Overwrite the manual measurement with the new value?', function(){ applyPanel(); }); return; }
  applyPanel();
}
function applyPanel(){
  if (!selectedArea) return;
  const a = selectedArea;
  const oldArea = parseFloat(a.area) || 0;
  const oldSub = a.substrate;
  const newSub = $('#fSubstrate').value;
  const newArea = parseFloat($('#fArea').value) || 0;
  const newStatus = $('#fStatus').value;
  const newProgress = parseFloat($('#fProgress').value) || 0;
  const newUnit = parseInt($('#fUnit').value, 10) || 0;
  let changed = [];
  if (newSub !== oldSub){ changed.push('Substrate ' + oldSub + ' → ' + newSub); }
  if (Math.abs(newArea - oldArea) > 0.001){ changed.push('Area ' + fmt(oldArea) + ' → ' + fmt(newArea) + ' m\u00b2'); }
  if (newStatus !== a.status){ changed.push('Status → ' + newStatus); }
  if (Math.abs(newProgress - (a.progress||0)) > 0.001){ changed.push('Progress → ' + newProgress + '%'); }
  const newNotes = $('#fNotes').value;
  if (newNotes !== (a.notes||'')){ changed.push('Notes updated'); }
  if (newUnit !== a.unit){ changed.push('Unit → ' + (STUDIO.units[newUnit]||{}).label); }
  if (newArea !== oldArea) a.manual = true;
  a.substrate = newSub; a.area = newArea; a.status = newStatus;
  a.progress = newProgress; a.notes = newNotes; a.unit = newUnit;
  a.unit_label = STUDIO.units[newUnit] ? STUDIO.units[newUnit].label : ('Unit ' + (newUnit+1));
  a.elevation = $('#fElevation').value;
  if (changed.length){ audit(a, changed.join(', ')); }
  if (a._mesh){ removeAreaMesh(a); addAreaMesh(a); }
  autosave();
  redrawElevations();
  toast('Area ' + a.id + ' updated.');
  updateTotals();
}
function deleteArea(a){
  askConfirm('Delete area ' + a.id + ' (' + fmt(a.area) + ' m\u00b2)?', function(){
    audit(a, 'Deleted');
    removeAreaMesh(a);
    areas = areas.filter(function(x){ return x!==a; });
    autosave();
    deselect();
    updateTotals();
    redrawElevations();
    toast('Area deleted.');
  });
}
function copyToTownhouses(){
  if (!selectedArea) return;
  const src = selectedArea;
  if (!src.geom || src.geom.kind==='2d'){ toast('Copy applies to 3D measurements only.'); return; }
  areas.forEach(function(a){ removeAreaMesh(a); });
  const base = JSON.parse(JSON.stringify(src));
  const created = [];
  for (let u=0; u<UCOUNT; u++){
    if (u===src.unit) continue;
    const copy = JSON.parse(JSON.stringify(base));
    copy.id = nextId();
    copy.unit = u;
    copy.unit_label = STUDIO.units[u].label;
    const dx = unitX(u) - unitX(src.unit);
    copy.geom.cx = Math.round((copy.geom.cx + dx)*1000)/1000;
    copy.geom.unit = u;
    copy.history = []; audit(copy, 'Copied from ' + src.id);
    created.push(copy);
  }
  areas = areas.concat(created);
  refreshAreaMeshes();
  autosave();
  updateTotals();
  toast('Copied ' + src.id + ' to ' + created.length + ' other townhouse(s).');
}

/* ================= undo / redo ================= */
function doUndo(){
  if (!undoStack.length) return;
  redoStack.push(JSON.stringify(areas));
  areas = JSON.parse(undoStack.pop());
  areas.forEach(function(a){ if(!a.history) a.history=[]; });
  refreshAreaMeshes(); deselect(); updateTotals();
}
function doRedo(){
  if (!redoStack.length) return;
  undoStack.push(JSON.stringify(areas));
  areas = JSON.parse(redoStack.pop());
  areas.forEach(function(a){ if(!a.history) a.history=[]; });
  refreshAreaMeshes(); deselect(); updateTotals();
}
function clearAll(){
  if (!areas.length) return;
  askConfirm('Remove all ' + areas.length + ' measurement areas?', function(){
    areas.forEach(function(a){ removeAreaMesh(a); });
    areas = []; autosave(); deselect(); updateTotals(); redrawElevations();
  });
}

/* ================= totals ================= */
function updateTotals(){
  let total=0, done=0;
  areas.forEach(function(a){
    if (a.status==='Paint Excluded') return;
    const v = Math.max(parseFloat(a.area)||0, 0);
    const p = Math.min(Math.max(parseFloat(a.progress)||0,0),100);
    total += v; done += v*p/100;
  });
  const left = Math.max(total-done, 0);
  $('#tTotal').textContent = fmt(total);
  $('#tDone').textContent = fmt(done);
  $('#tLeft').textContent = fmt(left);
  $('#tDonePct').textContent = total>0 ? ' — ' + (done/total*100).toFixed(1) + '%' : '';
  $('#tLeftPct').textContent = total>0 ? ' — ' + (left/total*100).toFixed(1) + '%' : '';
  return { total:total, done:done, left:left };
}

/* ================= legend / tools / views UI ================= */
function buildLegend(){
  const box = $('#legend'); box.innerHTML = '';
  STUDIO.substrates.forEach(function(s){
    const row = document.createElement('div'); row.className = 'swatch-row'; row.dataset.code = s.code;
    row.innerHTML = '<span class="dot" style="background:' + s.hex + '"></span><span class="swatch-code">' + esc(s.code) + '</span><span class="swatch-name">' + esc(s.name) + '</span>';
    row.addEventListener('click', function(){
      const active = highlightSubstrate === s.code;
      highlightSubstrate = active ? null : s.code;
      $$('.swatch-row').forEach(function(r){ r.classList.remove('active'); });
      if (highlightSubstrate){
        row.classList.add('active');
        const ids = highlightSubstrateAreas(s.code);
        toast('Highlighting ' + s.code + ' — ' + ids.length + ' area(s).');
      } else { highlightAreas([]); toast('Highlight cleared.'); }
    });
    box.appendChild(row);
  });
}
function setTool(t){
  tool = t;
  $$('.tool').forEach(function(b){ b.classList.toggle('active', b.dataset.tool===t); });
  const mode = $('#modeHint');
  if (t==='box'){ mode.innerHTML = '<b>Draw Box Mode</b><span class="instr">Click and drag to draw a box over any area.</span>'; }
  else if (t==='delete'){ mode.innerHTML = '<b>Delete Mode</b><span class="instr">Click a measured area to delete it.</span>'; }
  else if (t==='undo'){ doUndo(); }
  else if (t==='redo'){ doRedo(); }
  else if (t==='clear'){ clearAll(); }
  else { mode.innerHTML = '<b>Select Mode</b><span class="instr">Click a surface to select or create a measurement.</span>'; }
  if (t==='undo' || t==='redo' || t==='clear'){ setTimeout(function(){ setTool('select'); }, 1); }
}
function buildViewButtons(){
  const box = $('#modelViews'); box.innerHTML = '';
  VIEWS.forEach(function(v){
    const b = document.createElement('button'); b.className='view-btn'; b.textContent = v.label;
    b.dataset.view = v.id;
    b.addEventListener('click', function(){ setView(v.id); $$('.view-btn').forEach(function(x){ x.classList.remove('active'); }); b.classList.add('active'); });
    box.appendChild(b);
  });
}

/* ================= screens / nav ================= */
function showScreen(name){
  activeScreen = name;
  $$('.nav-btn').forEach(function(b){ b.classList.toggle('active', b.dataset.screen===(name==='3d'?'3d':name)); });
  const threeVisible = name==='3d';
  $('#viewport').style.display = threeVisible ? 'block' : 'none';
  $('#sidebar').style.display = threeVisible ? 'flex' : 'none';
  $('#bottombar').style.display = threeVisible ? 'flex' : 'none';
  $('#exportbar').style.display = threeVisible ? 'flex' : 'none';
  $('#elevations').classList.toggle('hidden', name!=='elev');
  $('#reports').classList.toggle('hidden', name!=='report');
  $('#export').classList.toggle('hidden', name!=='export');
  if (name==='3d'){ renderer.setSize(container.clientWidth, container.clientHeight); resumeAnim(); }
  else { pauseAnim(); }
  if (name==='elev'){ buildElevationScreen(); }
  if (name==='report'){ buildReports(); }
  if (name==='export'){ buildExport(); }
}

/* ================= elevations screen ================= */
let elevCache = [];
function buildElevationScreen(){
  const grid = $('#elevGrid'); grid.innerHTML = ''; elevCache = [];
  if (!STUDIO.elevations || !STUDIO.elevations.length){
    grid.innerHTML = '<div class="empty">No elevation drawings were found in the imported plans, so the faces are scaled straight from the measured floor-plan envelope. The 3D model and the faces below still use the plan measurements (metres per pixel).</div>';
    return;
  }
  STUDIO.elevations.forEach(function(ev){
    const card = document.createElement('div'); card.className='elev-card';
    let meta = '';
    if (ev.w_m && ev.h_m){
      meta = '<div class="elev-meta">' + (Number(ev.w_m).toLocaleString(undefined,{maximumFractionDigits:1})) + ' m wide &times; ' + (Number(ev.h_m).toLocaleString(undefined,{maximumFractionDigits:1})) + ' m high' + (ev.m_per_px ? ' &middot; ' + (1/ev.m_per_px).toFixed(0) + ' px/m' : '') + '</div>';
    }
    card.innerHTML = '<h4>' + esc(ev.label) + '</h4>' + meta;
    const canvas = document.createElement('canvas'); canvas.className='elev-canvas';
    card.appendChild(canvas);
    grid.appendChild(card);
    const img = new Image();
    img.onload = function(){ elevCache.push({ canvas:canvas, ev:ev, img:img }); drawElevation(canvas, ev, img); };
    img.src = ev.dataUrl;
  });
}
function redrawElevations(){
  if (activeScreen!=='elev') return;
  elevCache.forEach(function(c){ drawElevation(c.canvas, c.ev, c.img); });
}
function drawElevation(canvas, ev, img){
  const W = img.width, H = img.height;
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0);
  // zone overlays (highlight substrate areas)
  (ev.zones||[]).forEach(function(z){
    const r = { x:z.x*W/100, y:z.y*H/100, w:z.w*W/100, h:z.h*H/100 };
    const s = sub(z.substrate || 'RBL');
    ctx.strokeStyle = s.hex; ctx.lineWidth = Math.max(2, W/400);
    ctx.strokeRect(r.x, r.y, r.w, r.h);
    ctx.fillStyle = s.hex; ctx.globalAlpha = 0.18; ctx.fillRect(r.x, r.y, r.w, r.h); ctx.globalAlpha = 1;
  });
  // drawn areas
  areas.forEach(function(a){
    if (!a.geom || a.geom.kind!=='2d' || a.geom.elev!==ev.key) return;
    const r = { x:a.geom.x, y:a.geom.y, w:a.geom.w, h:a.geom.h };
    ctx.strokeStyle = '#22d3ee'; ctx.lineWidth = Math.max(2, W/300); ctx.setLineDash([8,6]);
    ctx.strokeRect(r.x, r.y, r.w, r.h);
    ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(59,130,246,.2)'; ctx.fillRect(r.x, r.y, r.w, r.h);
    const s = sub(a.substrate);
    ctx.fillStyle = '#22d3ee'; ctx.font = 'bold ' + Math.max(14, W/42) + 'px sans-serif';
    ctx.fillText(a.substrate + ' ' + fmt(a.area) + ' m\u00b2', r.x, Math.max(r.y-6, 16));
    void s;
  });
  let d2 = null, d2Pos = null;
  function rectFromEvent(e){
    const rect = canvas.getBoundingClientRect();
    const sx = (e.clientX-rect.left)/rect.width*W;
    const sy = (e.clientY-rect.top)/rect.height*H;
    return { x:sx, y:sy };
  }
  canvas.addEventListener('pointerdown', function(e){
    if (tool!=='box') return;
    d2Pos = rectFromEvent(e); d2 = rectFromEvent(e);
  });
  canvas.addEventListener('pointermove', function(e){
    if (d2) d2 = rectFromEvent(e);
  });
  canvas.addEventListener('pointerup', function(e){
    if (!d2 || !d2Pos) return;
    d2 = rectFromEvent(e);
    const x0 = Math.min(d2.x, d2Pos.x), y0 = Math.min(d2.y, d2Pos.y);
    const w = Math.abs(d2.x - d2Pos.x), h = Math.abs(d2.y - d2Pos.y);
    d2 = null; d2Pos = null;
    if (w < 4 || h < 4) return;
    const mpp = parseFloat(ev.m_per_px) || 0.05;
    const unit = Math.min(UCOUNT-1, Math.max(0, Math.floor((x0/W)*UCOUNT)));
    const zone = (ev.zones||[]).filter(function(z){ return x0>=z.x*W/100 && y0>=z.y*H/100 && x0<= (z.x+z.w)*W/100 && y0 <= (z.y+z.h)*H/100; })[0];
    const a = {
      id: nextId(),
      unit: unit,
      unit_label: STUDIO.units[unit] ? STUDIO.units[unit].label : ('Unit ' + (unit+1)),
      drawing: drawing,
      elevation: ev.label,
      face: ev.key,
      substrate: (zone && zone.substrate) || 'RBL',
      area: Math.round(w*h*mpp*mpp*100)/100,
      status: 'Paint Included',
      progress: 0,
      notes: '',
      manual: false,
      geom: { kind:'2d', elev:ev.key, x:Math.round(x0), y:Math.round(y0), w:Math.round(w), h:Math.round(h), m_per_px:mpp }
    };
    audit(a, 'Drawn on elevation (' + ev.label + ')');
    areas.push(a); autosave(); updateTotals();
    selectArea(a);
    drawElevation(canvas, ev, img);
  });
}

/* ================= reports ================= */
function buildReports(){
  const root = $('#reports'); root.innerHTML = '';
  const t = updateTotals();
  const kpis = document.createElement('div'); kpis.className='kpi-row';
  kpis.innerHTML = '<div class="kpi"><div class="v w">' + fmt(t.total) + '</div><div class="l">Total Areas m\u00b2</div></div>' +
    '<div class="kpi"><div class="v g">' + fmt(t.done) + '</div><div class="l">Completed m\u00b2</div></div>' +
    '<div class="kpi"><div class="v o">' + fmt(t.left) + '</div><div class="l">Remaining m\u00b2</div></div>';
  root.appendChild(kpis);
  const block = function(title){ const d=document.createElement('div'); d.className='report-block';
    d.innerHTML = '<h3>' + esc(title) + '</h3><table><thead></thead><tbody></tbody></table>';
    root.appendChild(d); return d; };
  const fillTable = function(blk, cols, rows){
    const th = blk.querySelector('thead'); th.innerHTML=''; const tr=document.createElement('tr');
    cols.forEach(function(c){ const x=document.createElement('th'); x.textContent=c; tr.appendChild(x); }); th.appendChild(tr);
    const tb = blk.querySelector('tbody'); tb.innerHTML='';
    rows.forEach(function(r){ const trr=document.createElement('tr');
      r.forEach(function(c){ const td=document.createElement('td'); td.textContent=c; trr.appendChild(td); }); tb.appendChild(trr); });
  };
  const seed = areas.filter(function(a){ return !a.geom; });
  if (seed.length){
    const b = block('Automatic take-off (seeded from plan measurements)');
    fillTable(b, ['Description','Substrate','Area m\u00b2','Status'],
      seed.map(function(a){ return [a.notes || a.elevation || a.id, a.substrate, fmt(a.area), a.status]; }));
  }
  const bySub = {};
  areas.forEach(function(a){ bySub[a.substrate] = (bySub[a.substrate]||0) + (a.status==='Paint Excluded'?0:Math.max(parseFloat(a.area)||0,0)); });
  const b1 = block('Substrate take-off summary');
  fillTable(b1, ['Substrate','Area m\u00b2'],
    Object.keys(bySub).sort().map(function(k){ return [subLabel(k), fmt(bySub[k])]; }));
  const byUnit = {};
  areas.forEach(function(a){ const u=a.unit!=null?('Unit '+(a.unit+1)):a.unit_label||'—';
    byUnit[u] = (byUnit[u]||0) + (a.status==='Paint Excluded'?0:Math.max(parseFloat(a.area)||0,0)); });
  const b2 = block('Breakdown by townhouse');
  fillTable(b2, ['Townhouse','Area m\u00b2'],
    Object.keys(byUnit).sort().map(function(k){ return [k, fmt(byUnit[k])]; }));
  const byElev = {};
  areas.forEach(function(a){ const e=a.elevation||'—';
    byElev[e] = (byElev[e]||0) + (a.status==='Paint Excluded'?0:Math.max(parseFloat(a.area)||0,0)); });
  const b3 = block('Breakdown by elevation');
  fillTable(b3, ['Elevation','Area m\u00b2'],
    Object.keys(byElev).sort().map(function(k){ return [k, fmt(byElev[k])]; }));
  const b4 = block('Measurement register');
  fillTable(b4, ['Area ID','Unit','Elevation','Substrate','Area m\u00b2','Status','Progress %','Notes'],
    areas.map(function(a){ return [a.id, a.unit_label||('Unit '+(a.unit+1)), a.elevation||'', subLabel(a.substrate), fmt(a.area), a.status, String(a.progress||0), (a.notes||'').slice(0,40)]; }));
}

/* ================= exports ================= */
function exportCsv(){
  const cols = ['Area ID','Building or unit','Drawing','Elevation','Substrate','Area','Status','Percentage completed','Completed square metres','Remaining square metres','Notes'];
  const rows = [cols];
  areas.forEach(function(a){
    const v = Math.max(parseFloat(a.area)||0,0);
    const p = Math.min(Math.max(parseFloat(a.progress)||0,0),100);
    const done = v*p/100;
    rows.push([a.id, a.unit_label||('Unit '+(a.unit+1)), a.drawing||drawing, a.elevation||'', a.substrate,
      (Math.round(v*100)/100).toFixed(2), a.status, String(p), (Math.round(done*100)/100).toFixed(2),
      (Math.round(Math.max(v-done,0)*100)/100).toFixed(2), (a.notes||'')]);
  });
  const csvText = rows.map(function(r){ return r.map(function(c){
    const s = String(c==null?'':c); return /[",\n]/.test(s) ? '"' + s.replace(/"/g,'""') + '"' : s; }).join(','); }).join('\r\n');
  download(csvText, 'takeoff_' + projectId + '.csv', 'text/csv;charset=utf-8');
  toast('CSV exported (' + areas.length + ' areas).');
}
function exportJson(){
  const payload = { app:'PB PlanRender Takeoff Studio', version:STUDIO.version, project:projectId, drawing:drawing,
    exported:nowStamp(), areas:areas };
  download(JSON.stringify(payload, null, 2), 'takeoff_' + projectId + '.json', 'application/json');
  toast('JSON exported.');
}
function download(content, name, type){
  const blob = new Blob([content], { type: type || 'application/octet-stream' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = name;
  document.body.appendChild(a); a.click();
  setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(url); }, 400);
}
function exportImage(){
  if (activeScreen!=='3d'){ showScreen('3d'); }
  setTimeout(function(){
    renderer.render(scene, camera);
    const url = renderer.domElement.toDataURL('image/png');
    const a = document.createElement('a'); a.href = url; a.download = 'takeoff_3d_' + projectId + '.png';
    document.body.appendChild(a); a.click();
    setTimeout(function(){ document.body.removeChild(a); }, 300);
    toast('Image exported.');
  }, 250);
}
function buildExport(){
  const root = $('#export'); root.innerHTML = '';
  const t = updateTotals();
  const kpis = document.createElement('div'); kpis.className='kpi-row';
  kpis.innerHTML = '<div class="kpi"><div class="v w">' + fmt(t.total) + '</div><div class="l">Total Areas m\u00b2</div></div>' +
    '<div class="kpi"><div class="v g">' + fmt(t.done) + '</div><div class="l">Completed m\u00b2</div></div>' +
    '<div class="kpi"><div class="v o">' + fmt(t.left) + '</div><div class="l">Remaining m\u00b2</div></div>';
  root.appendChild(kpis);
  const cards = document.createElement('div'); cards.className='export-card';
  cards.innerHTML =
    '<div class="card"><h4>CSV</h4><p>Area register with unit, elevation, substrate, status, progress and notes.</p><button class="btn primary" id="eCsv">Export CSV</button></div>' +
    '<div class="card"><h4>JSON</h4><p>Full measurement model including audit history — importable by other tools.</p><button class="btn primary" id="eJson">Export JSON</button></div>' +
    '<div class="card"><h4>Image</h4><p>Current 3D view with all measurement overlays and labels visible.</p><button class="btn primary" id="eImg">Download Image</button></div>' +
    '<div class="card"><h4>Excel / PDF</h4><p>Open the CSV in Excel or generate a PDF report from the Reports screen.</p><p style="color:#8b95a5">Use File &gt; Print in your browser after opening Reports.</p></div>' +
    '<div class="card"><h4>Premier Brushworks JobHub</h4><p>Send the take-off package into JobHub for the job folder and estimating.</p><p style="color:#8b95a5">Connect via the PlanReader "JobHub Sync" page.</p></div>' +
    '<div class="card"><h4>Xero-compatible estimate</h4><p>Estimate quantities by substrate are prepared for export in the JobHub estimating module.</p></div>';
  root.appendChild(cards);
  $('#eCsv').addEventListener('click', exportCsv);
  $('#eJson').addEventListener('click', exportJson);
  $('#eImg').addEventListener('click', exportImage);
}

/* ================= transparency / soffits ================= */
function applyViewMode(){
  const xray = viewMode==='xray';
  regions.forEach(function(r){
    const m = r.material;
    if (xray){ origOpaque.push({ r:r, o:m.opacity }); m.transparent = true; m.opacity = 0.22; m.depthWrite = false; }
    else { m.transparent = false; m.opacity = 1; m.depthWrite = true; }
  });
}
function applySoffits(){
  soffitGroup.visible = showSoffits;
}

/* ================= animation loop ================= */
let animating = true;
function animate(){
  if (!animating){ rafId = null; return; }
  rafId = requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}
function resumeAnim(){ if (!animating){ animating = true; animate(); } }
function pauseAnim(){ animating = false; if (rafId){ cancelAnimationFrame(rafId); rafId = null; } }

/* ================= init ================= */
function init(){
  $('#sub-version').textContent = ' ' + STUDIO.version;
  $('#version').textContent = 'PB PlanRender Takeoff Studio ' + STUDIO.version;
  const pSel = $('#selProject');
  STUDIO.projects.forEach(function(p){ const o=document.createElement('option'); o.value=p.id; o.textContent=p.label; pSel.appendChild(o); });
  pSel.value = projectId;
  const dSel = $('#selDrawing');
  STUDIO.drawings.forEach(function(d){ const o=document.createElement('option'); o.value=d.name; o.textContent=d.name; dSel.appendChild(o); });
  dSel.value = drawing;
  const vSel = $('#selView');
  STUDIO.viewOptions.forEach(function(v){ const o=document.createElement('option'); o.value=v; o.textContent=v; vSel.appendChild(o); });

  // dropdowns
  $('#fSubstrate').innerHTML = STUDIO.substrates.map(function(s){ return '<option value="' + esc(s.code) + '">' + esc(s.code + ' – ' + s.name) + '</option>'; }).join('');
  STUDIO.units.forEach(function(u,i){ const o=document.createElement('option'); o.value=String(i); o.textContent=u.label; $('#fUnit').appendChild(o); });
  const elevSel = $('#fElevation');
  Object.keys(STUDIO.elevationLabels).forEach(function(k){ const o=document.createElement('option'); o.value=STUDIO.elevationLabels[k]; o.textContent=STUDIO.elevationLabels[k]; elevSel.appendChild(o); });
  $('#fStatus').innerHTML = STUDIO.statuses.map(function(s){ return '<option>' + esc(s) + '</option>'; }).join('');

  // nav
  $$('.nav-btn').forEach(function(b){ b.addEventListener('click', function(){ showScreen(b.dataset.screen==='3d'?'3d':b.dataset.screen); }); });
  $('#btnHelp').addEventListener('click', function(){ $('#helpModal').classList.add('open'); });
  $('#btnHelpClose').addEventListener('click', function(){ $('#helpModal').classList.remove('open'); });
  $('#btnSettings').addEventListener('click', function(){ $('#fOperator').value = operatorName(); $('#settingsModal').classList.add('open'); });
  $('#btnSettingsClose').addEventListener('click', function(){
    localStorage.setItem('prts:operator', $('#fOperator').value.trim() || STUDIO.operator);
    $('#settingsModal').classList.remove('open');
  });
  $('#btnResetData').addEventListener('click', function(){
    askConfirm('Reset all measurements for this project/drawing?', function(){
      areas.forEach(function(a){ removeAreaMesh(a); });
      areas = []; localStorage.removeItem(storeKey()); refreshAreaMeshes(); updateTotals(); deselect(); redrawElevations(); toast('Local measurements reset.');
    });
  });
  $('#confirmOk').addEventListener('click', function(){
    $('#confirmModal').classList.remove('open');
    if (confirmCb){ const cb = confirmCb; confirmCb = null; cb(); }
  });
  $('#confirmCancel').addEventListener('click', function(){ $('#confirmModal').classList.remove('open'); confirmCb = null; });

  // tools
  $$('.tool').forEach(function(b){ b.addEventListener('click', function(){ setTool(b.dataset.tool); }); });

  // view mode / soffits
  $$('#viewMode input').forEach(function(inp){ inp.addEventListener('change', function(){ viewMode = inp.value; applyViewMode(); }); });
  $('#showSoffits').addEventListener('change', function(){ showSoffits = this.checked; applySoffits(); });

  // legend / views
  buildLegend(); buildViewButtons();

  // sidebar collapse
  $$('.sb-head').forEach(function(h){ h.addEventListener('click', function(){ h.parentElement.classList.toggle('closed'); }); });

  // panel
  $('#panelClose').addEventListener('click', deselect);
  $('#fProgress').addEventListener('input', updateProgressReadout);
  $('#fArea').addEventListener('input', updateProgressReadout);
  $('#btnUpdate').addEventListener('click', readPanel);
  $('#btnDelete').addEventListener('click', function(){ if (selectedArea) deleteArea(selectedArea); });
  $('#btnCopy').addEventListener('click', copyToTownhouses);

  // exports
  $('#btnCsv').addEventListener('click', exportCsv);
  $('#btnImg').addEventListener('click', exportImage);

  // view select
  vSel.addEventListener('change', function(){ setViewFromOption(vSel.value); });

  // pointer on 3D
  renderer.domElement.addEventListener('pointerdown', onPointerDown);
  renderer.domElement.addEventListener('pointermove', onPointerMove);
  renderer.domElement.addEventListener('pointerup', onPointerUp);
  renderer.domElement.style.cursor = 'grab';

  // resize
  window.addEventListener('resize', function(){
    if (activeScreen!=='3d') return;
    camera.aspect = container.clientWidth/container.clientHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(container.clientWidth, container.clientHeight);
  });

  // camera
  setView('front');
  $$('.view-btn').forEach(function(b){ if (b.dataset.view==='front') b.classList.add('active'); });

  // data
  loadAreas();
  refreshAreaMeshes();
  updateTotals();

  animate();
  toast('PB PlanRender Takeoff Studio loaded. Click a surface to start the take-off.');
}
function setViewFromOption(v){
  if (v.indexOf('3D Model')>-1) return;
  // map generic options onto a sensible camera
  setView('aerial');
}
try { init(); } catch(err){
  $('#glError').style.display = 'flex';
  $('#glError').textContent = 'Failed to start: ' + (err && err.message ? err.message : err);
}
</script>
</body>
</html>"""


def render_planrender_studio_html(studio_data: Dict[str, Any]) -> str:
    """Render the complete studio HTML with the studio data JSON embedded."""
    return _render_template().replace(
        "__STUDIO_JSON__", json.dumps(studio_data, separators=(",", ":"))
    )
