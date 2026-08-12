"""BrightHR Customer API request-body compatibility patch.

BrightHR's employee query examples send no JSON body on the first request and
only a continuationToken on subsequent pages.  The Blip clockings endpoint is
queried with a filters object.  Do not add generic pagination fields such as
pageSize to these requests unless BrightHR explicitly documents them for the
endpoint: the live API rejects an unexpected root request shape with HTTP 422.

This guard keeps the existing Blip staging/review/publish workflow intact and
only replaces the two read-only BrightHR query helpers.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

import requests

PATCH_MARKER = "_pb_blip_request_compat_guard"


def _phase_error(blip: Any, phase: str, response: Any) -> RuntimeError:
    """Build a safe, operator-useful API error without exposing endpoints/tokens."""
    status = getattr(response, "status_code", None)
    message = f"BrightHR {phase} failed with HTTP {status}." if status else f"BrightHR {phase} failed."
    try:
        detail = blip._problem_detail(response)
    except Exception:
        detail = ""
    if detail:
        message = f"{message} {detail}"
    return RuntimeError(message)


def _post_query(blip: Any, session: Any, endpoint: str, token: str, phase: str, body: dict[str, Any] | None) -> Any:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    kwargs: dict[str, Any] = {"headers": headers, "timeout": 45}
    if body is not None:
        # requests sets application/json automatically when json= is supplied.
        kwargs["json"] = body
    response = session.post(endpoint, **kwargs)
    try:
        response.raise_for_status()
    except requests.HTTPError:
        raise _phase_error(blip, phase, response) from None
    return response.json()


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
        while True:
            # BrightHR's documented first employee query has no body.  Only send
            # the continuation token after the API actually returns one.
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
            while True:
                # The live Blip endpoint expects the endpoint-specific filters
                # object.  Do not add pageSize: it can make the request fail
                # deserialisation at the root ($) with HTTP 422.
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
