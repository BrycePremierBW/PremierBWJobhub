"""Keep JobHub Setup crews and scheduler crew options synchronized.

JobHub Setup is the shared crew source used by the schedule board. Scheduler
crew saves/deletes are mirrored into the same setup tables so either screen can
be used without maintaining duplicate crew lists.
"""

from __future__ import annotations

from datetime import datetime
import sys
from typing import Any, Iterable

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None  # type: ignore[assignment]


PATCH_MARKER = "_pb_setup_scheduler_crew_bridge_guard"
SAVE_PATCH_MARKER = "_pb_setup_scheduler_crew_save_bridge"
DELETE_PATCH_MARKER = "_pb_setup_scheduler_crew_delete_bridge"


def _scheduler_module() -> Any:
    return sys.modules.get("pb_jobhub_visual_scheduler")


def _empty_crews_df() -> Any:
    if pd is None:
        return None
    return pd.DataFrame(
        columns=[
            "id", "crew_name", "lead_employee_id", "lead_name", "active",
            "notes", "member_names", "member_ids", "source",
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


def _ensure_setup_schema(scheduler: Any) -> None:
    execute = getattr(scheduler, "execute", None)
    table_exists = getattr(scheduler, "table_exists", None)
    ensure_column = getattr(scheduler, "ensure_column", None)
    use_postgres = bool(getattr(scheduler, "USE_POSTGRES", False))
    if not callable(execute):
        return
    pk = "SERIAL PRIMARY KEY" if use_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    execute(
        f"""
        CREATE TABLE IF NOT EXISTS jobhub_crews (
            id {pk},
            crew_name TEXT NOT NULL UNIQUE,
            lead_employee_id INTEGER,
            default_hourly_rate REAL DEFAULT 0,
            active INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    execute(
        f"""
        CREATE TABLE IF NOT EXISTS jobhub_crew_members (
            id {pk},
            crew_id INTEGER NOT NULL,
            employee_id INTEGER NOT NULL,
            crew_role TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(crew_id, employee_id)
        )
        """
    )
    try:
        if callable(table_exists) and table_exists("jobhub_crews") and callable(ensure_column):
            ensure_column("jobhub_crews", "lead_employee_id", "INTEGER")
            ensure_column("jobhub_crews", "updated_at", "TEXT")
        if callable(table_exists) and table_exists("jobhub_crew_members") and callable(ensure_column):
            ensure_column("jobhub_crew_members", "crew_role", "TEXT")
            ensure_column("jobhub_crew_members", "active", "INTEGER DEFAULT 1")
            ensure_column("jobhub_crew_members", "updated_at", "TEXT")
    except Exception:
        pass


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
        leader_id = configured_leader_id if configured_leader_id in member_ids else int(member_ids[0])
        leader_name = str(member_names[member_ids.index(leader_id)])
        # Negative ids prevent clashes with scheduler_crews ids
        rows.append(
            {
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
    if not active_only:
        return work
    # JobHub Setup wins when the same crew name exists in both stores.
    setup_names = {str(name or "").strip().casefold() for name in setup.get("crew_name", [])}
    work = work[~work["crew_name"].astype(str).str.strip().str.casefold().isin(setup_names)]
    return pd.concat([work, setup], ignore_index=True)


def _upsert_setup_crew(
    scheduler: Any,
    crew_name: str,
    lead_employee_id: int,
    member_employee_ids: Iterable[int],
    notes: str = "",
) -> int:
    _ensure_setup_schema(scheduler)
    execute = getattr(scheduler, "execute", None)
    query_df = getattr(scheduler, "query_df", None)
    if not callable(execute) or not callable(query_df):
        return 0
    name = str(crew_name or "").strip()
    if not name:
        return 0
    lead_id = int(lead_employee_id)
    member_ids = list(dict.fromkeys([lead_id, *[int(value) for value in member_employee_ids]]))
    now = datetime.now().isoformat(timespec="seconds")
    existing = query_df("SELECT id FROM jobhub_crews WHERE LOWER(TRIM(crew_name))=LOWER(TRIM(?)) LIMIT 1", (name,))
    if existing is None or getattr(existing, "empty", True):
        if bool(getattr(scheduler, "USE_POSTGRES", False)):
            row = query_df(
                """
                INSERT INTO jobhub_crews
                (crew_name,lead_employee_id,active,notes,created_at,updated_at)
                VALUES (?,?,1,?,?,?) RETURNING id
                """,
                (name, lead_id, str(notes or "").strip(), now, now),
            )
            setup_id = int(row.iloc[0]["id"])
        else:
            setup_id = int(execute(
                """
                INSERT INTO jobhub_crews
                (crew_name,lead_employee_id,active,notes,created_at,updated_at)
                VALUES (?,?,1,?,?,?)
                """,
                (name, lead_id, str(notes or "").strip(), now, now),
            ))
    else:
        setup_id = int(existing.iloc[0]["id"])
        execute(
            """
            UPDATE jobhub_crews
            SET crew_name=?,lead_employee_id=?,active=1,notes=?,updated_at=?
            WHERE id=?
            """,
            (name, lead_id, str(notes or "").strip(), now, setup_id),
        )
    execute("DELETE FROM jobhub_crew_members WHERE crew_id=?", (setup_id,))
    for employee_id in member_ids:
        execute(
            """
            INSERT INTO jobhub_crew_members
            (crew_id,employee_id,crew_role,active,created_at,updated_at)
            VALUES (?,?,?,1,?,?)
            """,
            (setup_id, employee_id, "Leader" if employee_id == lead_id else "Member", now, now),
        )
    return setup_id


def _delete_setup_crew_by_name(scheduler: Any, crew_name: str) -> None:
    execute = getattr(scheduler, "execute", None)
    query_df = getattr(scheduler, "query_df", None)
    if not callable(execute) or not callable(query_df):
        return
    match = query_df("SELECT id FROM jobhub_crews WHERE LOWER(TRIM(crew_name))=LOWER(TRIM(?)) LIMIT 1", (str(crew_name or "").strip(),))
    if match is None or getattr(match, "empty", True):
        return
    setup_id = int(match.iloc[0]["id"])
    execute("DELETE FROM jobhub_crew_members WHERE crew_id=?", (setup_id,))
    execute("DELETE FROM jobhub_crews WHERE id=?", (setup_id,))


def install_setup_scheduler_crew_bridge_guard() -> bool:
    scheduler = _scheduler_module()
    if scheduler is None:
        return False
    _ensure_setup_schema(scheduler)
    installed = False

    original_saved_crews = getattr(scheduler, "saved_crews", None)
    if original_saved_crews is not None and not getattr(original_saved_crews, PATCH_MARKER, False):
        def saved_crews_with_setup_bridge(active_only: bool = True):
            primary = original_saved_crews(active_only)
            setup = _setup_crews_df(scheduler, active_only)
            return _combine_crews(primary, setup, active_only)

        saved_crews_with_setup_bridge._pb_setup_scheduler_crew_bridge_guard = True
        saved_crews_with_setup_bridge._pb_original_saved_crews = original_saved_crews
        scheduler.saved_crews = saved_crews_with_setup_bridge
        installed = True

    original_save = getattr(scheduler, "save_scheduler_crew", None)
    if original_save is not None and not getattr(original_save, SAVE_PATCH_MARKER, False):
        def save_scheduler_crew_synced(
            crew_id: int | None,
            crew_name: str,
            lead_employee_id: int,
            member_employee_ids: Iterable[int],
            notes: str = "",
        ) -> int:
            saved_id = int(original_save(crew_id, crew_name, lead_employee_id, member_employee_ids, notes))
            _upsert_setup_crew(scheduler, crew_name, lead_employee_id, member_employee_ids, notes)
            return saved_id

        save_scheduler_crew_synced._pb_setup_scheduler_crew_save_bridge = True
        save_scheduler_crew_synced._pb_original_save_scheduler_crew = original_save
        scheduler.save_scheduler_crew = save_scheduler_crew_synced
        installed = True

    original_delete = getattr(scheduler, "delete_scheduler_crew", None)
    if original_delete is not None and not getattr(original_delete, DELETE_PATCH_MARKER, False):
        def delete_scheduler_crew_synced(crew_id: int) -> None:
            crew_name = ""
            try:
                row = scheduler.query_df("SELECT crew_name FROM scheduler_crews WHERE id=? LIMIT 1", (int(crew_id),))
                if row is not None and not row.empty:
                    crew_name = str(row.iloc[0]["crew_name"] or "")
            except Exception:
                crew_name = ""
            original_delete(crew_id)
            if crew_name:
                _delete_setup_crew_by_name(scheduler, crew_name)

        delete_scheduler_crew_synced._pb_setup_scheduler_crew_delete_bridge = True
        delete_scheduler_crew_synced._pb_original_delete_scheduler_crew = original_delete
        scheduler.delete_scheduler_crew = delete_scheduler_crew_synced
        installed = True

    return installed
