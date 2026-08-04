from __future__ import annotations

from datetime import datetime

import pandas as pd
import streamlit as st

from .common import AppContext, _clean, _float, _int, employee_options
from .ui import header, rerun_success, selected_row


SETTING_DEFAULTS = {
    "default_staff_hourly_rate": 60.0,
    "default_charge_out_hourly_rate": 120.0,
    "default_painter_day_hours": 8.0,
    "default_production_target_per_day": 1000.0,
    "default_internal_weight_percent": 65.0,
    "default_external_weight_percent": 35.0,
    "default_dwelling_count": 1.0,
    "stage_interior_prep_seal_percent": 30.0,
    "stage_interior_finish_coats_percent": 30.0,
    "stage_interior_cut_roll_doors_percent": 30.0,
    "stage_interior_doors_trims_percent": 10.0,
    "stage_interior_touchups_percent": 10.0,
    "stage_exterior_upper_scaff_percent": 45.0,
    "stage_exterior_lower_external_percent": 45.0,
    "stage_exterior_touchups_percent": 10.0,
}


def _ensure_setup_schema(ctx: AppContext) -> None:
    pk = "SERIAL PRIMARY KEY" if ctx.db.postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    ctx.db.execute(f"""
        CREATE TABLE IF NOT EXISTS jobhub_crews (
            id {pk},crew_name TEXT NOT NULL UNIQUE,lead_employee_id INTEGER,
            default_hourly_rate REAL DEFAULT 0,active INTEGER DEFAULT 1,
            notes TEXT,created_at TEXT,updated_at TEXT
        )
    """)
    ctx.db.execute(f"""
        CREATE TABLE IF NOT EXISTS jobhub_crew_members (
            id {pk},crew_id INTEGER NOT NULL,employee_id INTEGER NOT NULL,
            crew_role TEXT,active INTEGER DEFAULT 1,created_at TEXT,updated_at TEXT,
            UNIQUE(crew_id,employee_id)
        )
    """)
    ctx.db.ensure_column("jobhub_crews", "lead_employee_id", "INTEGER")


def _setting(ctx: AppContext, key: str) -> float:
    return _float(ctx.db.scalar("SELECT setting_value FROM app_settings WHERE setting_key=?", (key,), SETTING_DEFAULTS[key]))


def _save_settings(ctx: AppContext, values: dict[str, float]) -> None:
    for key, value in values.items():
        ctx.db.execute(
            """
            INSERT INTO app_settings(setting_key,setting_value) VALUES (?,?)
            ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value
            """,
            (key, str(value)),
        )
    ctx.audit("update", "app_settings", None, ", ".join(values))


def _rates_tab(ctx: AppContext) -> None:
    with st.form("lean_setup_rates"):
        c1, c2 = st.columns(2)
        staff_rate = c1.number_input(
            "Default estimating staff cost / hour",
            min_value=0.0, step=5.0,
            value=_setting(ctx, "default_staff_hourly_rate"),
            help="Used for estimating and forecasting only; it does not overwrite actual wage cost.",
        )
        charge_rate = c2.number_input(
            "Default charge-out / all-in hourly rate",
            min_value=0.0, step=5.0,
            value=_setting(ctx, "default_charge_out_hourly_rate"),
        )
        c3, c4 = st.columns(2)
        day_hours = c3.number_input(
            "Default painter-day hours",
            min_value=1.0, max_value=24.0, step=0.5,
            value=_setting(ctx, "default_painter_day_hours"),
        )
        target = c4.number_input(
            "Default production target per painter-day",
            min_value=0.0, step=100.0,
            value=_setting(ctx, "default_production_target_per_day"),
        )
        save = st.form_submit_button("Save rate and forecast defaults", type="primary")
    if save:
        _save_settings(ctx, {
            "default_staff_hourly_rate": staff_rate,
            "default_charge_out_hourly_rate": charge_rate,
            "default_painter_day_hours": day_hours,
            "default_production_target_per_day": target,
        })
        rerun_success("Rate and forecast defaults saved.")


def _stages_tab(ctx: AppContext) -> None:
    with st.form("lean_setup_stages"):
        c1, c2, c3 = st.columns(3)
        internal = c1.number_input("Default internal % of job", min_value=0.0, max_value=100.0, value=_setting(ctx, "default_internal_weight_percent"), step=0.5)
        external = c2.number_input("Default external % of job", min_value=0.0, max_value=100.0, value=_setting(ctx, "default_external_weight_percent"), step=0.5)
        dwellings = c3.number_input("Default dwelling count", min_value=1, max_value=500, value=max(1, int(_setting(ctx, "default_dwelling_count"))), step=1)
        st.markdown("#### Interior stage splits")
        i1, i2, i3, i4, i5 = st.columns(5)
        prep = i1.number_input("Prep and seal %", min_value=0.0, max_value=100.0, value=_setting(ctx, "stage_interior_prep_seal_percent"), step=1.0)
        finish = i2.number_input("Finish coats %", min_value=0.0, max_value=100.0, value=_setting(ctx, "stage_interior_finish_coats_percent"), step=1.0)
        cut_roll = i3.number_input("Cut/roll/doors %", min_value=0.0, max_value=100.0, value=_setting(ctx, "stage_interior_cut_roll_doors_percent"), step=1.0)
        doors = i4.number_input("Doors/trims %", min_value=0.0, max_value=100.0, value=_setting(ctx, "stage_interior_doors_trims_percent"), step=1.0)
        int_touch = i5.number_input("Interior touch ups %", min_value=0.0, max_value=100.0, value=_setting(ctx, "stage_interior_touchups_percent"), step=1.0)
        st.markdown("#### Exterior stage splits")
        e1, e2, e3 = st.columns(3)
        upper = e1.number_input("Upper scaff %", min_value=0.0, max_value=100.0, value=_setting(ctx, "stage_exterior_upper_scaff_percent"), step=1.0)
        lower = e2.number_input("Lower external %", min_value=0.0, max_value=100.0, value=_setting(ctx, "stage_exterior_lower_external_percent"), step=1.0)
        ext_touch = e3.number_input("External touch ups %", min_value=0.0, max_value=100.0, value=_setting(ctx, "stage_exterior_touchups_percent"), step=1.0)
        save = st.form_submit_button("Save stage defaults", type="primary")
    if save:
        if abs((internal + external) - 100) > 0.01:
            st.error("Internal and external job percentages must total 100%.")
            return
        _save_settings(ctx, {
            "default_internal_weight_percent": internal,
            "default_external_weight_percent": external,
            "default_dwelling_count": float(dwellings),
            "stage_interior_prep_seal_percent": prep,
            "stage_interior_finish_coats_percent": finish,
            "stage_interior_cut_roll_doors_percent": cut_roll,
            "stage_interior_doors_trims_percent": doors,
            "stage_interior_touchups_percent": int_touch,
            "stage_exterior_upper_scaff_percent": upper,
            "stage_exterior_lower_external_percent": lower,
            "stage_exterior_touchups_percent": ext_touch,
        })
        rerun_success("Stage defaults saved.")


def _crews_tab(ctx: AppContext) -> None:
    employees = employee_options(ctx, active_only=False)
    employee_labels = ["No leader"] + list(employees)
    crews = ctx.db.query(
        """
        SELECT c.id,c.crew_name AS "Crew",COALESCE(e.name,'') AS "Leader",
               COALESCE(c.default_hourly_rate,0) AS "Hourly Rate",
               CASE WHEN COALESCE(c.active,1)=1 THEN 'Active' ELSE 'Inactive' END AS "Status",
               COALESCE(c.notes,'') AS "Notes"
        FROM jobhub_crews c LEFT JOIN employees e ON e.id=c.lead_employee_id
        ORDER BY c.crew_name
        """
    )
    row = selected_row(crews, key="setup_crews_table")
    if row:
        st.session_state["lean_setup_crew_id"] = _int(row.get("id"))
    selected_id = _int(st.session_state.get("lean_setup_crew_id"))

    with st.expander("Create crew", expanded=crews.empty):
        with st.form("lean_setup_create_crew"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Crew name")
            rate = c2.number_input("Default crew hourly rate", min_value=0.0, value=_setting(ctx, "default_staff_hourly_rate"), step=5.0)
            leader_label = st.selectbox("Crew leader", employee_labels)
            notes = st.text_area("Crew notes")
            create = st.form_submit_button("Create crew", type="primary")
        if create:
            if not name.strip():
                st.error("Enter a crew name.")
            else:
                now = datetime.now().isoformat(timespec="seconds")
                crew_id = ctx.db.insert_id(
                    """
                    INSERT INTO jobhub_crews(crew_name,lead_employee_id,default_hourly_rate,active,notes,created_at,updated_at)
                    VALUES (?,?,?,1,?,?,?)
                    """,
                    (name.strip(), employees.get(leader_label), rate, notes.strip(), now, now),
                )
                ctx.audit("create", "jobhub_crews", crew_id, name.strip())
                rerun_success("Crew created.")

    if not selected_id:
        return
    detail = ctx.db.query("SELECT * FROM jobhub_crews WHERE id=?", (selected_id,))
    if detail.empty:
        st.session_state.pop("lean_setup_crew_id", None)
        st.rerun()
    crew = detail.iloc[0].to_dict()
    current_leader = next((label for label, employee_id in employees.items() if employee_id == _int(crew.get("lead_employee_id"))), "No leader")
    member_rows = ctx.db.query("SELECT employee_id FROM jobhub_crew_members WHERE crew_id=? AND COALESCE(active,1)=1", (selected_id,))
    current_members = set(member_rows["employee_id"].astype(int).tolist()) if not member_rows.empty else set()
    defaults = [label for label, employee_id in employees.items() if employee_id in current_members]
    with st.expander("Edit selected crew and members", expanded=True):
        with st.form(f"lean_setup_edit_crew_{selected_id}"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Crew name", value=_clean(crew.get("crew_name")))
            rate = c2.number_input("Default crew hourly rate", min_value=0.0, value=_float(crew.get("default_hourly_rate")), step=5.0)
            leader_label = st.selectbox("Crew leader", employee_labels, index=employee_labels.index(current_leader))
            members = st.multiselect("Crew members", list(employees), default=defaults)
            active = st.checkbox("Active", value=bool(_int(crew.get("active", 1))))
            notes = st.text_area("Crew notes", value=_clean(crew.get("notes")))
            save = st.form_submit_button("Save crew and members", type="primary")
        if save:
            now = datetime.now().isoformat(timespec="seconds")
            leader_id = employees.get(leader_label)
            chosen_ids = list(dict.fromkeys([employee_id for label, employee_id in employees.items() if label in members] + ([leader_id] if leader_id else [])))
            ctx.db.execute("UPDATE jobhub_crews SET crew_name=?,lead_employee_id=?,default_hourly_rate=?,active=?,notes=?,updated_at=? WHERE id=?", (name.strip(), leader_id, rate, int(active), notes.strip(), now, selected_id))
            ctx.db.execute("DELETE FROM jobhub_crew_members WHERE crew_id=?", (selected_id,))
            rows = [(selected_id, employee_id, "Leader" if employee_id == leader_id else "Member", 1, now, now) for employee_id in chosen_ids]
            ctx.db.execute_many("INSERT INTO jobhub_crew_members(crew_id,employee_id,crew_role,active,created_at,updated_at) VALUES (?,?,?,?,?,?)", rows)
            ctx.audit("update", "jobhub_crews", selected_id, name.strip())
            rerun_success("Crew and members saved.")


def setup_page(ctx: AppContext) -> None:
    _ensure_setup_schema(ctx)
    header("Setup & Defaults", "Rates, forecasts, stage assumptions, crews and crew members.")
    rates, stages, crews = st.tabs(["Rates & Forecast", "Stage Defaults", "Crews & Members"])
    with rates:
        _rates_tab(ctx)
    with stages:
        _stages_tab(ctx)
    with crews:
        _crews_tab(ctx)
