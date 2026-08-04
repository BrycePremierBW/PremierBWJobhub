from __future__ import annotations

import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from .common import AppContext, _clean, _int


def _safe_segment(value: Any, fallback: str = "record") -> str:
    text = re.sub(r"[^A-Za-z0-9._ -]", "_", str(value or "").strip()).strip(" .")
    return (text or fallback)[:120]


def _uploaded_bytes(uploaded_file: Any) -> bytes:
    if hasattr(uploaded_file, "getvalue"):
        return bytes(uploaded_file.getvalue())
    if hasattr(uploaded_file, "read"):
        return bytes(uploaded_file.read())
    raise ValueError("Uploaded file bytes are unavailable.")


def save_job_photo(
    ctx: AppContext,
    job_id: int,
    uploaded_file: Any,
    category: str,
    caption: str,
    notes: str,
    job_stage_id: int | None = None,
    stage_progress_update_id: int | None = None,
) -> None:
    job = ctx.db.query("SELECT job_no FROM jobs WHERE id=?", (int(job_id),))
    if job.empty:
        raise ValueError("The selected job no longer exists.")
    job_no = _safe_segment(job.iloc[0].get("job_no"), str(job_id))
    photo_folder = (ctx.job_files_dir / job_no / "Photos").resolve()
    root = ctx.job_files_dir.resolve()
    if root not in photo_folder.parents:
        raise ValueError("Unsafe photo storage path.")
    photo_folder.mkdir(parents=True, exist_ok=True)

    original_name = Path(str(getattr(uploaded_file, "name", "photo"))).name
    safe_name = _safe_segment(original_name, "photo")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = (photo_folder / f"{timestamp}_{safe_name}").resolve()
    if photo_folder not in target.parents:
        raise ValueError("Unsafe photo file name.")
    target.write_bytes(_uploaded_bytes(uploaded_file))
    photo_type = str(getattr(uploaded_file, "type", "") or mimetypes.guess_type(original_name)[0] or "application/octet-stream")

    ctx.db.execute(
        """
        INSERT INTO job_photos
        (job_id,photo_name,photo_type,photo_data,category,caption,uploaded_by,
         uploaded_at,notes,job_stage_id,stage_progress_update_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(job_id),
            original_name,
            photo_type,
            f"FILEPATH:{target}",
            _clean(category),
            _clean(caption),
            _clean(ctx.user.get("username")),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            _clean(notes),
            int(job_stage_id) if job_stage_id else None,
            int(stage_progress_update_id) if stage_progress_update_id else None,
        ),
    )


def create_management_notifications(
    ctx: AppContext,
    event_type: str,
    title: str,
    message: str,
    job_id: int | None = None,
    entity_type: str = "",
    entity_id: Any = "",
) -> int:
    if not ctx.db.table_exists("app_notifications"):
        return 0
    recipients = ctx.db.query(
        """
        SELECT id
        FROM app_users
        WHERE COALESCE(active,1)=1
          AND LOWER(COALESCE(role,'')) IN ('admin','manager')
        ORDER BY CASE LOWER(COALESCE(role,'')) WHEN 'admin' THEN 0 ELSE 1 END,id
        """
    )
    if recipients.empty:
        return 0
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        (
            _int(row.get("id")),
            _clean(event_type),
            _clean(title)[:200],
            _clean(message)[:2000],
            int(job_id) if job_id is not None else None,
            _clean(entity_type),
            _clean(entity_id),
            _clean(ctx.user.get("employee_name") or ctx.user.get("username") or "JobHub"),
            created_at,
            "",
        )
        for _, row in recipients.iterrows()
    ]
    ctx.db.execute_many(
        """
        INSERT INTO app_notifications
        (recipient_user_id,event_type,title,message,job_id,entity_type,
         entity_id,created_by,created_at,read_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    return len(rows)


def build_enterprise_context(ctx: AppContext) -> dict[str, Any]:
    from .estimating import recalc_estimate

    def record_audit_event(
        action: str,
        entity_type: str = "",
        entity_id: Any = None,
        details: Any = None,
    ) -> None:
        detail_text = details if isinstance(details, str) else json.dumps(details or {}, default=str, sort_keys=True)
        ctx.audit(action, entity_type, str(entity_id or ""), detail_text)

    return {
        "connect": ctx.db.connect,
        "df_query": ctx.db.query,
        "execute": ctx.db.execute,
        "execute_many": ctx.db.execute_many,
        "record_audit_event": record_audit_event,
        "recalc_estimate_totals": lambda estimate_id: recalc_estimate(ctx, int(estimate_id)),
        "create_management_notifications": lambda *args, **kwargs: create_management_notifications(ctx, *args, **kwargs),
        "get_current_user": lambda: ctx.user,
        "save_job_photo": lambda *args, **kwargs: save_job_photo(ctx, *args, **kwargs),
        "pb_success": st.success,
        "pb_error": st.error,
        "pb_rerun": st.rerun,
        "DATA_DIR": str(ctx.data_dir),
        "JOB_FILES_DIR": str(ctx.job_files_dir),
        "PHOTOS_DIR": str(ctx.job_files_dir),
        "EXPORTS_DIR": str(ctx.data_dir / "exports"),
        "USE_POSTGRES": ctx.db.postgres,
    }
