from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import streamlit as st

from .auth import can_manage, current_user, is_admin, login, logout_button
from .db import Database
from .pages import (
    AppContext,
    builders_page,
    dashboard_page,
    employees_page,
    equipment_page,
    estimating_page,
    external_page,
    job_files_page,
    jobs_page,
    materials_page,
    products_page,
    reports_page,
    system_page,
    timesheets_page,
    users_page,
)
from .schema import ensure_schema
from .ui import install_style, show_notice


BUILD = "2026.08.05-lean-rewrite-v2"


def _storage_root() -> Path:
    configured = os.getenv("DATA_DIR", "").strip()
    if configured:
        return Path(configured)
    render_disk = Path("/var/data")
    return render_disk if render_disk.exists() else Path.cwd() / "data"


def _initialise_retained_modules(db: Database) -> tuple[str, ...]:
    """Run retained module migrations once without importing the old guard package."""
    warnings: list[str] = []
    initialisers: list[tuple[str, Callable[[], None]]] = []
    try:
        from jobhub_enterprise import ensure_enterprise_schema
        initialisers.append(("Operations Hub", lambda: ensure_enterprise_schema(db.connect)))
    except Exception as exc:
        warnings.append(f"Operations Hub schema loader: {exc}")
    try:
        from jobhub_v2.schema import ensure_v2_schema
        initialisers.append(("JobHub V2", lambda: ensure_v2_schema(db.connect)))
    except Exception as exc:
        warnings.append(f"JobHub V2 schema loader: {exc}")
    try:
        from jobhub_v4.schema import ensure_v4_schema
        initialisers.append(("Painting Intelligence", lambda: ensure_v4_schema(db.connect)))
    except Exception as exc:
        warnings.append(f"Painting Intelligence schema loader: {exc}")
    try:
        from pb_jobhub_visual_scheduler import init_linked_schema
        initialisers.append(("Staff Scheduler", init_linked_schema))
    except Exception as exc:
        warnings.append(f"Staff Scheduler schema loader: {exc}")

    for label, initialise in initialisers:
        try:
            initialise()
        except Exception as exc:
            warnings.append(f"{label} schema initialisation: {exc}")
    return tuple(warnings)


@st.cache_resource(show_spinner=False)
def _runtime(database_url: str, data_dir_text: str) -> AppContext:
    data_dir = Path(data_dir_text)
    data_dir.mkdir(parents=True, exist_ok=True)
    job_files = data_dir / "job_files"
    job_files.mkdir(parents=True, exist_ok=True)
    db = Database(database_url, str(data_dir / "jobhub.db"))
    ensure_schema(db)
    startup_warnings = _initialise_retained_modules(db)
    return AppContext(
        db=db,
        data_dir=data_dir,
        job_files_dir=job_files,
        startup_warnings=startup_warnings,
    )


def _menu_options() -> list[str]:
    role = str(current_user().get("role") or "employee").lower()
    employee = ["Dashboard", "Field Mode", "Timesheets", "Job Files"]
    manager = [
        "Dashboard",
        "Jobs",
        "Builders & Clients",
        "Employees",
        "Products",
        "Staff Scheduler",
        "Timesheets",
        "Materials",
        "Equipment",
        "Estimating",
        "Job Progress",
        "Job Files",
        "Operations Hub",
        "Painting Intelligence",
        "Reports",
    ]
    admin = manager + ["User Access", "System"]
    return admin if role == "admin" else manager if role == "manager" else employee


def _dispatch(ctx: AppContext, page: str) -> None:
    local_pages: dict[str, Callable[[AppContext], None]] = {
        "Dashboard": dashboard_page,
        "Jobs": jobs_page,
        "Builders & Clients": builders_page,
        "Employees": employees_page,
        "Products": products_page,
        "Timesheets": timesheets_page,
        "Materials": materials_page,
        "Equipment": equipment_page,
        "Estimating": estimating_page,
        "Job Files": job_files_page,
        "Reports": reports_page,
        "User Access": users_page,
        "System": system_page,
    }
    handler = local_pages.get(page)
    if handler is not None:
        handler(ctx)
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
    current = st.session_state.get("lean_menu")
    if current not in options:
        current = options[0]
    page = st.sidebar.radio("Navigation", options, index=options.index(current), key="lean_menu")
    logout_button()

    try:
        _dispatch(ctx, page)
    except Exception as exc:
        st.error("This page encountered an error.")
        st.exception(exc)


__all__ = ["run"]
