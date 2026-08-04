"""Central role policy and read-only access audit for Premier Brushworks JobHub.

JobHub already authenticates users through ``app_users`` and assigns one of the
existing admin, manager or employee roles.  This module does not introduce a
second login system.  It centralises the effective permission rules, protects
sensitive injected routes from direct navigation, and gives administrators a
safe account/role audit that never displays password hashes or secrets.
"""

from __future__ import annotations

from datetime import datetime
import json
import sys
from typing import Any


PERMISSIONS_LABEL = "Permissions & Access Audit"
PERMISSIONS_STATE_KEY = "_pb_show_permissions_audit"
PATCH_MARKER = "_pb_permission_policy_guard"
VALID_ROLES = ("admin", "manager", "employee")

PERMISSION_DEFINITIONS: dict[str, dict[str, Any]] = {
    "users.manage": {
        "label": "Manage user accounts",
        "description": "Create, edit, deactivate and link JobHub login accounts.",
        "roles": {"admin"},
    },
    "permissions.audit": {
        "label": "Review permissions and access",
        "description": "View the role matrix and account-integrity audit.",
        "roles": {"admin"},
    },
    "system.health": {
        "label": "View system health",
        "description": "Review database, storage, backup and runtime health.",
        "roles": {"admin", "manager"},
    },
    "setup.manage": {
        "label": "Edit JobHub defaults",
        "description": "Maintain estimating, stage and crew defaults.",
        "roles": {"admin", "manager"},
    },
    "operations.manage": {
        "label": "Manage jobs and operations",
        "description": "Use management, estimating, scheduling and operational workflows.",
        "roles": {"admin", "manager"},
    },
    "field.use": {
        "label": "Use field and employee tools",
        "description": "Use assigned-job, request, timesheet, form and photo workflows.",
        "roles": {"admin", "manager", "employee"},
    },
}

ROUTE_REQUIREMENTS: dict[str, str] = {
    "User Access": "users.manage",
    PERMISSIONS_LABEL: "permissions.audit",
    "System Health": "system.health",
    "JobHub Setup / Edit Defaults": "setup.manage",
}

MANAGEMENT_MARKERS = {
    "User Access",
    "Builders & Clients",
    "Employees",
    "Staff Requests",
    "Products",
    "Equipment",
    "Wages",
    "JobHub Setup / Edit Defaults",
    "System Health",
}

RESET_SAFE_VALUES = {
    "main_menu": "Dashboard",
    "management_menu": "Builders & Clients",
}


def _st() -> Any:
    return sys.modules.get("streamlit")


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _df_query(sql: str, params: tuple[Any, ...] = ()) -> Any:
    query = _app_attr("df_query") or _app_attr("safe_df_query")
    if callable(query):
        return query(sql, params)
    raise RuntimeError("JobHub database query function is not available yet.")


def normalise_role(role: Any) -> str:
    return str(role or "").strip().lower()


def current_role() -> str:
    app_current_role = _app_attr("current_role")
    if callable(app_current_role) and app_current_role is not current_role:
        try:
            return normalise_role(app_current_role())
        except Exception:
            pass
    st = _st()
    if st is None:
        return ""
    try:
        user = st.session_state.get("user") or {}
    except Exception:
        return ""
    return normalise_role(user.get("role", ""))


def has_permission(role: Any, permission: str) -> bool:
    definition = PERMISSION_DEFINITIONS.get(str(permission or ""))
    if definition is None:
        return False
    return normalise_role(role) in set(definition.get("roles") or set())


def can_access_route(role: Any, route: Any) -> bool:
    requirement = ROUTE_REQUIREMENTS.get(str(route or ""))
    if not requirement:
        return True
    return has_permission(role, requirement)


def safe_route_for_role(role: Any, requested_route: Any) -> str:
    route = str(requested_route or "")
    if not route or can_access_route(role, route):
        return route
    return "Employee Portal" if normalise_role(role) == "employee" else "Dashboard"


def _is_admin() -> bool:
    return has_permission(current_role(), "permissions.audit")


def _permission_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for permission, definition in PERMISSION_DEFINITIONS.items():
        allowed = set(definition.get("roles") or set())
        rows.append(
            {
                "Permission": str(definition.get("label") or permission),
                "Key": permission,
                "Admin": "Allowed" if "admin" in allowed else "Blocked",
                "Manager": "Allowed" if "manager" in allowed else "Blocked",
                "Employee": "Allowed" if "employee" in allowed else "Blocked",
                "Purpose": str(definition.get("description") or ""),
            }
        )
    return rows


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value if value not in (None, "") else default))
    except Exception:
        return int(default)


def _account_rows() -> Any:
    return _df_query(
        """
        SELECT u.id,
               COALESCE(u.username, '') AS username,
               LOWER(TRIM(COALESCE(u.role, ''))) AS role,
               COALESCE(u.active, 0) AS active,
               u.employee_id,
               COALESCE(e.name, '') AS employee_name,
               COALESCE(e.status, '') AS employee_status,
               CASE WHEN COALESCE(u.password_hash, '') = '' THEN 0 ELSE 1 END AS has_password
        FROM app_users u
        LEFT JOIN employees e ON e.id = u.employee_id
        ORDER BY COALESCE(u.active, 0) DESC, LOWER(COALESCE(u.username, '')), u.id
        """
    )


def _analyse_accounts(frame: Any) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, int]]:
    accounts: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    role_counts = {role: 0 for role in VALID_ROLES}
    role_counts["invalid"] = 0
    role_counts["active"] = 0
    role_counts["inactive"] = 0

    if frame is None or getattr(frame, "empty", True):
        findings.append(
            {
                "Severity": "Critical",
                "Check": "User accounts",
                "Detail": "No JobHub user accounts were found.",
            }
        )
        return accounts, findings, role_counts

    usernames: dict[str, list[str]] = {}
    employee_links: dict[int, list[str]] = {}

    for _, row in frame.iterrows():
        user_id = _safe_int(row.get("id"), 0)
        username = str(row.get("username") or "").strip()
        role = normalise_role(row.get("role"))
        active = bool(_safe_int(row.get("active"), 0))
        employee_id_raw = row.get("employee_id")
        employee_id = None
        try:
            if employee_id_raw not in (None, "") and str(employee_id_raw).lower() != "nan":
                employee_id = _safe_int(employee_id_raw, 0) or None
        except Exception:
            employee_id = None
        employee_name = str(row.get("employee_name") or "").strip()
        employee_status = str(row.get("employee_status") or "").strip()
        has_password = bool(_safe_int(row.get("has_password"), 0))

        if role in VALID_ROLES:
            role_counts[role] += 1
        else:
            role_counts["invalid"] += 1
        role_counts["active" if active else "inactive"] += 1

        account_findings: list[str] = []
        severity = "Healthy" if active else "Inactive"
        if role not in VALID_ROLES:
            account_findings.append("Invalid or blank role")
            severity = "Critical"
        if active and not username:
            account_findings.append("Active account has no username")
            severity = "Critical"
        if active and not has_password:
            account_findings.append("Active account has no password credential")
            severity = "Critical"
        if role == "employee" and active and employee_id is None:
            account_findings.append("Employee login is not linked to an employee")
            if severity != "Critical":
                severity = "Warning"
        if employee_id is not None and not employee_name:
            account_findings.append("Linked employee record is missing")
            severity = "Critical"
        if active and employee_status.lower() in {"inactive", "terminated", "left", "former"}:
            account_findings.append(f"Active login is linked to {employee_status.lower()} employee")
            if severity != "Critical":
                severity = "Warning"

        normalised_username = username.casefold()
        if normalised_username:
            usernames.setdefault(normalised_username, []).append(username or f"User #{user_id}")
        if employee_id is not None:
            employee_links.setdefault(employee_id, []).append(username or f"User #{user_id}")

        accounts.append(
            {
                "User ID": user_id,
                "Username": username,
                "Role": role or "Invalid / blank",
                "Active": "Yes" if active else "No",
                "Employee": employee_name or "Not linked",
                "Employee Status": employee_status,
                "Audit Status": severity,
                "Findings": "; ".join(account_findings) if account_findings else "No issue found",
            }
        )

    for username, values in usernames.items():
        if len(values) > 1:
            findings.append(
                {
                    "Severity": "Critical",
                    "Check": "Duplicate username",
                    "Detail": f"{username}: {', '.join(values)}",
                }
            )
    for employee_id, values in employee_links.items():
        if len(values) > 1:
            findings.append(
                {
                    "Severity": "Critical",
                    "Check": "Employee linked to multiple logins",
                    "Detail": f"Employee ID {employee_id}: {', '.join(values)}",
                }
            )

    invalid_accounts = [row for row in accounts if row["Audit Status"] in {"Critical", "Warning"}]
    if invalid_accounts:
        for row in invalid_accounts:
            findings.append(
                {
                    "Severity": str(row["Audit Status"]),
                    "Check": f"Account {row['Username'] or row['User ID']}",
                    "Detail": str(row["Findings"]),
                }
            )
    elif not findings:
        findings.append(
            {
                "Severity": "Healthy",
                "Check": "Account integrity",
                "Detail": "All user accounts match the current role and employee-link rules.",
            }
        )

    return accounts, findings, role_counts


def build_access_audit() -> dict[str, Any]:
    frame = _account_rows()
    accounts, findings, role_counts = _analyse_accounts(frame)
    severity_counts = {
        severity: sum(1 for finding in findings if finding.get("Severity") == severity)
        for severity in ("Healthy", "Warning", "Critical")
    }
    overall = "Critical" if severity_counts["Critical"] else (
        "Warning" if severity_counts["Warning"] else "Healthy"
    )
    return {
        "overall_status": overall,
        "role_counts": role_counts,
        "severity_counts": severity_counts,
        "permissions": _permission_rows(),
        "accounts": accounts,
        "findings": findings,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def render_permissions_audit_page() -> None:
    st = _st()
    if st is None:
        return
    if not _is_admin():
        st.error("Permissions & Access Audit is available to JobHub administrators only.")
        return

    st.header(PERMISSIONS_LABEL)
    st.caption(
        "Read-only review of the existing admin, manager and employee roles. "
        "Password hashes, passwords, connection details and API secrets are never displayed."
    )

    report = build_access_audit()
    role_counts = report["role_counts"]
    severity_counts = report["severity_counts"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Overall", report["overall_status"])
    c2.metric("Admins", role_counts.get("admin", 0))
    c3.metric("Managers", role_counts.get("manager", 0))
    c4.metric("Employees", role_counts.get("employee", 0))
    c5.metric("Active logins", role_counts.get("active", 0))

    if report["overall_status"] == "Critical":
        st.error("One or more account or role problems need administrator attention.")
    elif report["overall_status"] == "Warning":
        st.warning("Access is operating, but one or more account links should be reviewed.")
    else:
        st.success("The available account and role checks passed.")

    tab_matrix, tab_accounts, tab_findings = st.tabs(
        ["Role matrix", "User account audit", "Findings"]
    )
    with tab_matrix:
        st.dataframe(report["permissions"], width="stretch", hide_index=True)
    with tab_accounts:
        st.dataframe(report["accounts"], width="stretch", hide_index=True)
    with tab_findings:
        st.dataframe(report["findings"], width="stretch", hide_index=True)
        st.caption(
            f"Critical: {severity_counts.get('Critical', 0)} · "
            f"Warnings: {severity_counts.get('Warning', 0)}"
        )

    st.download_button(
        "Download access audit",
        data=json.dumps(report, indent=2, default=str).encode("utf-8"),
        file_name=f"jobhub_access_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        mime="application/json",
        key="pb_download_access_audit",
    )


def _show_page(st: Any) -> None:
    st.session_state[PERMISSIONS_STATE_KEY] = True
    render_permissions_audit_page()
    st.stop()


def _labels(options: Any) -> list[str]:
    try:
        return [str(item) for item in list(options)]
    except Exception:
        return []


def _should_inject(label: Any, key: Any, options: Any, role: Any = None) -> bool:
    effective_role = normalise_role(current_role() if role is None else role)
    if not has_permission(effective_role, "permissions.audit"):
        return False
    labels = set(_labels(options))
    if PERMISSIONS_LABEL in labels:
        return True
    label_text = str(label or "")
    key_text = str(key or "")
    if key_text == "management_menu" or label_text == "Management Section":
        return True
    return len(labels.intersection(MANAGEMENT_MARKERS)) >= 2


def _patch_radio(owner: Any, st: Any) -> bool:
    original = getattr(owner, "radio", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        arg_list = list(args)
        options_index = None
        label = kwargs.get("label", "")
        if len(arg_list) >= 2 and isinstance(arg_list[0], str):
            label = arg_list[0]
            options_index = 1
        elif len(arg_list) >= 3:
            label = arg_list[1]
            options_index = 2
        elif "options" in kwargs and args:
            label = args[0]
        options = arg_list[options_index] if options_index is not None else kwargs.get("options")
        if _should_inject(label, kwargs.get("key"), options):
            try:
                labels = _labels(options)
                if PERMISSIONS_LABEL not in labels:
                    new_options = list(options)
                    new_options.append(PERMISSIONS_LABEL)
                    if options_index is not None:
                        arg_list[options_index] = new_options
                    else:
                        kwargs["options"] = new_options
            except Exception:
                pass
        result = original(*tuple(arg_list), **kwargs)
        if str(result) == PERMISSIONS_LABEL:
            if not _is_admin():
                st.error("This page is restricted to JobHub administrators.")
                st.stop()
            _show_page(st)
        return result

    setattr(wrapper, PATCH_MARKER, True)
    wrapper._pb_original_radio = original
    setattr(owner, "radio", wrapper)
    return True


def _install_session_navigation_guard(st: Any) -> bool:
    try:
        state_cls = type(st.session_state)
        original_pop = getattr(state_cls, "pop", None)
        original_get = getattr(state_cls, "get", None)
    except Exception:
        return False
    installed = False

    if original_pop is not None and not getattr(original_pop, PATCH_MARKER, False):
        def guarded_pop(self: Any, key: Any, *args: Any) -> Any:
            value = original_pop(self, key, *args)
            if str(key or "") == "go_to_menu" and value:
                role = ""
                try:
                    user = original_get(self, "user", {}) if original_get is not None else {}
                    role = normalise_role((user or {}).get("role", ""))
                except Exception:
                    role = ""
                safe_value = safe_route_for_role(role, value)
                if safe_value != str(value):
                    try:
                        self["_pb_permission_denied_route"] = str(value)
                    except Exception:
                        pass
                    return safe_value
            return value

        guarded_pop._pb_original_pop = original_pop
        setattr(guarded_pop, PATCH_MARKER, True)
        setattr(state_cls, "pop", guarded_pop)
        installed = True

    if original_get is not None and not getattr(original_get, PATCH_MARKER, False):
        def guarded_get(self: Any, key: Any, default: Any = None) -> Any:
            value = original_get(self, key, default)
            key_text = str(key or "")
            if key_text in RESET_SAFE_VALUES and str(value) == PERMISSIONS_LABEL:
                role = ""
                try:
                    user = original_get(self, "user", {}) or {}
                    role = normalise_role(user.get("role", ""))
                except Exception:
                    role = ""
                if not has_permission(role, "permissions.audit"):
                    return RESET_SAFE_VALUES[key_text]
            return value

        guarded_get._pb_original_get = original_get
        setattr(guarded_get, PATCH_MARKER, True)
        setattr(state_cls, "get", guarded_get)
        installed = True

    return installed


def install_permission_policy_guard() -> bool:
    st = _st()
    if st is None:
        return False
    installed = _install_session_navigation_guard(st)
    installed = _patch_radio(st, st) or installed
    try:
        delta_module = sys.modules.get("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_radio(delta_cls, st) or installed
    return installed
