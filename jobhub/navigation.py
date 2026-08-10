"""Role-aware JobHub navigation."""
from __future__ import annotations

from .runtime import st

ROUTE_ALIASES = {
    "Reports": "Reports / Export",
    "Management": "Builders & Clients",
    "Site Operations": "Timesheets",
    "Estimating": "Estimate Working Sheet",
    "AI Assistant": "JobHub AI Assistant",
    "Job Lookup / Links": "Control Centre",
    "Painting Take-off Generator": "Import Take-off / Model File",
}

NAV_ICONS = {
    "Home": "⌂",
    "Jobs": "▣",
    "Site & Team": "◷",
    "Estimating": "▤",
    "Reports": "▥",
    "Administration": "⚙",
    "My Work": "✓",
}


def navigation_for_role(role):
    if role == "employee":
        return {"My Work": {"Employee Portal": "Employee Portal"}}

    groups = {
        "Home": {"Dashboard": "Dashboard"},
        "Jobs": {
            "Job Folders": "Job Folders",
            "Job Register": "Jobs",
            "Planning & Claims": "Control Centre",
        },
        "Site & Team": {
            "Timesheets": "Timesheets",
            "Blip Attendance": "Blip Attendance",
            "Materials": "Material Costs",
            "Wages": "Wages",
            "Equipment": "Equipment",
            "Job Photos": "Job Photos",
            "PDF Import": "PDF Import Centre",
        },
        "Estimating": {
            "Estimate Worksheet": "Estimate Working Sheet",
            "Job Costs & Forecasting": "Job Costs / Forecasting",
            "Import Take-off / Model": "Import Take-off / Model File",
            "Progress & Billing": "Progress / Billing Model",
            "3D Model Viewer": "3D Model Viewer",
        },
        "Reports": {"Reports & Export": "Reports / Export"},
        "Administration": {
            "Builders & Clients": "Builders & Clients",
            "Employees": "Employees",
            "Products": "Products",
            "JobHub AI": "JobHub AI Assistant",
            "Developer Tools": "App Builder AI",
        },
    }
    if role != "manager":
        groups["Administration"] = {
            "User Accounts": "User Access",
            **groups["Administration"],
        }
    return groups


def render_navigation(role):
    groups = navigation_for_role(role)
    route_lookup = {
        route: (group, label)
        for group, pages in groups.items()
        for label, route in pages.items()
    }

    requested_section = st.session_state.pop("go_to_control_centre_section", None)
    requested_menu = st.session_state.pop("go_to_menu", None)
    if requested_section:
        requested_menu = "Control Centre"
        st.session_state["control_centre_section"] = requested_section

    requested_menu = ROUTE_ALIASES.get(requested_menu, requested_menu)
    if requested_menu in route_lookup:
        group, page = route_lookup[requested_menu]
        st.session_state["pb_nav_group"] = group
        st.session_state["pb_nav_page"] = page

    group_names = list(groups)
    if st.session_state.get("pb_nav_group") not in group_names:
        st.session_state["pb_nav_group"] = group_names[0]

    selected_group = st.sidebar.radio(
        "Navigation",
        group_names,
        key="pb_nav_group",
        format_func=lambda item: f"{NAV_ICONS.get(item, '•')}  {item}",
    )

    page_map = groups[selected_group]
    labels = list(page_map)
    if st.session_state.get("pb_nav_page") not in labels:
        st.session_state["pb_nav_page"] = labels[0]

    if len(labels) == 1:
        selected_page = labels[0]
        st.session_state["pb_nav_page"] = selected_page
    else:
        selected_page = st.sidebar.selectbox(
            "Page", labels, key="pb_nav_page", label_visibility="collapsed"
        )
    return page_map[selected_page]
