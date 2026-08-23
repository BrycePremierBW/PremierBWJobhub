"""Keep the existing staff-request workflow visible on the simple Employee Portal.

The employee home is intentionally short, but assigned office requests still
need to be obvious.  This extension adds a compact My Requests section after the
home timesheet without restoring the old portal clutter.
"""
from __future__ import annotations

import sys
from typing import Any

from . import employee_portal_home_guard as home


PATCH_MARKER = "_pb_employee_portal_requests_guard"


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _request_rows(employee_id: int) -> list[dict[str, Any]]:
    query = _app_attr("safe_df_query") or _app_attr("df_query")
    if not callable(query):
        return []
    try:
        frame = query(
            """
            SELECT sr.id,
                   COALESCE(sr.title,'') AS title,
                   COALESCE(sr.instructions,'') AS instructions,
                   COALESCE(sr.priority,'') AS priority,
                   COALESCE(sr.due_at,'') AS due_at,
                   COALESCE(sr.status,'Requested') AS status,
                   COALESCE(j.job_no,'') AS job_no,
                   COALESCE(j.job_name,'') AS job_name
            FROM staff_requests sr
            LEFT JOIN jobs j ON j.id=sr.job_id
            WHERE sr.employee_id=?
              AND LOWER(COALESCE(sr.status,'requested')) NOT IN
                  ('completed','complete','closed','cancelled','rejected')
            ORDER BY
                CASE LOWER(COALESCE(sr.priority,''))
                    WHEN 'urgent' THEN 0
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    ELSE 3
                END,
                COALESCE(sr.due_at,''),sr.id DESC
            LIMIT 8
            """,
            (int(employee_id),),
        )
    except Exception:
        return []
    if frame is None or getattr(frame, "empty", True):
        return []
    try:
        return [dict(row) for _, row in frame.iterrows()]
    except Exception:
        return []


def _render_requests(st: Any, employee_id: int) -> None:
    st.subheader("My Requests")
    rows = _request_rows(employee_id)
    if not rows:
        st.info("No office requests waiting for you.")
        return
    for row in rows:
        title = str(row.get("title") or "Request").strip()
        instructions = str(row.get("instructions") or "").strip()
        priority = str(row.get("priority") or "").strip()
        due = str(row.get("due_at") or "").strip()
        job_text = " · ".join(
            value
            for value in (
                str(row.get("job_no") or "").strip(),
                str(row.get("job_name") or "").strip(),
            )
            if value
        )
        meta = " | ".join(
            value for value in (job_text, priority and f"Priority: {priority}", due and f"Due: {due}") if value
        )
        st.markdown(f"**{title}**")
        if meta:
            st.caption(meta)
        if instructions:
            st.info(instructions)


def install_employee_portal_requests_guard() -> bool:
    original = getattr(home, "render_employee_home", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def render_with_requests(st: Any) -> None:
        original(st)
        user = home._current_user(st)
        try:
            employee_id = int(user.get("employee_id") or 0)
        except Exception:
            employee_id = 0
        if employee_id > 0:
            st.divider()
            _render_requests(st, employee_id)

    render_with_requests._pb_original_employee_home = original
    setattr(render_with_requests, PATCH_MARKER, True)
    home.render_employee_home = render_with_requests
    return True
