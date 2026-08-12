"""BrightHR Customer API request-body compatibility patch.

BrightHR's published Getting Started/Pagination guides show List Employees as a
POST with no body on page one and a continuationToken JSON body on later pages.
BrightHR's current API catalogue, however, labels the same employee query route
as GET. JobHub therefore uses the documented POST first and only falls back to a
read-only GET when that first-page POST repeatedly fails with a BrightHR 5xx.
Blip clocking requests continue to use their endpoint-specific filters object.

This guard keeps the existing Blip staging/review/publish workflow intact and
only replaces the read-only BrightHR query helpers.
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


def _post_query(
    blip: Any,
    session: Any,
    endpoint: str,
    token: str,
    phase: str,
    body: dict[str, Any] | None,
) -> Any:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    kwargs: dict[str, Any] = {"headers": headers, "timeout": 45}
    if body is not None:
        kwargs["json"] = body

    for attempt in range(3):
        response = session.post(endpoint, **kwargs)
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


def _get_query(blip: Any, session: Any, endpoint: str, token: str, phase: str) -> Any:
    """Read-only compatibility probe for BrightHR's API-catalogue GET method."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    for attempt in range(2):
        response = session.get(endpoint, headers=headers, timeout=45)
        status = int(getattr(response, "status_code", 0) or 0)
        if status in _RETRYABLE_STATUS and attempt < 1:
            time.sleep(0.5)
            continue
        try:
            response.raise_for_status()
        except requests.HTTPError:
            raise _phase_error(blip, phase, response) from None
        return response.json()
    raise RuntimeError(f"BrightHR {phase} failed after retries.")


def _is_retryable_server_failure(exc: BaseException) -> bool:
    return bool(re.search(r"HTTP (?:500|502|503|504)\b", str(exc or "")))


def install_blip_request_compat_guard() -> bool:
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
            body = {"continuationToken": continuation} if continuation else None

            if page_no == 1:
                try:
                    payload = _post_query(blip, session, endpoint, token, "employee query POST", body)
                except RuntimeError as post_exc:
                    if not _is_retryable_server_failure(post_exc):
                        raise
                    try:
                        payload = _get_query(blip, session, endpoint, token, "employee query GET fallback")
                    except RuntimeError as get_exc:
                        raise RuntimeError(
                            f"{post_exc} BrightHR API-catalogue GET fallback also failed: {get_exc} "
                            "OAuth token acquisition succeeded, so this now points to BrightHR's employee API or Customer API application/tenant provisioning."
                        ) from None
            else:
                payload = _post_query(blip, session, endpoint, token, "employee query POST", body)

            if isinstance(payload, Mapping):
                items = payload.get("items") or []
                next_token = payload.get("continuationToken")
            elif isinstance(payload, list):
                items = payload
                next_token = None
            else:
                items = []
                next_token = None

            for item in items:
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
            continuation = next_token
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
