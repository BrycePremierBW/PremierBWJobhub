"""3D building models and drawing progress mapping.

Generated from the previously working JobHub monolith.
"""

from __future__ import annotations

from .runtime import *


def render_progress_3d_model(display_sections, selected_ids, key_prefix="progress_3d_model"):
    """Render a polished dark-dashboard 3D progress model from the progress/take-off sections.

    This keeps the practical JobHub data model but presents it in a cleaner dashboard style:
    large central 3D view, right selected-section panel, progress summary, legend, floor selector,
    quick actions and model controls.
    """
    if display_sections is None or display_sections.empty:
        st.info("No progress sections to render yet. Create/import take-off lines, then generate the progress model.")
        return

    model_rows = []
    model_df = display_sections.copy().head(260)
    selected_set = {int(x) for x in selected_ids if str(x).isdigit() or isinstance(x, int)}

    for idx, (_, row) in enumerate(model_df.iterrows()):
        section_id = int(row.get("ID") or 0)
        total_m2 = app_float(row.get("Total m2"))
        completed_m2 = app_float(row.get("Completed m2"))
        remaining_m2 = app_float(row.get("Remaining m2"))
        completed_pct = app_float(row.get("Completed %"))
        value = app_float(row.get("Section Value Ex GST"))
        billable_value = app_float(row.get("Billable Value Ex GST"))
        remaining_value = app_float(row.get("Remaining Value Ex GST"))
        labour_hours = app_float(row.get("Total Labour Hours"))
        remaining_labour = app_float(row.get("Remaining Labour Hours"))
        paint_litres = app_float(row.get("Total Paint Litres"))
        remaining_paint = app_float(row.get("Remaining Paint Litres"))
        status = str(row.get("Status") or "Not Started")
        area = str(row.get("Area") or "")
        location = str(row.get("Location / Area") or "")
        substrate = str(row.get("Substrate") or "")
        labour = str(row.get("Labour Category") or "")
        lower_text = f"{area} {location} {substrate} {labour}".lower()
        if "roof" in lower_text or "ceiling" in lower_text or "soffit" in lower_text or "eave" in lower_text:
            floor = "Roof"
        elif "level 2" in lower_text or "upper" in lower_text or "first" in lower_text or "level 1" in lower_text:
            floor = "Level 2"
        elif "ground" in lower_text or "external" in lower_text or "front" in lower_text or "rear" in lower_text:
            floor = "Ground"
        else:
            floor = "Level 1"
        model_rows.append({
            "id": section_id,
            "index": idx,
            "code": str(row.get("Section Code") or f"S-{section_id}"),
            "area": area,
            "location": location,
            "substrate": substrate,
            "labour": labour,
            "status": status,
            "floor": floor,
            "m2": round(total_m2, 2),
            "completed_m2": round(completed_m2, 2),
            "remaining_m2": round(remaining_m2, 2),
            "completed_pct": round(completed_pct, 2),
            "value": round(value, 2),
            "billable_value": round(billable_value, 2),
            "remaining_value": round(remaining_value, 2),
            "labour_hours": round(labour_hours, 2),
            "remaining_labour": round(remaining_labour, 2),
            "paint_litres": round(paint_litres, 2),
            "remaining_paint": round(remaining_paint, 2),
            "selected": section_id in selected_set,
        })

    selected_rows = [r for r in model_rows if r["selected"]]
    source_for_summary = selected_rows if selected_rows else model_rows
    total_area = sum(r["m2"] for r in model_rows)
    total_completed_area = sum(r["completed_m2"] for r in model_rows)
    overall_completion = (total_completed_area / total_area * 100) if total_area else 0
    complete_rows = [r for r in model_rows if r["completed_pct"] >= 99.5 or "complete" in r["status"].lower()]
    progress_rows = [r for r in model_rows if 0 < r["completed_pct"] < 99.5 or "progress" in r["status"].lower()]
    not_started_rows = [r for r in model_rows if r not in complete_rows and r not in progress_rows]
    summary = {
        "selected_count": len(selected_rows),
        "shown_count": len(model_rows),
        "m2": round(sum(r["m2"] for r in source_for_summary), 2),
        "value": round(sum(r["value"] for r in source_for_summary), 2),
        "billable": round(sum(r["billable_value"] for r in source_for_summary), 2),
        "labour": round(sum(r["labour_hours"] for r in source_for_summary), 2),
        "paint": round(sum(r["paint_litres"] for r in source_for_summary), 2),
        "total_area": round(total_area, 2),
        "total_value": round(sum(r["value"] for r in model_rows), 2),
        "total_billable": round(sum(r["billable_value"] for r in model_rows), 2),
        "total_labour": round(sum(r["labour_hours"] for r in model_rows), 2),
        "total_paint": round(sum(r["paint_litres"] for r in model_rows), 2),
        "overall_completion": round(overall_completion, 1),
        "complete_count": len(complete_rows),
        "progress_count": len(progress_rows),
        "not_started_count": len(not_started_rows),
    }

    data_json = json.dumps(model_rows)
    selected_json = json.dumps(list(selected_set))
    summary_json = json.dumps(summary)

    st.markdown("### 3D Progress Model")
    st.caption("Dark dashboard style: click sections, rotate/zoom the model, and use the JobHub controls above to save progress changes.")

    html_template = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  * { box-sizing:border-box; }
  html, body { margin:0; padding:0; overflow:hidden; font-family: Inter, Arial, Helvetica, sans-serif; background:#05070a; color:#f8fafc; }
  #app { height:900px; width:100%; background: radial-gradient(circle at 40% 12%, #1f2937 0%, #0b1118 42%, #05070a 100%); border-radius:20px; overflow:hidden; border:1px solid rgba(148,163,184,.18); display:grid; grid-template-columns: 184px 1fr; }
  #nav { background:linear-gradient(180deg,#0b1118,#05070a); border-right:1px solid rgba(148,163,184,.18); padding:18px 12px; position:relative; }
  #brand { display:flex; align-items:center; gap:10px; margin-bottom:22px; }
  #pb { width:36px; height:36px; border-radius:50%; display:grid; place-items:center; font-weight:900; color:#fff; background:#2563eb; box-shadow:0 0 0 4px rgba(37,99,235,.18); }
  #brandText strong { font-size:17px; display:block; line-height:1; }
  #brandText span { font-size:10px; color:#94a3b8; }
  .navItem { display:flex; align-items:center; gap:10px; padding:10px 10px; border-radius:9px; color:#dbeafe; font-size:12px; margin:2px 0; }
  .navItem.active { background:#2563eb; color:#fff; box-shadow:0 8px 24px rgba(37,99,235,.30); }
  .navDivider { height:1px; background:rgba(148,163,184,.14); margin:12px 6px; }
  #navFoot { position:absolute; left:14px; right:14px; bottom:16px; display:flex; align-items:center; gap:10px; font-size:11px; color:#cbd5e1; }
  #main { display:grid; grid-template-rows:52px 1fr; min-width:0; }
  #top { height:52px; display:flex; align-items:center; justify-content:space-between; padding:0 20px; background:#05070a; border-bottom:1px solid rgba(148,163,184,.14); }
  #jobTitle { font-size:15px; letter-spacing:.1px; color:#e5e7eb; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  #backBtn { border:1px solid rgba(148,163,184,.24); background:#0b1118; color:#e5e7eb; padding:8px 12px; border-radius:8px; font-size:12px; }
  #content { padding:18px 18px 16px; display:grid; grid-template-columns:minmax(560px, 1fr) 280px; gap:16px; min-height:0; }
  #leftCol { display:grid; grid-template-rows: 42px 1fr 230px; gap:14px; min-width:0; min-height:0; }
  #toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; }
  #toolbar h2 { margin:0; font-size:18px; line-height:1; }
  #toolbar small { color:#94a3b8; font-size:11px; display:block; margin-top:6px; }
  .tabs { display:flex; border:1px solid rgba(148,163,184,.22); border-radius:9px; overflow:hidden; background:#0b1118; }
  .tab { padding:10px 13px; font-size:11px; color:#e5e7eb; border-right:1px solid rgba(148,163,184,.18); }
  .tab.active { background:#2563eb; color:#fff; }
  #viewerCard { position:relative; min-height:0; border-radius:14px; overflow:hidden; background:linear-gradient(145deg,#202936,#0f1720); border:1px solid rgba(148,163,184,.20); box-shadow:0 24px 70px rgba(0,0,0,.34); }
  #viewer { position:absolute; inset:0; }
  #help { position:absolute; bottom:14px; left:50%; transform:translateX(-50%); display:flex; gap:12px; align-items:center; background:rgba(5,7,10,.72); backdrop-filter:blur(12px); border:1px solid rgba(148,163,184,.18); border-radius:10px; padding:9px 14px; color:#e5e7eb; font-size:11px; box-shadow:0 12px 30px rgba(0,0,0,.28); }
  #viewerControls { position:absolute; top:14px; right:14px; display:flex; flex-direction:column; gap:8px; }
  .miniBtn { width:34px; height:34px; border-radius:9px; display:grid; place-items:center; background:rgba(5,7,10,.70); border:1px solid rgba(148,163,184,.18); color:#e5e7eb; cursor:pointer; }
  #sectionNumbersToggle { position:absolute; left:14px; top:14px; background:rgba(5,7,10,.70); border:1px solid rgba(148,163,184,.18); border-radius:9px; padding:8px 10px; font-size:11px; color:#cbd5e1; }
  #bottomCards { display:grid; grid-template-columns: 1fr 1.75fr 1.55fr 1.35fr; gap:14px; min-height:0; }
  .card { background:linear-gradient(180deg,rgba(15,23,32,.92),rgba(8,13,18,.96)); border:1px solid rgba(148,163,184,.18); border-radius:13px; padding:14px; box-shadow:0 16px 40px rgba(0,0,0,.20); min-height:0; }
  .card h3 { margin:0 0 13px; font-size:14px; }
  .legendRow { display:flex; align-items:center; gap:10px; margin:13px 0; font-size:12px; color:#e5e7eb; }
  .swatch { width:21px; height:21px; border-radius:5px; display:inline-block; box-shadow:inset 0 0 0 1px rgba(255,255,255,.16); }
  #floorGrid { display:grid; grid-template-columns:130px 1fr; gap:12px; align-items:center; }
  #floorStack { height:150px; position:relative; perspective:420px; }
  .floorPlate { position:absolute; left:16px; width:90px; height:55px; background:rgba(148,163,184,.30); border:1px solid rgba(226,232,240,.18); transform:rotateX(62deg) rotateZ(-25deg); border-radius:7px; }
  .floorPlate.roof { top:8px; background:rgba(22,163,74,.65); }
  .floorPlate.l2 { top:43px; background:rgba(37,99,235,.60); }
  .floorPlate.l1 { top:77px; background:rgba(245,158,11,.48); }
  .floorPlate.ground { top:111px; background:rgba(148,163,184,.40); }
  .floorBtn { display:block; width:100%; padding:10px; margin:6px 0; border-radius:9px; border:1px solid rgba(148,163,184,.16); background:rgba(255,255,255,.05); color:#e5e7eb; text-align:left; font-size:12px; cursor:pointer; }
  .floorBtn.active { background:rgba(37,99,235,.38); border-color:rgba(59,130,246,.75); }
  .action { display:block; width:100%; padding:10px 12px; margin:7px 0; border-radius:9px; background:rgba(255,255,255,.06); border:1px solid rgba(148,163,184,.14); color:#e5e7eb; font-size:12px; text-align:left; }
  .toggleRow { display:flex; justify-content:space-between; align-items:center; margin:15px 0; font-size:12px; color:#e5e7eb; }
  .toggle { width:36px; height:18px; border-radius:999px; background:#2563eb; position:relative; box-shadow:0 0 0 3px rgba(37,99,235,.15); }
  .toggle:after { content:''; position:absolute; right:2px; top:2px; width:14px; height:14px; border-radius:50%; background:white; }
  #rightCol { display:grid; grid-template-rows: 346px 1fr; gap:16px; min-height:0; }
  #selectedCard { overflow:auto; }
  #selectedSwatch { width:42px; height:42px; border-radius:9px; background:linear-gradient(135deg,#3b82f6,#2563eb); box-shadow:0 10px 26px rgba(37,99,235,.32); flex:0 0 auto; }
  .selectedHeader { display:flex; align-items:center; gap:11px; padding-bottom:12px; border-bottom:1px solid rgba(148,163,184,.16); }
  #selectedTitle { font-size:13px; font-weight:800; line-height:1.25; color:#fff; }
  #selectedSub { font-size:11px; color:#94a3b8; margin-top:3px; }
  .detailRow { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:9px 0; border-bottom:1px solid rgba(148,163,184,.10); font-size:12px; }
  .detailRow span:first-child { color:#cbd5e1; }
  .detailRow span:last-child { color:#fff; font-weight:700; text-align:right; }
  .primaryBtn { width:100%; margin-top:12px; padding:11px 12px; border:0; border-radius:9px; background:#2563eb; color:#fff; font-size:12px; font-weight:800; }
  #donutWrap { display:grid; grid-template-columns:82px 1fr; gap:12px; align-items:center; margin-bottom:14px; }
  #donut { width:82px; height:82px; border-radius:50%; background:conic-gradient(#16a34a 0deg, #16a34a var(--completeDeg), #f59e0b var(--completeDeg), #f59e0b var(--progressDeg), #d1d5db var(--progressDeg), #d1d5db 360deg); position:relative; }
  #donut:after { content:''; position:absolute; inset:24px; border-radius:50%; background:#0b1118; }
  .progressItem { font-size:11px; color:#cbd5e1; margin:7px 0; }
  .totals { border-top:1px solid rgba(148,163,184,.16); padding-top:12px; margin-top:10px; }
  .totalRow { display:flex; justify-content:space-between; font-size:12px; padding:6px 0; color:#e5e7eb; }
  #bar { height:9px; background:rgba(148,163,184,.16); border-radius:999px; overflow:hidden; margin-top:8px; }
  #bar > div { height:100%; background:#16a34a; width:0%; border-radius:999px; transition:.25s; }
  .numLabel { position:absolute; padding:2px 6px; border-radius:999px; background:rgba(5,7,10,.82); color:#fff; font-weight:900; font-size:10px; pointer-events:none; transform:translate(-50%,-50%); border:1px solid rgba(255,255,255,.14); }
</style>
</head>
<body>
<div id="app">
  <aside id="nav">
    <div id="brand"><div id="pb">PB</div><div id="brandText"><strong>JobHub</strong><span>Painting Progress</span></div></div>
    <div class="navItem">⌂ Dashboard</div>
    <div class="navItem">▣ Job Folders</div>
    <div class="navItem active">◇ 3D Progress Model</div>
    <div class="navItem">▦ Take-off</div>
    <div class="navItem">▤ Areas & Quantities</div>
    <div class="navItem">◉ Paint System</div>
    <div class="navItem">▱ Progress / Billing</div>
    <div class="navDivider"></div>
    <div class="navItem">◷ Scheduling</div>
    <div class="navItem">◴ Timesheets</div>
    <div class="navItem">♙ Wages</div>
    <div class="navItem">□ Photos</div>
    <div class="navItem">▧ Forms</div>
    <div class="navItem">▥ Reports</div>
    <div class="navDivider"></div>
    <div class="navItem">⚙ Settings</div>
    <div id="navFoot"><div id="pb" style="width:31px;height:31px;font-size:12px;">PB</div><div><b>Premier Brushworks</b><br>QBCC 15592041</div></div>
  </aside>
  <main id="main">
    <div id="top"><div id="jobTitle">Job: <b>Current Job</b> &nbsp; | &nbsp; 3D Painting Progress Model</div><button id="backBtn">↩ Back to Job</button></div>
    <section id="content">
      <div id="leftCol">
        <div id="toolbar"><div><h2>3D Progress Model <span style="font-size:12px;color:#64748b;">ⓘ</span></h2><small>Interactive 3D model showing painting progress by substrate/area. Click sections to see details.</small></div><div class="tabs"><div class="tab active">◇ 3D View</div><div class="tab">▣ Floor Plan</div><div class="tab">⬡ Isometric</div></div></div>
        <div id="viewerCard"><div id="viewer"></div><div id="sectionNumbersToggle">Section numbers: on</div><div id="viewerControls"><div class="miniBtn" id="resetCamera">☷</div><div class="miniBtn" id="zoomAll">⤢</div></div><div id="help"><span>🖱 Left click: Select</span><span>⟲ Drag: Rotate</span><span>☌ Scroll: Zoom</span><span>✋ Right click: Pan</span></div></div>
        <div id="bottomCards">
          <div class="card"><h3>Progress Legend</h3><div class="legendRow"><span class="swatch" style="background:#16a34a"></span>Complete</div><div class="legendRow"><span class="swatch" style="background:#f59e0b"></span>In Progress</div><div class="legendRow"><span class="swatch" style="background:#3b82f6"></span>Selected</div><div class="legendRow"><span class="swatch" style="background:#d1d5db"></span>Not Started</div></div>
          <div class="card"><h3 style="text-align:center;">Floor Selector</h3><div id="floorGrid"><div id="floorStack"><div class="floorPlate roof"></div><div class="floorPlate l2"></div><div class="floorPlate l1"></div><div class="floorPlate ground"></div></div><div><button class="floorBtn" data-floor="Roof">● Roof</button><button class="floorBtn active" data-floor="All">● All Levels</button><button class="floorBtn" data-floor="Level 2">● Level 2</button><button class="floorBtn" data-floor="Level 1">● Level 1</button><button class="floorBtn" data-floor="Ground">● Ground</button></div></div></div>
          <div class="card"><h3>Quick Actions</h3><button class="action">✓ Select All Complete</button><button class="action">⊗ Clear Selection</button><button class="action">▤ Generate Progress Claim</button><button class="action">⇩ Export 3D View</button></div>
          <div class="card"><h3>Model Controls</h3><div class="toggleRow"><span>Show Section Numbers</span><span class="toggle"></span></div><div class="toggleRow"><span>Show Area Labels</span><span class="toggle"></span></div><div class="toggleRow"><span>Show Substrate Colours</span><span class="toggle"></span></div><button class="action" id="resetViewAction" style="text-align:center;">↻ Reset View</button></div>
        </div>
      </div>
      <aside id="rightCol">
        <div class="card" id="selectedCard">
          <div class="selectedHeader"><div id="selectedSwatch"></div><div><div id="selectedTitle">Selected Section</div><div id="selectedSub">Click a section in the model</div></div></div>
          <div id="selectedDetails"></div>
          <button class="primaryBtn">↗ View Section Take-off</button>
        </div>
        <div class="card" id="summaryCard"><h3>Progress Summary</h3><div id="donutWrap"><div id="donut"></div><div><div class="progressItem"><span style="color:#16a34a">●</span> Complete: <b id="completeLabel">0%</b></div><div class="progressItem"><span style="color:#f59e0b">●</span> In Progress: <b id="progressLabel">0 sections</b></div><div class="progressItem"><span style="color:#d1d5db">●</span> Not Started: <b id="notStartedLabel">0 sections</b></div></div></div><div class="totals"><div class="totalRow"><span>Total Area</span><b id="totalArea">0m²</b></div><div class="totalRow"><span>Total Billable</span><b id="totalBillable">$0</b></div><div class="totalRow"><span>Total Labour</span><b id="totalLabour">0h</b></div><div class="totalRow"><span>Total Paint</span><b id="totalPaint">0L</b></div><div style="margin-top:12px;font-size:12px;color:#e5e7eb;">Overall Completion <span style="float:right" id="overallText">0%</span></div><div id="bar"><div></div></div></div></div>
      </aside>
    </section>
  </main>
</div>
<script src="https://cdn.jsdelivr.net/npm/three@0.124.0/build/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.124.0/examples/js/controls/OrbitControls.js"></script>
<script>
const sections = __DATA_JSON__;
const selectedIds = new Set(__SELECTED_JSON__);
const summary = __SUMMARY_JSON__;
let activeFloor = 'All';
let showNumbers = true;
const viewer = document.getElementById('viewer');
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x222b36);
const camera = new THREE.PerspectiveCamera(42, viewer.clientWidth / viewer.clientHeight, 0.1, 1200);
camera.position.set(15, 11, 18);
const renderer = new THREE.WebGLRenderer({ antialias:true, alpha:false });
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.setSize(viewer.clientWidth, viewer.clientHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
viewer.appendChild(renderer.domElement);
const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.06;
controls.target.set(0, 2, 0);
controls.minDistance = 6;
controls.maxDistance = 55;
const hemi = new THREE.HemisphereLight(0xffffff, 0x1f2937, 0.72);
scene.add(hemi);
const key = new THREE.DirectionalLight(0xffffff, 1.05);
key.position.set(9, 18, 13);
key.castShadow = true;
key.shadow.mapSize.width = 2048; key.shadow.mapSize.height = 2048;
scene.add(key);
const fill = new THREE.DirectionalLight(0x93c5fd, 0.32); fill.position.set(-9,8,-10); scene.add(fill);
const baseGeo = new THREE.BoxGeometry(19, .22, 14);
const baseMat = new THREE.MeshStandardMaterial({ color:0x6b7280, roughness:.78, metalness:.02 });
const base = new THREE.Mesh(baseGeo, baseMat); base.position.set(0,-.13,0); base.receiveShadow = true; scene.add(base);
const pathMat = new THREE.MeshStandardMaterial({ color:0x9ca3af, roughness:.9 });
function addBox(name,x,y,z,w,h,d, color, opacity=1) { const mat = new THREE.MeshStandardMaterial({ color, transparent: opacity<1, opacity, roughness:.58, metalness:.03 }); const geo = new THREE.BoxGeometry(w,h,d); const mesh = new THREE.Mesh(geo, mat); mesh.position.set(x,y,z); mesh.castShadow=true; mesh.receiveShadow=true; mesh.name=name; scene.add(mesh); return mesh; }
addBox('concrete pad',0,0.03,0,18.2,.12,13.2,0xa3a3a3,.50);
addBox('glazing front',-2,2.0,-6.65,5.4,3.6,.12,0x111827,.42);
addBox('glazing corner',4.9,2.0,-4.7,.12,3.6,3.5,0x111827,.42);
addBox('entry path',0,0.03,-8.0,4,.08,2.2,0x71717a,.72);
addBox('landscape l',-9.3,0.05,0,0.9,.1,13.3,0x166534,.65);
addBox('landscape f',0,0.05,-7.35,18.5,.1,.6,0x166534,.65);
const meshes = [];
const labelEls = [];
function fmtMoney(n){ return '$' + Number(n||0).toLocaleString(undefined,{maximumFractionDigits:0}); }
function pct(n){ return Number(n||0).toFixed(0) + '%'; }
function statusColor(s){ const status=String(s.status||'').toLowerCase(); if(s.selected)return 0x3b82f6; if(Number(s.completed_pct||0)>=99.5 || status.includes('complete')) return 0x16a34a; if(Number(s.completed_pct||0)>0 || status.includes('progress')) return 0xf59e0b; return 0xd1d5db; }
function statusTextColor(s){ const status=String(s.status||'').toLowerCase(); if(s.selected)return '#3b82f6'; if(Number(s.completed_pct||0)>=99.5 || status.includes('complete')) return '#16a34a'; if(Number(s.completed_pct||0)>0 || status.includes('progress')) return '#f59e0b'; return '#d1d5db'; }
function dimsFor(s){ const m2=Math.max(Number(s.m2||0),1); const t=String((s.substrate||'')+' '+(s.labour||'')+' '+(s.area||'')+' '+(s.location||'')).toLowerCase(); let w=Math.max(1.2, Math.min(4.2, Math.sqrt(m2)*0.46)); let h=2.7; let d=.20; if(t.includes('ceiling')||t.includes('roof')||t.includes('soffit')||t.includes('eave')){ w=Math.max(2.0, Math.min(5.4, Math.sqrt(m2)*0.58)); d=Math.max(1.6, Math.min(4.8, Math.sqrt(m2)*0.45)); h=.16; } else if(t.includes('door')||t.includes('frame')||t.includes('skirting')||t.includes('wood')||t.includes('timber')){ w=Math.max(.45, Math.min(1.2, Math.sqrt(m2)*0.22)); h=t.includes('skirting')?.22:2.1; d=.16; } return {w,h,d}; }
function positionFor(s,i){ const t=String((s.area||'')+' '+(s.location||'')+' '+(s.substrate||'')+' '+(s.labour||'')).toLowerCase(); const external=t.includes('external')||t.includes('front')||t.includes('rear')||t.includes('left')||t.includes('right')||t.includes('elevation'); const roof=s.floor==='Roof'; const level2=s.floor==='Level 2'; const level1=s.floor==='Level 1'; const ground=s.floor==='Ground'; const floorY = roof ? 6.3 : level2 ? 4.15 : level1 ? 2.05 : 1.15; const dim=dimsFor(s); let x=0,z=0,rot=0; const faceIndex=i%18; if(roof){ x=(faceIndex%4-1.5)*3.7; z=(Math.floor(faceIndex/4)-1)*3.2; return {x,y:6.05,z,rotX:0,rotY:0,rotZ:0}; } if(external){ const side=i%4; const slot=Math.floor(i/4)%5 - 2; if(side===0){ x=slot*3.1; z=-6.6; rot=0; } if(side===1){ x=slot*3.1; z=6.6; rot=0; } if(side===2){ x=-8.9; z=slot*2.6; rot=Math.PI/2; } if(side===3){ x=8.9; z=slot*2.6; rot=Math.PI/2; } return {x,y:floorY,z,rotX:0,rotY:rot,rotZ:0}; }
 const cols=4; x=((i%cols)-1.5)*3.2; z=(Math.floor(i/cols)%3-1)*2.35; return {x,y:floorY,z,rotX:0,rotY:(i%2)*Math.PI/2,rotZ:0}; }
function createLabel(text){ const el=document.createElement('div'); el.className='numLabel'; el.innerText=text; document.getElementById('viewerCard').appendChild(el); return el; }
sections.forEach((s,i)=>{ const dim=dimsFor(s); const geo=new THREE.BoxGeometry(dim.w,dim.h,dim.d); const mat=new THREE.MeshStandardMaterial({ color:statusColor(s), transparent:true, opacity:s.selected?.94:.82, roughness:.52, metalness:.02 }); const mesh=new THREE.Mesh(geo,mat); const p=positionFor(s,i); mesh.position.set(p.x,p.y,p.z); mesh.rotation.y=p.rotY||0; mesh.castShadow=true; mesh.receiveShadow=true; mesh.userData=s; scene.add(mesh); const edge=new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({color:0x020617,transparent:true,opacity:.35})); edge.position.copy(mesh.position); edge.rotation.copy(mesh.rotation); scene.add(edge); mesh.userData.edge=edge; meshes.push(mesh); const label=createLabel(String(i+1)); mesh.userData.label=label; labelEls.push(label); });
function sectionTitle(s){ return (s.location || s.code || 'Selected Section'); }
function showSection(s){ document.getElementById('selectedSwatch').style.background = s.selected ? 'linear-gradient(135deg,#60a5fa,#2563eb)' : statusTextColor(s); document.getElementById('selectedTitle').innerText = sectionTitle(s); document.getElementById('selectedSub').innerText = `${s.substrate || 'Substrate'} — ${s.labour || 'Paint System'}`; document.getElementById('selectedDetails').innerHTML = `<div class="detailRow"><span>Area</span><span>${Number(s.m2||0).toLocaleString(undefined,{maximumFractionDigits:2})} m²</span></div><div class="detailRow"><span>Billable Value</span><span>${fmtMoney(s.billable_value)}</span></div><div class="detailRow"><span>Section Value</span><span>${fmtMoney(s.value)}</span></div><div class="detailRow"><span>Labour Hours</span><span>${Number(s.labour_hours||0).toFixed(2)} hrs</span></div><div class="detailRow"><span>Paint Required</span><span>${Number(s.paint_litres||0).toFixed(1)} L</span></div><div class="detailRow"><span>Completion</span><span>${pct(s.completed_pct)}</span></div><div class="detailRow"><span>Status</span><span style="color:${statusTextColor(s)}">● ${s.status || 'Not Started'}</span></div><div class="detailRow"><span>Floor</span><span>${s.floor || 'All'}</span></div>`; }
function updateSummary(){ const c=summary.complete_count||0, p=summary.progress_count||0, n=summary.not_started_count||0, total=Math.max(c+p+n,1); const cDeg=(c/total*360); const pDeg=((c+p)/total*360); document.getElementById('donut').style.setProperty('--completeDeg', cDeg+'deg'); document.getElementById('donut').style.setProperty('--progressDeg', pDeg+'deg'); document.getElementById('completeLabel').innerText = `${Math.round(c/total*100)}% (${c} sections)`; document.getElementById('progressLabel').innerText = `${p} sections`; document.getElementById('notStartedLabel').innerText = `${n} sections`; document.getElementById('totalArea').innerText = Number(summary.total_area||0).toLocaleString(undefined,{maximumFractionDigits:2}) + ' m²'; document.getElementById('totalBillable').innerText = fmtMoney(summary.total_billable); document.getElementById('totalLabour').innerText = Number(summary.total_labour||0).toLocaleString(undefined,{maximumFractionDigits:2}) + ' hrs'; document.getElementById('totalPaint').innerText = Number(summary.total_paint||0).toLocaleString(undefined,{maximumFractionDigits:1}) + ' L'; document.getElementById('overallText').innerText = pct(summary.overall_completion); document.querySelector('#bar > div').style.width = Math.max(0,Math.min(100,Number(summary.overall_completion||0))) + '%'; }
function setFloor(floor){ activeFloor=floor; document.querySelectorAll('.floorBtn').forEach(b=>b.classList.toggle('active', b.dataset.floor===floor)); meshes.forEach(m=>{ const show=floor==='All' || m.userData.floor===floor; m.visible=show; if(m.userData.edge)m.userData.edge.visible=show; if(m.userData.label)m.userData.label.style.display=(show && showNumbers)?'block':'none'; }); }
document.querySelectorAll('.floorBtn').forEach(b=>b.onclick=()=>setFloor(b.dataset.floor));
document.getElementById('sectionNumbersToggle').onclick=()=>{ showNumbers=!showNumbers; document.getElementById('sectionNumbersToggle').innerText='Section numbers: '+(showNumbers?'on':'off'); meshes.forEach(m=>{ if(m.userData.label)m.userData.label.style.display=(m.visible&&showNumbers)?'block':'none'; }); };
const raycaster=new THREE.Raycaster(); const mouse=new THREE.Vector2(); let last=null;
renderer.domElement.addEventListener('click',(ev)=>{ const rect=renderer.domElement.getBoundingClientRect(); mouse.x=((ev.clientX-rect.left)/rect.width)*2-1; mouse.y=-((ev.clientY-rect.top)/rect.height)*2+1; raycaster.setFromCamera(mouse,camera); const hits=raycaster.intersectObjects(meshes.filter(m=>m.visible),false); if(hits.length){ if(last){ last.scale.set(1,1,1); } const m=hits[0].object; m.scale.set(1.06,1.06,1.06); last=m; showSection(m.userData); }});
function resetView(){ camera.position.set(15,11,18); controls.target.set(0,2,0); controls.update(); }
document.getElementById('resetCamera').onclick=resetView; document.getElementById('resetViewAction').onclick=resetView; document.getElementById('zoomAll').onclick=()=>{ camera.position.set(20,15,24); controls.target.set(0,2,0); controls.update(); };
function updateLabels(){ meshes.forEach((m,i)=>{ const el=m.userData.label; if(!el) return; if(!m.visible || !showNumbers){ el.style.display='none'; return; } const v=m.position.clone(); v.y += dimsFor(m.userData).h/2 + .15; v.project(camera); const x=(v.x*.5+.5)*viewer.clientWidth + 184 + 18; const y=(-v.y*.5+.5)*viewer.clientHeight + 52 + 18; el.style.left=x+'px'; el.style.top=y+'px'; }); }
window.addEventListener('resize',()=>{ camera.aspect=viewer.clientWidth/viewer.clientHeight; camera.updateProjectionMatrix(); renderer.setSize(viewer.clientWidth,viewer.clientHeight); });
updateSummary(); if(sections.length) showSection(sections.find(s=>s.selected)||sections[0]); setFloor('All');
function animate(){ requestAnimationFrame(animate); controls.update(); updateLabels(); renderer.render(scene,camera); } animate();
</script>
</body>
</html>
"""
    html_doc = (
        html_template
        .replace("__DATA_JSON__", data_json)
        .replace("__SELECTED_JSON__", selected_json)
        .replace("__SUMMARY_JSON__", summary_json)
    )
    st.iframe(html_doc, height=930)

def building_surface_colour(substrate="", labour_category="", area_type=""):
    s = f"{substrate} {labour_category} {area_type}".lower()
    if "ceiling" in s:
        return "#f8fafc"
    if "door" in s or "frame" in s or "wood" in s or "timber" in s or "skirting" in s:
        return "#8b5e3c"
    if "feature" in s or "dark" in s:
        return "#475569"
    if "render" in s or "hebel" in s:
        return "#d6c8b8"
    if "cladding" in s or "weatherboard" in s:
        return "#cbd5e1"
    if "soffit" in s or "eave" in s:
        return "#e5e7eb"
    if "external" in s:
        return "#e7d7c7"
    return "#fffaf2"

def infer_building_elevation(area_type="", location_area="", substrate="", labour_category="", index=0):
    text_value = f"{area_type} {location_area} {substrate} {labour_category}".lower()
    if "front" in text_value or "north" in text_value:
        return "Front"
    if "rear" in text_value or "back" in text_value or "south" in text_value:
        return "Rear"
    if "left" in text_value or "west" in text_value:
        return "Left"
    if "right" in text_value or "east" in text_value:
        return "Right"
    if "ceiling" in text_value or "soffit" in text_value or "eave" in text_value or "roof" in text_value:
        return "Ceiling / Roof"
    if "external" in text_value:
        return ["Front", "Right", "Rear", "Left"][index % 4]
    return "Internal"

def infer_surface_type(substrate="", labour_category="", area_type=""):
    text_value = f"{substrate} {labour_category} {area_type}".lower()
    if "ceiling" in text_value:
        return "Ceiling"
    if "soffit" in text_value or "eave" in text_value:
        return "Soffit / Eave"
    if "door" in text_value or "frame" in text_value or "window" in text_value or "wood" in text_value or "timber" in text_value or "skirting" in text_value:
        return "Woodwork / Frames"
    if "feature" in text_value:
        return "Feature"
    if "external" in text_value or "render" in text_value or "cladding" in text_value or "hebel" in text_value:
        return "External Wall"
    return "Internal Wall"

def default_building_surface_dimensions(total_m2, surface_type):
    m2_value = max(app_float(total_m2), 0.5)
    stype = str(surface_type or "").lower()
    if "ceiling" in stype or "soffit" in stype or "eave" in stype:
        width = max(1.2, min(6.0, m2_value ** 0.5))
        depth = max(1.2, min(5.5, m2_value / width if width else 1.5))
        return round(width, 2), 0.12, round(depth, 2)
    if "wood" in stype or "frame" in stype:
        return max(0.45, min(1.3, (m2_value ** 0.5) * 0.32)), 2.1, 0.12
    height = 3.0 if "external" in stype else 2.7
    width = max(0.85, min(5.8, m2_value / height))
    return round(width, 2), round(height, 2), 0.14

def building_model_surfaces_df(job_id, package_id=None):
    params = [job_id]
    where = "WHERE b.job_id = ?"
    if package_id:
        where += " AND b.package_id = ?"
        params.append(package_id)
    df = df_query(f"""
        SELECT b.id AS "ID",
               b.job_id AS "Job ID",
               b.package_id AS "Package ID",
               b.progress_section_id AS "Progress Section ID",
               b.takeoff_line_id AS "Takeoff Line ID",
               b.section_code AS "Section Code",
               b.surface_name AS "Surface Name",
               b.surface_type AS "Surface Type",
               b.elevation AS "Elevation",
               b.level_name AS "Level",
               b.x_pos AS "X",
               b.y_pos AS "Y",
               b.z_pos AS "Z",
               b.width AS "Width",
               b.height AS "Height",
               b.depth AS "Depth",
               b.rotation_y AS "Rotation Y",
               b.colour_hex AS "Colour",
               ps.area_type AS "Area",
               ps.location_area AS "Location / Area",
               ps.substrate AS "Substrate",
               ps.labour_category AS "Labour Category",
               ps.total_m2 AS "Total m2",
               ps.completed_m2 AS "Completed m2",
               ps.completed_percent AS "Completed %",
               ps.allocated_value_ex_gst AS "Section Value Ex GST",
               (ps.allocated_value_ex_gst * ps.completed_percent / 100.0) AS "Billable Value Ex GST",
               tl.labour_hours AS "Total Labour Hours",
               tl.paint_litres AS "Total Paint Litres",
               ps.status AS "Status",
               b.notes AS "Notes",
               b.updated_at AS "Updated At"
        FROM building_model_surfaces b
        LEFT JOIN painting_progress_sections ps ON ps.id = b.progress_section_id
        LEFT JOIN painting_takeoff_lines tl ON tl.id = b.takeoff_line_id
        {where}
        ORDER BY b.elevation, b.level_name, b.id
    """, tuple(params))
    numeric_cols = ["X", "Y", "Z", "Width", "Height", "Depth", "Rotation Y", "Total m2", "Completed m2", "Completed %", "Section Value Ex GST", "Billable Value Ex GST", "Total Labour Hours", "Total Paint Litres"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df

def generate_building_surfaces_from_takeoff(job_id, package_id=None, reset_existing=True):
    if not package_id:
        package_id = latest_takeoff_package_for_job(job_id)
    if not package_id:
        return 0
    ensure_progress_sections_for_package(package_id, reset_values=False)
    if reset_existing:
        execute("DELETE FROM building_model_surfaces WHERE job_id = ? AND package_id = ?", (job_id, package_id))
    else:
        existing = building_model_surfaces_df(job_id, package_id)
        if not existing.empty:
            return len(existing)

    sections = progress_sections_df(job_id, package_id)
    if sections.empty:
        return 0

    elevation_counts = {"Front": 0, "Rear": 0, "Left": 0, "Right": 0, "Internal": 0, "Ceiling / Roof": 0}
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    created_count = 0

    for idx, (_, row) in enumerate(sections.iterrows()):
        elevation = infer_building_elevation(row.get("Area"), row.get("Location / Area"), row.get("Substrate"), row.get("Labour Category"), idx)
        surface_type = infer_surface_type(row.get("Substrate"), row.get("Labour Category"), row.get("Area"))
        width, height, depth = default_building_surface_dimensions(row.get("Total m2"), surface_type)
        count = elevation_counts.get(elevation, 0)
        elevation_counts[elevation] = count + 1
        # Keep automatically generated sections in a horizontal row by default.
        # Only the plan-shaped mapper should create upper levels based on real Level 1/Level 2 wording.
        per_row = 10
        level_index = 0
        pos_index = count % per_row
        row_index = count // per_row
        level_name = "Ground"
        y_base = 0.05
        rotation_y = 0.0

        if elevation == "Front":
            x_pos, z_pos, y_pos = -10.0 + pos_index * 2.25, -4.2 + row_index * 0.18, y_base + height / 2
        elif elevation == "Rear":
            x_pos, z_pos, y_pos = 10.0 - pos_index * 2.25, 4.2 - row_index * 0.18, y_base + height / 2
        elif elevation == "Left":
            x_pos, z_pos, y_pos, rotation_y = -6.2 - row_index * 0.18, -4.5 + pos_index * 1.0, y_base + height / 2, 1.5708
        elif elevation == "Right":
            x_pos, z_pos, y_pos, rotation_y = 6.2 + row_index * 0.18, 4.5 - pos_index * 1.0, y_base + height / 2, 1.5708
        elif elevation == "Ceiling / Roof":
            x_pos, z_pos, y_pos = -8.0 + pos_index * 1.8, -1.8 + row_index * 1.0, 2.85
        else:
            x_pos, z_pos, y_pos = -8.0 + pos_index * 1.8, -1.4 + row_index * 0.8, y_base + height / 2

        execute("""
            INSERT INTO building_model_surfaces
            (job_id, package_id, progress_section_id, takeoff_line_id, section_code, surface_name,
             surface_type, elevation, level_name, x_pos, y_pos, z_pos, width, height, depth,
             rotation_y, colour_hex, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, package_id, int(row.get("ID") or 0),
            int(row.get("Takeoff Line ID") or 0) if app_float(row.get("Takeoff Line ID")) else None,
            str(row.get("Section Code") or f"S-{idx+1:03d}"), str(row.get("Location / Area") or f"Section {idx+1}"),
            surface_type, elevation, level_name, float(x_pos), float(y_pos), float(z_pos),
            float(width), float(height), float(depth), float(rotation_y),
            building_surface_colour(row.get("Substrate"), row.get("Labour Category"), row.get("Area")),
            "Auto-generated from painting take-off/progress model. Adjust in 3D Building Mapper if required.",
            now_text, now_text,
        ))
        created_count += 1
    return created_count

def mapper_level_index_from_text(text_value, max_levels=2):
    t = str(text_value or "").lower()
    # Important: do NOT treat townhouse/unit numbers as storeys.
    # Previous versions saw text like "Unit 5" and pushed it upward as Level 1,
    # which made the 3D mapper build a tower instead of a row.
    if any(x in t for x in ["level 3", "lvl 3", "third floor", "3rd floor", "upper 2"]):
        return min(2, max_levels - 1)
    if any(x in t for x in ["level 2", "lvl 2", "second floor", "2nd floor"]):
        return min(1, max_levels - 1)
    if any(x in t for x in ["level 1", "lvl 1", "first floor", "1st floor", "upper floor"]):
        return min(1, max_levels - 1)
    return 0

def mapper_unit_index_from_text(text_value):
    """Return a zero-based townhouse/unit index if the section name contains Unit/Villa/Dwelling text."""
    import re
    t = str(text_value or "").lower()
    match = re.search(r"\b(?:unit|villa|dwelling|townhouse|lot)\s*([0-9]{1,2})\b", t)
    if not match:
        return None
    try:
        return max(int(match.group(1)) - 1, 0)
    except Exception:
        return None

def mapper_level_name(level_index):
    if int(level_index or 0) <= 0:
        return "Ground"
    if int(level_index or 0) == 1:
        return "Level 1"
    return f"Level {int(level_index or 0)}"

def mapper_wall_position(elevation, cursor, width, building_length, building_depth, level_index, level_height):
    """Return x, y, z, rotation for a wall segment along the selected elevation."""
    building_length = max(float(building_length or 12), 2.0)
    building_depth = max(float(building_depth or 8), 2.0)
    level_height = max(float(level_height or 2.7), 2.1)
    usable_front = building_length * 0.92
    usable_side = building_depth * 0.92
    width = max(float(width or 1.0), 0.2)
    row_gap = 0.18
    stack = int(cursor // 1) if cursor > 99999 else 0
    # cursor is actual running length; wrap if a facade row is filled
    elev = str(elevation or "Internal")
    if elev in ["Front", "Rear"]:
        usable = usable_front
        wrap_index = int(cursor // max(usable, 1))
        local_cursor = cursor % max(usable, 1)
        x = -usable / 2 + min(local_cursor + width / 2, usable - width / 2)
        z = -building_depth / 2 if elev == "Front" else building_depth / 2
        y = level_index * level_height + level_height / 2 + wrap_index * row_gap
        rot = 0.0
        return x, y, z, rot
    if elev in ["Left", "Right"]:
        usable = usable_side
        wrap_index = int(cursor // max(usable, 1))
        local_cursor = cursor % max(usable, 1)
        z = -usable / 2 + min(local_cursor + width / 2, usable - width / 2)
        x = -building_length / 2 if elev == "Left" else building_length / 2
        y = level_index * level_height + level_height / 2 + wrap_index * row_gap
        rot = 1.5708
        return x, y, z, rot
    if elev == "Ceiling / Roof":
        usable = building_length * 0.82
        local_cursor = cursor % max(usable, 1)
        x = -usable / 2 + min(local_cursor + width / 2, usable - width / 2)
        z = -building_depth * 0.22 + (int(cursor // max(usable, 1)) * max(0.7, building_depth * 0.18))
        y = (level_index + 1) * level_height + 0.08
        rot = 0.0
        return x, y, z, rot
    # Internal surfaces are placed as internal partitions inside the footprint.
    usable = building_length * 0.72
    local_cursor = cursor % max(usable, 1)
    row = int(cursor // max(usable, 1))
    x = -usable / 2 + min(local_cursor + width / 2, usable - width / 2)
    z = -building_depth * 0.25 + row * max(0.75, building_depth * 0.16)
    y = level_index * level_height + level_height / 2
    rot = 0.0 if row % 2 == 0 else 1.5708
    return x, y, z, rot

def generate_plan_shape_surfaces_from_takeoff(job_id, package_id=None, building_length=18.0, building_depth=9.0, level_count=2, level_height=2.7, template="Rectangular building", roof_style="Flat roof", reset_existing=True):
    """Create a more plan-faithful 3D progress model by placing take-off sections around a real footprint size."""
    if not package_id:
        package_id = latest_takeoff_package_for_job(job_id)
    if not package_id:
        return 0
    ensure_progress_sections_for_package(package_id, reset_values=False)
    if reset_existing:
        execute("DELETE FROM building_model_surfaces WHERE job_id = ? AND package_id = ?", (job_id, package_id))
    sections = progress_sections_df(job_id, package_id)
    if sections.empty:
        return 0

    building_length = max(app_float(building_length), 2.0)
    building_depth = max(app_float(building_depth), 2.0)
    level_count = max(int(app_float(level_count) or 1), 1)
    level_height = max(app_float(level_height), 2.1)
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursors = {}
    created = 0

    for idx, (_, row) in enumerate(sections.iterrows()):
        text_value = " ".join([str(row.get(c) or "") for c in ["Area", "Location / Area", "Substrate", "Labour Category", "Section Code"]])
        elevation = infer_building_elevation(row.get("Area"), row.get("Location / Area"), row.get("Substrate"), row.get("Labour Category"), idx)
        surface_type = infer_surface_type(row.get("Substrate"), row.get("Labour Category"), row.get("Area"))
        level_index = mapper_level_index_from_text(text_value, level_count)
        level_name = mapper_level_name(level_index)
        m2_value = max(app_float(row.get("Total m2")), 0.25)

        if "Ceiling" in surface_type or "Soffit" in surface_type:
            height = 0.10
            width = min(max((m2_value ** 0.5), 1.0), max(building_length * 0.75, 1.0))
            depth = min(max(m2_value / max(width, 0.1), 0.55), max(building_depth * 0.55, 0.55))
            elevation = "Ceiling / Roof"
        elif "Woodwork" in surface_type:
            height = min(level_height * 0.82, 2.2)
            width = min(max(m2_value / max(height, 0.1), 0.35), 1.2)
            depth = 0.10
        else:
            height = min(max(default_building_surface_dimensions(m2_value, surface_type)[1], 2.4), level_height * 0.96)
            width = max(m2_value / max(height, 0.1), 0.45)
            max_width = building_length * 0.92 if elevation in ["Front", "Rear", "Internal", "Ceiling / Roof"] else building_depth * 0.92
            width = min(width, max(max_width, 0.6))
            depth = 0.12

        key = (elevation, level_index)
        cursor = cursors.get(key, 0.0)
        x_pos, y_pos, z_pos, rotation_y = mapper_wall_position(elevation, cursor, width, building_length, building_depth, level_index, level_height)
        cursors[key] = cursor + max(width, 0.4) + 0.12

        # Plan templates add slight realistic offsets so the model doesn't look like a single flat box.
        template_lower = str(template or "").lower()
        if "townhouse" in template_lower and elevation in ["Front", "Rear"]:
            # If a section name contains Unit/Villa/Dwelling numbers, place those units
            # horizontally across the frontage instead of letting them stack upward.
            unit_index = mapper_unit_index_from_text(text_value)
            likely_units = max(3, min(12, int(building_length // 4.5) or 3))
            bay_width = building_length / max(likely_units, 1)
            if unit_index is not None:
                bay = min(unit_index, likely_units - 1)
                x_pos = -building_length / 2 + bay_width * bay + bay_width / 2
                width = min(max(width, 0.45), max(bay_width * 0.84, 0.45))
            else:
                bay = int((x_pos + building_length / 2) // max(bay_width, 0.1))
            z_pos += (-0.25 if bay % 2 else 0.25) if elevation == "Front" else (0.25 if bay % 2 else -0.25)
        if "l-shape" in template_lower and x_pos > building_length * 0.10 and z_pos > 0:
            z_pos -= building_depth * 0.18
        if "switchgear" in template_lower:
            # Long simple service building: keep roof low, make frontage long and clean.
            if elevation == "Ceiling / Roof":
                y_pos = level_height + 0.10

        execute("""
            INSERT INTO building_model_surfaces
            (job_id, package_id, progress_section_id, takeoff_line_id, section_code, surface_name,
             surface_type, elevation, level_name, x_pos, y_pos, z_pos, width, height, depth,
             rotation_y, colour_hex, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, package_id, int(row.get("ID") or 0),
            int(row.get("Takeoff Line ID") or 0) if app_float(row.get("Takeoff Line ID")) else None,
            str(row.get("Section Code") or f"S-{idx+1:03d}"), str(row.get("Location / Area") or f"Section {idx+1}"),
            surface_type, elevation, level_name, float(x_pos), float(y_pos), float(z_pos),
            float(max(width, 0.08)), float(max(height, 0.08)), float(max(depth, 0.04)), float(rotation_y),
            building_surface_colour(row.get("Substrate"), row.get("Labour Category"), row.get("Area")),
            f"Plan-shaped model generated using {template}; approx footprint {building_length:g}m x {building_depth:g}m, {level_count} level(s), {roof_style}.",
            now_text, now_text,
        ))
        created += 1
    return created

def render_building_mapper_3d(surface_df, selected_progress_ids=None, key_prefix="building_mapper_3d"):
    if surface_df is None or surface_df.empty:
        st.info("No 3D building surfaces have been mapped yet. Generate a building-shaped model from the take-off first.")
        return
    selected_set = {int(x) for x in (selected_progress_ids or []) if str(x).isdigit() or isinstance(x, int)}
    rows = []
    for idx, (_, row) in enumerate(surface_df.head(320).iterrows()):
        progress_id = int(row.get("Progress Section ID") or 0)
        status = str(row.get("Status") or "Not Started")
        completed_pct = app_float(row.get("Completed %"))
        rows.append({
            "id": int(row.get("ID") or 0), "progress_id": progress_id,
            "code": str(row.get("Section Code") or f"M-{idx+1:03d}"),
            "name": str(row.get("Surface Name") or row.get("Location / Area") or "Surface"),
            "surface_type": str(row.get("Surface Type") or "Surface"), "elevation": str(row.get("Elevation") or "Internal"),
            "level": str(row.get("Level") or "Ground"), "area": str(row.get("Area") or ""),
            "substrate": str(row.get("Substrate") or ""), "labour": str(row.get("Labour Category") or ""),
            "x": round(app_float(row.get("X")), 3), "y": round(app_float(row.get("Y")), 3), "z": round(app_float(row.get("Z")), 3),
            "w": max(round(app_float(row.get("Width")), 3), 0.08), "h": max(round(app_float(row.get("Height")), 3), 0.08),
            "d": max(round(app_float(row.get("Depth")), 3), 0.04), "rotY": round(app_float(row.get("Rotation Y")), 4),
            "colour": str(row.get("Colour") or "#fffaf2"), "m2": round(app_float(row.get("Total m2")), 2),
            "completed_pct": round(completed_pct, 2), "value": round(app_float(row.get("Section Value Ex GST")), 2),
            "billable": round(app_float(row.get("Billable Value Ex GST")), 2),
            "labour_hours": round(app_float(row.get("Total Labour Hours")), 2), "paint_litres": round(app_float(row.get("Total Paint Litres")), 2),
            "status": status, "selected": progress_id in selected_set,
        })
    data_json = json.dumps(rows)
    st.markdown("### Building-Shaped 3D Progress Render")
    st.caption("Drag to rotate, scroll to zoom and click a surface. Green is complete, orange is in progress and blue is selected in JobHub.")
    html_doc = f"""
<!DOCTYPE html><html><head><meta charset="utf-8" />
<style>
html,body{{margin:0;padding:0;overflow:hidden;font-family:Arial,Helvetica,sans-serif;background:#f6f1ea;}}
#wrap{{display:flex;height:720px;width:100%;background:linear-gradient(180deg,#f7f2ec 0%,#ede4d9 100%);border-radius:18px;overflow:hidden;border:1px solid #d6c8b8;}}
#viewer{{flex:1;position:relative;min-width:0;}}#side{{width:350px;background:rgba(255,255,255,.96);border-left:1px solid #d6c8b8;padding:14px;overflow:auto;box-sizing:border-box;}}
.title{{font-weight:900;color:#111827;font-size:17px;line-height:1.2;margin-bottom:6px;}}.hint{{color:#4b5563;font-size:12px;line-height:1.35;margin-bottom:12px;}}
.metricGrid{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;}}.metric{{background:#f8fafc;border:1px solid #e5e7eb;border-radius:12px;padding:8px;}}.metric .label{{color:#6b7280;font-size:11px;}}.metric .value{{color:#111827;font-size:16px;font-weight:900;margin-top:2px;}}
#info{{background:#111827;color:#fff;border-radius:14px;padding:12px;margin-top:10px;min-height:160px;box-shadow:0 10px 25px rgba(17,24,39,.20);}}#info .small{{color:#d1d5db;font-size:12px;margin-top:4px;}}
#legend{{position:absolute;left:14px;bottom:14px;background:rgba(255,255,255,.92);border:1px solid #e5e7eb;border-radius:13px;padding:8px 10px;font-size:12px;color:#111827;box-shadow:0 10px 30px rgba(0,0,0,.12);}}.dot{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;vertical-align:middle;}}
#topbar{{position:absolute;left:14px;top:14px;background:rgba(17,24,39,.88);color:#fff;border-radius:14px;padding:10px 12px;font-size:12px;max-width:390px;box-shadow:0 10px 30px rgba(0,0,0,.22);}}.sectionRow{{border:1px solid #e5e7eb;border-radius:11px;padding:8px;margin-bottom:6px;cursor:pointer;background:#fff;}}.sectionRow:hover{{background:#eff6ff;border-color:#93c5fd;}}.sectionCode{{font-size:12px;font-weight:800;color:#111827;}}.sectionMeta{{font-size:11px;color:#4b5563;margin-top:2px;}}
</style></head><body><div id="wrap"><div id="viewer"><div id="topbar"><strong>PB 3D Building Mapper</strong><br>Plan-shaped progress model. Enter the plan footprint size, then adjust surfaces to match the drawings.</div><div id="legend"><span class="dot" style="background:#2563eb"></span>Selected&nbsp;&nbsp;<span class="dot" style="background:#16a34a"></span>Complete&nbsp;&nbsp;<span class="dot" style="background:#f59e0b"></span>In progress&nbsp;&nbsp;<span class="dot" style="background:#d1d5db"></span>Not started</div></div><div id="side"><div class="title">Building progress projection</div><div class="hint">Click a mapped surface to inspect m², value, labour and paint.</div><div class="metricGrid"><div class="metric"><div class="label">Surfaces</div><div class="value" id="metricSections">0</div></div><div class="metric"><div class="label">m²</div><div class="value" id="metricM2">0</div></div><div class="metric"><div class="label">Value</div><div class="value" id="metricValue">$0</div></div><div class="metric"><div class="label">Billable</div><div class="value" id="metricBillable">$0</div></div><div class="metric"><div class="label">Labour</div><div class="value" id="metricLabour">0h</div></div><div class="metric"><div class="label">Paint</div><div class="value" id="metricPaint">0L</div></div></div><div id="info"><strong>Click a mapped surface</strong><div class="small">Surface details will show here.</div></div><div class="title" style="margin-top:14px;font-size:14px;">Mapped surfaces</div><div id="sectionList"></div></div></div>
<script src="https://cdn.jsdelivr.net/npm/three@0.124.0/build/three.min.js"></script><script src="https://cdn.jsdelivr.net/npm/three@0.124.0/examples/js/controls/OrbitControls.js"></script>
<script>
const surfaces={data_json};const container=document.getElementById('viewer');const scene=new THREE.Scene();scene.background=new THREE.Color(0xf6f1ea);const maxX=Math.max(8,...surfaces.map(s=>Math.abs(Number(s.x||0))+Number(s.w||1)/2));const maxZ=Math.max(5,...surfaces.map(s=>Math.abs(Number(s.z||0))+Number(s.d||1)/2));const maxY=Math.max(3,...surfaces.map(s=>Number(s.y||0)+Number(s.h||1)/2));const camera=new THREE.PerspectiveCamera(48,container.clientWidth/container.clientHeight,.1,1000);camera.position.set(maxX*1.35,maxY+4,maxZ*1.75);const renderer=new THREE.WebGLRenderer({{antialias:true,alpha:false}});renderer.setPixelRatio(window.devicePixelRatio||1);renderer.setSize(container.clientWidth,container.clientHeight);renderer.shadowMap.enabled=true;container.appendChild(renderer.domElement);const controls=new THREE.OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.dampingFactor=.07;controls.target.set(0,Math.min(maxY/2,3.5),0);scene.add(new THREE.AmbientLight(0xffffff,.72));const sun=new THREE.DirectionalLight(0xffffff,.9);sun.position.set(maxX,maxY+8,maxZ);sun.castShadow=true;scene.add(sun);const floor=new THREE.Mesh(new THREE.PlaneGeometry(maxX*2.8,maxZ*2.8),new THREE.MeshStandardMaterial({{color:0xf1e8dd,roughness:.86}}));floor.rotation.x=-Math.PI/2;floor.receiveShadow=true;scene.add(floor);const grid=new THREE.GridHelper(Math.max(maxX*2.6,maxZ*2.6),24,0xcbbba8,0xe3d8ca);grid.position.y=.01;scene.add(grid);const base=new THREE.Mesh(new THREE.BoxGeometry(maxX*2+.6,.18,maxZ*2+.6),new THREE.MeshStandardMaterial({{color:0xd9cbbd,transparent:true,opacity:.28}}));base.position.y=.09;base.receiveShadow=true;scene.add(base);const roof=new THREE.Mesh(new THREE.BoxGeometry(maxX*2+.9,.12,maxZ*2+.9),new THREE.MeshStandardMaterial({{color:0x4b5563,transparent:true,opacity:.13}}));roof.position.y=maxY+.08;roof.receiveShadow=true;scene.add(roof);
function fmtMoney(n){{return '$'+Number(n||0).toLocaleString(undefined,{{maximumFractionDigits:0}})}}function hexToInt(h){{return parseInt(String(h||'#ffffff').replace('#',''),16)}}function colourFor(s){{const status=String(s.status||'').toLowerCase();if(s.selected)return 0x2563eb;if(status.includes('complete'))return 0x16a34a;if(status.includes('progress'))return 0xf59e0b;if(status.includes('hold')||status.includes('review'))return 0xfb923c;return hexToInt(s.colour||'#d1d5db')}}function opacityFor(s){{if(s.selected)return .96;const pct=Number(s.completed_pct||0);if(pct<=0)return .62;return .72+Math.min(pct,100)/100*.24}}const meshes=[];surfaces.forEach(s=>{{const geo=new THREE.BoxGeometry(Number(s.w||1),Number(s.h||1),Number(s.d||.1));const mat=new THREE.MeshStandardMaterial({{color:colourFor(s),transparent:true,opacity:opacityFor(s),roughness:.56,metalness:.02}});const mesh=new THREE.Mesh(geo,mat);mesh.position.set(Number(s.x||0),Number(s.y||0),Number(s.z||0));mesh.rotation.y=Number(s.rotY||0);mesh.castShadow=true;mesh.receiveShadow=true;mesh.userData=s;scene.add(mesh);const edge=new THREE.LineSegments(new THREE.EdgesGeometry(geo),new THREE.LineBasicMaterial({{color:0x111827,transparent:true,opacity:.26}}));edge.position.copy(mesh.position);edge.rotation.copy(mesh.rotation);scene.add(edge);meshes.push(mesh);}});
function sourceRows(){{return surfaces.some(s=>s.selected)?surfaces.filter(s=>s.selected):surfaces}}function updateMetrics(){{const src=sourceRows();document.getElementById('metricSections').innerText=surfaces.some(s=>s.selected)?`${{src.length}} selected`:`${{surfaces.length}} mapped`;document.getElementById('metricM2').innerText=Number(src.reduce((a,b)=>a+Number(b.m2||0),0)).toLocaleString(undefined,{{maximumFractionDigits:1}});document.getElementById('metricValue').innerText=fmtMoney(src.reduce((a,b)=>a+Number(b.value||0),0));document.getElementById('metricBillable').innerText=fmtMoney(src.reduce((a,b)=>a+Number(b.billable||0),0));document.getElementById('metricLabour').innerText=Number(src.reduce((a,b)=>a+Number(b.labour_hours||0),0)).toFixed(1)+'h';document.getElementById('metricPaint').innerText=Number(src.reduce((a,b)=>a+Number(b.paint_litres||0),0)).toFixed(1)+'L';}}
function showSurface(s){{document.getElementById('info').innerHTML=`<strong>${{s.code}} — ${{s.name}}</strong><div class="small">${{s.elevation}} • ${{s.level}} • ${{s.surface_type}}</div><div class="small">${{s.substrate}} • ${{s.labour}}</div><div style="margin-top:10px;display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:12px;"><div><b>${{Number(s.m2||0).toLocaleString(undefined,{{maximumFractionDigits:1}})}}m²</b><br><span class="small">substrate</span></div><div><b>${{Number(s.completed_pct||0).toFixed(1)}}%</b><br><span class="small">complete</span></div><div><b>${{fmtMoney(s.value)}}</b><br><span class="small">section value</span></div><div><b>${{fmtMoney(s.billable)}}</b><br><span class="small">billable now</span></div><div><b>${{Number(s.labour_hours||0).toFixed(1)}}h</b><br><span class="small">labour</span></div><div><b>${{Number(s.paint_litres||0).toFixed(1)}}L</b><br><span class="small">paint</span></div></div><div class="small" style="margin-top:10px;">Status: ${{s.status}}</div>`;}}
function buildList(){{const list=document.getElementById('sectionList');list.innerHTML='';surfaces.slice(0,180).forEach(s=>{{const div=document.createElement('div');div.className='sectionRow';div.innerHTML=`<div class="sectionCode">${{s.selected?'🔵 ':''}}${{s.code}} — ${{s.name}}</div><div class="sectionMeta">${{s.elevation}} • ${{s.surface_type}} • ${{Number(s.m2||0).toFixed(1)}}m² • ${{fmtMoney(s.value)}} • ${{s.status}}</div>`;div.onclick=()=>showSurface(s);list.appendChild(div);}});}}updateMetrics();buildList();const raycaster=new THREE.Raycaster();const mouse=new THREE.Vector2();let lastClicked=null;renderer.domElement.addEventListener('click',event=>{{const rect=renderer.domElement.getBoundingClientRect();mouse.x=((event.clientX-rect.left)/rect.width)*2-1;mouse.y=-((event.clientY-rect.top)/rect.height)*2+1;raycaster.setFromCamera(mouse,camera);const hits=raycaster.intersectObjects(meshes,false);if(hits.length){{if(lastClicked)lastClicked.scale.set(1,1,1);const mesh=hits[0].object;mesh.scale.set(1.06,1.06,1.06);lastClicked=mesh;showSurface(mesh.userData);}}}});window.addEventListener('resize',()=>{{camera.aspect=container.clientWidth/container.clientHeight;camera.updateProjectionMatrix();renderer.setSize(container.clientWidth,container.clientHeight);}});function animate(){{requestAnimationFrame(animate);controls.update();renderer.render(scene,camera);}}animate();</script></body></html>
"""
    st.iframe(html_doc, height=740)

def building_mapper_page(default_job_id=None):
    pb_page_header("3D Building Mapper", "Map take-off sections into a plan-shaped 3D progress model that can be adjusted to closely match the building drawings.", "Plan Trace Model")
    jobs_df = job_lookup_dataframe(include_archived=True)
    if jobs_df.empty:
        st.info("Add a job first.")
        return
    selected_job_id = select_job_from_dataframe(jobs_df, "Select job", key=f"building_mapper_job_select_{default_job_id or 'main'}", default_job_id=default_job_id or st.session_state.get("building_mapper_selected_job_id"))
    if not selected_job_id:
        return
    st.session_state["building_mapper_selected_job_id"] = int(selected_job_id)
    package_options = progress_package_options(selected_job_id)
    if not package_options:
        st.warning("Create or import a painting take-off first. The 3D Building Mapper builds from take-off/progress sections.")
        if st.button("Open Painting Take-off Generator", key=f"building_mapper_open_takeoff_{selected_job_id}"):
            st.session_state["go_to_menu"] = "Painting Take-off Generator"
            st.rerun()
        return
    package_label = st.selectbox("Take-off package / progress model", list(package_options.keys()), key=f"building_mapper_package_{selected_job_id}")
    package_id = package_options[package_label]
    render_quick_pdf_import_buttons(selected_job_id, categories=["Architectural Plans", "Specifications", "Colour Schedule", "Scope of Works"], title="Upload plans/elevations for mapping reference", key_prefix=f"building_mapper_pdf_{selected_job_id}", expanded=False)
    with st.expander("Make the 3D shape match the plans", expanded=True):
        st.caption("Enter the main dimensions from the floor plan/elevations. This rebuilds the selectable model around that footprint so it resembles the actual building instead of a generic block.")
        p1, p2, p3, p4 = st.columns(4)
        mapper_template = p1.selectbox("Building shape template", ["Rectangular building", "Townhouse row", "Switchgear / service building", "L-shape building"], key=f"building_mapper_template_{selected_job_id}_{package_id}")
        plan_length = p2.number_input("Plan length / frontage m", min_value=2.0, value=18.0, step=0.5, key=f"building_mapper_plan_length_{selected_job_id}_{package_id}")
        plan_depth = p3.number_input("Plan depth m", min_value=2.0, value=9.0, step=0.5, key=f"building_mapper_plan_depth_{selected_job_id}_{package_id}")
        level_count = p4.number_input("Number of levels", min_value=1, max_value=5, value=2, step=1, key=f"building_mapper_levels_{selected_job_id}_{package_id}")
        q1, q2, q3 = st.columns(3)
        level_height = q1.number_input("Typical level height m", min_value=2.1, value=2.7, step=0.1, key=f"building_mapper_level_height_{selected_job_id}_{package_id}")
        roof_style = q2.selectbox("Roof style", ["Flat roof", "Skillion roof", "Gable roof"], key=f"building_mapper_roof_style_{selected_job_id}_{package_id}")
        if q3.button("Rebuild to plan shape", key=f"building_mapper_rebuild_plan_shape_{selected_job_id}_{package_id}", width="stretch"):
            count = generate_plan_shape_surfaces_from_takeoff(selected_job_id, package_id, plan_length, plan_depth, int(level_count), level_height, mapper_template, roof_style, reset_existing=True)
            st.success(f"Plan-shaped model rebuilt with {count} mapped surface(s).")
            st.rerun()
        st.info("For best results, use dimensions straight from the plan: overall building length, overall depth, number of levels and typical wall height. Then fine-tune each surface in the mapped surface schedule below.")
    cols = st.columns(3)
    if cols[0].button("Generate building-shaped model from take-off", key=f"building_mapper_generate_{selected_job_id}_{package_id}", width="stretch"):
        count = generate_building_surfaces_from_takeoff(selected_job_id, package_id, reset_existing=False)
        st.success(f"Building mapper has {count} mapped surface(s).")
        st.rerun()
    if cols[1].button("Rebuild / reset mapped model", key=f"building_mapper_rebuild_{selected_job_id}_{package_id}", width="stretch"):
        count = generate_building_surfaces_from_takeoff(selected_job_id, package_id, reset_existing=True)
        st.success(f"Rebuilt {count} mapped surface(s) from the take-off.")
        st.rerun()
    if cols[2].button("Open Progress / Billing", key=f"building_mapper_open_progress_{selected_job_id}_{package_id}", width="stretch"):
        st.session_state["go_to_menu"] = "Progress / Billing Model"
        st.rerun()
    surfaces = building_model_surfaces_df(selected_job_id, package_id)
    if surfaces.empty:
        st.info("No building-shaped model is mapped yet. Press Generate building-shaped model from take-off.")
        return
    render_building_mapper_3d(surfaces, key_prefix=f"building_mapper_3d_{selected_job_id}_{package_id}")
    st.markdown("### Mapped Surface Schedule")
    st.caption("Adjust X/Y/Z and size values to make the model resemble the building more closely. Use Front/Rear/Left/Right/Internal/Ceiling elevations to organise the model.")
    edit_cols = ["ID", "Section Code", "Surface Name", "Surface Type", "Elevation", "Level", "X", "Y", "Z", "Width", "Height", "Depth", "Rotation Y", "Substrate", "Total m2", "Completed %", "Status"]
    edited = st.data_editor(surfaces[[c for c in edit_cols if c in surfaces.columns]].copy(), hide_index=True, width="stretch", key=f"building_mapper_editor_{selected_job_id}_{package_id}", disabled=["ID", "Section Code", "Substrate", "Total m2", "Completed %", "Status"])
    if st.button("Save 3D mapper layout changes", key=f"building_mapper_save_{selected_job_id}_{package_id}"):
        for _, row in edited.iterrows():
            execute("""
                UPDATE building_model_surfaces
                SET surface_name = ?, surface_type = ?, elevation = ?, level_name = ?,
                    x_pos = ?, y_pos = ?, z_pos = ?, width = ?, height = ?, depth = ?,
                    rotation_y = ?, updated_at = ?
                WHERE id = ?
            """, (str(row.get("Surface Name") or ""), str(row.get("Surface Type") or ""), str(row.get("Elevation") or ""), str(row.get("Level") or ""), app_float(row.get("X")), app_float(row.get("Y")), app_float(row.get("Z")), max(app_float(row.get("Width")), 0.05), max(app_float(row.get("Height")), 0.05), max(app_float(row.get("Depth")), 0.03), app_float(row.get("Rotation Y")), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), int(row.get("ID"))))
        st.success("3D mapper layout saved.")
        st.rerun()
    with st.expander("Manually add one mapped surface"):
        sections = progress_sections_df(selected_job_id, package_id)
        if sections.empty:
            st.info("No progress sections found for this take-off package.")
        else:
            labels = {progress_section_label(r): r for _, r in sections.iterrows()}
            with st.form(f"manual_building_surface_{selected_job_id}_{package_id}"):
                selected_label = st.selectbox("Link to take-off/progress section", list(labels.keys()))
                c1, c2, c3 = st.columns(3)
                surface_name = c1.text_input("Surface name", "Mapped surface")
                surface_type = c2.selectbox("Surface type", ["Internal Wall", "External Wall", "Ceiling", "Soffit / Eave", "Woodwork / Frames", "Feature", "Other"])
                elevation = c3.selectbox("Elevation / area", ["Front", "Rear", "Left", "Right", "Internal", "Ceiling / Roof"])
                d1, d2, d3, d4 = st.columns(4)
                x_pos = d1.number_input("X", value=0.0, step=0.25)
                y_pos = d2.number_input("Y", value=1.35, step=0.25)
                z_pos = d3.number_input("Z", value=0.0, step=0.25)
                rotation_y = d4.number_input("Rotation Y", value=0.0, step=0.1)
                e1, e2, e3 = st.columns(3)
                width = e1.number_input("Width", min_value=0.05, value=2.0, step=0.25)
                height = e2.number_input("Height", min_value=0.05, value=2.7, step=0.25)
                depth = e3.number_input("Depth", min_value=0.03, value=0.12, step=0.05)
                if st.form_submit_button("Add mapped surface"):
                    src_row = labels[selected_label]
                    execute("""
                        INSERT INTO building_model_surfaces
                        (job_id, package_id, progress_section_id, takeoff_line_id, section_code, surface_name,
                         surface_type, elevation, level_name, x_pos, y_pos, z_pos, width, height, depth,
                         rotation_y, colour_hex, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (selected_job_id, package_id, int(src_row.get("ID") or 0), int(src_row.get("Takeoff Line ID") or 0) if app_float(src_row.get("Takeoff Line ID")) else None, str(src_row.get("Section Code") or ""), surface_name, surface_type, elevation, "Manual", x_pos, y_pos, z_pos, width, height, depth, rotation_y, building_surface_colour(src_row.get("Substrate"), src_row.get("Labour Category"), src_row.get("Area")), "Manually mapped surface.", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    st.success("Mapped surface added.")
                    st.rerun()
    with st.expander("Delete mapped surface"):
        delete_options = {f"{row['ID']} - {row['Surface Name']} ({row['Elevation']})": int(row["ID"]) for _, row in surfaces.iterrows()}
        if delete_options:
            selected_delete = st.selectbox("Select mapped surface to delete", list(delete_options.keys()), key=f"building_mapper_delete_select_{selected_job_id}_{package_id}")
            if st.button("Delete selected mapped surface", key=f"building_mapper_delete_btn_{selected_job_id}_{package_id}"):
                execute("DELETE FROM building_model_surfaces WHERE id = ?", (delete_options[selected_delete],))
                st.success("Mapped surface deleted.")
                st.rerun()

def drawing_mapper_image_documents_df(job_id):
    return df_query("""
        SELECT id AS "ID",
               document_type AS "Type",
               file_name AS "File Name",
               file_path AS "File Path",
               created_at AS "Uploaded",
               notes AS "Notes"
        FROM job_documents
        WHERE job_id = ?
          AND LOWER(COALESCE(file_name, '')) LIKE '%.png'
           OR (job_id = ? AND LOWER(COALESCE(file_name, '')) LIKE '%.jpg')
           OR (job_id = ? AND LOWER(COALESCE(file_name, '')) LIKE '%.jpeg')
           OR (job_id = ? AND LOWER(COALESCE(file_name, '')) LIKE '%.webp')
        ORDER BY id DESC
    """, (job_id, job_id, job_id, job_id))

def drawing_mapper_reference_pdfs_df(job_id):
    return df_query("""
        SELECT id AS "ID",
               document_type AS "Type",
               file_name AS "File Name",
               file_path AS "File Path",
               created_at AS "Uploaded",
               notes AS "Notes"
        FROM job_documents
        WHERE job_id = ?
          AND LOWER(COALESCE(file_name, '')) LIKE '%.pdf'
        ORDER BY id DESC
    """, (job_id,))

def drawing_progress_zones_df(job_id, package_id=None, document_id=None):
    params = [job_id]
    where = "WHERE z.job_id = ?"
    if package_id:
        where += " AND z.package_id = ?"
        params.append(package_id)
    if document_id:
        where += " AND z.document_id = ?"
        params.append(document_id)
    df = df_query(f"""
        SELECT z.id AS "ID",
               z.job_id AS "Job ID",
               z.package_id AS "Package ID",
               z.document_id AS "Document ID",
               z.progress_section_id AS "Progress Section ID",
               z.takeoff_line_id AS "Takeoff Line ID",
               z.view_name AS "View",
               z.zone_name AS "Zone Name",
               z.x_percent AS "X %",
               z.y_percent AS "Y %",
               z.width_percent AS "Width %",
               z.height_percent AS "Height %",
               z.colour_hex AS "Base Colour",
               ps.section_code AS "Section Code",
               ps.area_type AS "Area",
               ps.location_area AS "Location / Area",
               ps.substrate AS "Substrate",
               ps.labour_category AS "Labour Category",
               ps.total_m2 AS "Total m2",
               ps.completed_m2 AS "Completed m2",
               ps.completed_percent AS "Completed %",
               ps.allocated_value_ex_gst AS "Section Value Ex GST",
               (ps.allocated_value_ex_gst * ps.completed_percent / 100.0) AS "Billable Value Ex GST",
               tl.labour_hours AS "Total Labour Hours",
               tl.paint_litres AS "Total Paint Litres",
               ps.status AS "Status",
               z.notes AS "Notes",
               z.updated_at AS "Updated At"
        FROM drawing_progress_zones z
        LEFT JOIN painting_progress_sections ps ON ps.id = z.progress_section_id
        LEFT JOIN painting_takeoff_lines tl ON tl.id = z.takeoff_line_id
        {where}
        ORDER BY z.view_name, z.id
    """, tuple(params))
    numeric_cols = ["X %", "Y %", "Width %", "Height %", "Total m2", "Completed m2", "Completed %", "Section Value Ex GST", "Billable Value Ex GST", "Total Labour Hours", "Total Paint Litres"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    return df

def progress_zone_status_colour(row):
    try:
        pct = float(row.get("Completed %") or 0)
    except Exception:
        pct = 0
    if pct >= 99.5:
        return "#16a34a"  # complete
    if pct > 0:
        return "#f59e0b"  # in progress
    return str(row.get("Base Colour") or building_surface_colour(row.get("Substrate"), row.get("Labour Category"), row.get("Area")) or "#60a5fa")

def render_actual_drawing_progress_overlay(image_path, zones_df, key_prefix="actual_drawing_mapper"):
    if not image_path or not os.path.exists(str(image_path)):
        st.warning("The selected drawing image file could not be found. Upload the plan/elevation image again.")
        return
    try:
        with Image.open(image_path) as img:
            width, height = img.size
        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        ext = os.path.splitext(str(image_path))[1].lower().replace(".", "") or "png"
        mime = "image/jpeg" if ext in ["jpg", "jpeg"] else f"image/{ext}"
        data_uri = f"data:{mime};base64,{encoded}"
    except Exception as e:
        st.error(f"Could not load drawing image: {e}")
        return

    zones = []
    if zones_df is not None and not zones_df.empty:
        for _, row in zones_df.iterrows():
            pct = app_float(row.get("Completed %"))
            colour = progress_zone_status_colour(row)
            zones.append({
                "id": int(row.get("ID") or 0),
                "name": str(row.get("Zone Name") or row.get("Location / Area") or "Zone"),
                "section": str(row.get("Section Code") or ""),
                "area": str(row.get("Area") or ""),
                "location": str(row.get("Location / Area") or ""),
                "substrate": str(row.get("Substrate") or ""),
                "labour": float(app_float(row.get("Total Labour Hours"))),
                "paint": float(app_float(row.get("Total Paint Litres"))),
                "m2": float(app_float(row.get("Total m2"))),
                "value": float(app_float(row.get("Section Value Ex GST"))),
                "billable": float(app_float(row.get("Billable Value Ex GST"))),
                "pct": float(pct),
                "status": str(row.get("Status") or "Not Started"),
                "x": max(0, min(100, app_float(row.get("X %")))),
                "y": max(0, min(100, app_float(row.get("Y %")))),
                "w": max(1, min(100, app_float(row.get("Width %")))),
                "h": max(1, min(100, app_float(row.get("Height %")))),
                "colour": colour,
            })
    zones_json = json.dumps(zones)
    aspect = max(width / max(height, 1), 0.2)
    html_doc = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<style>
  body {{ margin:0; background:#0b0f12; font-family: Inter, Arial, sans-serif; color:#f8fafc; }}
  .wrap {{ display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:14px; height:720px; padding:12px; box-sizing:border-box; }}
  .drawingPanel {{ background:#111827; border:1px solid rgba(255,255,255,.12); border-radius:16px; padding:14px; overflow:auto; }}
  .title {{ font-size:18px; font-weight:800; margin-bottom:4px; }}
  .hint {{ font-size:12px; color:#cbd5e1; margin-bottom:10px; }}
  .canvasOuter {{ min-width:760px; max-width:100%; }}
  .canvas {{ position:relative; width:100%; aspect-ratio:{aspect}; background-image:url('{data_uri}'); background-size:contain; background-repeat:no-repeat; background-position:center; border-radius:10px; border:1px solid rgba(255,255,255,.18); box-shadow:0 12px 28px rgba(0,0,0,.35); overflow:hidden; }}
  .zone {{ position:absolute; border:2px solid rgba(255,255,255,.95); border-radius:5px; box-sizing:border-box; cursor:pointer; display:flex; align-items:flex-start; justify-content:flex-start; padding:3px; color:#071014; font-size:11px; font-weight:800; text-shadow:0 1px 0 rgba(255,255,255,.4); opacity:.72; transition:all .12s ease-in-out; overflow:hidden; }}
  .zone:hover {{ opacity:.95; transform:scale(1.015); z-index:50; box-shadow:0 0 0 3px rgba(37,99,235,.7); }}
  .zone.selected {{ box-shadow:0 0 0 4px rgba(37,99,235,.9); opacity:.95; }}
  .side {{ background:#0f172a; border:1px solid rgba(255,255,255,.12); border-radius:16px; padding:16px; overflow:auto; }}
  .metricGrid {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:12px 0; }}
  .metric {{ background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.1); border-radius:12px; padding:10px; }}
  .label {{ color:#94a3b8; font-size:11px; }}
  .value {{ font-size:17px; font-weight:800; margin-top:3px; }}
  .info {{ background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.1); border-radius:12px; padding:12px; line-height:1.5; }}
  .pill {{ display:inline-block; padding:4px 8px; border-radius:999px; background:rgba(255,255,255,.1); margin:3px 3px 3px 0; font-size:11px; }}
  .legend {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:8px; font-size:12px; color:#cbd5e1; }}
  .dot {{ display:inline-block; width:12px; height:12px; border-radius:50%; margin-right:5px; vertical-align:-2px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="drawingPanel">
    <div class="title">Actual Plan / Elevation Progress Mapper</div>
    <div class="hint">This uses the real uploaded drawing image as the background. Coloured zones are linked to take-off/progress sections.</div>
    <div class="canvasOuter"><div class="canvas" id="canvas"></div></div>
    <div class="legend"><span><span class="dot" style="background:#16a34a"></span>Complete</span><span><span class="dot" style="background:#f59e0b"></span>In progress</span><span><span class="dot" style="background:#60a5fa"></span>Not started / mapped</span></div>
  </div>
  <div class="side">
    <div class="title">Selected zone</div>
    <div class="hint">Click a coloured zone on the actual plan/elevation.</div>
    <div class="metricGrid">
      <div class="metric"><div class="label">Mapped zones</div><div class="value" id="count">0</div></div>
      <div class="metric"><div class="label">Total m²</div><div class="value" id="totalM2">0</div></div>
      <div class="metric"><div class="label">Total value</div><div class="value" id="totalValue">$0</div></div>
      <div class="metric"><div class="label">Billable</div><div class="value" id="totalBillable">$0</div></div>
    </div>
    <div id="info" class="info"><strong>No zone selected</strong><br><span style="color:#94a3b8">Click an overlay zone to inspect m², labour, paint and billable value.</span></div>
  </div>
</div>
<script>
const zones = {zones_json};
const canvas = document.getElementById('canvas');
function money(v) {{ return '$' + Number(v || 0).toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}}); }}
function num(v, d=1) {{ return Number(v || 0).toLocaleString(undefined, {{minimumFractionDigits:d, maximumFractionDigits:d}}); }}
let totalM2 = 0, totalValue = 0, totalBillable = 0;
zones.forEach(z => {{
  totalM2 += z.m2 || 0; totalValue += z.value || 0; totalBillable += z.billable || 0;
  const el = document.createElement('div');
  el.className = 'zone';
  el.style.left = z.x + '%';
  el.style.top = z.y + '%';
  el.style.width = z.w + '%';
  el.style.height = z.h + '%';
  el.style.background = z.colour || '#60a5fa';
  el.title = z.name;
  el.innerHTML = '<span>' + (z.section || z.id) + '</span>';
  el.onclick = () => {{
    document.querySelectorAll('.zone').forEach(x => x.classList.remove('selected'));
    el.classList.add('selected');
    document.getElementById('info').innerHTML = `
      <strong>${{z.name}}</strong><br>
      <span class="pill">${{z.substrate || 'Substrate'}}</span><span class="pill">${{z.status || 'Status'}}</span><br>
      <div style="margin-top:8px;color:#cbd5e1">${{z.location || ''}}</div>
      <hr style="border-color:rgba(255,255,255,.12)">
      Area: <strong>${{num(z.m2,1)}} m²</strong><br>
      Value: <strong>${{money(z.value)}}</strong><br>
      Billable: <strong>${{money(z.billable)}}</strong><br>
      Labour: <strong>${{num(z.labour,1)}} hrs</strong><br>
      Paint: <strong>${{num(z.paint,1)}} L</strong><br>
      Completion: <strong>${{num(z.pct,0)}}%</strong>
    `;
  }};
  canvas.appendChild(el);
}});
document.getElementById('count').innerText = zones.length;
document.getElementById('totalM2').innerText = num(totalM2,1);
document.getElementById('totalValue').innerText = money(totalValue);
document.getElementById('totalBillable').innerText = money(totalBillable);
</script>
</body>
</html>
"""
    st.iframe(html_doc, height=760)

def create_grid_zones_from_progress_sections(job_id, package_id, document_id, view_name="Plan / Elevation", reset_existing=False):
    if reset_existing:
        execute("DELETE FROM drawing_progress_zones WHERE job_id = ? AND package_id = ? AND document_id = ?", (job_id, package_id, document_id))
    sections = progress_sections_df(job_id, package_id)
    if sections.empty:
        ensure_progress_sections_for_package(package_id, reset_values=False)
        sections = progress_sections_df(job_id, package_id)
    if sections.empty:
        return 0
    existing = drawing_progress_zones_df(job_id, package_id, document_id)
    existing_progress_ids = set()
    if not existing.empty and "Progress Section ID" in existing.columns:
        existing_progress_ids = {int(x) for x in existing["Progress Section ID"].dropna().astype(int).tolist() if int(x) > 0}
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    created = 0
    cols = 5
    cell_w = 17.5
    cell_h = 12.0
    gap_x = 1.5
    gap_y = 1.8
    start_x = 4.0
    start_y = 6.0
    for idx, (_, row) in enumerate(sections.iterrows()):
        progress_id = int(row.get("ID") or 0)
        if progress_id in existing_progress_ids:
            continue
        grid_i = created
        col = grid_i % cols
        line = grid_i // cols
        x = start_x + col * (cell_w + gap_x)
        y = start_y + line * (cell_h + gap_y)
        if y + cell_h > 96:
            y = 6 + (line % 6) * (cell_h + gap_y)
        zone_name = str(row.get("Location / Area") or row.get("Section Code") or f"Zone {created+1}")
        execute("""
            INSERT INTO drawing_progress_zones
            (job_id, package_id, document_id, progress_section_id, takeoff_line_id, view_name, zone_name,
             x_percent, y_percent, width_percent, height_percent, colour_hex, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            job_id, package_id, document_id, progress_id,
            int(row.get("Takeoff Line ID") or 0) if app_float(row.get("Takeoff Line ID")) else None,
            view_name, zone_name, float(x), float(y), float(cell_w), float(cell_h),
            building_surface_colour(row.get("Substrate"), row.get("Labour Category"), row.get("Area")),
            "Auto-created grid zone. Drag by editing X/Y/Width/Height so it lines up with the actual drawing.",
            now_text, now_text,
        ))
        created += 1
    return created

def building_progress_mapper_page(default_job_id=None):
    pb_page_header("Actual Plan & Elevation Progress Mapper", "Use the real plan/elevation drawing as the background, then place clickable coloured zones over the actual building areas.", "Actual Drawing Overlay")
    jobs_df = job_lookup_dataframe(include_archived=False)
    selected_job_id = select_job_from_dataframe(jobs_df, "Select job", key=f"actual_mapper_job_select_{default_job_id or 'main'}", default_job_id=default_job_id or st.session_state.get("actual_mapper_selected_job_id"))
    if not selected_job_id:
        st.info("Create/select a job first.")
        return
    selected_job_id = int(selected_job_id)
    st.session_state["actual_mapper_selected_job_id"] = selected_job_id

    package_id = latest_takeoff_package_for_job(selected_job_id)
    packages = takeoff_packages_for_job(selected_job_id)
    if not packages.empty:
        package_options = {f"{int(r['id'])} - {r['package_name']} ({r['status']})": int(r["id"]) for _, r in packages.iterrows()}
        default_label = next((label for label, pid in package_options.items() if pid == package_id), list(package_options.keys())[0])
        selected_label = st.selectbox("Take-off package / progress model", list(package_options.keys()), index=list(package_options.keys()).index(default_label), key=f"actual_mapper_package_select_{selected_job_id}")
        package_id = package_options[selected_label]
    else:
        st.warning("Create or import a painting take-off first. The mapper links drawing zones to take-off/progress sections.")
        if st.button("Open Painting Take-off Generator", key=f"actual_mapper_open_takeoff_{selected_job_id}"):
            st.session_state["go_to_menu"] = "Painting Take-off Generator"
            st.rerun()
        return

    st.markdown("### 1. Convert or upload actual drawing page images")
    st.caption("For the closest match to the plans, use the PDF converter below to turn plan/elevation PDF pages into clean PNG/JPEG images, then place clickable zones over the real drawing.")
    render_smart_plan_set_import(selected_job_id, key_prefix=f"actual_mapper_smart_import_{selected_job_id}", expanded=False)
    render_quick_pdf_import_buttons(selected_job_id, categories=["Architectural Plans", "Specifications", "Colour Schedule", "Scope of Works"], title="Attach PDFs as reference", key_prefix=f"actual_mapper_reference_pdf_{selected_job_id}", expanded=False)

    with st.expander("Convert PDF plans/elevations to PNG/JPEG for mapper", expanded=True):
        st.info("Use this when you have a PDF plan set. Convert the exact elevation/floor plan pages you need, then select the converted image below for mapping.")
        pc1, pc2, pc3, pc4 = st.columns([1.2, 1, 1, 1])
        pdf_view_name = pc1.selectbox(
            "Converted drawing view",
            [
                "Front Elevation",
                "Rear Elevation",
                "Left Elevation",
                "Right Elevation",
                "Ground Floor Plan",
                "Level 1 Plan",
                "Level 2 Plan",
                "Roof / Soffit Plan",
                "Internal Areas",
                "Other",
            ],
            key=f"actual_mapper_pdf_convert_view_{selected_job_id}",
        )
        pdf_page_selection = pc2.text_input("Pages to convert", value="", placeholder="e.g. 1,3,5-7", key=f"actual_mapper_pdf_convert_pages_{selected_job_id}")
        pdf_dpi = pc3.selectbox("Image quality", [150, 200, 220, 300], index=2, key=f"actual_mapper_pdf_convert_dpi_{selected_job_id}")
        pdf_image_format = pc4.selectbox("Output", ["PNG", "JPEG"], key=f"actual_mapper_pdf_convert_format_{selected_job_id}")
        uploaded_pdf_plans = st.file_uploader(
            "Upload one or more PDF plan/elevation files to convert",
            type=["pdf"],
            accept_multiple_files=True,
            key=f"actual_mapper_pdf_converter_upload_{selected_job_id}",
        )
        st.caption("Blank page selection converts every page. For big drawing sets, enter only the pages you need so the app stays fast. PNG is best quality; JPEG is smaller.")
        if st.button("Convert PDF page(s) to mapper image(s)", key=f"actual_mapper_pdf_convert_btn_{selected_job_id}", width="stretch"):
            saved_count = maybe_convert_uploaded_pdf_to_mapper_images(
                selected_job_id,
                uploaded_pdf_plans,
                pdf_view_name,
                pdf_page_selection,
                pdf_dpi,
                pdf_image_format,
                key_prefix=f"actual_mapper_pdf_convert_{selected_job_id}",
            )
            if saved_count:
                st.success(f"Created {saved_count} clean drawing image(s). Select one below to build the progress overlay.")
                st.rerun()

    with st.expander("Upload existing plan/elevation image(s) for clickable overlay", expanded=False):
        c1, c2 = st.columns([1, 2])
        view_name_upload = c1.selectbox(
            "Drawing view",
            [
                "Auto-detect from file name",
                "Front Elevation",
                "Rear Elevation",
                "Left Elevation",
                "Right Elevation",
                "Ground Floor Plan",
                "Level 1 Plan",
                "Level 2 Plan",
                "Roof / Soffit Plan",
                "Internal Areas",
                "Other",
            ],
            key=f"actual_mapper_upload_view_{selected_job_id}",
        )
        uploaded_images = c2.file_uploader(
            "Upload one or more plan/elevation images",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key=f"actual_mapper_image_upload_{selected_job_id}",
        )
        st.caption("You can select multiple drawing screenshots at once. Use clear file names like Front Elevation, Rear Elevation, Ground Floor Plan or Roof Plan so JobHub can label them automatically.")

        def guess_drawing_view_from_filename(file_name, fallback_view):
            if fallback_view and fallback_view != "Auto-detect from file name":
                return fallback_view
            name = str(file_name or "").lower().replace("_", " ").replace("-", " ")
            checks = [
                (["front", "north"], "Front Elevation"),
                (["rear", "back", "south"], "Rear Elevation"),
                (["left", "west"], "Left Elevation"),
                (["right", "east"], "Right Elevation"),
                (["ground", "gf", "floor plan", "floorplan"], "Ground Floor Plan"),
                (["level 1", "lvl 1", "first floor", "l1"], "Level 1 Plan"),
                (["level 2", "lvl 2", "second floor", "l2"], "Level 2 Plan"),
                (["roof", "soffit", "eaves"], "Roof / Soffit Plan"),
                (["internal", "room", "rooms"], "Internal Areas"),
            ]
            for keywords, label in checks:
                if any(keyword in name for keyword in keywords):
                    return label
            return "Other"

        if st.button("Upload Drawing Image(s)", key=f"actual_mapper_upload_image_btn_{selected_job_id}"):
            if not uploaded_images:
                st.error("Choose at least one PNG/JPG/WEBP drawing image first.")
            else:
                saved_count = 0
                failed_count = 0
                saved_labels = []
                for uploaded_image in uploaded_images:
                    try:
                        guessed_view = guess_drawing_view_from_filename(uploaded_image.name, view_name_upload)
                        save_uploaded_job_document(
                            selected_job_id,
                            uploaded_image,
                            f"Drawing Mapper - {guessed_view}",
                            notes="Image background for Actual Plan & Elevation Progress Mapper",
                        )
                        saved_count += 1
                        saved_labels.append(f"{uploaded_image.name} → {guessed_view}")
                    except Exception as e:
                        failed_count += 1
                        st.error(f"Could not upload {uploaded_image.name}: {e}")
                if saved_count:
                    st.success(f"Uploaded {saved_count} drawing image(s). Select any uploaded drawing below to place zones over it.")
                    with st.expander("Uploaded drawing labels", expanded=False):
                        for label in saved_labels:
                            st.write(label)
                    st.rerun()
                elif failed_count:
                    st.error("No drawing images were uploaded successfully.")

    image_docs = drawing_mapper_image_documents_df(selected_job_id)
    if image_docs.empty:
        pdf_docs = drawing_mapper_reference_pdfs_df(selected_job_id)
        if not pdf_docs.empty:
            st.info("PDF plans are attached. Use the PDF converter above to turn the correct plan/elevation pages into PNG/JPEG images, then select the converted image here for the clickable progress overlay.")
            st.dataframe(pdf_docs[["Type", "File Name", "Uploaded"]], width="stretch", hide_index=True)
        else:
            st.info("No plan/elevation images are uploaded yet. Upload a screenshot/export of the plan or elevation page above.")
        return

    st.markdown("### 2. Select drawing background")
    with st.expander("Uploaded drawing gallery", expanded=True):
        gallery_cols = st.columns(3)
        for gallery_index, (_, doc_row) in enumerate(image_docs.iterrows()):
            file_path = str(doc_row.get("File Path") or "")
            with gallery_cols[gallery_index % 3]:
                st.markdown(f"**{doc_row.get('Type', 'Drawing')}**")
                st.caption(str(doc_row.get("File Name") or ""))
                if file_path and os.path.exists(file_path):
                    st.image(file_path, width="stretch")
                else:
                    st.warning("Image file missing")
    doc_options = {f"{int(r['ID'])} - {r['Type']} - {r['File Name']}": int(r["ID"]) for _, r in image_docs.iterrows()}
    selected_doc_label = st.selectbox("Actual plan/elevation image", list(doc_options.keys()), key=f"actual_mapper_doc_select_{selected_job_id}_{package_id}")
    document_id = doc_options[selected_doc_label]
    selected_doc = image_docs[image_docs["ID"] == document_id].iloc[0]
    image_path = str(selected_doc.get("File Path") or "")

    st.markdown("### 3. Create and position mapped zones")
    zc1, zc2, zc3 = st.columns(3)
    if zc1.button("Auto-create zones from take-off", key=f"actual_mapper_auto_zones_{selected_job_id}_{package_id}_{document_id}", width="stretch"):
        created = create_grid_zones_from_progress_sections(selected_job_id, package_id, document_id, view_name=str(selected_doc.get("Type") or "Drawing"), reset_existing=False)
        st.success(f"Created {created} new mapped zone(s). Move them into place using the zone schedule below.")
        st.rerun()
    if zc2.button("Reset zones for this drawing", key=f"actual_mapper_reset_zones_{selected_job_id}_{package_id}_{document_id}", width="stretch"):
        created = create_grid_zones_from_progress_sections(selected_job_id, package_id, document_id, view_name=str(selected_doc.get("Type") or "Drawing"), reset_existing=True)
        st.success(f"Reset and created {created} mapped zone(s).")
        st.rerun()
    if zc3.button("Open Progress / Billing", key=f"actual_mapper_open_progress_{selected_job_id}_{package_id}", width="stretch"):
        st.session_state["go_to_menu"] = "Progress / Billing Model"
        st.rerun()

    zones = drawing_progress_zones_df(selected_job_id, package_id, document_id)
    render_actual_drawing_progress_overlay(image_path, zones, key_prefix=f"actual_mapper_overlay_{selected_job_id}_{package_id}_{document_id}")

    st.markdown("### 4. Move zones to match the drawing")
    st.caption("Edit X%, Y%, Width% and Height% until each coloured zone sits over the matching part of the actual plan/elevation. This is what makes the model look nearly identical to the plans.")
    if not zones.empty:
        edit_cols = ["ID", "View", "Zone Name", "X %", "Y %", "Width %", "Height %", "Section Code", "Substrate", "Total m2", "Completed %", "Section Value Ex GST", "Status"]
        edited = st.data_editor(zones[[c for c in edit_cols if c in zones.columns]].copy(), hide_index=True, width="stretch", key=f"actual_mapper_zone_editor_{selected_job_id}_{package_id}_{document_id}", disabled=["ID", "Section Code", "Substrate", "Total m2", "Completed %", "Section Value Ex GST", "Status"])
        if st.button("Save zone positions", key=f"actual_mapper_save_zones_{selected_job_id}_{package_id}_{document_id}"):
            for _, row in edited.iterrows():
                execute("""
                    UPDATE drawing_progress_zones
                    SET view_name = ?, zone_name = ?, x_percent = ?, y_percent = ?, width_percent = ?, height_percent = ?, updated_at = ?
                    WHERE id = ?
                """, (str(row.get("View") or ""), str(row.get("Zone Name") or ""), max(0, min(100, app_float(row.get("X %")))), max(0, min(100, app_float(row.get("Y %")))), max(1, min(100, app_float(row.get("Width %")))), max(1, min(100, app_float(row.get("Height %")))), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), int(row.get("ID"))))
            st.success("Zone positions saved.")
            st.rerun()

        st.markdown("### 5. Mark selected drawing zones as complete / partly complete")
        zone_options = {f"{int(row['ID'])} - {row['Zone Name']} | {row['Substrate']} | {app_float(row['Total m2']):.1f}m² | {app_float(row['Completed %']):.0f}%": int(row["Progress Section ID"] or 0) for _, row in zones.iterrows() if int(row.get("Progress Section ID") or 0) > 0}
        selected_zone_labels = st.multiselect("Select mapped zones to update", list(zone_options.keys()), key=f"actual_mapper_select_zones_{selected_job_id}_{package_id}_{document_id}")
        progress_percent = st.slider("Completion percentage for selected zones", min_value=0, max_value=100, value=100, step=5, key=f"actual_mapper_completion_pct_{selected_job_id}_{package_id}_{document_id}")
        if st.button("Apply completion to selected zones", key=f"actual_mapper_apply_completion_{selected_job_id}_{package_id}_{document_id}"):
            if not selected_zone_labels:
                st.error("Select at least one mapped zone.")
            else:
                now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                updated = 0
                for label in selected_zone_labels:
                    progress_id = zone_options[label]
                    row_df = zones[zones["Progress Section ID"] == progress_id]
                    total_m2 = app_float(row_df.iloc[0].get("Total m2")) if not row_df.empty else 0
                    completed_m2 = total_m2 * float(progress_percent) / 100.0
                    status = "Complete" if progress_percent >= 100 else ("In Progress" if progress_percent > 0 else "Not Started")
                    execute("""
                        UPDATE painting_progress_sections
                        SET completed_percent = ?, completed_m2 = ?, status = ?, updated_by = ?, updated_at = ?
                        WHERE id = ?
                    """, (float(progress_percent), float(completed_m2), status, current_username(), now_text, int(progress_id)))
                    updated += 1
                st.success(f"Updated {updated} mapped zone(s).")
                st.rerun()
    else:
        st.info("No zones mapped yet. Press Auto-create zones from take-off, then move them over the real plan/elevation.")

    with st.expander("Manually add one zone"):
        sections = progress_sections_df(selected_job_id, package_id)
        if sections.empty:
            st.info("No progress sections found. Refresh the progress model from the take-off first.")
        else:
            labels = {progress_section_label(r): r for _, r in sections.iterrows()}
            with st.form(f"actual_mapper_manual_zone_{selected_job_id}_{package_id}_{document_id}"):
                selected_section_label = st.selectbox("Link to take-off/progress section", list(labels.keys()))
                z1, z2, z3 = st.columns(3)
                zone_name = z1.text_input("Zone name", "Mapped area")
                view_name = z2.text_input("View name", str(selected_doc.get("Type") or "Drawing"))
                zone_note = z3.text_input("Notes", "")
                p1, p2, p3, p4 = st.columns(4)
                x_percent = p1.number_input("X %", min_value=0.0, max_value=100.0, value=5.0, step=1.0)
                y_percent = p2.number_input("Y %", min_value=0.0, max_value=100.0, value=5.0, step=1.0)
                width_percent = p3.number_input("Width %", min_value=1.0, max_value=100.0, value=15.0, step=1.0)
                height_percent = p4.number_input("Height %", min_value=1.0, max_value=100.0, value=10.0, step=1.0)
                if st.form_submit_button("Add mapped drawing zone"):
                    src_row = labels[selected_section_label]
                    execute("""
                        INSERT INTO drawing_progress_zones
                        (job_id, package_id, document_id, progress_section_id, takeoff_line_id, view_name, zone_name,
                         x_percent, y_percent, width_percent, height_percent, colour_hex, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (selected_job_id, package_id, document_id, int(src_row.get("ID") or 0), int(src_row.get("Takeoff Line ID") or 0) if app_float(src_row.get("Takeoff Line ID")) else None, view_name, zone_name, x_percent, y_percent, width_percent, height_percent, building_surface_colour(src_row.get("Substrate"), src_row.get("Labour Category"), src_row.get("Area")), zone_note, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    st.success("Mapped zone added.")
                    st.rerun()

    with st.expander("Delete mapped zone"):
        zones_for_delete = drawing_progress_zones_df(selected_job_id, package_id, document_id)
        if zones_for_delete.empty:
            st.info("No mapped zones to delete.")
        else:
            delete_options = {f"{int(row['ID'])} - {row['Zone Name']}": int(row["ID"]) for _, row in zones_for_delete.iterrows()}
            selected_delete = st.selectbox("Select zone to delete", list(delete_options.keys()), key=f"actual_mapper_delete_zone_select_{selected_job_id}_{package_id}_{document_id}")
            if st.button("Delete selected mapped zone", key=f"actual_mapper_delete_zone_btn_{selected_job_id}_{package_id}_{document_id}"):
                execute("DELETE FROM drawing_progress_zones WHERE id = ?", (delete_options[selected_delete],))
                st.success("Mapped zone deleted.")
                st.rerun()

def render_progress_billing_model(job_id, package_id=None, key_prefix="progress_model"):
    if not package_id:
        package_id = latest_takeoff_package_for_job(job_id)
    if not package_id:
        st.info("Create or generate a painting take-off first. The progress/billing model is built from the take-off lines.")
        return

    st.markdown("### Interactive Progress, Substrate & Billing Model")
    st.caption("Select any itemised sections with your mouse, view the selected m², substrate breakdown, labour/material projection and dollar value, then mark selected work as complete or partially complete.")

    c_model1, c_model2 = st.columns(2)
    if c_model1.button("Generate / Refresh Model from Take-off", key=f"{key_prefix}_refresh_model_{job_id}_{package_id}"):
        created = ensure_progress_sections_for_package(package_id, reset_values=False)
        st.success(f"Progress model refreshed. {created} new section(s) created.")
        refresh()
    reset_values = c_model2.checkbox("Reset section values pro-rata from contract", key=f"{key_prefix}_reset_values_{job_id}_{package_id}")
    if reset_values and c_model2.button("Apply Pro-rata Values", key=f"{key_prefix}_apply_reset_values_{job_id}_{package_id}"):
        ensure_progress_sections_for_package(package_id, reset_values=True)
        st.success("Section values reset from current contract value and approved variations.")
        refresh()

    sections_check = progress_sections_df(job_id, package_id)
    if sections_check.empty:
        ensure_progress_sections_for_package(package_id, reset_values=False)

    summary, sections = progress_model_summary(job_id, package_id)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Completed", f"{summary['completed_percent']:.1f}%")
    c2.metric("Completed m²", f"{summary['completed_m2']:,.1f}")
    c3.metric("Remaining m²", f"{summary['remaining_m2']:,.1f}")
    c4.metric("Billable Value", pb_money(summary["billable_value"]))
    c5.metric("Billed", pb_money(summary["billed_value"]))
    st.progress(min(max(summary["completed_percent"] / 100, 0), 1))

    claim_balance = summary["claim_available"]
    if claim_balance > 0:
        st.success(f"Estimated billable balance available: {pb_money(claim_balance)} ex GST.")
    elif claim_balance < 0:
        st.warning(f"Billed value is currently {pb_money(abs(claim_balance))} ahead of measured progress.")
    else:
        st.info("Billable value and billed value are currently balanced.")

    if sections.empty:
        st.info("No progress sections have been created yet.")
        return

    display_sections = sections.copy()
    numeric_cols = [
        "Total m2", "Completed m2", "Remaining m2", "Completed %", "Section Value Ex GST", "Billable Value Ex GST",
        "Remaining Value Ex GST", "Total Labour Hours", "Completed Labour Hours", "Remaining Labour Hours",
        "Total Paint Litres", "Completed Paint Litres", "Remaining Paint Litres"
    ]
    for col in numeric_cols:
        if col not in display_sections.columns:
            display_sections[col] = 0.0
        display_sections[col] = pd.to_numeric(display_sections[col], errors="coerce").fillna(0)

    selection_key = f"{key_prefix}_selected_section_ids_{job_id}_{package_id}"
    selected_ids = [int(x) for x in st.session_state.get(selection_key, []) if str(x).isdigit() or isinstance(x, int)]

    with st.sidebar.expander("Progress model selector", expanded=False):
        st.caption("Filter and select the exact parts of the job you want to view or mark complete.")
        area_values = sorted([str(x) for x in display_sections["Area"].fillna("").unique() if str(x).strip()])
        substrate_values = sorted([str(x) for x in display_sections["Substrate"].fillna("").unique() if str(x).strip()])
        labour_values = sorted([str(x) for x in display_sections["Labour Category"].fillna("").unique() if str(x).strip()])
        status_values = sorted([str(x) for x in display_sections["Status"].fillna("").unique() if str(x).strip()])
        area_filter = st.multiselect("Area", area_values, default=area_values, key=f"{key_prefix}_area_filter_{job_id}_{package_id}")
        substrate_filter = st.multiselect("Substrate", substrate_values, default=substrate_values, key=f"{key_prefix}_substrate_filter_{job_id}_{package_id}")
        labour_filter = st.multiselect("Labour", labour_values, default=labour_values, key=f"{key_prefix}_labour_filter_{job_id}_{package_id}")
        status_filter = st.multiselect("Status", status_values, default=status_values, key=f"{key_prefix}_status_filter_{job_id}_{package_id}")

    filtered_sections = display_sections.copy()
    if area_filter:
        filtered_sections = filtered_sections[filtered_sections["Area"].astype(str).isin(area_filter)]
    if substrate_filter:
        filtered_sections = filtered_sections[filtered_sections["Substrate"].astype(str).isin(substrate_filter)]
    if labour_filter:
        filtered_sections = filtered_sections[filtered_sections["Labour Category"].astype(str).isin(labour_filter)]
    if status_filter:
        filtered_sections = filtered_sections[filtered_sections["Status"].astype(str).isin(status_filter)]

    section_labels = {progress_section_label(row): int(row["ID"]) for _, row in filtered_sections.iterrows()}
    selected_label_defaults = [label for label, sid in section_labels.items() if sid in selected_ids]
    with st.sidebar.expander("Selected itemised sections", expanded=True):
        selected_labels = st.multiselect(
            "Select / deselect sections",
            list(section_labels.keys()),
            default=selected_label_defaults,
            key=f"{key_prefix}_section_multiselect_{job_id}_{package_id}",
        )
        selected_ids = [section_labels[label] for label in selected_labels]
        if st.button("Clear selected sections", key=f"{key_prefix}_clear_selected_{job_id}_{package_id}"):
            selected_ids = []
            st.session_state[selection_key] = []
            st.rerun()
    st.session_state[selection_key] = selected_ids

    st.markdown("### Mouse Select Sections")
    st.caption("Tick rows to select sections. The selected value, labour, paint and substrate totals update below.")
    selector_cols = [
        "ID", "Section Code", "Area", "Location / Area", "Substrate", "Labour Category", "Total m2",
        "Completed %", "Remaining m2", "Section Value Ex GST", "Billable Value Ex GST", "Remaining Value Ex GST", "Status"
    ]
    selector_df = filtered_sections[selector_cols].copy()
    selector_df.insert(0, "Select", selector_df["ID"].astype(int).isin(selected_ids))
    disabled_cols = [c for c in selector_df.columns if c != "Select"]
    edited_selector = st.data_editor(
        selector_df,
        hide_index=True,
        width="stretch",
        disabled=disabled_cols,
        key=f"{key_prefix}_mouse_selector_{job_id}_{package_id}",
    )
    try:
        selected_ids = edited_selector.loc[edited_selector["Select"] == True, "ID"].astype(int).tolist()
        st.session_state[selection_key] = selected_ids
    except Exception:
        pass

    selected_df = display_sections[display_sections["ID"].astype(int).isin(selected_ids)].copy()
    selected_summary = progress_selection_summary(selected_df)

    st.markdown("### Selected Section Projection")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Selected m²", f"{selected_summary['selected_m2']:,.1f}")
    s2.metric("Selected Value", pb_money(selected_summary["selected_value"]))
    s3.metric("Current Billable", pb_money(selected_summary["current_billable"]))
    s4.metric("Extra Billable if Complete", pb_money(selected_summary["available_if_complete"]))

    s5, s6, s7, s8 = st.columns(4)
    s5.metric("Projected Labour", f"{selected_summary['labour_hours']:,.1f} hrs")
    s6.metric("Remaining Labour", f"{selected_summary['remaining_labour_hours']:,.1f} hrs")
    s7.metric("Projected Paint", f"{selected_summary['paint_litres']:,.1f} L")
    s8.metric("Remaining Paint", f"{selected_summary['remaining_paint_litres']:,.1f} L")

    if selected_df.empty:
        st.info("Select one or more sections above to view substrate totals and mark them as complete.")
    else:
        st.markdown("#### Selected Breakdown by Substrate")
        selected_by_substrate = selected_df.groupby(["Area", "Substrate", "Labour Category"], dropna=False).agg({
            "Total m2": "sum",
            "Completed m2": "sum",
            "Remaining m2": "sum",
            "Section Value Ex GST": "sum",
            "Billable Value Ex GST": "sum",
            "Remaining Value Ex GST": "sum",
            "Total Labour Hours": "sum",
            "Remaining Labour Hours": "sum",
            "Total Paint Litres": "sum",
            "Remaining Paint Litres": "sum",
        }).reset_index()
        st.dataframe(selected_by_substrate, width="stretch", hide_index=True)

    st.markdown("### Mark Selected Work")
    action_col1, action_col2, action_col3 = st.columns(3)
    if action_col1.button("Mark Selected as Complete", key=f"{key_prefix}_mark_selected_complete_{job_id}_{package_id}", disabled=not bool(selected_ids), width="stretch"):
        for _, row in selected_df.iterrows():
            update_progress_section(int(row["ID"]), app_float(row["Total m2"]), app_float(row["Section Value Ex GST"]), "Complete", str(row.get("Notes") or ""))
        st.success("Selected sections marked as complete and will remain highlighted green.")
        refresh()

    bulk_percent = action_col2.number_input("Set selected to %", min_value=0.0, max_value=100.0, value=100.0, step=5.0, key=f"{key_prefix}_bulk_percent_{job_id}_{package_id}")
    if action_col2.button("Apply % to Selected", key=f"{key_prefix}_apply_selected_percent_{job_id}_{package_id}", disabled=not bool(selected_ids), width="stretch"):
        for _, row in selected_df.iterrows():
            completed_m2 = app_float(row["Total m2"]) * bulk_percent / 100.0
            status = "Complete" if bulk_percent >= 99.99 else "In Progress" if bulk_percent > 0 else "Not Started"
            update_progress_section(int(row["ID"]), completed_m2, app_float(row["Section Value Ex GST"]), status, str(row.get("Notes") or ""))
        st.success(f"Selected sections updated to {bulk_percent:.1f}% complete.")
        refresh()

    selected_group_m2 = action_col3.number_input("Completed m² across selected", min_value=0.0, value=0.0, step=1.0, key=f"{key_prefix}_bulk_group_m2_{job_id}_{package_id}")
    if action_col3.button("Apply m² Across Selected", key=f"{key_prefix}_apply_selected_m2_{job_id}_{package_id}", disabled=not bool(selected_ids), width="stretch"):
        total_selected_m2 = float(selected_df["Total m2"].sum()) if not selected_df.empty else 0.0
        if total_selected_m2 <= 0:
            st.error("Selected sections have no measurable m².")
        else:
            capped_group_m2 = min(max(selected_group_m2, 0.0), total_selected_m2)
            for _, row in selected_df.iterrows():
                section_total = app_float(row["Total m2"])
                completed_m2 = section_total * capped_group_m2 / total_selected_m2
                pct = completed_m2 / section_total * 100 if section_total else 0
                status = "Complete" if pct >= 99.99 else "In Progress" if pct > 0 else "Not Started"
                update_progress_section(int(row["ID"]), completed_m2, app_float(row["Section Value Ex GST"]), status, str(row.get("Notes") or ""))
            st.success(f"{capped_group_m2:,.1f} completed m² allocated across selected sections.")
            refresh()

    mapped_surfaces = building_model_surfaces_df(job_id, package_id)
    if mapped_surfaces.empty:
        st.info("No building-shaped 3D mapper surfaces found yet. Generate them to make the 3D render resemble the building more closely.")
        if st.button("Generate Building-Shaped 3D Model", key=f"{key_prefix}_generate_building_mapper_{job_id}_{package_id}"):
            generate_building_surfaces_from_takeoff(job_id, package_id, reset_existing=False)
            st.success("Building-shaped 3D model generated from the take-off.")
            st.rerun()
        render_progress_3d_model(display_sections, selected_ids, key_prefix=f"{key_prefix}_3d_{job_id}_{package_id}")
    else:
        render_building_mapper_3d(mapped_surfaces, selected_progress_ids=selected_ids, key_prefix=f"{key_prefix}_building_3d_{job_id}_{package_id}")
        if st.button("Open 3D Building Mapper to adjust shape", key=f"{key_prefix}_open_building_mapper_{job_id}_{package_id}"):
            st.session_state["go_to_menu"] = "3D Model Viewer"
            st.rerun()

    render_progress_visual_cards(display_sections, selected_ids, key_prefix=f"{key_prefix}_cards_{job_id}_{package_id}")

    st.markdown("### Progress by Substrate")
    by_substrate = display_sections.groupby(["Area", "Substrate"], dropna=False).agg({
        "Total m2": "sum",
        "Completed m2": "sum",
        "Remaining m2": "sum",
        "Section Value Ex GST": "sum",
        "Billable Value Ex GST": "sum",
        "Remaining Value Ex GST": "sum",
        "Total Labour Hours": "sum",
        "Remaining Labour Hours": "sum",
        "Total Paint Litres": "sum",
        "Remaining Paint Litres": "sum",
    }).reset_index()
    by_substrate["Completed %"] = by_substrate.apply(lambda r: round((r["Completed m2"] / r["Total m2"] * 100), 2) if r["Total m2"] else 0, axis=1)
    st.dataframe(by_substrate, width="stretch", hide_index=True)

    st.markdown("### Update One Section Exactly")
    section_options = {
        f"{row['Section Code']} | {row['Area']} | {row['Location / Area']} | {row['Substrate']} | {float(row['Total m2'] or 0):,.1f}m² | {float(row['Completed %'] or 0):.1f}% complete": int(row["ID"])
        for _, row in display_sections.iterrows()
    }
    selected_label = st.selectbox("Select section/substrate area", list(section_options.keys()), key=f"{key_prefix}_section_select_{job_id}_{package_id}")
    selected_id = section_options[selected_label]
    selected_row = display_sections[display_sections["ID"].astype(int) == int(selected_id)].iloc[0]

    with st.form(f"{key_prefix}_update_form_{job_id}_{package_id}_{selected_id}"):
        u1, u2, u3, u4 = st.columns(4)
        total_m2 = app_float(selected_row["Total m2"])
        current_completed = app_float(selected_row["Completed m2"])
        current_percent = app_float(selected_row["Completed %"])
        update_method = u1.selectbox("Update Method", ["Completed m²", "Completed %"], key=f"{key_prefix}_method_{selected_id}")
        if update_method == "Completed %":
            new_percent = u2.number_input("Completed %", min_value=0.0, max_value=100.0, step=5.0, value=float(current_percent), key=f"{key_prefix}_percent_{selected_id}")
            new_completed_m2 = round(total_m2 * new_percent / 100, 2)
            u3.metric("Completed m²", f"{new_completed_m2:,.2f}")
        else:
            new_completed_m2 = u2.number_input("Completed m²", min_value=0.0, max_value=float(max(total_m2, current_completed, 1.0)), step=1.0, value=float(current_completed), key=f"{key_prefix}_completed_m2_{selected_id}")
            new_percent = round((new_completed_m2 / total_m2) * 100, 2) if total_m2 else 0.0
            u3.metric("Completed %", f"{new_percent:.1f}%")
        new_value = u4.number_input("Section Value Ex GST", min_value=0.0, step=100.0, value=float(app_float(selected_row["Section Value Ex GST"])), key=f"{key_prefix}_section_value_{selected_id}")
        status_options = ["Not Started", "In Progress", "Complete", "On Hold", "Needs Review"]
        current_status = str(selected_row.get("Status") or "Not Started")
        status_index = status_options.index(current_status) if current_status in status_options else 0
        status = st.selectbox("Status", status_options, index=status_index, key=f"{key_prefix}_status_{selected_id}")
        notes = st.text_area("Notes / claim comments", value=str(selected_row.get("Notes") or ""), key=f"{key_prefix}_notes_{selected_id}")
        save_update = st.form_submit_button("Save Section Progress")
        if save_update:
            update_progress_section(selected_id, new_completed_m2, new_value, status, notes)
            st.success("Progress section updated.")
            refresh()

    st.markdown("### Full Progress Model")
    full_model = display_sections.copy()
    full_model["Selected"] = full_model["ID"].astype(int).isin(selected_ids).map({True: "✅", False: ""})
    full_model_view = full_model.drop(columns=["ID", "Package ID", "Takeoff Line ID"], errors="ignore")
    st.dataframe(style_progress_rows(full_model_view), width="stretch", hide_index=True)

    st.markdown("### Claim / Billing")
    claim_col1, claim_col2 = st.columns(2)
    claim_col1.metric("Measured Billable Value", pb_money(summary["billable_value"]))
    claim_col2.metric("Unbilled / Available to Claim", pb_money(summary["claim_available"]))
    claim_description = f"Progress claim from measured painting progress to {summary['completed_percent']:.1f}% complete"
    confirm_claim = st.checkbox("Confirm create draft claim for available billable balance", key=f"{key_prefix}_confirm_claim_{job_id}_{package_id}")
    if st.button("Create Draft Claim from Progress", key=f"{key_prefix}_create_claim_{job_id}_{package_id}"):
        if not confirm_claim:
            st.error("Tick confirm first so a duplicate claim is not created accidentally.")
        elif summary["claim_available"] <= 0:
            st.error("There is no positive unbilled value available to claim.")
        else:
            claim_no = f"PC-{jobhub_today().strftime('%Y%m%d')}-{int(package_id)}"
            execute("""
                INSERT INTO invoice_claims
                (job_id, claim_no, description, amount_ex_gst, invoice_date, due_date, paid_date, status, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job_id, claim_no, claim_description, round(summary["claim_available"], 2),
                str(jobhub_today()), "", "", "Draft",
                f"Generated from progress model package ID {package_id}. Review before sending.",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ))
            st.success(f"Draft claim {claim_no} created. Review it in Control Centre → Invoice / Claim Tracker before sending.")
            refresh()

    export_bytes = progress_export_excel(job_id, package_id)
    st.download_button(
        "Download Progress / Billing Model Excel",
        data=export_bytes,
        file_name=f"{safe_file_name(get_job_no_for_id(job_id))}_Progress_Billing_Model.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=f"{key_prefix}_export_{job_id}_{package_id}",
    )

def takeoff_import_page(default_job_id=None):
    pb_page_header(
        "Import Take-off / Model File",
        "Import the CSV or ZIP created by MeasureTakeoff Studio and create the JobHub progress model.",
        "Estimating"
    )
    st.info("Use this page for files created by PB MeasureTakeoff Studio. It creates the take-off package, progress sections and 3D model source data for JobHub.")
    job_options = get_job_options()
    if not job_options:
        st.warning("Create or link a job in JobHub first, then come back here to import the take-off/model file.")
        return

    labels = list(job_options.keys())
    index = 0
    if default_job_id:
        for i, label in enumerate(labels):
            if int(job_options[label]) == int(default_job_id):
                index = i
                break
    selected_job = st.selectbox("Select Job", labels, index=index, key=f"clean_import_job_select_{default_job_id or 'main'}")
    job_id = int(job_options[selected_job])

    uploaded = st.file_uploader(
        "Upload MeasureTakeoff Studio export ZIP or takeoff CSV",
        type=["zip", "csv"],
        key=f"clean_takeoff_import_file_{job_id}",
        help="Use the JobHub import ZIP from MeasureTakeoff Studio, or upload takeoff_lines.csv directly."
    )
    notes = st.text_area("Import notes", value="Imported from MeasureTakeoff Studio.", key=f"clean_takeoff_import_notes_{job_id}")

    st.markdown("#### What this import will create")
    st.write("• Painting take-off package")
    st.write("• Take-off lines")
    st.write("• Progress / billing sections")
    st.write("• Source data for the 3D progress model")

    if st.button("Import and create progress model", disabled=uploaded is None, key=f"clean_takeoff_import_button_{job_id}"):
        try:
            file_name = getattr(uploaded, "name", "uploaded_file")
            raw = uploaded.getvalue()
            csv_bytes = None
            csv_name = file_name

            if file_name.lower().endswith(".zip"):
                with zipfile.ZipFile(BytesIO(raw)) as zf:
                    names = [n for n in zf.namelist() if not n.endswith("/")]
                    preferred = [n for n in names if n.lower().endswith("takeoff_lines.csv")]
                    fallback = [n for n in names if n.lower().endswith(".csv")]
                    if preferred:
                        csv_name = preferred[0]
                    elif fallback:
                        csv_name = fallback[0]
                    else:
                        raise ValueError("The ZIP did not contain a CSV. Export again from MeasureTakeoff Studio and include takeoff_lines.csv.")
                    csv_bytes = zf.read(csv_name)
            else:
                csv_bytes = raw

            csv_file = BytesIO(csv_bytes)
            csv_file.name = csv_name
            package_id, imported_count = import_takeoff_csv_to_package(
                job_id,
                csv_file,
                source_name=csv_name,
                notes=notes,
            )
            try:
                generate_building_surfaces_from_takeoff(job_id, package_id, reset_existing=True)
            except Exception:
                pass
            st.session_state["go_to_menu"] = "Progress / Billing Model"
            st.success(f"Imported {imported_count} take-off line(s). Progress model created for {selected_job}.")
            st.rerun()
        except Exception as e:
            st.error(f"Could not import take-off/model file: {e}")

def progress_billing_model_page(default_job_id=None):
    pb_page_header(
        "Progress / Billing Model",
        "Generate a basic job model from the take-off, mark completed substrates, calculate remaining work and compare billable value against billed claims.",
        "Estimating"
    )
    job_options = get_job_options()
    if not job_options:
        st.info("Create a job first.")
        return
    labels = list(job_options.keys())
    index = 0
    if default_job_id:
        for i, label in enumerate(labels):
            if int(job_options[label]) == int(default_job_id):
                index = i
                break
    selected_job = st.selectbox("Select Job", labels, index=index, key=f"progress_job_select_{default_job_id or 'main'}")
    job_id = int(job_options[selected_job])
    render_quick_pdf_import_buttons(
        job_id,
        categories=pdf_import_categories_for_context("progress"),
        title="Import progress, claim, variation or sign-off PDFs",
        key_prefix=f"progress_pdf_import_{job_id}",
        expanded=False,
    )
    packages = progress_package_options(job_id)
    if not packages:
        st.info("No painting take-off package has been created for this job yet. Import the MeasureTakeoff Studio file first.")
        if st.button("Open Import Take-off / Model File", key=f"progress_open_takeoff_{job_id}"):
            st.session_state["go_to_menu"] = "Import Take-off / Model File"
            st.rerun()
        return
    selected_package = st.selectbox("Select Take-off Package / Model Source", list(packages.keys()), key=f"progress_package_select_{job_id}")
    package_id = int(packages[selected_package])
    render_progress_billing_model(job_id, package_id, key_prefix=f"progress_page_{job_id}")

def three_d_model_viewer_page(default_job_id=None):
    pb_page_header(
        "3D Model Viewer",
        "View the imported MeasureTakeoff Studio 3D/progress model. Generation and plan mapping are handled in the separate MeasureTakeoff app.",
        "Estimating"
    )
    st.info("This page is view-only for JobHub. Build or edit the model in PB MeasureTakeoff Studio, then import the export ZIP here using Estimating → Import Take-off / Model File.")

    job_options = get_job_options()
    if not job_options:
        st.warning("Create or link a job first, then import a model file.")
        return

    labels = list(job_options.keys())
    index = 0
    if default_job_id:
        for i, label in enumerate(labels):
            if int(job_options[label]) == int(default_job_id):
                index = i
                break
    selected_job = st.selectbox("Select Job", labels, index=index, key=f"three_d_viewer_job_select_{default_job_id or 'main'}")
    job_id = int(job_options[selected_job])

    packages = progress_package_options(job_id)
    if not packages:
        st.info("No imported take-off/model package exists for this job yet.")
        if st.button("Open Import Take-off / Model File", key=f"three_d_viewer_open_import_{job_id}"):
            st.session_state["go_to_menu"] = "Import Take-off / Model File"
            st.rerun()
        return

    selected_package = st.selectbox(
        "Select imported package / model source",
        list(packages.keys()),
        key=f"three_d_viewer_package_select_{job_id}",
    )
    package_id = int(packages[selected_package])

    surfaces = pd.DataFrame()
    try:
        surfaces = building_model_surfaces_df(job_id, package_id)
    except Exception:
        surfaces = pd.DataFrame()

    sections = progress_sections_df(job_id, package_id)
    if sections.empty:
        try:
            ensure_progress_sections_for_package(package_id, reset_values=False)
            sections = progress_sections_df(job_id, package_id)
        except Exception:
            pass

    c1, c2, c3, c4 = st.columns(4)
    if sections is not None and not sections.empty:
        c1.metric("Total m²", f"{float(sections['Total m2'].sum()):,.1f}")
        c2.metric("Completed m²", f"{float(sections['Completed m2'].sum()):,.1f}")
        total_val = float(sections['Section Value Ex GST'].sum()) if 'Section Value Ex GST' in sections.columns else 0
        billable_val = float(sections['Billable Value Ex GST'].sum()) if 'Billable Value Ex GST' in sections.columns else 0
        c3.metric("Total Value", pb_money(total_val))
        c4.metric("Billable", pb_money(billable_val))
    else:
        c1.metric("Total m²", "0")
        c2.metric("Completed m²", "0")
        c3.metric("Total Value", "$0")
        c4.metric("Billable", "$0")

    st.markdown("### Imported 3D / Progress View")
    if surfaces is not None and not surfaces.empty:
        render_building_mapper_3d(surfaces, selected_progress_ids=[], key_prefix=f"three_d_viewer_surface_{job_id}_{package_id}")
    elif sections is not None and not sections.empty:
        render_progress_3d_model(sections, selected_ids=[], key_prefix=f"three_d_viewer_progress_{job_id}_{package_id}")
    else:
        st.warning("This package has no model sections yet. Import a MeasureTakeoff Studio export ZIP or a takeoff_lines.csv file first.")

    st.markdown("### Model Sections")
    if sections is not None and not sections.empty:
        st.dataframe(sections.drop(columns=["ID", "Package ID", "Takeoff Line ID"], errors="ignore"), width="stretch", hide_index=True)
    else:
        st.info("No sections to show.")
