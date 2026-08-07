"""PlanReader external 3D render helpers.

Builds a lightweight 3D model of the *external* building from a PlanReader job:
the envelope footprint, external wall colours, soffits/eaves, fascia trim and
window/door openings lifted from elevation box measurements. The scene is
rendered as an interactive Three.js view with realistic daylight, soft shadows
and orbit controls.

The module is intentionally standalone (no Streamlit, no JobHub guard cascade)
so PlanReader can keep loading it without running JobHub startup code.
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

DEFAULT_WALL_HEIGHT_M = 2.7
DEFAULT_EAVE_DEPTH_M = 0.45
DEFAULT_WALL_THICKNESS_M = 0.15

COMMON_HEX: Dict[str, str] = {
    "white": "#FFFFFF",
    "off white": "#F5F3EC",
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
    "dune": "#D6CDBF",
    "monument": "#4A4A48",
    "vivid white": "#FBFAF6",
    "lexicon": "#EFECE4",
    "snowfield": "#F5F7F6",
    "natural": "#EDE9DE",
    "sky": "#C7D6E0",
    "grey": "#A8A8A8",
    "gray": "#A8A8A8",
    "blue": "#3B6EA5",
    "navy": "#1E3A5F",
    "teal": "#2F6E6E",
    "green": "#5C7A4A",
    "red": "#8C3B3B",
    "brown": "#6E4A2F",
    "timber": "#8B5E3C",
    "wood": "#8B5E3C",
    "slate": "#5B6570",
    "zinc": "#7C8186",
}

WINDOW_KEYWORDS = ("window", "door", "glazing", "frame", "louvre", "garage door", "roller")
FACE_ORDER = ("front", "rear", "left", "right")


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def default_resolve_hex(colour: Any) -> str:
    name = str(colour or "").strip().lower()
    if not name:
        return "#F1EDE4"
    if name.startswith("#") and len(name) == 7:
        return name
    return COMMON_HEX.get(name, "#F1EDE4")


def _schedule_row(
    schedule: List[Dict[str, Any]],
    area_keywords: Tuple[str, ...],
    surface_keywords: Tuple[str, ...],
) -> Optional[Dict[str, Any]]:
    for row in schedule or []:
        area = str(row.get("area_location") or "").strip().lower()
        surface = str(row.get("surface") or "").strip().lower()
        area_ok = any(kw in area for kw in area_keywords) if area_keywords else True
        surface_ok = any(kw in surface for kw in surface_keywords) if surface_keywords else True
        if area_ok and surface_ok:
            return row
    return None


def _row_colour(row: Optional[Dict[str, Any]], resolve_hex: Callable[[Any], str], default_hex: str) -> str:
    if not row:
        return default_hex
    hex_value = str(row.get("hex") or "").strip()
    if len(hex_value) == 7 and hex_value.startswith("#"):
        return hex_value
    return resolve_hex(row.get("colour"))


def _face_from_key(text: Any) -> Optional[str]:
    value = str(text or "").lower()
    if not value:
        return None
    for face in FACE_ORDER:
        if face == "left":
            if "left" in value or "west" in value:
                return "left"
        elif face == "right":
            if "right" in value or "east" in value:
                return "right"
        elif face == "front":
            if "front" in value or "north" in value:
                return "front"
        elif face == "rear":
            if "rear" in value or "south" in value or "back " in value:
                return "rear"
    return None


def _elevation_faces(job: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Assign elevation box zones (windows/doors) to building faces."""
    faces: Dict[str, List[Dict[str, Any]]] = {face: [] for face in FACE_ORDER}
    progress = job.get("elevation_progress") or {}
    for image_path, entry in progress.items():
        face = _face_from_key(str(image_path))
        if face is None:
            continue
        for zone in (entry or {}).get("zones") or []:
            substrate = str(zone.get("substrate") or "").lower()
            label = str(zone.get("label") or "").lower()
            if not any(kw in substrate or kw in label for kw in WINDOW_KEYWORDS):
                continue
            x = _f(zone.get("x"))
            y = _f(zone.get("y"))
            w = _f(zone.get("w"))
            h = _f(zone.get("h"))
            if w <= 0 or h <= 0:
                continue
            faces[face].append({
                "x_frac": (x + w / 2.0) / 100.0,
                "y_frac": (100.0 - (y + h / 2.0)) / 100.0,
                "w_frac": min(max(w / 100.0, 0.02), 0.9),
                "h_frac": min(max(h / 100.0, 0.02), 0.9),
                "area_m2": _f(zone.get("qty_m2")) or _f(zone.get("manual_m2")),
            })
    return faces


def external_scene_data(
    job: Dict[str, Any],
    envelope: Optional[Dict[str, Any]] = None,
    external_info: Optional[Dict[str, Any]] = None,
    resolve_hex: Callable[[Any], str] = default_resolve_hex,
) -> Dict[str, Any]:
    """Build the external 3D scene model from a PlanReader job dict."""
    envelope = envelope or {}
    width = _f(envelope.get("envelope_w_m"))
    depth = _f(envelope.get("envelope_h_m"))
    if width <= 0 or depth <= 0:
        width = 12.0
        depth = 9.0

    wall_height = _f((external_info or {}).get("wall_height_m"))
    if wall_height <= 0:
        wall_height = _f((job.get("external_settings") or {}).get("wall_height_m"))
    if wall_height <= 0:
        wall_height = DEFAULT_WALL_HEIGHT_M

    wall_thickness = _f((external_info or {}).get("wall_thickness_m"))
    if wall_thickness <= 0:
        wall_thickness = _f((job.get("external_settings") or {}).get("wall_thickness_m"))
    if wall_thickness <= 0:
        wall_thickness = DEFAULT_WALL_THICKNESS_M

    eave_depth = _f((external_info or {}).get("eave_depth_m"))
    if eave_depth <= 0:
        eave_depth = _f((job.get("external_settings") or {}).get("eave_depth_m"))
    if eave_depth <= 0:
        eave_depth = DEFAULT_EAVE_DEPTH_M

    schedule = job.get("colour_schedule") or []

    wall_row = _schedule_row(
        schedule,
        ("external",),
        ("wall", "render", "cladding", "weatherboard", "hebel", "colourbond"),
    )
    wall_colour = _row_colour(wall_row, resolve_hex, "#E7D7C7")

    soffit_row = _schedule_row(schedule, ("soffit", "eave"), ())
    soffit_colour = _row_colour(soffit_row, resolve_hex, "#E5E7EB")

    trim_row = _schedule_row(schedule, ("fascia", "trim", "gutter"), ())
    trim_colour = _row_colour(trim_row, resolve_hex, "#D1D5DB")

    roof_row = _schedule_row(schedule, ("roof",), ())
    roof_colour = _row_colour(roof_row, resolve_hex, "#5B6570")

    faces = _elevation_faces(job)

    openings_area = _f((external_info or {}).get("openings_m2"))
    if openings_area <= 0:
        openings_area = sum(z.get("area_m2") or 0 for face in faces.values() for z in face)

    palette: Dict[str, str] = {}
    for name, hex_value in (
        (wall_row.get("colour") if wall_row else None, wall_colour),
        (soffit_row.get("colour") if soffit_row else None, soffit_colour),
        (trim_row.get("colour") if trim_row else None, trim_colour),
        (roof_row.get("colour") if roof_row else None, roof_colour),
    ):
        if name and str(name).strip():
            palette[str(name).strip()] = hex_value

    return {
        "envelope": {
            "w": round(width, 2),
            "d": round(depth, 2),
            "h": round(wall_height, 2),
            "t": round(wall_thickness, 3),
            "perimeter_m": round(_f(envelope.get("perimeter_m"), 2 * (width + depth)), 2),
            "method": str(envelope.get("method") or "none"),
            "note": str(envelope.get("note") or ""),
        },
        "eave": {"depth": round(eave_depth, 2), "colour": soffit_colour},
        "walls": {"colour": wall_colour},
        "trim": {"colour": trim_colour},
        "roof": {"colour": roof_colour},
        "openings": {
            face: [
                {
                    "x_frac": round(z["x_frac"], 4),
                    "y_frac": round(z["y_frac"], 4),
                    "w_frac": round(z["w_frac"], 4),
                    "h_frac": round(z["h_frac"], 4),
                }
                for z in zones
            ]
            for face, zones in faces.items()
        },
        "palette": [
            {"name": name, "hex": hex_value}
            for name, hex_value in palette.items()
        ],
        "summary": {
            "perimeter_m": round(_f(envelope.get("perimeter_m"), 2 * (width + depth)), 2),
            "wall_height_m": round(wall_height, 2),
            "gross_walls_m2": round(max(2 * (width + depth) * wall_height, 0), 2),
            "openings_m2": round(openings_area, 2),
            "net_walls_m2": round(max(2 * (width + depth) * wall_height - openings_area, 0), 2),
            "soffits_m2": round(max(2 * (width + depth + 2 * eave_depth) * eave_depth, 0), 2),
            "method": str(envelope.get("method") or "none"),
        },
    }


def _render_html_template() -> str:
    return r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>External 3D Render</title>
<style>
  html, body { margin:0; padding:0; height:100%; overflow:hidden; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; }
  #viewer { position:absolute; inset:0; }
  #panel { position:absolute; top:14px; left:14px; z-index:5; max-width:300px; background:rgba(15,23,42,.82); color:#eef2f7; border-radius:12px; padding:12px 14px; font-size:13px; line-height:1.45; box-shadow:0 8px 24px rgba(0,0,0,.28); backdrop-filter:blur(4px); }
  #panel h2 { margin:0 0 4px; font-size:15px; color:#fff; }
  #panel .note { color:#94a3b8; font-size:12px; margin:0 0 8px; }
  #panel .metric { display:flex; justify-content:space-between; padding:2px 0; color:#cbd5e1; }
  #panel .metric b { color:#f8fafc; font-weight:600; }
  #legend { margin-top:8px; border-top:1px solid rgba(255,255,255,.14); padding-top:8px; }
  .swatch { display:flex; align-items:center; gap:8px; padding:2px 0; }
  .dot { width:14px; height:14px; border-radius:4px; border:1px solid rgba(255,255,255,.25); flex:0 0 auto; }
  #hint { position:absolute; bottom:14px; left:14px; z-index:5; color:#cbd5e1; font-size:12px; background:rgba(15,23,42,.6); padding:6px 10px; border-radius:8px; }
  .btn { position:absolute; right:14px; bottom:14px; z-index:6; border:0; border-radius:8px; padding:8px 14px; cursor:pointer; font-size:13px; font-weight:600; background:#2563eb; color:#fff; box-shadow:0 6px 16px rgba(0,0,0,.3); }
  .btn:hover { background:#1d4ed8; }
</style>
</head>
<body>
<div id="viewer"></div>
<div id="panel">
  <h2>External Render</h2>
  <p class="note" id="note"></p>
  <div class="metric"><span>Footprint</span><b id="mFoot"></b></div>
  <div class="metric"><span>Wall Height</span><b id="mHeight"></b></div>
  <div class="metric"><span>Gross Walls</span><b id="mGross"></b></div>
  <div class="metric"><span>Openings</span><b id="mOpen"></b></div>
  <div class="metric"><span>Net Walls</span><b id="mNet"></b></div>
  <div class="metric"><span>Soffits / Eaves</span><b id="mSoffit"></b></div>
  <div id="legend"></div>
</div>
<div id="hint">Drag to orbit &middot; scroll to zoom &middot; right-drag to pan</div>
<button class="btn" id="reset">Reset view</button>
<script src="https://cdn.jsdelivr.net/npm/three@0.124.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.124.0/examples/js/controls/OrbitControls.js"></script>
<script>
const S = __SCENE_JSON__;
const viewer = document.getElementById('viewer');
const W = S.envelope.w, D = S.envelope.d, H = S.envelope.h, T = S.envelope.t;
const E = S.eave.depth;
const halfW = W/2, halfD = D/2;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x9fc5e8);
scene.fog = new THREE.Fog(0x9fc5e8, 90, 220);

const camera = new THREE.PerspectiveCamera(45, viewer.clientWidth/viewer.clientHeight, 0.1, 800);
const renderer = new THREE.WebGLRenderer({ antialias:true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(viewer.clientWidth, viewer.clientHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputEncoding = THREE.sRGBEncoding;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.12;
viewer.appendChild(renderer.domElement);

const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.07;
controls.maxPolarAngle = Math.PI/2.02;

const radius = Math.max(W, D)/2 + H;
function resetView(){
  camera.position.set(radius*1.15, radius*0.85 + H, radius*1.45);
  controls.target.set(0, H*0.5, 0);
  controls.minDistance = radius*0.5;
  controls.maxDistance = radius*4;
  controls.update();
}
resetView();
document.getElementById('reset').onclick = resetView;

const hemi = new THREE.HemisphereLight(0xcfe4ff, 0x7d735f, 0.85);
scene.add(hemi);
const sun = new THREE.DirectionalLight(0xfff3d6, 1.5);
sun.position.set(radius*1.4, radius*2.2, radius*1.1);
sun.castShadow = true;
sun.shadow.mapSize.width = 2048; sun.shadow.mapSize.height = 2048;
const sh = radius*2.2;
sun.shadow.camera.left = -sh; sun.shadow.camera.right = sh;
sun.shadow.camera.top = sh; sun.shadow.camera.bottom = -sh;
sun.shadow.camera.far = radius*8;
sun.shadow.bias = -0.0005;
scene.add(sun);
scene.add(sun.target);
const fill = new THREE.DirectionalLight(0xbcd3ff, 0.35);
fill.position.set(-radius*1.4, radius*0.6, -radius*1.2);
scene.add(fill);

const groundMat = new THREE.MeshStandardMaterial({ color:0x7d9468, roughness:1, metalness:0 });
const ground = new THREE.Mesh(new THREE.PlaneGeometry(radius*12, radius*12), groundMat);
ground.rotation.x = -Math.PI/2;
ground.position.y = -0.02;
ground.receiveShadow = true;
scene.add(ground);

function box(w, h, d, color, opts){
  opts = opts || {};
  const mat = new THREE.MeshStandardMaterial({ color:color, roughness: opts.rough!=null?opts.rough:0.65, metalness: opts.metalness!=null?opts.metalness:0.05 });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w,h,d), mat);
  mesh.castShadow = opts.cast!==false;
  mesh.receiveShadow = opts.receive!==false;
  scene.add(mesh);
  if(opts.edge){
    const edge = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), new THREE.LineBasicMaterial({ color:0x101828, transparent:true, opacity:0.28 }));
    scene.add(edge);
    mesh.userData.edge = edge;
  }
  return mesh;
}

function hexToJs(c){ const n = parseInt(c.replace('#',''), 16); return n; }

const pad = box(W+0.8, 0.14, D+0.8, 0x9ca3af, { rough:0.85, receive:true });
pad.position.set(0, 0.05, 0);

const wallMat = new THREE.MeshStandardMaterial({ color:hexToJs(S.walls.colour), roughness:0.72, metalness:0.03 });
const glassMat = new THREE.MeshStandardMaterial({ color:0x1f2937, roughness:0.12, metalness:0.5 });

function addWall(w,h,d,x,y,z){
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w,h,d), wallMat);
  mesh.position.set(x,y,z);
  mesh.castShadow = true; mesh.receiveShadow = true;
  scene.add(mesh);
  const edge = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), new THREE.LineBasicMaterial({ color:0x101828, transparent:true, opacity:0.25 }));
  edge.position.copy(mesh.position);
  scene.add(edge);
}

// Four external walls (each drawn as 3 slabs so openings don't cut through).
function buildWallFace(face){
  const panels = [];
  if(face === 'front' || face === 'rear'){
    const z = face === 'front' ? halfD : -halfD;
    addWall(W, H, T, 0, H/2, z);
  } else {
    const x = face === 'right' ? halfW : -halfW;
    addWall(T, H, D, x, H/2, 0);
  }
}
buildWallFace('front'); buildWallFace('rear'); buildWallFace('left'); buildWallFace('right');

// Window / door openings.
function addOpening(face, o){
  let w, h, cx, cz, cy, depth;
  const wFrac = o.w_frac, hFrac = o.h_frac;
  if(face === 'front' || face === 'rear'){
    w = Math.max(W*wFrac, 0.3);
    h = Math.max(H*hFrac, 0.3);
    depth = T*2.6;
    cx = (o.x_frac - 0.5) * W;
    cz = face === 'front' ? halfD + 0.02 : -halfD - 0.02;
    cy = Math.min(o.y_frac, 1) * H - h/2 + 0.4;
  } else {
    w = Math.max(D*wFrac, 0.3);
    h = Math.max(H*hFrac, 0.3);
    depth = T*2.6;
    cx = face === 'right' ? halfW + 0.02 : -halfW - 0.02;
    cz = (o.x_frac - 0.5) * D;
    cy = Math.min(o.y_frac, 1) * H - h/2 + 0.4;
  }
  cy = Math.max(cy, h/2);
  const mesh = box(w, h, depth, 0x1f2937, { rough:0.12, metalness:0.55 });
  mesh.position.set(cx, cy, cz);
  const sill = box(w*1.06, 0.08, depth*1.15, hexToJs(S.trim.colour), { rough:0.5, cast:false });
  sill.position.set(cx, Math.max(cy - h/2 - 0.05, 0.05), cz);
  const frame = box(w*1.08, 0.09, depth*1.1, hexToJs(S.trim.colour), { rough:0.5, cast:false });
  frame.position.set(cx, cy + h/2 + 0.02, cz);
}

S.openings.front.forEach(o=>addOpening('front', o));
S.openings.rear.forEach(o=>addOpening('rear', o));
S.openings.left.forEach(o=>addOpening('left', o));
S.openings.right.forEach(o=>addOpening('right', o));

// Soffit / eave slab with a subtle underside.
const soffit = box(W+E*2, 0.12, D+E*2, hexToJs(S.eave.colour), { rough:0.6 });
soffit.position.set(0, H + 0.06, 0);

// Fascia trim around the top edge.
function fascia(x, z, w, d, rotY){
  const m = box(w, 0.16, d, hexToJs(S.trim.colour), { rough:0.45 });
  m.position.set(x, H + 0.18, z);
  m.rotation.y = rotY || 0;
  return m;
}
fascia(0, halfD+E, W+E*2+0.2, 0.18);        // front
fascia(0, -halfD-E, W+E*2+0.2, 0.18);       // rear
fascia(halfW+E, 0, 0.18, D+E*2+0.2, 0);     // right
fascia(-halfW-E, 0, 0.18, D+E*2+0.2, 0);    // left

// Flat roof slab for a finished silhouette.
const roof = box(W+E*2+0.5, 0.22, D+E*2+0.5, hexToJs(S.roof.colour), { rough:0.85, receive:true });
roof.position.set(0, H + 0.3, 0);

// Face labels.
function makeLabel(text){
  const c = document.createElement('canvas'); c.width=512; c.height=96;
  const ctx=c.getContext('2d');
  ctx.fillStyle='rgba(15,23,42,0.68)';
  const r=16; ctx.beginPath(); ctx.moveTo(r,0); ctx.arcTo(512,0,512,96,0); ctx.arcTo(512,96,0,96,0); ctx.arcTo(0,96,0,0,0); ctx.arcTo(0,0,512,0,r); ctx.closePath(); ctx.fill();
  ctx.fillStyle='#ffffff'; ctx.font='bold 48px system-ui, sans-serif'; ctx.textAlign='center'; ctx.textBaseline='middle';
  ctx.fillText(text, 256, 48);
  const tex=new THREE.CanvasTexture(c); tex.minFilter=THREE.LinearFilter;
  const sp=new THREE.Sprite(new THREE.SpriteMaterial({ map:tex, transparent:true, depthWrite:false }));
  sp.scale.set(4.6, 0.86, 1);
  return sp;
}
function placeLabel(text, x, y, z){
  const sp = makeLabel(text);
  sp.position.set(x, y, z);
  scene.add(sp);
}
placeLabel('Front', 0, H*0.55, halfD+E+1.1);
placeLabel('Rear', 0, H*0.55, -halfD-E-1.1);
placeLabel('Left', -halfW-E-1.1, H*0.55, 0);
placeLabel('Right', halfW+E+1.1, H*0.55, 0);

// Metrics panel.
document.getElementById('note').innerText = S.envelope.method === 'none'
  ? 'No reliable plan scale found - envelope is a placeholder rectangle.'
  : (S.envelope.note || 'External envelope from plan measurements.');
document.getElementById('mFoot').innerText = W.toFixed(1) + ' x ' + D.toFixed(1) + ' m';
document.getElementById('mHeight').innerText = H.toFixed(2) + ' m';
document.getElementById('mGross').innerText = S.summary.gross_walls_m2.toFixed(1) + ' m2';
document.getElementById('mOpen').innerText = S.summary.openings_m2.toFixed(1) + ' m2';
document.getElementById('mNet').innerText = S.summary.net_walls_m2.toFixed(1) + ' m2';
document.getElementById('mSoffit').innerText = S.summary.soffits_m2.toFixed(1) + ' m2';

const legend = document.getElementById('legend');
(S.palette.length ? S.palette : [{name:'External walls', hex:S.walls.colour}, {name:'Soffits / eaves', hex:S.eave.colour}, {name:'Fascia / trim', hex:S.trim.colour}, {name:'Roof', hex:S.roof.colour}])
  .forEach(function(p){
    const row = document.createElement('div'); row.className='swatch';
    const dot = document.createElement('div'); dot.className='dot'; dot.style.background = p.hex;
    const name = document.createElement('span'); name.innerText = p.name;
    row.appendChild(dot); row.appendChild(name); legend.appendChild(row);
  });

function onResize(){
  camera.aspect = viewer.clientWidth/viewer.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(viewer.clientWidth, viewer.clientHeight);
}
window.addEventListener('resize', onResize);

function animate(){ requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); }
animate();
</script>
</body>
</html>"""


def render_planreader_3d_html(scene: Dict[str, Any]) -> str:
    """Render the Three.js HTML for an external scene model."""
    return _render_html_template().replace(
        "__SCENE_JSON__", json.dumps(scene, separators=(",", ":"))
    )
