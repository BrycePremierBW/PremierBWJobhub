"""Add a crew leader field to JobHub Setup crews.

The first setup-crew version stored members but did not store which person was
the leader. Staff Scheduling needs that leader so clicking the leader's tile can
ask whether to schedule that person alone or the whole crew.
"""

from __future__ import annotations

import sys
from typing import Any

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None  # type: ignore[assignment]

from . import setup_defaults_guard


PATCH_MARKER = "_pb_setup_crew_leader_guard"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value if value not in (None, "") else default))
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except Exception:
        return float(default)


def _table_columns(table: str) -> set[str]:
    try:
        if setup_defaults_guard._use_postgres():
            df = setup_defaults_guard._df_query(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name=?
                """,
                (table,),
            )
            return set(df["column_name"].astype(str)) if df is not None and not df.empty else set()
        df = setup_defaults_guard._df_query(f"PRAGMA table_info({table})")
        if df is None or getattr(df, "empty", True):
            return set()
        # SQLite PRAGMA column name is usually returned as 'name'.
        if "name" in df.columns:
            return set(df["name"].astype(str))
        if 1 in df.columns:
            return set(df[1].astype(str))
    except Exception:
        return set()
    return set()


def _ensure_column(table: str, column: str, definition: str) -> None:
    if column in _table_columns(table):
        return
    if setup_defaults_guard._use_postgres():
        setup_defaults_guard._execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
    else:
        setup_defaults_guard._execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_schema_with_leader(original: Any) -> None:
    if callable(original):
        original()
    _ensure_column("jobhub_crews", "lead_employee_id", "INTEGER")


def _employees() -> Any:
    return setup_defaults_guard._employees()


def _employee_label_options(employees: Any) -> dict[str, int]:
    if employees is None or getattr(employees, "empty", True):
        return {}
    return {f"{row['name']} ({row['status']})": int(row["id"]) for _, row in employees.iterrows()}


def _label_for_employee(employee_options: dict[str, int], employee_id: int | None) -> str | None:
    if not employee_id:
        return None
    for label, option_id in employee_options.items():
        if int(option_id) == int(employee_id):
            return label
    return None


def _crew_member_ids(crew_id: int) -> list[int]:
    return setup_defaults_guard._crew_member_ids(crew_id)


def _crews_with_leader() -> Any:
    try:
        return setup_defaults_guard._df_query(
            """
            SELECT c.id, c.crew_name, COALESCE(c.default_hourly_rate,0) AS default_hourly_rate,
                   COALESCE(c.lead_employee_id,0) AS lead_employee_id,
                   COALESCE(lead.name,'') AS crew_leader,
                   COALESCE(c.active,1) AS active, COALESCE(c.notes,'') AS notes
            FROM jobhub_crews c
            LEFT JOIN employees lead ON lead.id=c.lead_employee_id
            ORDER BY c.crew_name
            """
        )
    except Exception:
        return None


def _save_crew_header(
    crew_id: int | None,
    crew_name: str,
    lead_employee_id: int,
    default_hourly_rate: float,
    notes: str,
) -> int | None:
    if crew_id is None:
        setup_defaults_guard._execute(
            """
            INSERT INTO jobhub_crews
                (crew_name, lead_employee_id, default_hourly_rate, active, notes, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(crew_name) DO UPDATE SET
                lead_employee_id=excluded.lead_employee_id,
                default_hourly_rate=excluded.default_hourly_rate,
                notes=excluded.notes,
                active=1,
                updated_at=excluded.updated_at
            """,
            (
                crew_name.strip(),
                int(lead_employee_id),
                float(default_hourly_rate or 0),
                notes.strip(),
                setup_defaults_guard._now(),
                setup_defaults_guard._now(),
            ),
        )
        df = setup_defaults_guard._df_query(
            "SELECT id FROM jobhub_crews WHERE LOWER(TRIM(crew_name))=LOWER(TRIM(?)) LIMIT 1",
            (crew_name.strip(),),
        )
        if df is not None and not getattr(df, "empty", True):
            return _safe_int(df.iloc[0]["id"], 0)
        return None

    setup_defaults_guard._execute(
        """
        UPDATE jobhub_crews
        SET crew_name=?, lead_employee_id=?, default_hourly_rate=?, notes=?, active=1, updated_at=?
        WHERE id=?
        """,
        (
            crew_name.strip(),
            int(lead_employee_id),
            float(default_hourly_rate or 0),
            notes.strip(),
            setup_defaults_guard._now(),
            int(crew_id),
        ),
    )
    return int(crew_id)


def _save_crew_members(crew_id: int, member_ids: list[int], lead_employee_id: int) -> None:
    clean_ids = list(dict.fromkeys([int(lead_employee_id), *[int(value) for value in member_ids]]))
    setup_defaults_guard._execute("DELETE FROM jobhub_crew_members WHERE crew_id=?", (int(crew_id),))
    for employee_id in clean_ids:
        role = "Leader" if int(employee_id) == int(lead_employee_id) else "Member"
        setup_defaults_guard._execute(
            """
            INSERT INTO jobhub_crew_members (crew_id, employee_id, crew_role, active, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?)
            """,
            (
                int(crew_id),
                int(employee_id),
                role,
                setup_defaults_guard._now(),
                setup_defaults_guard._now(),
            ),
        )


def _render_create_crew(st: Any, employees: Any, employee_options: dict[str, int], crews: Any) -> None:
    with st.expander("Create crew", expanded=crews is None or getattr(crews, "empty", True)):
        if not employee_options:
            st.info("Add employees first, then you can create a crew and choose a leader.")
            return
        with st.form("jobhub_create_crew_with_leader_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            crew_name = c1.text_input("Crew name", placeholder="e.g. Crew A / Internal crew / Spray crew")
            leader_label = c2.selectbox("Crew leader", list(employee_options), key="jobhub_create_crew_leader")
            crew_rate = c3.number_input(
                "Default hourly rate per person",
                min_value=0.0,
                step=5.0,
                value=setup_defaults_guard.get_setting_float("default_staff_hourly_rate", 60.0),
                help="This is per person, not the whole crew combined.",
            )
            member_labels = st.multiselect(
                "Crew members",
                list(employee_options),
                default=[leader_label],
                help="The selected leader is automatically included even if you forget to tick them here.",
            )
            notes = st.text_area("Crew notes")
            create = st.form_submit_button("Create crew", type="primary")
        if not create:
            return
        if not crew_name.strip():
            setup_defaults_guard._error("Enter a crew name first.")
            return
        try:
            leader_id = int(employee_options[leader_label])
            member_ids = [int(employee_options[label]) for label in member_labels]
            crew_id = _save_crew_header(None, crew_name, leader_id, crew_rate, notes)
            if crew_id:
                _save_crew_members(crew_id, member_ids, leader_id)
            setup_defaults_guard._success(f"Crew saved: {crew_name.strip()} with {leader_label.split(' (', 1)[0]} as leader.")
            setup_defaults_guard._safe_rerun(st)
        except Exception as exc:
            setup_defaults_guard._error(f"Could not save crew: {exc}")


def _render_edit_crew(st: Any, employees: Any, employee_options: dict[str, int], crews: Any) -> None:
    if crews is None or getattr(crews, "empty", True):
        st.info("No crews saved yet.")
        return

    display = crews.copy()
    display = display.rename(
        columns={
            "crew_name": "Crew",
            "crew_leader": "Leader",
            "default_hourly_rate": "Hourly Rate / Person",
            "active": "Active",
            "notes": "Notes",
        }
    )
    columns = [col for col in ["Crew", "Leader", "Hourly Rate / Person", "Active", "Notes"] if col in display.columns]
    st.dataframe(display[columns], width="stretch", hide_index=True)

    if not employee_options:
        st.info("Add employees first, then you can edit crew leaders and members.")
        return

    crew_options = {str(row["crew_name"]): int(row["id"]) for _, row in crews.iterrows()}
    selected_crew_name = st.selectbox("Edit crew", list(crew_options), key="jobhub_setup_edit_crew")
    selected_crew_id = crew_options[selected_crew_name]
    selected_row = crews[crews["id"].astype(int) == int(selected_crew_id)].iloc[0]

    current_member_ids = set(_crew_member_ids(selected_crew_id))
    current_leader_id = _safe_int(selected_row.get("lead_employee_id"), 0)
    if not current_leader_id and current_member_ids:
        current_leader_id = sorted(current_member_ids)[0]

    default_leader_label = _label_for_employee(employee_options, current_leader_id) or list(employee_options)[0]
    default_member_labels = [
        label for label, employee_id in employee_options.items()
        if int(employee_id) in current_member_ids or int(employee_id) == int(current_leader_id)
    ]
    if default_leader_label not in default_member_labels:
        default_member_labels.insert(0, default_leader_label)

    with st.form(f"jobhub_edit_crew_with_leader_{selected_crew_id}"):
        c1, c2, c3 = st.columns(3)
        edit_name = c1.text_input("Crew name", value=str(selected_row["crew_name"] or ""))
        leader_label = c2.selectbox(
            "Crew leader",
            list(employee_options),
            index=list(employee_options).index(default_leader_label),
            key=f"jobhub_setup_crew_leader_{selected_crew_id}",
        )
        crew_rate = c3.number_input(
            "Default hourly rate per person",
            min_value=0.0,
            step=5.0,
            value=_safe_float(selected_row.get("default_hourly_rate"), 0.0),
            help="This is per person, not the whole crew combined.",
        )
        member_labels = st.multiselect(
            "Crew members",
            list(employee_options),
            default=default_member_labels,
            key=f"jobhub_setup_crew_members_{selected_crew_id}",
        )
        notes = st.text_area("Crew notes", value=str(selected_row.get("notes") or ""))
        save = st.form_submit_button("Save crew leader and members", type="primary")

    if save:
        if not edit_name.strip():
            setup_defaults_guard._error("Crew name is required.")
            return
        try:
            leader_id = int(employee_options[leader_label])
            member_ids = [int(employee_options[label]) for label in member_labels]
            saved_id = _save_crew_header(selected_crew_id, edit_name, leader_id, crew_rate, notes)
            if saved_id:
                _save_crew_members(saved_id, member_ids, leader_id)
            setup_defaults_guard._success(
                f"Saved {edit_name.strip()} with {leader_label.split(' (', 1)[0]} as leader."
            )
            setup_defaults_guard._safe_rerun(st)
        except Exception as exc:
            setup_defaults_guard._error(f"Could not save crew: {exc}")


def _render_crews_tab_with_leader(st: Any) -> None:
    _ensure_schema_with_leader(getattr(setup_defaults_guard, "_pb_original_ensure_schema", None))
    employees = _employees()
    employee_options = _employee_label_options(employees)
    crews = _crews_with_leader()
    _render_create_crew(st, employees, employee_options, crews)
    crews = _crews_with_leader()
    _render_edit_crew(st, employees, employee_options, crews)


def install_setup_crew_leader_guard() -> bool:
    original_ensure = getattr(setup_defaults_guard, "_ensure_schema", None)
    original_render = getattr(setup_defaults_guard, "_render_crews_tab", None)
    if original_render is None or getattr(original_render, PATCH_MARKER, False):
        return False

    def ensure_schema() -> None:
        _ensure_schema_with_leader(original_ensure)

    def render_crews_tab(st: Any) -> None:
        _render_crews_tab_with_leader(st)

    ensure_schema._pb_setup_crew_leader_guard = True
    ensure_schema._pb_original_ensure_schema = original_ensure
    render_crews_tab._pb_setup_crew_leader_guard = True
    render_crews_tab._pb_original_render_crews_tab = original_render
    setup_defaults_guard._pb_original_ensure_schema = original_ensure
    setup_defaults_guard._ensure_schema = ensure_schema
    setup_defaults_guard._render_crews_tab = render_crews_tab
    return True
