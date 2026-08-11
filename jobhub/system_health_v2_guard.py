"""Operationally accurate System Health page for Premier Brushworks JobHub.

This version keeps the existing read-only health reporting contract while
separating three things that were previously easy to confuse:

* PostgreSQL live connectivity and core-data integrity;
* JobHub's local /var/data archive ZIPs;
* a verified PostgreSQL restore drill.

It also uses disk thresholds that make sense for the service's actual disk size
and gives managers/admins a controlled way to resolve retained application error
records without deleting audit history.
"""

from __future__ import annotations

from datetime import datetime
from jobhub_time import jobhub_now
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any


SYSTEM_HEALTH_LABEL = "System Health"
SYSTEM_HEALTH_STATE_KEY = "_pb_show_system_health_page"
PATCH_MARKER = "_pb_system_health_v2_guard"

RESET_SAFE_VALUES = {
    "main_menu": "Dashboard",
    "management_menu": "Builders & Clients",
}

MANAGEMENT_MARKERS = {
    "Builders & Clients",
    "Employees",
    "Products",
    "Wages",
    "Notifications",
    "JobHub Setup / Edit Defaults",
}

CORE_TABLES = (
    ("jobs", "Jobs"),
    ("employees", "Employees"),
    ("builders_clients", "Builders / clients"),
)

RESTORE_VERIFIED_ENV = "JOBHUB_POSTGRES_RESTORE_VERIFIED_AT"
RESTORE_HEALTHY_DAYS = 90
RESTORE_WARNING_DAYS = 180


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


def _execute(sql: str, params: tuple[Any, ...] = ()) -> Any:
    execute = _app_attr("execute")
    if callable(execute):
        return execute(sql, params)
    raise RuntimeError("JobHub database execute function is not available yet.")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value if value not in (None, "") else default))
    except Exception:
        return int(default)


def _first_value(frame: Any, default: Any = None) -> Any:
    if frame is None or getattr(frame, "empty", True):
        return default
    try:
        return frame.iloc[0, 0]
    except Exception:
        try:
            row = frame.iloc[0]
            return row.iloc[0]
        except Exception:
            return default


def _query_scalar(sql: str, params: tuple[Any, ...] = (), default: Any = None) -> Any:
    return _first_value(_df_query(sql, params), default)


def _format_bytes(value: Any) -> str:
    amount = float(value or 0)
    units = ("B", "KB", "MB", "GB", "TB")
    unit = units[0]
    for unit in units:
        if abs(amount) < 1024.0 or unit == units[-1]:
            break
        amount /= 1024.0
    return f"{amount:,.1f} {unit}"


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is not None:
                return parsed.astimezone().replace(tzinfo=None)
            return parsed
        except Exception:
            continue
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except Exception:
            continue
    return None


def _age_hours(value: Any) -> float | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return max(0.0, (jobhub_now() - parsed).total_seconds() / 3600.0)


def _check(area: str, name: str, status: str, detail: str) -> dict[str, str]:
    return {
        "Area": str(area),
        "Check": str(name),
        "Status": str(status),
        "Detail": str(detail),
    }


def _management_allowed() -> bool:
    get_current_user = _app_attr("get_current_user")
    if callable(get_current_user):
        try:
            user = get_current_user() or {}
            role = str(user.get("role", "") or "").strip().lower()
            if role:
                return role in {"admin", "manager"}
        except Exception:
            pass
    st = _st()
    if st is None:
        return False
    try:
        user = st.session_state.get("user") or {}
        role = str(user.get("role", "") or "").strip().lower()
        return not role or role in {"admin", "manager"}
    except Exception:
        return True


def _current_username() -> str:
    get_current_user = _app_attr("get_current_user")
    if callable(get_current_user):
        try:
            user = get_current_user() or {}
            value = str(user.get("username", "") or "").strip()
            if value:
                return value
        except Exception:
            pass
    st = _st()
    if st is not None:
        try:
            user = st.session_state.get("user") or {}
            value = str(user.get("username", "") or "").strip()
            if value:
                return value
        except Exception:
            pass
    return "JobHub manager"


def _database_report() -> tuple[list[dict[str, str]], dict[str, Any]]:
    checks: list[dict[str, str]] = []
    metrics: dict[str, Any] = {}
    use_postgres = bool(_app_attr("USE_POSTGRES", False))
    backend = "PostgreSQL" if use_postgres else "SQLite"
    metrics["Database backend"] = backend

    try:
        probe = _safe_int(_query_scalar("SELECT 1 AS health_probe", default=0), 0)
        if probe != 1:
            raise RuntimeError("Database probe returned an unexpected result.")
        checks.append(_check("Database", "Connection", "Healthy", f"{backend} accepted a live query."))
    except Exception as exc:
        checks.append(_check("Database", "Connection", "Critical", f"Live query failed: {exc}"))
        return checks, metrics

    if use_postgres:
        try:
            database_name = str(_query_scalar("SELECT current_database() AS database_name", default="") or "")
            checks.append(
                _check(
                    "Database",
                    "Selected database",
                    "Healthy" if database_name else "Warning",
                    database_name or "Connected, but the database name could not be read.",
                )
            )
            metrics["Database name"] = database_name or "Unknown"
        except Exception as exc:
            checks.append(_check("Database", "Selected database", "Warning", f"Could not read name: {exc}"))

    for table_name, display_name in CORE_TABLES:
        try:
            count = _safe_int(_query_scalar(f"SELECT COUNT(*) FROM {table_name}", default=0), 0)
            metrics[display_name] = count
            checks.append(_check("Core data", display_name, "Healthy", f"{count:,} records available."))
        except Exception as exc:
            metrics[display_name] = "Unavailable"
            checks.append(_check("Core data", display_name, "Critical", f"Table/query failed: {exc}"))

    try:
        duplicate_jobs = _safe_int(
            _query_scalar(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT job_no
                    FROM jobs
                    WHERE COALESCE(job_no, '') <> ''
                    GROUP BY job_no
                    HAVING COUNT(*) > 1
                ) duplicate_job_numbers
                """,
                default=0,
            ),
            0,
        )
        checks.append(
            _check(
                "Data integrity",
                "Duplicate job numbers",
                "Warning" if duplicate_jobs else "Healthy",
                f"{duplicate_jobs:,} duplicate job number group(s).",
            )
        )
        metrics["Duplicate job numbers"] = duplicate_jobs
    except Exception as exc:
        checks.append(_check("Data integrity", "Duplicate job numbers", "Warning", f"Check unavailable: {exc}"))

    try:
        orphan_builders = _safe_int(
            _query_scalar(
                """
                SELECT COUNT(*)
                FROM jobs j
                LEFT JOIN builders_clients b ON b.id = j.builder_client_id
                WHERE j.builder_client_id IS NOT NULL AND b.id IS NULL
                """,
                default=0,
            ),
            0,
        )
        checks.append(
            _check(
                "Data integrity",
                "Jobs with missing builder/client",
                "Warning" if orphan_builders else "Healthy",
                f"{orphan_builders:,} orphaned job record(s).",
            )
        )
        metrics["Orphaned jobs"] = orphan_builders
    except Exception as exc:
        checks.append(_check("Data integrity", "Jobs with missing builder/client", "Warning", f"Check unavailable: {exc}"))

    try:
        unresolved_errors = _safe_int(
            _query_scalar(
                "SELECT COUNT(*) FROM app_error_events WHERE COALESCE(resolved_at, '') = ''",
                default=0,
            ),
            0,
        )
        status = "Healthy" if unresolved_errors == 0 else ("Warning" if unresolved_errors < 20 else "Critical")
        checks.append(
            _check(
                "Application",
                "Unresolved error events",
                status,
                f"{unresolved_errors:,} unresolved event(s) retained in the audit log.",
            )
        )
        metrics["Unresolved errors"] = unresolved_errors
    except Exception:
        checks.append(_check("Application", "Unresolved error events", "Info", "Error-event table is not available."))

    return checks, metrics


def _storage_report() -> tuple[list[dict[str, str]], dict[str, Any]]:
    checks: list[dict[str, str]] = []
    metrics: dict[str, Any] = {}
    configured_paths = (
        ("Data", _app_attr("DATA_DIR", "")),
        ("Job files", _app_attr("JOB_FILES_DIR", "")),
        ("Photos", _app_attr("PHOTOS_DIR", "")),
        ("Exports", _app_attr("EXPORTS_DIR", "")),
    )

    for label, raw_path in configured_paths:
        path_text = str(raw_path or "").strip()
        if not path_text:
            checks.append(_check("Storage", label, "Critical", "No path is configured."))
            continue
        path = Path(path_text)
        if not path.exists() or not path.is_dir():
            checks.append(_check("Storage", label, "Critical", f"Folder is missing: {path}"))
            continue
        writable = os.access(path, os.W_OK)
        checks.append(
            _check(
                "Storage",
                label,
                "Healthy" if writable else "Critical",
                f"{path} — {'writable' if writable else 'not writable'}.",
            )
        )

    data_dir = str(_app_attr("DATA_DIR", "") or "").strip()
    if data_dir and Path(data_dir).exists():
        try:
            usage = shutil.disk_usage(data_dir)
            free_percent = (usage.free / usage.total * 100.0) if usage.total else 0.0
            metrics["Disk total"] = _format_bytes(usage.total)
            metrics["Disk free"] = _format_bytes(usage.free)
            metrics["Disk free percent"] = round(free_percent, 1)
            # Use both percentage and a small absolute safety floor.  The old
            # 1GB-warning threshold made a 1GB disk report Warning almost all the
            # time even when more than a quarter of the disk was free.
            if usage.free < 100 * 1024 * 1024 or free_percent < 5.0:
                status = "Critical"
            elif usage.free < 200 * 1024 * 1024 or free_percent < 15.0:
                status = "Warning"
            else:
                status = "Healthy"
            checks.append(
                _check(
                    "Storage",
                    "Free disk space",
                    status,
                    f"{_format_bytes(usage.free)} free of {_format_bytes(usage.total)} ({free_percent:.1f}%).",
                )
            )
        except Exception as exc:
            checks.append(_check("Storage", "Free disk space", "Warning", f"Check unavailable: {exc}"))

    return checks, metrics


def _archive_report() -> tuple[list[dict[str, str]], dict[str, Any]]:
    checks: list[dict[str, str]] = []
    metrics: dict[str, Any] = {}
    try:
        frame = _df_query(
            """
            SELECT file_path, created_at, COALESCE(size_bytes, 0) AS size_bytes,
                   COALESCE(status, '') AS status
            FROM backup_runs
            ORDER BY id DESC
            LIMIT 1
            """
        )
    except Exception:
        frame = None

    if frame is None or getattr(frame, "empty", True):
        checks.append(
            _check(
                "Recovery",
                "Latest JobHub data archive",
                "Warning",
                "No completed JobHub data archive is recorded. This check is separate from PostgreSQL recovery.",
            )
        )
        metrics["Latest JobHub archive"] = "Not found"
        return checks, metrics

    row = frame.iloc[0]
    created_at = str(row.get("created_at", "") or "")
    size_bytes = _safe_int(row.get("size_bytes", 0), 0)
    status_text = str(row.get("status", "") or "")
    file_path = str(row.get("file_path", "") or "")
    age = _age_hours(created_at)
    if age is None:
        status = "Warning"
        age_text = "age unknown"
    elif age > 168:
        status = "Critical"
        age_text = f"{age / 24.0:.1f} days old"
    elif age > 48:
        status = "Warning"
        age_text = f"{age / 24.0:.1f} days old"
    else:
        status = "Healthy"
        age_text = f"{age:.1f} hours old"
    detail = f"{created_at} ({age_text})"
    if size_bytes:
        detail += f", {_format_bytes(size_bytes)}"
    if status_text:
        detail += f", status: {status_text}"
    if file_path:
        detail += f" — {file_path}"
    detail += ". This is a JobHub archive, not proof of a PostgreSQL restore."
    checks.append(_check("Recovery", "Latest JobHub data archive", status, detail))
    metrics["Latest JobHub archive"] = created_at
    metrics["JobHub archive age hours"] = round(age, 1) if age is not None else "Unknown"
    return checks, metrics


def _postgres_restore_report() -> tuple[list[dict[str, str]], dict[str, Any]]:
    checks: list[dict[str, str]] = []
    metrics: dict[str, Any] = {}
    if not bool(_app_attr("USE_POSTGRES", False)):
        checks.append(_check("Recovery", "PostgreSQL restore drill", "Info", "Not applicable while JobHub uses SQLite."))
        metrics["PostgreSQL restore verified"] = "Not applicable"
        return checks, metrics

    verified_at = str(os.getenv(RESTORE_VERIFIED_ENV, "") or "").strip()
    metrics["PostgreSQL restore verified at"] = verified_at or "Not recorded"
    if not verified_at:
        checks.append(
            _check(
                "Recovery",
                "PostgreSQL restore drill",
                "Warning",
                "No verified PostgreSQL restore drill is recorded. Create an isolated Render recovery/export restore, verify it, then set JOBHUB_POSTGRES_RESTORE_VERIFIED_AT to the verification timestamp.",
            )
        )
        return checks, metrics

    age = _age_hours(verified_at)
    if age is None:
        checks.append(_check("Recovery", "PostgreSQL restore drill", "Warning", f"{RESTORE_VERIFIED_ENV} is set but cannot be parsed as a date/time."))
        return checks, metrics

    age_days = age / 24.0
    if age_days <= RESTORE_HEALTHY_DAYS:
        status = "Healthy"
    elif age_days <= RESTORE_WARNING_DAYS:
        status = "Warning"
    else:
        status = "Critical"
    checks.append(
        _check(
            "Recovery",
            "PostgreSQL restore drill",
            status,
            f"Last verified {age_days:.1f} days ago at {verified_at}. Keep restore drills within {RESTORE_HEALTHY_DAYS} days.",
        )
    )
    metrics["PostgreSQL restore age days"] = round(age_days, 1)
    return checks, metrics


def _runtime_report() -> dict[str, Any]:
    st = _st()
    render_commit = str(os.getenv("RENDER_GIT_COMMIT", "") or "")
    return {
        "JobHub build": str(_app_attr("PB_JOBHUB_BUILD", "Unknown") or "Unknown"),
        "Python": platform.python_version(),
        "Platform": platform.platform(),
        "Streamlit": str(getattr(st, "__version__", "Unknown")) if st is not None else "Unknown",
        "Database backend": "PostgreSQL" if bool(_app_attr("USE_POSTGRES", False)) else "SQLite",
        "Render service": str(os.getenv("RENDER_SERVICE_NAME", "") or "Not detected"),
        "Render commit": render_commit[:12] if render_commit else "Not detected",
        "Checked at": jobhub_now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_system_health_report() -> dict[str, Any]:
    database_checks, database_metrics = _database_report()
    storage_checks, storage_metrics = _storage_report()
    archive_checks, archive_metrics = _archive_report()
    restore_checks, restore_metrics = _postgres_restore_report()
    checks = database_checks + storage_checks + archive_checks + restore_checks
    status_counts = {
        status: sum(1 for row in checks if row.get("Status") == status)
        for status in ("Healthy", "Warning", "Critical", "Info")
    }
    overall = "Critical" if status_counts["Critical"] else ("Warning" if status_counts["Warning"] else "Healthy")
    return {
        "overall_status": overall,
        "status_counts": status_counts,
        "checks": checks,
        "metrics": {
            **database_metrics,
            **storage_metrics,
            **archive_metrics,
            **restore_metrics,
        },
        "runtime": _runtime_report(),
    }


def _safe_rerun(st: Any) -> None:
    rerun = _app_attr("pb_rerun") or _app_attr("refresh") or getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


def _unresolved_error_rows() -> Any:
    try:
        return _df_query(
            """
            SELECT id, created_at AS "Created", COALESCE(username, '') AS "User",
                   COALESCE(area, '') AS "Area", COALESCE(error_type, '') AS "Type",
                   COALESCE(message, '') AS "Message"
            FROM app_error_events
            WHERE COALESCE(resolved_at, '') = ''
            ORDER BY id DESC
            LIMIT 100
            """
        )
    except Exception:
        return None


def _render_unresolved_errors(st: Any) -> None:
    st.subheader("Unresolved application errors")
    frame = _unresolved_error_rows()
    if frame is None:
        st.info("The application error log is not available in this database version.")
        return
    if getattr(frame, "empty", True):
        st.success("No unresolved application errors are recorded.")
        return

    st.dataframe(frame, width="stretch", hide_index=True)
    options: dict[str, int] = {}
    for _, row in frame.iterrows():
        error_id = _safe_int(row.get("id"), 0)
        label = f"#{error_id} · {str(row.get('Created') or '')} · {str(row.get('Area') or '')} · {str(row.get('Type') or '')}"
        options[label] = error_id
    selected = st.selectbox("Error to resolve", list(options), key="pb_health_error_to_resolve")
    resolution = st.text_area(
        "Resolution notes",
        value="Issue reviewed after recovery; retained in audit history and marked resolved.",
        key="pb_health_error_resolution_notes",
    )
    if st.button("Mark selected error resolved", key="pb_health_resolve_error", type="primary"):
        _execute(
            """
            UPDATE app_error_events
            SET resolved_at = ?, resolved_by = ?, resolution_notes = ?
            WHERE id = ? AND COALESCE(resolved_at, '') = ''
            """,
            (
                jobhub_now().strftime("%Y-%m-%d %H:%M:%S"),
                _current_username(),
                str(resolution or "").strip(),
                options[selected],
            ),
        )
        st.success("Error was marked resolved; the audit record was retained.")
        _safe_rerun(st)


def render_system_health_page() -> None:
    st = _st()
    if st is None:
        return
    if not _management_allowed():
        st.error("System Health is available to JobHub managers and administrators only.")
        return

    st.header("System Health")
    st.caption(
        "Live database, persistent storage, JobHub archives, PostgreSQL restore readiness, runtime and core-data integrity. Connection strings, passwords and API secrets are never displayed."
    )
    if st.button("Refresh health check", key="pb_refresh_system_health"):
        _safe_rerun(st)

    report = build_system_health_report()
    counts = report["status_counts"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Overall", report["overall_status"])
    c2.metric("Healthy", counts.get("Healthy", 0))
    c3.metric("Warnings", counts.get("Warning", 0))
    c4.metric("Critical", counts.get("Critical", 0))

    if report["overall_status"] == "Critical":
        st.error("JobHub has one or more critical health checks requiring attention.")
    elif report["overall_status"] == "Warning":
        st.warning("JobHub is operating, but one or more checks need review.")
    else:
        st.success("All available JobHub health checks passed.")

    tab_checks, tab_data, tab_runtime, tab_errors = st.tabs(
        ["Health checks", "Data snapshot", "Runtime", "Unresolved errors"]
    )
    with tab_checks:
        st.dataframe(report["checks"], width="stretch", hide_index=True)
    with tab_data:
        metrics = [{"Metric": key, "Value": value} for key, value in report["metrics"].items()]
        st.dataframe(metrics, width="stretch", hide_index=True)
    with tab_runtime:
        runtime = [{"Setting": key, "Value": value} for key, value in report["runtime"].items()]
        st.dataframe(runtime, width="stretch", hide_index=True)
    with tab_errors:
        _render_unresolved_errors(st)

    report_bytes = json.dumps(report, indent=2, default=str).encode("utf-8")
    st.download_button(
        "Download health report",
        data=report_bytes,
        file_name=f"jobhub_health_{jobhub_now().strftime('%Y%m%d_%H%M%S')}.json",
        mime="application/json",
        key="pb_download_system_health",
    )


def _show_page(st: Any) -> None:
    st.session_state[SYSTEM_HEALTH_STATE_KEY] = True
    render_system_health_page()
    st.stop()


def _labels(options: Any) -> list[str]:
    try:
        return [str(item) for item in list(options)]
    except Exception:
        return []


def _should_inject(label: Any, key: Any, options: Any) -> bool:
    labels = set(_labels(options))
    if SYSTEM_HEALTH_LABEL in labels:
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
                if SYSTEM_HEALTH_LABEL not in labels:
                    new_options = list(options)
                    new_options.append(SYSTEM_HEALTH_LABEL)
                    if options_index is not None:
                        arg_list[options_index] = new_options
                    else:
                        kwargs["options"] = new_options
            except Exception:
                pass
        result = original(*tuple(arg_list), **kwargs)
        if str(result) == SYSTEM_HEALTH_LABEL:
            _show_page(st)
        return result

    setattr(wrapper, PATCH_MARKER, True)
    wrapper._pb_original_radio = original
    setattr(owner, "radio", wrapper)
    return True


def _install_session_state_reset_guard(st: Any) -> bool:
    try:
        state_cls = type(st.session_state)
        original_get = getattr(state_cls, "get", None)
    except Exception:
        return False
    if original_get is None or getattr(original_get, PATCH_MARKER, False):
        return False

    def guarded_get(self: Any, key: Any, default: Any = None) -> Any:
        value = original_get(self, key, default)
        key_text = str(key or "")
        if key_text in RESET_SAFE_VALUES and str(value) == SYSTEM_HEALTH_LABEL:
            return RESET_SAFE_VALUES[key_text]
        return value

    guarded_get._pb_original_get = original_get
    setattr(guarded_get, PATCH_MARKER, True)
    setattr(state_cls, "get", guarded_get)
    return True


def install_system_health_guard() -> bool:
    st = _st()
    if st is None:
        return False
    installed = _patch_radio(st, st)
    try:
        delta_module = sys.modules.get("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_radio(delta_cls, st) or installed
    installed = _install_session_state_reset_guard(st) or installed
    return installed
