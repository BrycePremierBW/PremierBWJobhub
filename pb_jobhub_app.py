"""Premier Brushworks JobHub entry point.

The application is intentionally thin: startup, role-aware navigation and page dispatch.
Business logic lives in the jobhub package.
"""
from __future__ import annotations

from jobhub.runtime import st

st.set_page_config(page_title="Premier Brushworks JobHub", layout="wide")

from jobhub import (
    ai_tools,
    control_centre,
    database,
    documents,
    estimating,
    job_views,
    mapping,
    material_orders,
    operations,
    security,
    takeoff,
    takeoff_pages,
    ui,
)
from jobhub.navigation import render_navigation
from jobhub.pages import (
    builders_clients,
    dashboard,
    employees,
    equipment,
    jobs,
    materials,
    products,
    reports,
    wages,
)
from jobhub.registry import bind_modules

MODULES = [
    ui,
    database,
    documents,
    security,
    operations,
    estimating,
    ai_tools,
    control_centre,
    takeoff,
    mapping,
    material_orders,
    takeoff_pages,
    job_views,
    dashboard,
    jobs,
    builders_clients,
    employees,
    products,
    materials,
    wages,
    equipment,
    reports,
]

globals().update(bind_modules(MODULES))

apply_pb_branding()


@st.cache_resource(show_spinner="Starting JobHub...")
def initialise_jobhub():
    init_db()
    set_app_setting("starter_jobs_disabled", "yes")
    set_app_setting("starter_data_seeded", "yes")
    mark_seeded_if_existing_data_present()
    seed_data()
    seed_app_users()
    return True


initialise_jobhub()
require_login()
pb_sidebar_header()
logout_button()

menu = render_navigation(current_role())

PAGE_DISPATCH = {
    "Employee Portal": employee_portal,
    "App Builder AI": app_builder_ai_page,
    "User Access": user_access_page,
    "Control Centre": control_centre_page,
    "Job Lookup / Links": job_lookup_links_page,
    "Job Folders": job_folders_page,
    "PDF Import Centre": pdf_import_centre_page,
    "Dashboard": render_dashboard,
    "Jobs": render_jobs,
    "Import Take-off / Model File": takeoff_import_page,
    "Painting Take-off Generator": painting_takeoff_generator_page,
    "Progress / Billing Model": progress_billing_model_page,
    "3D Model Viewer": three_d_model_viewer_page,
    "3D Building Mapper": building_mapper_page,
    "Building Progress Mapper": building_progress_mapper_page,
    "Estimate Working Sheet": estimate_working_sheet_page,
    "Job Costs / Forecasting": job_costs_forecasting_page,
    "JobHub AI Assistant": jobhub_ai_assistant_page,
    "Builders & Clients": render_builders_clients,
    "Employees": render_employees,
    "Products": render_products,
    "Material Costs": render_material_costs,
    "Wages": render_wages,
    "Timesheets": timesheets_page,
    "Equipment": render_equipment,
    "Job Photos": job_photos_page,
    "Reports / Export": render_reports,
}

renderer = PAGE_DISPATCH.get(menu)
if renderer is None:
    st.error(f"Unknown JobHub page: {menu}")
else:
    renderer()
