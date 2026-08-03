"""Bridge JobHub Setup crews into the staff scheduler crew picker.

The scheduler already had scheduler_crews / scheduler_crew_members. The new
JobHub Setup page stores shared setup crews in jobhub_crews / jobhub_crew_members.
This guard lets the scheduler read those setup crews in its allocation picker
without forcing users to recreate the same crews in two places.
"""

from __future__ import annotations

import sys
from typing import Any

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None  # type: ignore[assignment]


PATCH_MARKER = "_pb_setup_scheduler_crew_bridge_guard"


def _scheduler_module() -> Any:
    return sys.modules.get("pb_jobhub_visual_scheduler")


def _empty_crews_df() -> Any:
    if pd is None:
        return None
    return pd.DataFrame(
        columns=[
            "id",
            "crew_name",
            "lead_employee_id",
            "lead_name",
            "active",
            "notes",
            "member_names",
            "member_ids",
            "source",
        ]
    )


def _table_columns(scheduler: Any, table: str) -> set[str]:
    query_df = getattr(scheduler, "query_df", None)
    table_exists = getattr(scheduler, "table_exists", None)
    use_postgres = bool(getattr(scheduler, "USE_POSTGRES", False))
    if not callable(query_df) or not callable(table_exists):
        return set()
    try:
        if not table_exists(table):
            return set()
        if use_postgres:
            df = query_df(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=?
                """,
                (table,),
            )
            return set(df["column_name"].astype(str)) if df is not None and not df.empty else set()
        df = query_df(f"PRAGMA table_info({table})")
        if df is None or getattr(df, "empty", True):
            return set()
        if "name" in df.columns:
            return set(df["name"].astype(str))
        if 1 in df.columns:
            return set(df[1].astype(str))
    except Exception:
        return set()
    return set()


def _setup_crews_df(scheduler: Any, active_only: bool = True) -> Any:
    if pd is None:
        return None
    table_exists = getattr(scheduler, "table_exists", None)
    query_df = getattr(scheduler, "query_df", None)
    if not callable(table_exists) or not callable(query_df):
        return _empty_crews_df()
    try:
        if not table_exists("jobhub_crews") or not table_exists("jobhub_crew_members"):
            return _empty_crews_df()
    except Exception:
        return _empty_crews_df()

    columns = _table_columns(scheduler, "jobhub_crews")
    leader_expr = "COALESCE(c.lead_employee_id,0)" if "lead_employee_id" in columns else "0"
    where = "WHERE COALESCE(c.active,1)=1" if active_only else ""
    try:
        crews = query_df(
            f"""
            SELECT c.id, c.crew_name, {leader_expr} AS lead_employee_id,
                   COALESCE(c.active,1) AS active, COALESCE(c.notes,'') AS notes
            FROM jobhub_crews c
            {where}
            ORDER BY c.crew_name
            """
        )
    except Exception:
        return _empty_crews_df()
    if crews is None or getattr(crews, "empty", True):
        return _empty_crews_df()

    rows: list[dict[str, Any]] = []
    member_columns = _table_columns(scheduler, "jobhub_crew_members")
    role_order = "CASE WHEN COALESCE(cm.crew_role,'')='Leader' THEN 0 ELSE 1 END," if "crew_role" in member_columns else ""
    for _, crew in crews.iterrows():
        configured_leader_id = int(crew.get("lead_employee_id") or 0)
        try:
            members = query_df(
                f"""
                SELECT e.id, e.name
                FROM jobhub_crew_members cm
                JOIN employees e ON e.id=cm.employee_id
                WHERE cm.crew_id=? AND COALESCE(cm.active,1)=1
                ORDER BY CASE WHEN e.id=? THEN 0 ELSE 1 END, {role_order} e.name
                """,
                (int(crew["id"]), configured_leader_id),
            )
        except Exception:
            members = None
        if members is None or getattr(members, "empty", True):
            continue
        member_ids = [int(value) for value in members["id"].tolist()]
        member_names = [str(value) for value in members["name"].tolist()]
        if configured_leader_id in member_ids:
            leader_id = configured_leader_id
        else:
            leader_id = int(member_ids[0])
        leader_name = str(member_names[member_ids.index(leader_id)]) if leader_id in member_ids else str(member_names[0])
        rows.append(
            {
                # Negative ids prevent clashes with scheduler_crews ids. These
                # rows are read-only inside scheduling and are used for selection.
                "id": -int(crew["id"]),
                "crew_name": str(crew["crew_name"]),
                "lead_employee_id": int(leader_id),
                "lead_name": leader_name,
                "active": int(crew["active"] or 1),
                "notes": str(crew["notes"] or ""),
                "member_names": ", ".join(member_names),
                "member_ids": member_ids,
                "source": "JobHub Setup",
            }
        )
    return pd.DataFrame(rows) if rows else _empty_crews_df()


def _combine_crews(primary: Any, setup: Any, active_only: bool) -> Any:
    if pd is None:
        return primary
    if setup is None or getattr(setup, "empty", True):
        return primary
    if primary is None or getattr(primary, "empty", True):
        return setup
    work = primary.copy()
    if "source" not in work.columns:
        work["source"] = "Staff Scheduler"
    # Only merge setup crews into picker-style reads. The scheduler's own crew
    # edit tab calls active_only=False and should not try to edit the read-only
    # setup rows with negative ids.
    if not active_only:
        return work
    existing_names = {str(name or "").strip().casefold() for name in work.get("crew_name", [])}
    setup = setup[~setup["crew_name"].astype(str).str.strip().str.casefold().isin(existing_names)]
    if setup.empty:
        return work
    return pd.concat([work, setup], ignore_index=True)


def install_setup_scheduler_crew_bridge_guard() -> bool:
    scheduler = _scheduler_module()
    if scheduler is None:
        return False
    original_saved_crews = getattr(scheduler, "saved_crews", None)
    if original_saved_crews is None or getattr(original_saved_crews, PATCH_MARKER, False):
        return False

    def saved_crews_with_setup_bridge(active_only: bool = True):
        primary = original_saved_crews(active_only)
        setup = _setup_crews_df(scheduler, active_only)
        return _combine_crews(primary, setup, active_only)

    saved_crews_with_setup_bridge._pb_setup_scheduler_crew_bridge_guard = True
    saved_crews_with_setup_bridge._pb_original_saved_crews = original_saved_crews
    scheduler.saved_crews = saved_crews_with_setup_bridge
    return True
