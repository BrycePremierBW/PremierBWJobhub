"""Read-only system health diagnostics for Premier Brushworks JobHub.

The live application is intentionally large and supports both SQLite and
PostgreSQL.  This module adds a protected Management page without rewriting the
main app.  It reports database, storage, backup, runtime and basic data-integrity
health while avoiding destructive actions and never exposing connection secrets.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any


SYSTEM_HEALTH_LABEL = "System Health"
SYSTEM_HEALTH_STATE_KEY = "_pb_show_system_health_page"
PATCH_MARKER = "_pb_system_health_guard"

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
    candidates = (text, text.replace("Z", "+00:00"))
    for candidate in candidates:
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
    return max(0.0, (datetime.now() - parsed).total_seconds() / 3600.0)


def _check(area: str, name: str, status: str, detail: str) -> dict[str, str]:
    return {
        "Area": str(area),
        "Check": str(name),
        "Status": str(status),
        "Detail": str(detail),
    }


def _management_allowed() -> bool:
    get_current_user = _app_attr("get_current_user")
    if not callable(get_current_user):
        return True
    try:
        user = get_current_user() or {}
    except Exception:
        return True
    role = str(user.get("role", "") or "").strip().lower()
    if not role:
        return True
    return role in {"admin", "manager"}


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
    else:
        try:
            quick_check = str(_query_scalar("PRAGMA quick_check", default="") or "").strip()
            status = "Healthy" if quick_check.lower() == "ok" else "Critical"
            checks.append(_check("Database", "SQLite quick check", status, quick_check or "No result returned."))
        except Exception as exc:
            checks.append(_check("Database", "SQLite quick check", "Warning", f"Check unavailable: {exc}"))

    for table_name, display_name in CORE_TABLES:
        try:
            count = _safe_int(
                _query_scalar(f"SELECT COUNT(*) AS record_count FROM {table_name}", default=0),
                0,
            )
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
        checks.append(
            _check("Data integrity", "Jobs with missing builder/client", "Warning", f"Check unavailable: {exc}")
        )

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
                f"{unresolved_errors:,} unresolved event(s) recorded.",
            )
        )
        metrics["Unresolved errors"] = unresolved_errors
    except Exception:
        checks.append(
            _check(
                "Application",
                "Unresolved error events",
                "Info",
                "Error-event table is not available in this database version.",
            )
        )

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
            if usage.free < 250 * 1024 * 1024 or free_percent < 3.0:
                status = "Critical"
            elif usage.free < 1024 * 1024 * 1024 or free_percent < 10.0:
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


def _latest_backup_file() -> tuple[str, str, int] | None:
    exports_dir = str(_app_attr("EXPORTS_DIR", "") or "").strip()
    if not exports_dir:
        return None
    root = Path(exports_dir)
    if not root.exists() or not root.is_dir():
        return None
    candidates: list[Path] = []
    try:
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name.lower()
            if "backup" in name or path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".zip"}:
                candidates.append(path)
            if len(candidates) >= 5000:
                break
    except Exception:
        return None
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: item.stat().st_mtime)
    modified = datetime.fromtimestamp(latest.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return str(latest), modified, int(latest.stat().st_size)


def _backup_report() -> tuple[list[dict[str, str]], dict[str, Any]]:
    checks: list[dict[str, str]] = []
    metrics: dict[str, Any] = {}
    backup_path = ""
    backup_time = ""
    backup_size = 0
    backup_status = ""

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
        if frame is not None and not getattr(frame, "empty", True):
            row = frame.iloc[0]
            backup_path = str(row.get("file_path", "") or "")
            backup_time = str(row.get("created_at", "") or "")
            backup_size = _safe_int(row.get("size_bytes", 0), 0)
            backup_status = str(row.get("status", "") or "")
    except Exception:
        pass

    if not backup_time:
        file_backup = _latest_backup_file()
        if file_backup is not None:
            backup_path, backup_time, backup_size = file_backup
            backup_status = "Found on disk"

    if not backup_time:
        checks.append(
            _check(
                "Recovery",
                "Latest backup",
                "Warning",
                "No completed backup record or backup file was found.",
            )
        )
        metrics["Latest backup"] = "Not found"
        return checks, metrics

    age = _age_hours(backup_time)
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

    detail = f"{backup_time} ({age_text})"
    if backup_size:
        detail += f", {_format_bytes(backup_size)}"
    if backup_status:
        detail += f", status: {backup_status}"
    if backup_path:
        detail += f" — {backup_path}"
    checks.append(_check("Recovery", "Latest backup", status, detail))
    metrics["Latest backup"] = backup_time
    metrics["Backup age hours"] = round(age, 1) if age is not None else "Unknown"
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
        "Checked at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_system_health_report() -> dict[str, Any]:
    database_checks, database_metrics = _database_report()
    storage_checks, storage_metrics = _storage_report()
    backup_checks, backup_metrics = _backup_report()
    checks = database_checks + storage_checks + backup_checks
    status_counts = {
        status: sum(1 for row in checks if row.get("Status") == status)
        for status in ("Healthy", "Warning", "Critical", "Info")
    }
    overall = "Critical" if status_counts["Critical"] else ("Warning" if status_counts["Warning"] else "Healthy")
    return {
        "overall_status": overall,
        "status_counts": status_counts,
        "checks": checks,
        "metrics": {**database_metrics, **storage_metrics, **backup_metrics},
        "runtime": _runtime_report(),
    }


def _safe_rerun(st: Any) -> None:
    rerun = _app_attr("pb_rerun") or _app_attr("refresh") or getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


def render_system_health_page() -> None:
    st = _st()
    if st is None:
        return
    if not _management_allowed():
        st.error("System Health is available to JobHub managers and administrators only.")
        return

    st.header("System Health")
    st.caption(
        "Read-only checks for the live JobHub database, persistent storage, backups, runtime and core data integrity. "
        "Connection strings, passwords and API secrets are never displayed."
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

    tab_checks, tab_data, tab_runtime = st.tabs(["Health checks", "Data snapshot", "Runtime"])
    with tab_checks:
        st.dataframe(report["checks"], width="stretch", hide_index=True)
    with tab_data:
        metrics = [{"Metric": key, "Value": value} for key, value in report["metrics"].items()]
        st.dataframe(metrics, width="stretch", hide_index=True)
    with tab_runtime:
        runtime = [{"Setting": key, "Value": value} for key, value in report["runtime"].items()]
        st.dataframe(runtime, width="stretch", hide_index=True)

    report_bytes = json.dumps(report, indent=2, default=str).encode("utf-8")
    st.download_button(
        "Download health report",
        data=report_bytes,
        file_name=f"jobhub_health_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
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
    installed = _install_session_state_reset_guard(st)
    installed = _patch_radio(st, st) or installed
    try:
        delta_module = sys.modules.get("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_radio(delta_cls, st) or installed
    return installed
