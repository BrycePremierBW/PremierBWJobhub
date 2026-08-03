"""Reliability patch for SWMS signature saving."""

from __future__ import annotations

from . import swms_guard


def install_swms_signature_index_guard() -> bool:
    original = getattr(swms_guard, "ensure_swms_schema", None)
    if original is None or getattr(original, "_pb_swms_signature_index_guard", False):
        return False

    def ensured_with_full_signature_index() -> None:
        original()
        try:
            swms_guard._execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_job_swms_signature_unique_full
                ON job_swms_signatures(job_swms_id, employee_id)
                """
            )
        except Exception:
            pass

    ensured_with_full_signature_index._pb_swms_signature_index_guard = True
    ensured_with_full_signature_index._pb_original_ensure_swms_schema = original
    swms_guard.ensure_swms_schema = ensured_with_full_signature_index
    return True
