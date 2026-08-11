"""Generic SWMS attachment and electronic sign-off for JobHub."""

from __future__ import annotations

import os
import re
import sys
import textwrap
from datetime import datetime
from jobhub_time import jobhub_now
from pathlib import Path
from typing import Any

import pandas as pd

ACK_TEXT = (
    "I have read and understood this SWMS. I confirm I have the skills and training "
    "to conduct the task as described and agree to comply with the controls, safe "
    "work instructions and PPE requirements."
)

EMPLOYEE_TABS = [
    "My Job Info", "Requests", "Submit Timesheet", "View Equipment",
    "Generate Forms", "Upload Photos", "Change Password",
]


def _app(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _st() -> Any:
    return sys.modules.get("streamlit")


def _query(sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    fn = _app("df_query") or _app("safe_df_query")
    return fn(sql, params) if callable(fn) else pd.DataFrame()


def _execute(sql: str, params: tuple[Any, ...] = ()) -> None:
    fn = _app("execute")
    if not callable(fn):
        raise RuntimeError("JobHub database helper is not available.")
    fn(sql, params)


def _safe_name(value: Any) -> str:
    fn = _app("safe_file_name")
    if callable(fn):
        try:
            return fn(value)
        except Exception:
            pass
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("_") or "swms"


def _feedback(kind: str, message: str) -> None:
    st = _st()
    fn = _app("pb_success" if kind == "success" else "pb_error")
    if callable(fn):
        fn(message)
    elif st is not None:
        getattr(st, "success" if kind == "success" else "error")(message)


def _rerun() -> None:
    fn = _app("refresh") or _app("pb_rerun")
    if callable(fn):
        fn()


def ensure_swms_schema() -> None:
    _execute("""
        CREATE TABLE IF NOT EXISTS job_swms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            swms_no TEXT,
            principal_contractor TEXT,
            job_description TEXT,
            file_name TEXT,
            file_path TEXT,
            status TEXT DEFAULT 'Issued',
            created_at TEXT NOT NULL,
            created_by TEXT,
            notes TEXT
        )
    """)
    _execute("""
        CREATE TABLE IF NOT EXISTS job_swms_signatures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_swms_id INTEGER NOT NULL,
            job_id INTEGER NOT NULL,
            employee_id INTEGER,
            employee_name TEXT NOT NULL,
            signed_by_user_id INTEGER,
            signature_text TEXT NOT NULL,
            general_induction_card TEXT,
            acknowledgement_text TEXT NOT NULL,
            signed_at TEXT NOT NULL,
            notes TEXT
        )
    """)
    try:
        _execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_job_swms_signature_unique
            ON job_swms_signatures(job_swms_id, employee_id)
            WHERE employee_id IS NOT NULL
        """)
    except Exception:
        pass


def _job(job_id: int) -> dict[str, Any]:
    df = _query("""
        SELECT j.id,j.job_no,j.job_name,j.site_address,j.leading_hand,
               COALESCE(bc.name,'') AS builder_client
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id=j.builder_client_id
        WHERE j.id=? LIMIT 1
    """, (int(job_id),))
    return df.iloc[0].to_dict() if not df.empty else {"id": int(job_id)}


def _job_folder(job_id: int, job_no: str) -> Path:
    fn = _app("get_job_folder")
    if callable(fn):
        try:
            folder = Path(fn(job_no or f"job_{job_id}"))
            folder.mkdir(parents=True, exist_ok=True)
            return folder
        except Exception:
            pass
    root = Path(str(_app("JOB_FILES_DIR", os.getenv("DATA_DIR", "/var/data"))))
    folder = root / _safe_name(job_no or f"job_{job_id}")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _wrap(c: Any, text: str, x: float, y: float, chars: int, size: int = 8, lead: int = 11) -> float:
    from reportlab.lib.units import mm
    c.setFont("Helvetica", size)
    for line in textwrap.wrap(str(text or ""), chars):
        c.drawString(x, y, line)
        y -= lead
    return y - 1 * mm


def _make_swms_pdf(job_id: int, principal: str, description: str, prepared_by: str, notes: str) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    job = _job(job_id)
    job_no = str(job.get("job_no") or f"JOB-{job_id}")
    job_name = str(job.get("job_name") or "")
    address = str(job.get("site_address") or "")
    builder = str(job.get("builder_client") or "")
    leading_hand = str(job.get("leading_hand") or "Site Supervisor")
    swms_no = f"SWMS-{job_no}-{jobhub_now().strftime('%Y%m%d')}"
    folder = _job_folder(job_id, job_no)
    pdf_path = folder / f"{_safe_name(job_no)}_generic_swms_{jobhub_now().strftime('%Y%m%d_%H%M%S')}.pdf"

    page_w, page_h = A4
    margin = 18 * mm
    c = canvas.Canvas(str(pdf_path), pagesize=A4)

    def header() -> None:
        c.setFillColor(colors.HexColor("#e6dcd0"))
        c.rect(0, page_h - 15 * mm, page_w, 15 * mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#1f1f1f"))
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, page_h - 10 * mm, "Premier Brushworks - Safe Work Method Statement")
        c.setFont("Helvetica", 7)
        c.drawRightString(page_w - margin, page_h - 10 * mm, f"Page {c.getPageNumber()}")

    def new_page() -> float:
        c.showPage(); header(); return page_h - 25 * mm

    def field(label: str, value: str, x: float, y: float, width: float) -> float:
        c.setFont("Helvetica-Bold", 7); c.drawString(x, y, label)
        y -= 4 * mm
        c.setFont("Helvetica", 8); c.drawString(x, y, str(value or ""))
        c.line(x, y - 1 * mm, x + width, y - 1 * mm)
        return y - 7 * mm

    header()
    y = page_h - 26 * mm
    c.setFont("Helvetica-Bold", 18); c.drawCentredString(page_w / 2, y, "SAFE WORK METHOD STATEMENT (SWMS)")
    y -= 8 * mm
    c.setFont("Helvetica-Bold", 11); c.drawCentredString(page_w / 2, y, "Generic Painting & Decorating Works")
    y -= 12 * mm
    col_w = (page_w - 2 * margin - 8 * mm) / 2
    left = [("Principal Contractor", principal or builder), ("Project", f"{job_no} - {job_name}".strip(" -")), ("Project Address", address), ("Job Description", description or "Painting and decorating works")]
    right = [("SWMS #", swms_no), ("Date Prepared", jobhub_now().strftime("%Y-%m-%d")), ("Prepared By", prepared_by), ("Responsible Person", leading_hand)]
    y_left = y_right = y
    for label, value in left:
        y_left = field(label, value, margin, y_left, col_w)
    for label, value in right:
        y_right = field(label, value, margin + col_w + 8 * mm, y_right, col_w)
    y = min(y_left, y_right) - 4 * mm

    sections = {
        "High Risk Construction Work - confirm as applicable": [
            "Risk of a person falling more than 2 metres",
            "Movement of powered mobile plant",
            "Work on/near electrical installations or services",
            "Work on/near road or other traffic corridor",
            "Chemical/fuel/refrigerant lines or hazardous substances",
            "Other site-specific high risk work",
        ],
        "Mandatory / Task Specific PPE": [
            "Hard hat", "Safety boots", "Gloves", "Hi-vis", "Eye protection",
            "Ear protection", "Dust mask / respirator", "Face shield / coveralls as required",
        ],
    }
    for heading, items in sections.items():
        c.setFont("Helvetica-Bold", 11); c.drawString(margin, y, heading); y -= 7 * mm
        for item in items:
            c.rect(margin, y - 1, 7, 7)
            c.setFont("Helvetica", 8); c.drawString(margin + 12, y, item)
            y -= 5 * mm
        y -= 4 * mm

    y = new_page()
    c.setFont("Helvetica-Bold", 11); c.drawString(margin, y, "Four Step Hazard Identification Tool"); y -= 7 * mm
    for line in [
        "1. LOOK - What am I about to do? What has changed in my work environment?",
        "2. THINK - What could go wrong? What can I do to make the job safer?",
        "3. CHOOSE - Can the job be done safely?",
        "4. ACT - Call your supervisor immediately if you think the job is unsafe.",
    ]:
        c.setFont("Helvetica", 8); c.drawString(margin, y, line); y -= 5 * mm

    y -= 5 * mm
    c.setFont("Helvetica-Bold", 11); c.drawString(margin, y, "Safe Work Steps and Controls"); y -= 8 * mm
    rows = [
        ("Planning the works", "Insufficient site/task knowledge; inexperienced workers; poor communication.", "Site induction, competent workers, read/understand SWMS, follow principal contractor policies."),
        ("Manual handling / loading", "Muscle strain from heavy or awkward loads.", "Use team lifts over 20 kg, keep load close, bend knees, use lifting aids."),
        ("Site establishment", "Slips, trips and disorganised work area.", "Establish storage, use drop sheets, keep site tidy and remove rubbish."),
        ("Dusting / cleaning surfaces", "Dust inhalation and eye splashes.", "Use P2 mask, eye protection, wet down only where safe, vacuum where practicable."),
        ("Priming / top coats", "Substance exposure, fumes, ladder falls, repetitive strain.", "Use PPE, ventilate, barricade work zone, follow SDS and manufacturer instructions."),
        ("Clean-up / no washout", "Environmental pollution and chemical exposure.", "No washout to drains, dry/dispose correctly, keep spill kit nearby and report spills."),
        ("EWP / access equipment", "Rollover, falls, collision, falling objects, entanglement.", "Daily pre-start, flat surfaces, exclusion zone, spotter where needed, secure tools/materials."),
        ("Platform ladders", "Falls, falling objects and manual handling.", "Inspect before use, three points of contact, stand only on platform, do not overreach."),
        ("Compressor / spray gun", "Noise, fire/explosion, electricity, hose whip and substance exposure.", "Wear hearing/respiratory protection, inspect hoses/gauges, use tools as intended."),
    ]
    for activity, hazard, control in rows:
        if y < 32 * mm:
            y = new_page()
        c.setFillColor(colors.HexColor("#e6dcd0")); c.rect(margin, y - 4 * mm, page_w - 2 * margin, 8 * mm, fill=1, stroke=0)
        c.setFillColor(colors.black); c.setFont("Helvetica-Bold", 8); c.drawString(margin + 2 * mm, y - 1.5 * mm, activity)
        y -= 8 * mm
        c.setFont("Helvetica-Bold", 7); c.drawString(margin, y, "Hazards:")
        c.setFont("Helvetica", 7); c.drawString(margin + 23 * mm, y, hazard); y -= 4 * mm
        c.setFont("Helvetica-Bold", 7); c.drawString(margin, y, "Controls:")
        y = _wrap(c, control, margin + 23 * mm, y, 92, size=7, lead=10)
        c.setFont("Helvetica", 7); c.drawString(margin, y, "Responsible: Site Supervisor    Risk: Initial Medium/High - Residual Low")
        y -= 8 * mm

    y = new_page()
    c.setFont("Helvetica-Bold", 11); c.drawString(margin, y, "Emergency Response, Review and Worker Sign-off"); y -= 7 * mm
    for line in [
        "CALL 000 IMMEDIATELY for life-threatening emergencies.",
        "Follow the site-specific emergency management plan where applicable.",
        "Workers must have access to first aid, M/SDS, communications, fire protection and rescue equipment where required.",
        "Review controls if work changes, new hazards are identified, after an incident, or if controls are not effective.",
    ]:
        y = _wrap(c, line, margin, y, 105, size=8, lead=11)
    if notes:
        y -= 2 * mm
        c.setFont("Helvetica-Bold", 9); c.drawString(margin, y, "Job-specific notes"); y -= 5 * mm
        y = _wrap(c, notes, margin, y, 105, size=8, lead=11)
    y -= 4 * mm
    c.setFont("Helvetica-Bold", 11); c.drawString(margin, y, "Worker Electronic Acknowledgement"); y -= 6 * mm
    y = _wrap(c, ACK_TEXT, margin, y, 105, size=8, lead=11)
    y -= 2 * mm
    for n in range(1, 8):
        if y < 25 * mm:
            y = new_page()
        c.setFont("Helvetica", 7)
        c.drawString(margin, y, f"{n}. Name: ____________________  Card #: __________  Signature/typed name: ____________________  Date/Time: __________")
        y -= 7 * mm
    c.save()
    return pdf_path


def _attach(job_id: int, pdf_path: Path) -> None:
    fn = _app("attach_document_to_job")
    if callable(fn):
        fn(int(job_id), "SWMS", str(pdf_path), notes="Generic SWMS generated in JobHub.", mime_type="application/pdf")
    else:
        _execute("""
            INSERT INTO job_documents (job_id,document_type,file_name,file_path,created_at,notes,mime_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (int(job_id), "SWMS", pdf_path.name, str(pdf_path), jobhub_now().strftime("%Y-%m-%d %H:%M:%S"), "Generic SWMS generated in JobHub.", "application/pdf"))


def create_swms_for_job(job_id: int, principal: str, description: str, prepared_by: str, notes: str) -> int:
    ensure_swms_schema()
    pdf_path = _make_swms_pdf(job_id, principal, description, prepared_by, notes)
    _attach(job_id, pdf_path)
    job = _job(job_id)
    swms_no = f"SWMS-{job.get('job_no') or job_id}-{jobhub_now().strftime('%Y%m%d')}"
    title = f"Generic Painting SWMS - {job.get('job_no') or job_id}"
    created_at = jobhub_now().strftime("%Y-%m-%d %H:%M:%S")
    _execute("""
        INSERT INTO job_swms (job_id,title,swms_no,principal_contractor,job_description,file_name,file_path,status,created_at,created_by,notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (int(job_id), title, swms_no, principal or "", description or "Painting and decorating works", pdf_path.name, str(pdf_path), "Issued", created_at, prepared_by or "", notes or ""))
    found = _query("SELECT id FROM job_swms WHERE file_path=? ORDER BY id DESC LIMIT 1", (str(pdf_path),))
    swms_id = int(found.iloc[0]["id"]) if not found.empty else 0
    audit = _app("record_audit_event")
    if callable(audit):
        audit("swms_created", "job_swms", swms_id, {"job_id": int(job_id)})
    return swms_id


def _job_options(employee_mode: bool) -> dict[str, int]:
    user = (_app("get_current_user", lambda: {})() or {})
    emp_id = user.get("employee_id")
    if employee_mode and emp_id:
        fn = _app("get_employee_job_options")
        if callable(fn):
            try:
                options = fn(emp_id) or {}
                return {str(k): int(v) for k, v in options.items()}
            except Exception:
                pass
    fn = _app("get_job_options")
    if callable(fn):
        return {str(k): int(v) for k, v in (fn() or {}).items()}
    return {}


def _swms_rows(job_id: int) -> pd.DataFrame:
    ensure_swms_schema()
    return _query("""
        SELECT id,title AS "Title",swms_no AS "SWMS #",status AS "Status",
               created_at AS "Created At",created_by AS "Created By",file_name,file_path,notes
        FROM job_swms WHERE job_id=? ORDER BY id DESC
    """, (int(job_id),))


def _signature_rows(job_id: int) -> pd.DataFrame:
    ensure_swms_schema()
    return _query("""
        SELECT s.signed_at AS "Signed At",s.employee_name AS "Employee",s.signature_text AS "Signature",
               s.general_induction_card AS "Card #",w.title AS "SWMS",s.notes AS "Notes"
        FROM job_swms_signatures s JOIN job_swms w ON w.id=s.job_swms_id
        WHERE s.job_id=? ORDER BY s.signed_at DESC,s.id DESC
    """, (int(job_id),))


def render_swms_panel(employee_mode: bool = True, key_prefix: str = "employee_swms") -> None:
    st = _st()
    if st is None:
        return
    try:
        ensure_swms_schema()
    except Exception as exc:
        st.warning(f"SWMS tools could not start: {exc}")
        return
    with st.container(border=True):
        st.markdown("### SWMS / Safety Sign-off")
        st.caption("Attach a generic painting SWMS to the job, download it, and record each employee's electronic acknowledgement.")
        options = _job_options(employee_mode)
        if not options:
            st.info("No jobs are available for SWMS access.")
            return
        label = st.selectbox("SWMS Job", list(options.keys()), key=f"{key_prefix}_job")
        job_id = int(options[label])
        user = (_app("get_current_user", lambda: {})() or {})
        employee_id = user.get("employee_id")
        employee_name = str(user.get("employee_name") or user.get("username") or "")
        job = _job(job_id)

        with st.expander("Create / attach generic SWMS to this job", expanded=False):
            principal = st.text_input("Principal Contractor", value=str(job.get("builder_client") or ""), key=f"{key_prefix}_principal_{job_id}")
            description = st.text_input("Job Description", value="Painting and decorating works", key=f"{key_prefix}_description_{job_id}")
            prepared_by = st.text_input("Prepared By", value=employee_name, key=f"{key_prefix}_prepared_by_{job_id}")
            notes = st.text_area("Job-specific SWMS notes", placeholder="Access, live site, EWP, scaffold, spraying, special PPE, SDS notes, etc.", key=f"{key_prefix}_notes_{job_id}")
            if st.button("Generate and attach SWMS to this job", type="primary", key=f"{key_prefix}_create_{job_id}"):
                try:
                    swms_id = create_swms_for_job(job_id, principal, description, prepared_by, notes)
                    _feedback("success", f"SWMS created and attached to this job. SWMS record #{swms_id}.")
                    _rerun()
                except Exception as exc:
                    _feedback("error", f"Could not create SWMS: {exc}")

        rows = _swms_rows(job_id)
        if rows.empty:
            st.info("No SWMS has been attached to this job yet.")
            return
        st.dataframe(rows.drop(columns=["file_path", "notes"], errors="ignore"), width="stretch", hide_index=True, key=f"{key_prefix}_table_{job_id}")
        choices = {f"#{int(r['id'])} - {r['Title']} ({r['Created At']})": int(r["id"]) for _, r in rows.iterrows()}
        selected = st.selectbox("SWMS to view/sign", list(choices.keys()), key=f"{key_prefix}_selected_{job_id}")
        swms_id = int(choices[selected])
        row = rows[rows["id"].astype(int) == swms_id].iloc[0]
        path = Path(str(row.get("file_path") or ""))
        if path.exists():
            try:
                st.download_button("Download selected SWMS PDF", data=path.read_bytes(), file_name=str(row.get("file_name") or path.name), mime="application/pdf", key=f"{key_prefix}_download_{swms_id}", width="stretch")
            except TypeError:
                st.download_button("Download selected SWMS PDF", data=path.read_bytes(), file_name=str(row.get("file_name") or path.name), mime="application/pdf", key=f"{key_prefix}_download_{swms_id}")
        else:
            st.warning("The SWMS record exists, but the PDF file is not available on this server storage.")

        existing = pd.DataFrame()
        if employee_id:
            existing = _query("SELECT id,signed_at,signature_text FROM job_swms_signatures WHERE job_swms_id=? AND employee_id=? LIMIT 1", (swms_id, int(employee_id)))
        if employee_id and not existing.empty:
            signed = existing.iloc[0]
            st.success(f"You signed this SWMS on {signed['signed_at']} as {signed['signature_text']}.")
        else:
            st.markdown("#### Sign SWMS electronically")
            st.caption("This is a JobHub electronic acknowledgement, not a certificate-based cryptographic PDF signature.")
            sig = st.text_input("Type your full name as your electronic signature", value=employee_name, key=f"{key_prefix}_sig_{swms_id}")
            card = st.text_input("General Induction Card Number / White Card (optional)", key=f"{key_prefix}_card_{swms_id}")
            sign_notes = st.text_area("Signature notes (optional)", key=f"{key_prefix}_sign_notes_{swms_id}")
            accepted = st.checkbox(ACK_TEXT, key=f"{key_prefix}_ack_{swms_id}")
            if st.button("Sign / acknowledge this SWMS", type="primary", disabled=not accepted or not sig.strip(), key=f"{key_prefix}_sign_{swms_id}"):
                try:
                    signed_at = jobhub_now().strftime("%Y-%m-%d %H:%M:%S")
                    _execute("""
                        INSERT INTO job_swms_signatures
                        (job_swms_id,job_id,employee_id,employee_name,signed_by_user_id,signature_text,general_induction_card,acknowledgement_text,signed_at,notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(job_swms_id, employee_id) DO UPDATE SET
                            employee_name=excluded.employee_name,signed_by_user_id=excluded.signed_by_user_id,
                            signature_text=excluded.signature_text,general_induction_card=excluded.general_induction_card,
                            acknowledgement_text=excluded.acknowledgement_text,signed_at=excluded.signed_at,notes=excluded.notes
                    """, (swms_id, job_id, int(employee_id) if employee_id else None, sig.strip(), int(user.get("id")) if user.get("id") else None, sig.strip(), card.strip(), ACK_TEXT, signed_at, sign_notes.strip()))
                    audit = _app("record_audit_event")
                    if callable(audit):
                        audit("swms_signed", "job_swms", swms_id, {"job_id": job_id, "employee_id": employee_id})
                    _feedback("success", "SWMS electronic acknowledgement saved.")
                    _rerun()
                except Exception as exc:
                    _feedback("error", f"Could not save SWMS signature: {exc}")

        signatures = _signature_rows(job_id)
        if not signatures.empty:
            with st.expander("SWMS signature register for this job", expanded=False):
                st.dataframe(signatures, width="stretch", hide_index=True, key=f"{key_prefix}_register_{job_id}")


class _TabProxy:
    def __init__(self, tab: Any) -> None:
        self._tab = tab
    def __getattr__(self, name: str) -> Any:
        return getattr(self._tab, name)
    def __enter__(self) -> Any:
        return self._tab.__enter__()
    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        try:
            if exc_type is None:
                render_swms_panel(employee_mode=True, key_prefix="employee_swms")
        finally:
            res = self._tab.__exit__(exc_type, exc, tb)
        return res


def install_swms_guard() -> bool:
    st = _st()
    if st is None:
        return False
    original_tabs = getattr(st, "tabs", None)
    if original_tabs is None or getattr(original_tabs, "_pb_swms_guard", False):
        return False
    def pb_swms_tabs(labels: Any, *args: Any, **kwargs: Any):
        tabs = list(original_tabs(labels, *args, **kwargs))
        try:
            label_list = [str(v) for v in labels]
        except Exception:
            return tabs
        if label_list == EMPLOYEE_TABS:
            return [_TabProxy(tab) if label == "Generate Forms" else tab for label, tab in zip(label_list, tabs)]
        return tabs
    pb_swms_tabs._pb_swms_guard = True
    pb_swms_tabs._pb_original_tabs = original_tabs
    st.tabs = pb_swms_tabs
    return True
