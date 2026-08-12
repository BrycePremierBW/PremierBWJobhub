"""BrightHR Blip attendance integration for Premier Brushworks JobHub.

Blip remains the attendance source. JobHub stages imported attendance, requires
explicit BrightHR employee mappings, and only publishes completed sessions that
a manager has explicitly assigned to a JobHub job during review. Blip clockings
do not include a site/location field, so job assignment is a deliberate
review-time step rather than an automatic guess.

Secrets are read only from environment variables and are never persisted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import sys
from typing import Any, Mapping, Sequence

import requests


BLIP_PAGE_LABEL = "Blip Attendance"
PATCH_MARKER = "_pb_blip_integration_guard"

ENV_CLIENT_ID = "BRIGHTHR_CLIENT_ID"
ENV_CLIENT_SECRET = "BRIGHTHR_CLIENT_SECRET"
ENV_TOKEN_URL = "BRIGHTHR_TOKEN_URL"
ENV_EMPLOYEES_URL = "BRIGHTHR_EMPLOYEES_URL"
ENV_ATTENDANCE_URL = "BRIGHTHR_BLIP_ATTENDANCE_URL"
ENV_TOKEN_AUTH_MODE = "BRIGHTHR_TOKEN_AUTH_MODE"
ENV_SCOPE = "BRIGHTHR_SCOPE"
ENV_SYNC_FROM = "BRIGHTHR_SYNC_FROM"
ENV_SYNC_TO = "BRIGHTHR_SYNC_TO"

_ALLOWED_ROLES = {"manager", "admin", "administrator", "owner"}


def _st() -> Any:
    return sys.modules.get("streamlit")


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _use_postgres() -> bool:
    try:
        return bool(_app_attr("USE_POSTGRES", False))
    except Exception:
        return False


def _fallback_execute(sql: str, params: tuple[Any, ...] = ()) -> Any:
    from .database import connect

    conn = connect()
    try:
        cur = conn.cursor()
        raw_sql = sql.replace("?", "%s") if _use_postgres() else sql
        cur.execute(raw_sql, params)
        lastrowid = getattr(cur, "lastrowid", None)
        conn.commit()
        return lastrowid
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def _execute(sql: str, params: tuple[Any, ...] = ()) -> Any:
    fn = _app_attr("execute")
    if callable(fn):
        return fn(sql, params)
    return _fallback_execute(sql, params)


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    query = _app_attr("df_query") or _app_attr("safe_df_query")
    if callable(query):
        frame = query(sql, params)
        if frame is None or getattr(frame, "empty", True):
            return []
        return [dict(row) for row in frame.to_dict("records")]

    from .database import connect

    conn = connect()
    try:
        cur = conn.cursor()
        raw_sql = sql.replace("?", "%s") if _use_postgres() else sql
        cur.execute(raw_sql, params)
        values = cur.fetchall()
        columns = [str(item[0]) for item in (cur.description or [])]
        return [dict(zip(columns, row)) for row in values]
    finally:
        conn.close()


def _one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    values = _rows(sql, params)
    return values[0] if values else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _current_user() -> dict[str, Any]:
    st = _st()
    if st is None:
        return {}
    try:
        user = st.session_state.get("user") or {}
        return dict(user) if isinstance(user, dict) else {}
    except Exception:
        return {}


def _current_role() -> str:
    fn = _app_attr("current_role")
    if callable(fn):
        try:
            return str(fn() or "").strip().lower()
        except Exception:
            pass
    return str(_current_user().get("role") or "").strip().lower()


def _allowed() -> bool:
    return _current_role() in _ALLOWED_ROLES


def _actor() -> str:
    user = _current_user()
    for key in ("username", "name", "display_name", "email"):
        value = str(user.get(key) or "").strip()
        if value:
            return value[:160]
    return "JobHub user"


def _ensure_schema() -> None:
    pk = "BIGSERIAL PRIMARY KEY" if _use_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"

    _execute(
        f"""
        CREATE TABLE IF NOT EXISTS blip_employee_map (
            id {pk},
            provider_employee_id TEXT NOT NULL UNIQUE,
            provider_employee_name TEXT,
            provider_employee_email TEXT,
            employee_id INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            updated_by TEXT
        )
        """
    )
    _execute(
        f"""
        CREATE TABLE IF NOT EXISTS blip_attendance_entries (
            id {pk},
            provider_event_id TEXT UNIQUE,
            source_hash TEXT NOT NULL UNIQUE,
            provider_employee_id TEXT NOT NULL,
            provider_employee_name TEXT,
            provider_employee_email TEXT,
            provider_location_id TEXT,
            provider_location_name TEXT,
            employee_id INTEGER,
            job_id INTEGER,
            work_date TEXT,
            start_time TEXT,
            end_time TEXT,
            break_minutes INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Needs mapping',
            raw_payload TEXT NOT NULL,
            published_timesheet_id INTEGER,
            imported_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by TEXT,
            notes TEXT
        )
        """
    )
    _execute(
        f"""
        CREATE TABLE IF NOT EXISTS blip_sync_runs (
            id {pk},
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            fetched_count INTEGER NOT NULL DEFAULT 0,
            inserted_count INTEGER NOT NULL DEFAULT 0,
            updated_count INTEGER NOT NULL DEFAULT 0,
            unmatched_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT
        )
        """
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_blip_attendance_status ON blip_attendance_entries(status, imported_at)",
        "CREATE INDEX IF NOT EXISTS idx_blip_attendance_employee ON blip_attendance_entries(provider_employee_id, work_date)",
        "CREATE INDEX IF NOT EXISTS idx_blip_attendance_published ON blip_attendance_entries(published_timesheet_id)",
    ):
        _execute(statement)


def configuration_state() -> dict[str, bool]:
    return {
        ENV_CLIENT_ID: bool(os.environ.get(ENV_CLIENT_ID, "").strip()),
        ENV_CLIENT_SECRET: bool(os.environ.get(ENV_CLIENT_SECRET, "").strip()),
        ENV_TOKEN_URL: bool(os.environ.get(ENV_TOKEN_URL, "").strip()),
        ENV_EMPLOYEES_URL: bool(os.environ.get(ENV_EMPLOYEES_URL, "").strip()),
        ENV_ATTENDANCE_URL: bool(os.environ.get(ENV_ATTENDANCE_URL, "").strip()),
    }


def configuration_ready() -> bool:
    return all(configuration_state().values())


def _safe_error(exc: BaseException) -> str:
    """Return an operator-safe error without URLs, credentials or response bodies."""
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        summary = f"BrightHR request failed with HTTP {status}." if status else "BrightHR request failed."
        detail = _problem_detail(response)
        return f"{summary} {detail}" if detail else summary
    text = str(exc or "").strip()
    text = re.sub(r"https?://\S+", "[endpoint]", text)
    text = re.sub(r"(?i)(client_secret|authorization|bearer|token)\s*[:=]\s*\S+", r"\1=[redacted]", text)
    return text[:300] or exc.__class__.__name__


def _problem_detail(response: Any) -> str:
    try:
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    errors = payload.get("errors")
    if not isinstance(errors, dict):
        return ""
    parts = []
    for field, messages in errors.items():
        if isinstance(messages, list):
            parts.append(f"{field}: {'; '.join(_string(message) for message in messages[:3])}")
        else:
            parts.append(f"{field}: {_string(messages)}")
    text = " ".join(parts)
    text = re.sub(r"https?://\S+", "[endpoint]", text)
    return text[:300]


def _normalise_datetime_filter(value: Any) -> str:
    """BrightHR clocking filters require a full date-time, not a date-only value."""
    text = _string(value)
    if not text:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00Z"
    return text


def _request_token(session: Any = requests) -> str:
    client_id = os.environ.get(ENV_CLIENT_ID, "").strip()
    client_secret = os.environ.get(ENV_CLIENT_SECRET, "").strip()
    token_url = os.environ.get(ENV_TOKEN_URL, "").strip()
    if not client_id or not client_secret or not token_url:
        raise RuntimeError("BrightHR credentials and token URL are not fully configured.")

    data: dict[str, str] = {"grant_type": "client_credentials"}
    scope = os.environ.get(ENV_SCOPE, "").strip()
    if scope:
        data["scope"] = scope

    auth_mode = os.environ.get(ENV_TOKEN_AUTH_MODE, "body").strip().lower()
    if auth_mode == "basic":
        response = session.post(token_url, data=data, auth=(client_id, client_secret), timeout=20)
    elif auth_mode == "body":
        data["client_id"] = client_id
        data["client_secret"] = client_secret
        response = session.post(token_url, data=data, timeout=20)
    else:
        raise RuntimeError("BRIGHTHR_TOKEN_AUTH_MODE must be 'body' or 'basic'.")

    response.raise_for_status()
    payload = response.json()
    token = str(payload.get("access_token") or "").strip() if isinstance(payload, Mapping) else ""
    if not token:
        raise RuntimeError("BrightHR token response did not contain an access token.")
    return token


def _authorized_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _fetch_employees_with_token(session: Any, token: str) -> list[dict[str, str]]:
    """Enumerate BrightHR employees via POST /employees/v1/query."""
    endpoint = os.environ.get(ENV_EMPLOYEES_URL, "").strip()
    if not endpoint:
        raise RuntimeError("BRIGHTHR_EMPLOYEES_URL is not configured.")
    employees: list[dict[str, str]] = []
    continuation: Any = None
    while True:
        body: dict[str, Any] = {"pageSize": 100}
        if continuation:
            body["continuationToken"] = continuation
        response = session.post(
            endpoint,
            json=body,
            headers=_authorized_headers(token),
            timeout=45,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, Mapping) else None
        for item in items or []:
            if not isinstance(item, Mapping):
                continue
            name_obj = item.get("name") if isinstance(item.get("name"), Mapping) else {}
            given = _string(name_obj.get("givenName"))
            family = _string(name_obj.get("familyName"))
            employee_id = _string(item.get("id"))
            if not employee_id:
                continue
            employees.append(
                {
                    "id": employee_id,
                    "name": _string(" ".join(part for part in (given, family) if part)),
                    "email": _string(item.get("email")),
                }
            )
        continuation = payload.get("continuationToken") if isinstance(payload, Mapping) else None
        if not continuation:
            break
    return employees


def _fetch_attendance_payload(session: Any = requests) -> list[Mapping[str, Any]]:
    """Fetch Blip clockings using the documented BrightHR Customer API.

    The clockings endpoint (POST /blip/v1/clockings/query) is per-employee and
    JSON-bodied, so every BrightHR employee is enumerated first and queried in
    turn, paging through each employee's continuation token. Each clocking is
    enriched with the employee's name/email so the review UI can display names.
    """
    endpoint = os.environ.get(ENV_ATTENDANCE_URL, "").strip()
    if not endpoint:
        raise RuntimeError("BRIGHTHR_BLIP_ATTENDANCE_URL is not configured.")
    token = _request_token(session)
    employees = _fetch_employees_with_token(session, token)
    if not employees:
        return []

    sync_from = _normalise_datetime_filter(os.environ.get(ENV_SYNC_FROM, "").strip())
    sync_to = _normalise_datetime_filter(os.environ.get(ENV_SYNC_TO, "").strip())

    records: list[Mapping[str, Any]] = []
    for employee in employees:
        continuation: Any = None
        while True:
            body: dict[str, Any] = {"filters": {"employeeId": employee["id"]}}
            if sync_from:
                body["filters"]["from"] = sync_from
            if sync_to:
                body["filters"]["to"] = sync_to
            body["pageSize"] = 100
            if continuation:
                body["continuationToken"] = continuation
            response = session.post(
                endpoint,
                json=body,
                headers=_authorized_headers(token),
                timeout=45,
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items") if isinstance(payload, Mapping) else None
            for item in items or []:
                if isinstance(item, Mapping):
                    enriched = {
                        "employee": {
                            "id": employee["id"],
                            "name": employee["name"],
                            "email": employee["email"],
                        },
                        **item,
                    }
                    records.append(enriched)
            continuation = payload.get("continuationToken") if isinstance(payload, Mapping) else None
            if not continuation:
                break
    return records


def _dig(record: Mapping[str, Any], *paths: str) -> Any:
    for path in paths:
        value: Any = record
        ok = True
        for part in path.split("."):
            if not isinstance(value, Mapping) or part not in value:
                ok = False
                break
            value = value.get(part)
        if ok and value not in (None, ""):
            return value
    return None


def _string(value: Any) -> str:
    return str(value or "").strip()


def _time_part(value: Any) -> str:
    text = _string(value)
    if not text:
        return ""
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed.strftime("%H:%M")
    except ValueError:
        pass
    match = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?", text)
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else text[:16]


def _date_part(explicit: Any, start: Any) -> str:
    text = _string(explicit)
    if text:
        match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        return match.group(0) if match else text[:10]
    start_text = _string(start)
    match = re.search(r"\d{4}-\d{2}-\d{2}", start_text)
    return match.group(0) if match else ""


def _dt(value: Any) -> datetime | None:
    text = _string(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _zone(tz_value: Any) -> timezone | None:
    """Resolve an IANA name (or simple +/-hh:mm offset) to a timezone."""
    text = _string(tz_value)
    if not text:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)  # type: ignore[return-value]
    except Exception:
        pass
    match = re.match(r"^([+-]?)(\d{1,2}):(\d{2})$", text)
    if match:
        try:
            sign = -1 if match.group(1) == "-" else 1
            hours = int(match.group(2))
            minutes = int(match.group(3))
            if hours <= 14 and minutes < 60:
                return timezone(timedelta(hours=sign * hours, minutes=sign * minutes))
        except (TypeError, ValueError):
            pass
    return None


def _local_time(value: Any, tz_value: Any, fallback: str) -> str:
    """Convert a UTC clocking timestamp to the recorded local timezone."""
    parsed = _dt(value)
    if parsed is None or parsed.tzinfo is None:
        return fallback
    zone = _zone(tz_value)
    if zone is None:
        return parsed.strftime("%H:%M")
    return parsed.astimezone(zone).strftime("%H:%M")


def _local_date(value: Any, tz_value: Any, fallback: str) -> str:
    parsed = _dt(value)
    if parsed is None or parsed.tzinfo is None:
        return fallback
    zone = _zone(tz_value)
    if zone is None:
        return parsed.strftime("%Y-%m-%d")
    return parsed.astimezone(zone).strftime("%Y-%m-%d")


def _break_minutes(record: Mapping[str, Any]) -> int:
    value = _dig(
        record,
        "breakMinutes",
        "break_minutes",
        "breakDurationMinutes",
        "break_duration_minutes",
        "break.durationMinutes",
        "break.duration_minutes",
    )
    if value not in (None, ""):
        try:
            return max(0, int(round(float(value or 0))))
        except (TypeError, ValueError):
            pass
    breaks = record.get("breaks") if isinstance(record, Mapping) else None
    if isinstance(breaks, list):
        total = 0
        for break_item in breaks:
            if not isinstance(break_item, Mapping):
                continue
            start = _dt(break_item.get("start"))
            end = _dt(break_item.get("end"))
            if start is not None and end is not None and end > start:
                total += int((end - start).total_seconds() // 60)
        return max(0, total)
    return 0


def _extract_records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        raise ValueError("BrightHR attendance response is not a JSON object or list.")
    for key in ("items", "data", "results", "records", "attendance"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        if isinstance(value, Mapping):
            for nested_key in ("items", "results", "records"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return [item for item in nested if isinstance(item, Mapping)]
    if any(key in payload for key in ("employeeId", "employee_id", "clockIn", "clock_in", "startTime", "start_time")):
        return [payload]
    raise ValueError(
        "BrightHR response shape was not recognised. Configure the attendance endpoint to return attendance records."
    )


def normalise_blip_record(record: Mapping[str, Any]) -> dict[str, Any]:
    event_id = _string(
        _dig(record, "id", "eventId", "event_id", "sessionId", "session_id", "attendanceId", "attendance_id")
    )
    employee_id = _string(
        _dig(
            record,
            "employee.id",
            "employee.employeeId",
            "employee.employee_id",
            "user.id",
            "employeeId",
            "employee_id",
            "userId",
            "user_id",
        )
    )
    employee_name = _string(
        _dig(record, "employee.name", "employee.fullName", "user.name", "employeeName", "employee_name", "name")
    )
    employee_email = _string(
        _dig(record, "employee.email", "user.email", "employeeEmail", "employee_email", "email")
    )
    location_id = _string(
        _dig(
            record,
            "location.id",
            "location.locationId",
            "site.id",
            "site.siteId",
            "locationId",
            "location_id",
            "siteId",
            "site_id",
        )
    )
    location_name = _string(
        _dig(record, "location.name", "site.name", "locationName", "location_name", "siteName", "site_name")
    )
    start_raw = _dig(record, "clockIn", "clock_in", "startTime", "start_time", "start", "startedAt", "started_at")
    end_raw = _dig(record, "clockOut", "clock_out", "endTime", "end_time", "end", "endedAt", "ended_at")
    start_tz = _dig(record, "startTimeZone", "start_time_zone", "timeZone", "timezone")
    end_tz = _dig(record, "endTimeZone", "end_time_zone", "timeZone", "timezone")
    work_date = _date_part(_dig(record, "workDate", "work_date", "date"), start_raw)

    if not employee_id:
        raise ValueError("Attendance record has no BrightHR employee identifier.")
    if not _string(start_raw):
        raise ValueError("Attendance record has no clock-in/start time.")

    return {
        "provider_event_id": event_id,
        "provider_employee_id": employee_id,
        "provider_employee_name": employee_name,
        "provider_employee_email": employee_email,
        "provider_location_id": location_id,
        "provider_location_name": location_name,
        "work_date": _local_date(start_raw, start_tz, work_date),
        "start_time": _local_time(start_raw, start_tz, _time_part(start_raw)),
        "end_time": _local_time(end_raw, end_tz, _time_part(end_raw)),
        "break_minutes": _break_minutes(record),
        "raw_payload": json.dumps(record, sort_keys=True, separators=(",", ":"), default=str),
    }


def normalise_blip_records(payload: Any) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, record in enumerate(_extract_records(payload), start=1):
        try:
            values.append(normalise_blip_record(record))
        except ValueError as exc:
            errors.append(f"record {index}: {exc}")
    if not values and errors:
        raise ValueError("No usable BrightHR attendance records: " + "; ".join(errors[:3]))
    return values


def source_hash(record: Mapping[str, Any]) -> str:
    raw = _string(record.get("raw_payload"))
    if not raw:
        raw = json.dumps(dict(record), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def attendance_status(
    *,
    end_time: Any,
    employee_id: Any,
    job_id: Any,
    published_timesheet_id: Any = None,
) -> str:
    if published_timesheet_id not in (None, "", 0, "0"):
        return "Published"
    if not _string(end_time):
        return "Open"
    if employee_id in (None, "", 0, "0") or job_id in (None, "", 0, "0"):
        return "Needs mapping"
    return "Ready"


def _employee_mapping(provider_employee_id: str) -> dict[str, Any] | None:
    return _one(
        """
        SELECT employee_id
        FROM blip_employee_map
        WHERE provider_employee_id=? AND active=1
        LIMIT 1
        """,
        (provider_employee_id,),
    )


def _upsert_attendance(record: Mapping[str, Any]) -> str:
    row = dict(record)
    digest = source_hash(row)
    event_id = _string(row.get("provider_event_id"))
    existing = (
        _one("SELECT * FROM blip_attendance_entries WHERE provider_event_id=? LIMIT 1", (event_id,))
        if event_id
        else None
    )
    if existing is None:
        existing = _one("SELECT * FROM blip_attendance_entries WHERE source_hash=? LIMIT 1", (digest,))

    emp_map = _employee_mapping(_string(row.get("provider_employee_id"))) or {}
    employee_id = emp_map.get("employee_id")
    job_id = existing.get("job_id") if existing else None
    published_id = existing.get("published_timesheet_id") if existing else None
    status = attendance_status(
        end_time=row.get("end_time"),
        employee_id=employee_id,
        job_id=job_id,
        published_timesheet_id=published_id,
    )
    if existing:
        _execute(
            """
            UPDATE blip_attendance_entries
            SET source_hash=?, provider_employee_name=?, provider_employee_email=?,
                provider_location_id=?, provider_location_name=?, employee_id=?, job_id=?,
                work_date=?, start_time=?, end_time=?, break_minutes=?, status=?,
                raw_payload=?, imported_at=?
            WHERE id=?
            """,
            (
                digest,
                _string(row.get("provider_employee_name")),
                _string(row.get("provider_employee_email")),
                _string(row.get("provider_location_id")),
                _string(row.get("provider_location_name")),
                employee_id,
                job_id,
                _string(row.get("work_date")),
                _string(row.get("start_time")),
                _string(row.get("end_time")),
                int(row.get("break_minutes") or 0),
                status,
                _string(row.get("raw_payload")),
                _now(),
                existing["id"],
            ),
        )
        return "updated"

    _execute(
        """
        INSERT INTO blip_attendance_entries(
            provider_event_id,source_hash,provider_employee_id,provider_employee_name,
            provider_employee_email,provider_location_id,provider_location_name,
            employee_id,job_id,work_date,start_time,end_time,break_minutes,status,
            raw_payload,imported_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event_id or None,
            digest,
            _string(row.get("provider_employee_id")),
            _string(row.get("provider_employee_name")),
            _string(row.get("provider_employee_email")),
            _string(row.get("provider_location_id")),
            _string(row.get("provider_location_name")),
            employee_id,
            job_id,
            _string(row.get("work_date")),
            _string(row.get("start_time")),
            _string(row.get("end_time")),
            int(row.get("break_minutes") or 0),
            status,
            _string(row.get("raw_payload")),
            _now(),
        ),
    )
    return "inserted"


def sync_blip(session: Any = requests) -> dict[str, int]:
    _ensure_schema()
    started_at = _now()
    _execute(
        "INSERT INTO blip_sync_runs(started_at,status) VALUES(?,?)",
        (started_at, "Running"),
    )
    run = _one("SELECT id FROM blip_sync_runs WHERE started_at=? ORDER BY id DESC LIMIT 1", (started_at,))
    run_id = int(run["id"]) if run else None

    try:
        records = normalise_blip_records(_fetch_attendance_payload(session))
        inserted = 0
        updated = 0
        for record in records:
            outcome = _upsert_attendance(record)
            inserted += int(outcome == "inserted")
            updated += int(outcome == "updated")
        unmatched = int(
            (_one(
                """
                SELECT COUNT(*) AS n FROM blip_attendance_entries
                WHERE published_timesheet_id IS NULL AND status='Needs mapping'
                """
            ) or {}).get("n") or 0
        )
        if run_id:
            _execute(
                """
                UPDATE blip_sync_runs
                SET finished_at=?,status='Completed',fetched_count=?,inserted_count=?,
                    updated_count=?,unmatched_count=?,error_message=NULL
                WHERE id=?
                """,
                (_now(), len(records), inserted, updated, unmatched, run_id),
            )
        return {
            "fetched": len(records),
            "inserted": inserted,
            "updated": updated,
            "unmatched": unmatched,
        }
    except Exception as exc:
        if run_id:
            _execute(
                "UPDATE blip_sync_runs SET finished_at=?,status='Failed',error_message=? WHERE id=?",
                (_now(), _safe_error(exc), run_id),
            )
        raise RuntimeError(_safe_error(exc)) from exc


def save_employee_mapping(
    provider_employee_id: str,
    employee_id: int,
    *,
    provider_name: str = "",
    provider_email: str = "",
    actor: str | None = None,
) -> None:
    _ensure_schema()
    provider_employee_id = _string(provider_employee_id)
    if not provider_employee_id:
        raise ValueError("BrightHR employee ID is required.")
    existing = _one(
        "SELECT id FROM blip_employee_map WHERE provider_employee_id=?",
        (provider_employee_id,),
    )
    params = (
        _string(provider_name),
        _string(provider_email),
        int(employee_id),
        _now(),
        _string(actor or _actor()),
    )
    if existing:
        _execute(
            """
            UPDATE blip_employee_map
            SET provider_employee_name=?,provider_employee_email=?,employee_id=?,
                active=1,updated_at=?,updated_by=?
            WHERE provider_employee_id=?
            """,
            (*params, provider_employee_id),
        )
    else:
        _execute(
            """
            INSERT INTO blip_employee_map(
                provider_employee_id,provider_employee_name,provider_employee_email,
                employee_id,active,updated_at,updated_by
            ) VALUES(?,?,?,?,1,?,?)
            """,
            (provider_employee_id, *params),
        )
    refresh_mappings()


def refresh_mappings() -> None:
    _ensure_schema()
    entries = _rows(
        """
        SELECT id,provider_employee_id,end_time,job_id,published_timesheet_id
        FROM blip_attendance_entries
        WHERE published_timesheet_id IS NULL
        """
    )
    for entry in entries:
        emp = _employee_mapping(_string(entry.get("provider_employee_id"))) or {}
        employee_id = emp.get("employee_id")
        status = attendance_status(
            end_time=entry.get("end_time"),
            employee_id=employee_id,
            job_id=entry.get("job_id"),
        )
        _execute(
            "UPDATE blip_attendance_entries SET employee_id=?,status=? WHERE id=?",
            (employee_id, status, entry["id"]),
        )


def publish_attendance_entry(entry_id: int, *, actor: str | None = None) -> int:
    _ensure_schema()
    entry = _one("SELECT * FROM blip_attendance_entries WHERE id=?", (int(entry_id),))
    if not entry:
        raise ValueError("Blip attendance entry was not found.")
    if entry.get("published_timesheet_id"):
        return int(entry["published_timesheet_id"])

    required = ("employee_id", "job_id", "work_date", "start_time", "end_time")
    missing = [field for field in required if entry.get(field) in (None, "", 0, "0")]
    if missing:
        raise ValueError("Attendance entry is not ready to publish: " + ", ".join(missing) + " is missing.")
    if str(entry.get("status") or "") != "Ready":
        raise ValueError(f"Attendance entry status is {entry.get('status')!s}, not Ready.")

    external_key = _string(entry.get("provider_event_id")) or _string(entry.get("source_hash"))[:16]
    description = f"BrightHR Blip attendance import [blip:{external_key}]"

    existing = _one(
        """
        SELECT id FROM timesheet_entries
        WHERE employee_id=? AND job_id=? AND work_date=? AND start_time=? AND end_time=?
          AND description=?
        ORDER BY id DESC LIMIT 1
        """,
        (
            int(entry["employee_id"]),
            int(entry["job_id"]),
            _string(entry["work_date"]),
            _string(entry["start_time"]),
            _string(entry["end_time"]),
            description,
        ),
    )
    if existing:
        timesheet_id = int(existing["id"])
    else:
        created = _now()
        _execute(
            """
            INSERT INTO timesheet_entries(
                employee_id,job_id,work_date,start_time,end_time,break_minutes,
                description,status,rejection_reason,created_at,submitted_at,
                approved_at,approved_by
            ) VALUES(?,?,?,?,?,?,?,'Submitted',NULL,?,?,NULL,NULL)
            """,
            (
                int(entry["employee_id"]),
                int(entry["job_id"]),
                _string(entry["work_date"]),
                _string(entry["start_time"]),
                _string(entry["end_time"]),
                int(entry.get("break_minutes") or 0),
                description,
                created,
                created,
            ),
        )
        inserted = _one(
            """
            SELECT id FROM timesheet_entries
            WHERE employee_id=? AND job_id=? AND work_date=? AND start_time=? AND end_time=?
              AND description=?
            ORDER BY id DESC LIMIT 1
            """,
            (
                int(entry["employee_id"]),
                int(entry["job_id"]),
                _string(entry["work_date"]),
                _string(entry["start_time"]),
                _string(entry["end_time"]),
                description,
            ),
        )
        if not inserted:
            raise RuntimeError("JobHub created the timesheet but could not confirm its ID.")
        timesheet_id = int(inserted["id"])

    _execute(
        """
        UPDATE blip_attendance_entries
        SET published_timesheet_id=?,status='Published',reviewed_at=?,reviewed_by=?
        WHERE id=?
        """,
        (timesheet_id, _now(), _string(actor or _actor()), int(entry_id)),
    )
    return timesheet_id


def _employees() -> list[dict[str, Any]]:
    return _rows(
        """
        SELECT id,COALESCE(name,'') AS name,COALESCE(role,'') AS role,
               COALESCE(status,'') AS status
        FROM employees
        ORDER BY CASE WHEN LOWER(COALESCE(status,''))='active' THEN 0 ELSE 1 END,
                 LOWER(COALESCE(name,'')),id
        """
    )


def _jobs() -> list[dict[str, Any]]:
    return _rows(
        """
        SELECT id,COALESCE(job_no,'') AS job_no,COALESCE(job_name,'') AS job_name,
               COALESCE(site_address,'') AS site_address,COALESCE(status,'') AS status
        FROM jobs
        ORDER BY CASE WHEN LOWER(COALESCE(status,'')) IN ('active','in progress','current') THEN 0 ELSE 1 END,
                 COALESCE(job_no,''),LOWER(COALESCE(job_name,'')),id
        """
    )


def _employee_label(row: Mapping[str, Any]) -> str:
    name = _string(row.get("name")) or f"Employee #{row.get('id')}"
    role = _string(row.get("role"))
    return f"{name} — {role}" if role else name


def _job_label(row: Mapping[str, Any]) -> str:
    number = _string(row.get("job_no"))
    name = _string(row.get("job_name"))
    address = _string(row.get("site_address"))
    core = " — ".join(part for part in (number, name) if part) or f"Job #{row.get('id')}"
    return f"{core} · {address}" if address else core


def _render_overview(st: Any) -> None:
    state = configuration_state()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Client ID", "Configured" if state[ENV_CLIENT_ID] else "Missing")
    c2.metric("Client secret", "Configured" if state[ENV_CLIENT_SECRET] else "Missing")
    c3.metric("Token endpoint", "Configured" if state[ENV_TOKEN_URL] else "Missing")
    c4.metric("Employees endpoint", "Configured" if state[ENV_EMPLOYEES_URL] else "Missing")
    c5.metric("Blip attendance endpoint", "Configured" if state[ENV_ATTENDANCE_URL] else "Missing")
    st.caption("Secrets are read from Render environment variables and are not stored in JobHub.")
    if state[ENV_ATTENDANCE_URL]:
        st.caption(
            "Set BRIGHTHR_SYNC_FROM / BRIGHTHR_SYNC_TO (e.g. 2026-08-01 / 2026-08-12, range at most 31 days, "
            "no more than 90 days in the past) to pull a historical window. Without a range only currently "
            "active clockings are returned."
        )

    latest = _one("SELECT * FROM blip_sync_runs ORDER BY id DESC LIMIT 1")
    if latest:
        st.write(
            f"Last sync: **{latest.get('status')}** · {latest.get('finished_at') or latest.get('started_at')} · "
            f"fetched {latest.get('fetched_count', 0)}, inserted {latest.get('inserted_count', 0)}, "
            f"updated {latest.get('updated_count', 0)}, unmatched {latest.get('unmatched_count', 0)}"
        )
        if latest.get("status") == "Failed" and latest.get("error_message"):
            st.warning(str(latest["error_message"]))

    if st.button("Sync Blip now", type="primary", disabled=not configuration_ready(), key="blip_sync_now"):
        with st.spinner("Syncing BrightHR Blip attendance..."):
            try:
                result = sync_blip()
                st.success(
                    f"Blip sync complete: {result['fetched']} fetched, {result['inserted']} new, "
                    f"{result['updated']} updated, {result['unmatched']} still need mapping."
                )
                st.rerun()
            except Exception as exc:
                st.error(_safe_error(exc))


def _render_employee_mapping(st: Any) -> None:
    providers = _rows(
        """
        SELECT provider_employee_id,
               MAX(COALESCE(provider_employee_name,'')) AS provider_employee_name,
               MAX(COALESCE(provider_employee_email,'')) AS provider_employee_email,
               COUNT(*) AS attendance_rows
        FROM blip_attendance_entries
        GROUP BY provider_employee_id
        ORDER BY LOWER(MAX(COALESCE(provider_employee_name,''))),provider_employee_id
        """
    )
    if not providers:
        st.info("Run a Blip sync first to discover BrightHR employees.")
        return
    employees = _employees()
    if not employees:
        st.warning("JobHub has no employees available to map.")
        return

    provider_labels = {
        f"{row.get('provider_employee_name') or row['provider_employee_id']} · "
        f"{row.get('provider_employee_email') or row['provider_employee_id']}": row
        for row in providers
    }
    selected_provider_label = st.selectbox(
        "BrightHR employee", list(provider_labels), key="blip_map_employee_provider"
    )
    provider = provider_labels[selected_provider_label]
    employee_labels = {_employee_label(row): row for row in employees}
    selected_employee_label = st.selectbox(
        "JobHub employee", list(employee_labels), key="blip_map_employee_jobhub"
    )
    if st.button("Save employee mapping", key="blip_save_employee_map"):
        target = employee_labels[selected_employee_label]
        save_employee_mapping(
            _string(provider["provider_employee_id"]),
            int(target["id"]),
            provider_name=_string(provider.get("provider_employee_name")),
            provider_email=_string(provider.get("provider_employee_email")),
        )
        st.success("Employee mapping saved.")
        st.rerun()

    current = _rows(
        """
        SELECT m.provider_employee_name,m.provider_employee_email,e.name AS jobhub_employee,m.updated_at,m.updated_by
        FROM blip_employee_map m LEFT JOIN employees e ON e.id=m.employee_id
        WHERE m.active=1 ORDER BY LOWER(COALESCE(m.provider_employee_name,'')),m.provider_employee_id
        """
    )
    if current:
        st.dataframe(current, use_container_width=True, hide_index=True)


def _render_attendance(st: Any) -> None:
    status_filter = st.selectbox(
        "Attendance status",
        ["All", "Needs mapping", "Open", "Ready", "Published"],
        key="blip_attendance_status_filter",
    )
    where = "" if status_filter == "All" else "WHERE b.status=?"
    params: tuple[Any, ...] = () if status_filter == "All" else (status_filter,)
    rows = _rows(
        f"""
        SELECT b.id,b.work_date,b.start_time,b.end_time,b.break_minutes,b.status,
               b.employee_id,b.provider_employee_name,b.provider_employee_email,
               e.name AS jobhub_employee,j.job_no,j.job_name,b.published_timesheet_id,b.imported_at
        FROM blip_attendance_entries b
        LEFT JOIN employees e ON e.id=b.employee_id
        LEFT JOIN jobs j ON j.id=b.job_id
        {where}
        ORDER BY COALESCE(b.work_date,'') DESC,COALESCE(b.start_time,'') DESC,b.id DESC
        LIMIT 500
        """,
        params,
    )
    if not rows:
        st.info("No Blip attendance rows match this filter.")
        return
    st.dataframe(rows, use_container_width=True, hide_index=True)

    unreviewed = [row for row in rows if not row.get("published_timesheet_id")]
    if unreviewed:
        st.subheader("Assign job")
        st.caption("BrightHR Blip clockings carry no site field, so each attendance row is assigned to a JobHub job here, during review.")
        job_labels = {_job_label(job): int(job["id"]) for job in _jobs()}
        if not job_labels:
            st.warning("JobHub has no jobs available to assign.")
        else:
            review_choices = {
                f"#{row['id']} · {row.get('work_date')} {row.get('start_time')}–{row.get('end_time')} · "
                f"{row.get('jobhub_employee') or row.get('provider_employee_name') or row.get('provider_employee_email')} · "
                f"{row.get('job_no') or row.get('job_name') or 'no job'}": int(row["id"])
                for row in unreviewed
            }
            selected_label = st.selectbox("Attendance row", list(review_choices), key="blip_review_row")
            review_row = next(row for row in unreviewed if int(row["id"]) == review_choices[selected_label])
            current_job_id = int(review_row["job_id"]) if review_row.get("job_id") else None
            job_index = list(job_labels.values()).index(current_job_id) if current_job_id in job_labels.values() else 0
            selected_job_label = st.selectbox(
                "JobHub job", list(job_labels), index=job_index, key="blip_review_job"
            )
            if st.button("Save job assignment", key="blip_review_save"):
                new_job_id = job_labels[selected_job_label]
                new_status = attendance_status(
                    end_time=review_row.get("end_time"),
                    employee_id=review_row.get("employee_id"),
                    job_id=new_job_id,
                )
                _execute(
                    "UPDATE blip_attendance_entries SET job_id=?,status=? WHERE id=?",
                    (new_job_id, new_status, int(review_row["id"])),
                )
                st.success("Job assignment saved.")
                st.rerun()

    ready = [row for row in rows if row.get("status") == "Ready"]
    if not ready:
        return
    choices = {
        f"#{row['id']} · {row.get('work_date')} {row.get('start_time')}–{row.get('end_time')} · "
        f"{row.get('jobhub_employee') or row.get('provider_employee_name')} · "
        f"{row.get('job_no') or row.get('job_name')}": int(row["id"])
        for row in ready
    }
    selected = st.multiselect(
        "Ready entries to publish to JobHub timesheets",
        list(choices),
        key="blip_publish_entries",
    )
    if st.button(
        f"Publish {len(selected)} selected timesheet(s)",
        type="primary",
        disabled=not bool(selected),
        key="blip_publish_selected",
    ):
        published = 0
        errors: list[str] = []
        for label in selected:
            try:
                publish_attendance_entry(choices[label])
                published += 1
            except Exception as exc:
                errors.append(f"{label}: {_safe_error(exc)}")
        if published:
            st.success(f"Published {published} Blip attendance entr{'y' if published == 1 else 'ies'} to Timesheets.")
        for error in errors:
            st.error(error)
        if published and not errors:
            st.rerun()


def render_blip_attendance_page() -> None:
    st = _st()
    if st is None:
        return
    st.header("BrightHR Blip Attendance")
    if not _allowed():
        st.warning("Blip attendance integration is limited to JobHub managers and administrators.")
        return

    _ensure_schema()
    st.caption(
        "Blip remains the clocking source. JobHub stages attendance first, matches BrightHR employees to "
        "JobHub staff, and only publishes sessions a manager has explicitly assigned to a job during review."
    )
    tabs = st.tabs(["Sync", "Employee mappings", "Attendance review"])
    with tabs[0]:
        _render_overview(st)
    with tabs[1]:
        _render_employee_mapping(st)
    with tabs[2]:
        _render_attendance(st)


def _labels(options: Any) -> list[str]:
    try:
        return [str(item) for item in list(options)]
    except Exception:
        return []


def _is_site_team_page_picker(options: Any) -> bool:
    labels = set(_labels(options))
    anchors = {"Timesheets", "Materials", "Wages", "Equipment", "Job Photos", "Document Centre", "PDF Import"}
    return len(labels & anchors) >= 3


def _patch_choice(owner: Any, method_name: str, st: Any) -> bool:
    original = getattr(owner, method_name, None)
    marker = f"{PATCH_MARKER}_{method_name}"
    if original is None or getattr(original, marker, False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        arg_list = list(args)
        options_index = None
        if len(arg_list) >= 2 and isinstance(arg_list[0], str):
            options_index = 1
        elif len(arg_list) >= 3:
            options_index = 2
        options = arg_list[options_index] if options_index is not None else kwargs.get("options")
        if _allowed() and _is_site_team_page_picker(options):
            values = list(options)
            if BLIP_PAGE_LABEL not in _labels(values):
                values.append(BLIP_PAGE_LABEL)
            if options_index is not None:
                arg_list[options_index] = values
            else:
                kwargs["options"] = values
        result = original(*tuple(arg_list), **kwargs)
        if str(result) == BLIP_PAGE_LABEL:
            render_blip_attendance_page()
            st.stop()
        return result

    setattr(wrapper, marker, True)
    setattr(wrapper, "_pb_original_choice", original)
    setattr(owner, method_name, wrapper)
    return True


def install_blip_integration_guard() -> bool:
    """Expose manager/admin Blip attendance without adding startup DB work."""
    st = _st()
    if st is None:
        return False
    installed = False
    for method_name in ("radio", "selectbox"):
        installed = _patch_choice(st, method_name, st) or installed
    try:
        delta_module = sys.modules.get("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        for method_name in ("radio", "selectbox"):
            installed = _patch_choice(delta_cls, method_name, st) or installed
    return installed
