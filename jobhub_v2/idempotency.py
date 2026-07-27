"""Pure helpers for safe offline-to-online Field Mode synchronisation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any


SUPPORTED_SYNC_TYPES = {
    "clock_on",
    "clock_off",
    "timesheet",
    "progress_photo",
    "field_form",
}


def normalise_sync_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event_type = str(payload.get("event_type", "")).strip().lower()
    if event_type not in SUPPORTED_SYNC_TYPES:
        raise ValueError(f"Unsupported sync event type: {event_type or '<blank>'}")

    employee_id = int(payload["employee_id"])
    job_id = int(payload["job_id"])
    occurred_at = str(payload.get("occurred_at", "")).strip()
    if not occurred_at:
        occurred_at = datetime.now(timezone.utc).isoformat()

    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise ValueError("Sync event data must be an object.")

    return {
        "event_type": event_type,
        "employee_id": employee_id,
        "job_id": job_id,
        "occurred_at": occurred_at,
        "data": data,
    }


def build_idempotency_key(payload: dict[str, Any]) -> str:
    normalised = normalise_sync_payload(payload)
    canonical = json.dumps(
        normalised,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
