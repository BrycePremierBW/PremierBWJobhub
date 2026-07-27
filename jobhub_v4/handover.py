"""Builder-facing close-out manifest and handover ZIP generation."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from io import BytesIO, StringIO
import json
from typing import Any, Iterable
import zipfile


DEFAULT_REQUIREMENTS = {
    "colour_approval": "Approved colour schedule",
    "progress_photo": "Progress photos",
    "completion_photo": "Completion photos",
    "defect_closeout": "Closed defect evidence",
    "warranty": "Warranty / product information",
}


def build_handover_manifest(
    *,
    job: dict[str, Any],
    evidence: Iterable[dict[str, Any]],
    colour_approvals: Iterable[dict[str, Any]] = (),
    requirements: dict[str, str] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic readiness manifest for a builder handover."""
    requirements = requirements or DEFAULT_REQUIREMENTS
    evidence_rows = [dict(row) for row in evidence]
    colour_rows = [dict(row) for row in colour_approvals]
    available_types = {
        str(row.get("evidence_type") or "").strip().casefold()
        for row in evidence_rows
        if str(row.get("status") or "active").strip().casefold() != "void"
    }
    if any(
        str(row.get("status") or "").strip().casefold() == "approved"
        for row in colour_rows
    ):
        available_types.add("colour_approval")

    checklist = [
        {
            "requirement": key,
            "label": label,
            "complete": key.casefold() in available_types,
        }
        for key, label in requirements.items()
    ]
    missing = [item["label"] for item in checklist if not item["complete"]]
    return {
        "schema_version": 1,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "job": {
            "id": job.get("id"),
            "job_no": str(job.get("job_no") or ""),
            "job_name": str(job.get("job_name") or ""),
            "site_address": str(job.get("site_address") or ""),
            "builder_client": str(
                job.get("builder_client") or job.get("client_name") or ""
            ),
        },
        "ready": not missing,
        "missing_requirements": missing,
        "checklist": checklist,
        "evidence_count": len(evidence_rows),
        "colour_approval_count": len(colour_rows),
        "evidence": evidence_rows,
        "colour_approvals": colour_rows,
    }


def _csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    columns: list[str] = []
    for row in rows:
        for column in row:
            if column not in columns:
                columns.append(column)
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def build_handover_zip(manifest: dict[str, Any]) -> bytes:
    """Package the manifest and audit-friendly CSV schedules."""
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "handover_manifest.json",
            json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
        )
        archive.writestr(
            "evidence_schedule.csv",
            _csv_bytes([dict(row) for row in manifest.get("evidence", [])]),
        )
        archive.writestr(
            "colour_approvals.csv",
            _csv_bytes([dict(row) for row in manifest.get("colour_approvals", [])]),
        )
        archive.writestr(
            "README.txt",
            (
                "Premier Brushworks JobHub handover pack\n"
                f"Job: {manifest.get('job', {}).get('job_no', '')} - "
                f"{manifest.get('job', {}).get('job_name', '')}\n"
                f"Ready: {'Yes' if manifest.get('ready') else 'No'}\n"
                "Review handover_manifest.json for the complete evidence checklist.\n"
            ),
        )
    return output.getvalue()
