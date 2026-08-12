"""BrightHR Customer API request-body compatibility patch.

BrightHR's employee query uses continuation-token pagination. The live API has
proved sensitive to the exact POST body shape, so JobHub sends an empty JSON
object for the first page and only a continuationToken on later pages. Blip
clocking requests use their endpoint-specific filters object. Generic pagination
fields are intentionally omitted because BrightHR supplies a default page size.

This guard keeps the existing Blip staging/review/publish workflow intact and
only replaces the two read-only BrightHR query helpers.

Pagination is protected against non-progress: if BrightHR ever repeats a
continuation token, or returns more than the page ceiling, the sync raises a
clear error instead of looping forever.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Mapping

import requests

PATCH_MARKER = "_pb_blip_request_compat_guard"
_RETRYABLE_STATUS = {500, 502, 503, 504}
_MAX_PAGINATION_PAGES = 10_000


def _safe_problem_text(response: Any) -> str:
    """Return BrightHR RFC-7807 title/detail without leaking URLs or credentials."""
    try:
        payload = response.json()
    except Exception:
        return ""
    if not isinstance(payload, Mapping):
        return ""

    parts: list[str] = []
    for key in ("title", "detail"):
        value = str(payload.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    text = " ".join(parts)
    text = re.sub(r"https?://\S+", "[endpoint]", text)
    text = re.sub(r"(?i)(client_secret|authorization|bearer|token)\s*[:=]\s*\S+", r"\1=[redacted]", text)
    return text[:300]


def _phase_error(blip: Any, phase: str, response: Any) -> RuntimeError:
    """Build a safe, operator-useful API error without exposing endpoints/tokens."""
    status = getattr(response, "status_code", None)
    message = f"BrightHR {phase} failed with HTTP {status}." if status else f"BrightHR {phase} failed."

    details: list[str] = []
    problem = _safe_problem_text(response)
    if problem:
        details.append(problem)
    try:
        validation = blip._problem_detail(response)
    except Exception:
        validation = ""
    if validation and validation not in details:
        details.append(validation)
    if details:
        message = f"{message} {' '.join(details)}"
    return RuntimeError(message)


def _ensure_pagination_progress(phase: str, seen: set[Any], continuation: Any, page_no: int) -> None:
    """Bail out if BrightHR pagination stops making progress."""
    if page_no > _MAX_PAGINATION_PAGES:
        raise RuntimeError(
            f"BrightHR {phase} returned more than {_MAX_PAGINATION_PAGES} pages; refusing to continue."
        )
    if continuation is not None and continuation in seen:
        raise RuntimeError(
            f"BrightHR {phase} returned the same continuationToken twice; refusing to loop forever."
        )
    if continuation is not None:
        seen.add(continuation)


def _post_query(blip: Any, session: Any, endpoint: str, token: str, phase: str, body: dict[str, Any]) -> Any:
    """POST one BrightHR query, retrying only transient server failures."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    for attempt in range(3):
        response = session.post(
            endpoint,
            json=body,
            headers=headers,
            timeout=45,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        if status in _RETRYABLE_STATUS and attempt < 2:
            time.sleep(0.5 * (attempt + 1))
            continue
        try:
            response.raise_for_status()
        except requests.HTTPError:
            raise _phase_error(blip, phase, response) from None
        return response.json()
    raise RuntimeError(f"BrightHR {phase} failed after retries.")


def install_blip_request_compat_guard() -> bool:
    """Patch BrightHR employee/clocking query bodies to match the live API."""
    from . import blip_integration_guard as blip

    if getattr(blip, PATCH_MARKER, False):
        return False

    def _fetch_employees_with_token(session: Any, token: str) -> list[dict[str, str]]:
        endpoint = os.environ.get(blip.ENV_EMPLOYEES_URL, "").strip()
        if not endpoint:
            raise RuntimeError("BRIGHTHR_EMPLOYEES_URL is not configured.")

        employees: list[dict[str, str]] = []
        continuation: Any = None
        seen_tokens: set[Any] = set()
        page_no = 0
        while True:
            page_no += 1
            _ensure_pagination_progress("employee query", seen_tokens, continuation, page_no)
            # Use an explicit empty JSON object on page one. This keeps the
            # documented semantics (no continuation token) while avoiding a
            # BrightHR server-side failure seen with a zero-length POST body.
            body: dict[str, Any] = {}
            if continuation:
                body["continuationToken"] = continuation
            payload = _post_query(blip, session, endpoint, token, "employee query", body)
            items = payload.get("items") if isinstance(payload, Mapping) else None
            for item in items or []:
                if not isinstance(item, Mapping):
                    continue
                name_obj = item.get("name") if isinstance(item.get("name"), Mapping) else {}
                given = blip._string(name_obj.get("givenName"))
                family = blip._string(name_obj.get("familyName"))
                employee_id = blip._string(item.get("id"))
                if not employee_id:
                    continue
                employees.append(
                    {
                        "id": employee_id,
                        "name": blip._string(" ".join(part for part in (given, family) if part)),
                        "email": blip._string(item.get("email")),
                    }
                )
            continuation = payload.get("continuationToken") if isinstance(payload, Mapping) else None
            if not continuation:
                break
        return employees

    def _iter_attendance_records(session: Any = requests):
        endpoint = os.environ.get(blip.ENV_ATTENDANCE_URL, "").strip()
        if not endpoint:
            raise RuntimeError("BRIGHTHR_BLIP_ATTENDANCE_URL is not configured.")

        token = blip._request_token(session)
        employees = _fetch_employees_with_token(session, token)
        sync_from = blip._normalise_datetime_filter(os.environ.get(blip.ENV_SYNC_FROM, "").strip())
        sync_to = blip._normalise_datetime_filter(os.environ.get(blip.ENV_SYNC_TO, "").strip())

        for employee in employees:
            continuation: Any = None
            seen_tokens: set[Any] = set()
            page_no = 0
            while True:
                page_no += 1
                _ensure_pagination_progress("Blip clocking query", seen_tokens, continuation, page_no)
                filters: dict[str, Any] = {"employeeId": employee["id"]}
                if sync_from:
                    filters["from"] = sync_from
                if sync_to:
                    filters["to"] = sync_to
                body: dict[str, Any] = {"filters": filters}
                if continuation:
                    body["continuationToken"] = continuation

                payload = _post_query(blip, session, endpoint, token, "Blip clocking query", body)
                items = payload.get("items") if isinstance(payload, Mapping) else None
                for item in items or []:
                    if isinstance(item, Mapping):
                        yield {
                            "employee": {
                                "id": employee["id"],
                                "name": employee["name"],
                                "email": employee["email"],
                            },
                            **item,
                        }
                continuation = payload.get("continuationToken") if isinstance(payload, Mapping) else None
                if not continuation:
                    break

    blip._fetch_employees_with_token = _fetch_employees_with_token
    blip._iter_attendance_records = _iter_attendance_records
    setattr(blip, PATCH_MARKER, True)
    return True
