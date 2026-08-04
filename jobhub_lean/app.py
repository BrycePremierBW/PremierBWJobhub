from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Callable

import streamlit as st


def _isolate_legacy_jobhub_package() -> None:
    """Allow legacy helper imports without executing the old guard installers."""
    if "jobhub" in sys.modules:
        return
    package = types.ModuleType("jobhub")
    package.__package__ = "jobhub"
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "jobhub")]
    sys.modules["jobhub"] = package


_isolate_legacy_jobhub_package()

from .auth import current_user, login, logout_button
from .db import Database
from .migrations import ensure_compatibility_schema
from .mobile import install_mobile_shell, render_phone_push_opt_in
from .pages import (
    AppContext,
    builders_page,
    dashboard_page,
    employee_portal_page,
    employees_page,
    equipment_page,
    estimating_page,
    external_page,
    job_files_page,
    job_pack_import_page,
    jobs_page,
    materials_page,
    po_upload_page,
    products_page,
    reports_page,
    setup_page,
    staff_requests_page,
    system_page,
    timesheets_page,
    users_page,
)
from .schema import ensure_schema
from .ui import install_style, show_notice


BUILD = "2026.08.05-lean-rewrite-v10"

LEGACY_ROUTE_ALIASES = {
    "Job Folders": "Job Files",
    "Estimate Working Sheet": "Estimating",
    "Job Progress Tracker": "Job Progress",
    "Employee Portal": "Employee Portal",
    "Staff Requests": "Staff Requests",
}


def _storage_root() -> Path:
    configured = os.getenv("DATA_DIR", "").strip()
    if configured:
        return Path(configured)
    render_disk = Path("/var/data")
    return render_disk if render_disk.exists() else Path.cwd() / "data"


@st.cache_resource(show_spinner=False)
def _runtime(database_url: str, data_dir_text: str) -> AppContext:
    """Initialise the lean core and additive legacy-database migrations once."""
    data_dir = Path(data_dir_text)
    data_dir.mkdir(parents=True, exist_ok=True)
    job_files = data_dir / "job_files"
    job_files.mkdir(parents=True, exist_ok=True)
    db = Database(database_url, str(data_dir / "jobhub.db"))
    ensure_schema(db)
    ensure_compatibility_schema(db)
    return AppContext(db=db, data_dir=data_dir, job_files_dir=job_files)


def _menu_options() -> list[str]:
    role = str(current_user().get("role") or "employee").lower()
    employee = ["Employee Portal", "Dashboard", "Field Mode", "Timesheets", "Job Files"]
    manager = [
        "Dashboard", "Jobs", "Upload PO", "Job Pack Import", "Builders & Clients",
        "Employees", "Staff Requests", "Products", "Staff Scheduler", "Timesheets",
        "Materials", "Equipment", "Estimating", "Job Progress", "Job Files",
        "Operations Hub", "Painting Intelligence", "Reports",
    ]
    admin = manager + ["Setup & Defaults", "User Access", "System"]
    return admin if role == "admin" else manager if role == "manager" else employee


def _apply_requested_route(options: list[str]) -> None:
    requested = st.session_state.pop("go_to_menu", None)
    if not requested:
        return
    target = LEGACY_ROUTE_ALIASES.get(str(requested), str(requested))
    if target in options:
        st.session_state["lean_menu"] = target


def _render_employee_request_summary(ctx: AppContext) -> None:
    employee_id = int(ctx.user.get("employee_id") or 0)
    if not employee_id:
        return
    requests = ctx.db.query(
        """
        SELECT title
        FROM staff_requests
        WHERE employee_id=?
          AND LOWER(COALESCE(status,'Requested')) NOT IN ('completed','cancelled')
        ORDER BY due_at,id DESC
        LIMIT 8
        """,
        (employee_id,),
    )
    if requests.empty:
        return
    titles = [str(value).strip() for value in requests["title"].tolist() if str(value).strip()]
    if titles:
        st.info("Open requests: " + " | ".join(titles))


def _dispatch(ctx: AppContext, page: str) -> None:
    local_pages: dict[str, Callable[[AppContext], None]] = {
        "Dashboard": dashboard_page,
        "Employee Portal": employee_portal_page,
        "Jobs": jobs_page,
        "Upload PO": po_upload_page,
        "Job Pack Import": job_pack_import_page,
        "Builders & Clients": builders_page,
        "Employees": employees_page,
        "Staff Requests": staff_requests_page,
        "Products": products_page,
        "Timesheets": timesheets_page,
        "Materials": materials_page,
        "Equipment": equipment_page,
        "Estimating": estimating_page,
        "Job Files": job_files_page,
        "Reports": reports_page,
        "Setup & Defaults": setup_page,
        "User Access": users_page,
        "System": system_page,
    }
    handler = local_pages.get(page)
    if handler is not None:
        handler(ctx)
        if page == "Employee Portal":
            _render_employee_request_summary(ctx)
        return
    external_page(ctx, page)


def run() -> None:
    st.set_page_config(
        page_title="Premier Brushworks JobHub",
        page_icon="🎨",
        layout="wide",
        initial_sidebar_state="auto",
    )
    install_style()
    install_mobile_shell()
    data_dir = _storage_root()
    ctx = _runtime(os.getenv("DATABASE_URL", "").strip(), str(data_dir))
    login(ctx.db)
    show_notice()

    st.sidebar.markdown("## Premier Brushworks")
    st.sidebar.caption(f"JobHub · {BUILD}")
    user = current_user()
    st.sidebar.write(f"**{user.get('employee_name') or user.get('username')}**")
    st.sidebar.caption(str(user.get("role") or "employee").title())
    options = _menu_options()
    _apply_requested_route(options)
    current = st.session_state.get("lean_menu")
    if current not in options:
        st.session_state["lean_menu"] = options[0]
    page = st.sidebar.radio("Navigation", options, key="lean_menu")
    render_phone_push_opt_in()
    logout_button()

    try:
        _dispatch(ctx, page)
    except Exception as exc:
        st.error("This page encountered an error.")
        st.exception(exc)


__all__ = ["run"]
