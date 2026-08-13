"""BrightHR Customer API request compatibility guard.

BrightHR's published Getting Started and Pagination guides show List Employees
as POST /employees/v1/query, with no request body on page one and only the
returned continuationToken in JSON on later pages. A live 405 response also
confirmed that GET is not supported for this tenant, so JobHub uses POST only.

This guard keeps the existing Blip staging/review/publish workflow intact and
only replaces the read-only BrightHR query helpers. It also surfaces safe
BrightHR request/correlation references when available so provider support can
trace server-side failures without exposing credentials or endpoint URLs.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Mapping

import requests

PATCH_MARKER = "_pb_blip_request_compat_guard"
_RETRYABLE_STATUS = {500, 502, 503, 504}
_MAX_PAGINATION_PAGES = 10_000
_SUPPORT_HEADER_NAMES = (
    "x-correlation-id",
    "x-request-id",
    "request-id",
    "trace-id",
    "x-trace-id",
    "traceparent",
)


def _safe_problem_text(response: Any) -> str:
    """Return a bounded, redacted description of a BrightHR error response."""
    payload: Any = None
    try:
        payload = response.json()
    except Exception:
        pass

    if payload is None:
        raw = str(getattr(response, "text", "") or "").strip()
        if not raw:
            return ""
        try:
            payload = json.loads(raw)
        except Exception:
            return _redact(raw)

    if not isinstance(payload, Mapping):
        return _redact(str(payload or ""))

    parts: list[str] = []
    for key in ("title", "detail"):
        value = str(payload.get(key) or "").strip()
        if value and value not in parts:
            parts.append(value)
    problem_type = payload.get("type")
    if isinstance(problem_type, str):
        problem_type = problem_type.strip().rstrip("/")
        if problem_type and problem_type != "about:blank":
            identifier = problem_type.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
            identifier = re.sub(r"[^A-Za-z0-9._-]", "", identifier)
            if identifier:
                parts.append(f"type={identifier}")

    return _redact(" ".join(parts))


def _redact(text: str) -> str:
    text = re.sub(r"https?://\S+", "[endpoint]", text)
    text = re.sub(
        r"(?i)(client_secret|access_token|authorization|bearer|token)\s*\"?\s*[:=]\s*\"?(?:bearer\s+)?[^\"',\s}]+",
        r"\1=[redacted]",
        text,
    )
    return text[:300]


def _support_reference(response: Any) -> str:
    """Return non-secret provider request IDs useful to BrightHR support."""
    headers = getattr(response, "headers", None)
    if not headers:
        return ""

    try:
        lowered = {str(key).lower(): str(value).strip() for key, value in headers.items()}
    except Exception:
        return ""

    refs: list[str] = []
    for name in _SUPPORT_HEADER_NAMES:
        value = lowered.get(name, "")
        if not value:
            continue
        # IDs/trace headers are safe diagnostics, but keep the output bounded
        # and strip unexpected characters so secrets/URLs cannot leak here.
        value = re.sub(r"[^A-Za-z0-9._:/;=-]", "", value)[:160]
        if value:
            refs.append(f"{name}={value}")
    return "; ".join(refs[:3])


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

    support_ref = _support_reference(response)
    if support_ref:
        details.append(f"BrightHR support reference: {support_ref}.")

    if phase == "employee query" and status in _RETRYABLE_STATUS:
        details.append(
            "OAuth token acquisition succeeded and JobHub used BrightHR's documented POST List Employees request. "
            "This is now a BrightHR Customer API server-side processing failure; BrightHR should verify the API "
            "application is correctly associated with this customer tenant."
        )

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

            # BrightHR documents the first POST with no body at all. Only later
            # pages send the continuation token as JSON.
            body = {"continuationToken": continuation} if continuation else None
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
