"""Fallback attachment path for generated SWMS PDFs.

Some JobHub builds have different attach_document_to_job signatures. This guard
keeps SWMS generation reliable by falling back to a direct job_documents insert
if the helper cannot be called with the current signature.
"""

from __future__ import annotations

from datetime import datetime
from jobhub_time import jobhub_now
from pathlib import Path
from typing import Any

from . import swms_guard


def _fallback_attach(job_id: int, pdf_path: Path) -> None:
    swms_guard._execute(
        """
        INSERT INTO job_documents (job_id,document_type,file_name,file_path,created_at,notes,mime_type)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(job_id),
            "SWMS",
            Path(pdf_path).name,
            str(pdf_path),
            jobhub_now().strftime("%Y-%m-%d %H:%M:%S"),
            "Generic SWMS generated in JobHub.",
            "application/pdf",
        ),
    )


def install_swms_attach_fallback_guard() -> bool:
    original = getattr(swms_guard, "_attach", None)
    if original is None or getattr(original, "_pb_swms_attach_fallback_guard", False):
        return False

    def attach_with_fallback(job_id: int, pdf_path: Any) -> None:
        try:
            original(int(job_id), Path(pdf_path))
            return
        except TypeError:
            _fallback_attach(int(job_id), Path(pdf_path))
            return
        except Exception as original_exc:
            try:
                _fallback_attach(int(job_id), Path(pdf_path))
                return
            except Exception:
                raise original_exc

    attach_with_fallback._pb_swms_attach_fallback_guard = True
    attach_with_fallback._pb_original_attach = original
    swms_guard._attach = attach_with_fallback
    return True
