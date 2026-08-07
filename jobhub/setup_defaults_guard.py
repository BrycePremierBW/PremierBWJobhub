"""Admin setup page for JobHub defaults, rates and crews."""

from __future__ import annotations

from datetime import datetime
import sys
from typing import Any


SETUP_LABEL = "JobHub Setup / Edit Defaults"
SETUP_STATE_KEY = "_pb_show_setup_defaults_page"
SESSION_GET_PATCH_KEY = "_pb_setup_defaults_session_get_guard"

RESET_SAFE_VALUES = {
    "main_menu": "Dashboard",
    "management_menu": "Builders & Clients",
    "site_operations_menu": "Staff Scheduler",
    "estimating_menu": "Import / Create Job Pack",
    "ai_menu": "JobHub AI Assistant",
}

MENU_MARKERS = {
    "Dashboard", "Jobs", "Job Folders", "Estimating", "Site Operations",
    "Management", "Reports", "Staff Scheduler", "Job Progress Tracker",
    "Import / Create Job Pack", "Estimate Working Sheet", "Upload PO",
}

SETTING_DEFAULTS: dict[str, float] = {
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


def _st() -> Any:
    return sys.modules.get("streamlit")


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _execute(sql: str, params: tuple[Any, ...] = ()) -> Any:
    execute = _app_attr("execute")
    if callable(execute):
        return execute(sql, params)
    raise RuntimeError("JobHub database execute function is not available yet.")


def _df_query(sql: str, params: tuple[Any, ...] = ()) -> Any:
    df_query = _app_attr("df_query") or _app_attr("safe_df_query")
    if callable(df_query):
        return df_query(sql, params)
    raise RuntimeError("JobHub database query function is not available yet.")


def _use_postgres() -> bool:
    try:
        return bool(_app_attr("USE_POSTGRES", False))
    except Exception:
        return False


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value if value not in (None, "") else default))
    except Exception:
        return int(default)


def _success(message: str) -> None:
    fn = _app_attr("pb_success")
    st = _st()
    if callable(fn):
        fn(message)
    elif st is not None:
        st.success(message)


def _error(message: str) -> None:
    fn = _app_attr("pb_error")
    st = _st()
    if callable(fn):
        fn(message)
    elif st is not None:
        st.error(message)


def _safe_rerun(st: Any) -> None:
    rerun = _app_attr("pb_rerun") or _app_attr("refresh") or getattr(st, "rerun", None)
    if callable(rerun):
        rerun()


def _ensure_schema() -> None:
    pk = "SERIAL PRIMARY KEY" if _use_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    _execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT
        )
        """
    )
    _execute(
        f"""
        CREATE TABLE IF NOT EXISTS jobhub_crews (
            id {pk},
            crew_name TEXT NOT NULL UNIQUE,
            default_hourly_rate REAL DEFAULT 0,
            active INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    _execute(
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


def get_setting_float(key: str, default: float = 0.0) -> float:
    try:
        df = _df_query("SELECT setting_value FROM app_settings WHERE setting_key=? LIMIT 1", (key,))
        if df is not None and not getattr(df, "empty", True):
            return _safe_float(df.iloc[0]["setting_value"], default)
    except Exception:
        pass
    return float(default)


def _set_setting(key: str, value: Any) -> None:
    _execute(
        """
        INSERT INTO app_settings (setting_key, setting_value)
        VALUES (?, ?)
        ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value
        """,
        (str(key), str(value)),
    )


def _save_settings(values: dict[str, Any]) -> None:
    for key, value in values.items():
        _set_setting(key, value)


def _employees() -> Any:
    try:
        return _df_query(
            """
            SELECT id, name, COALESCE(status,'Active') AS status
            FROM employees
            ORDER BY name
            """
        )
    except Exception:
        return None


def _crews() -> Any:
    try:
        return _df_query(
            """
            SELECT id, crew_name, COALESCE(default_hourly_rate,0) AS default_hourly_rate,
                   COALESCE(active,1) AS active, COALESCE(notes,'') AS notes
            FROM jobhub_crews
            ORDER BY crew_name
            """
        )
    except Exception:
        return None


def _crew_member_ids(crew_id: int) -> list[int]:
    try:
        df = _df_query(
            "SELECT employee_id FROM jobhub_crew_members WHERE crew_id=? AND COALESCE(active,1)=1",
            (int(crew_id),),
        )
        if df is None or getattr(df, "empty", True):
            return []
        return [_safe_int(value) for value in df["employee_id"].tolist()]
    except Exception:
        return []


def _render_rates_tab(st: Any) -> None:
    with st.form("jobhub_setup_rates_form"):
        c1, c2 = st.columns(2)
        staff_rate = c1.number_input(
            "Default estimating staff cost / hour",
            min_value=0.0,
            step=5.0,
            value=get_setting_float("default_staff_hourly_rate", SETTING_DEFAULTS["default_staff_hourly_rate"]),
            help="Used for estimating, forecasting and planning. This does not overwrite actual payroll cost.",
        )
        charge_rate = c2.number_input(
            "Default charge-out / all-in hourly rate",
            min_value=0.0,
            step=5.0,
            value=get_setting_float("default_charge_out_hourly_rate", SETTING_DEFAULTS["default_charge_out_hourly_rate"]),
        )
        c3, c4 = st.columns(2)
        day_hours = c3.number_input(
            "Default painter-day hours",
            min_value=1.0,
            max_value=24.0,
            step=0.5,
            value=get_setting_float("default_painter_day_hours", SETTING_DEFAULTS["default_painter_day_hours"]),
        )
        production_target = c4.number_input(
            "Default production target per painter-day",
            min_value=0.0,
            step=100.0,
            value=get_setting_float("default_production_target_per_day", SETTING_DEFAULTS["default_production_target_per_day"]),
        )
        save = st.form_submit_button("Save rate and forecast defaults", type="primary")
    if save:
        _save_settings(
            {
                "default_staff_hourly_rate": staff_rate,
                "default_charge_out_hourly_rate": charge_rate,
                "default_painter_day_hours": day_hours,
                "default_production_target_per_day": production_target,
            }
        )
        _success("Rate and forecast defaults saved.")
        _safe_rerun(st)


def _render_stage_tab(st: Any) -> None:
    st.caption("These defaults drive the new dwelling stage builder and bulk stage creator.")
    with st.form("jobhub_setup_stage_defaults_form"):
        c1, c2, c3 = st.columns(3)
        internal_weight = c1.number_input(
            "Default internal % of job",
            min_value=0.0,
            max_value=100.0,
            step=0.5,
            value=get_setting_float("default_internal_weight_percent", SETTING_DEFAULTS["default_internal_weight_percent"]),
        )
        external_weight = c2.number_input(
            "Default external % of job",
            min_value=0.0,
            max_value=100.0,
            step=0.5,
            value=get_setting_float("default_external_weight_percent", SETTING_DEFAULTS["default_external_weight_percent"]),
        )
        dwellings = c3.number_input(
            "Default dwelling count",
            min_value=1,
            max_value=500,
            step=1,
            value=max(1, _safe_int(get_setting_float("default_dwelling_count", SETTING_DEFAULTS["default_dwelling_count"]), 1)),
        )
        st.markdown("#### Interior stage splits")
        i1, i2, i3, i4, i5 = st.columns(5)
        prep = i1.number_input("Prep and seal %", min_value=0.0, max_value=100.0, step=1.0, value=get_setting_float("stage_interior_prep_seal_percent", 30.0))
        finish = i2.number_input("Finish coats %", min_value=0.0, max_value=100.0, step=1.0, value=get_setting_float("stage_interior_finish_coats_percent", 30.0))
        cut_roll = i3.number_input("Cut/roll/doors %", min_value=0.0, max_value=100.0, step=1.0, value=get_setting_float("stage_interior_cut_roll_doors_percent", 30.0))
        doors = i4.number_input("Doors/trims %", min_value=0.0, max_value=100.0, step=1.0, value=get_setting_float("stage_interior_doors_trims_percent", 10.0))
        int_touch = i5.number_input("Interior touch ups %", min_value=0.0, max_value=100.0, step=1.0, value=get_setting_float("stage_interior_touchups_percent", 10.0))
        st.markdown("#### Exterior stage splits")
        e1, e2, e3 = st.columns(3)
        upper = e1.number_input("Upper scaff %", min_value=0.0, max_value=100.0, step=1.0, value=get_setting_float("stage_exterior_upper_scaff_percent", 45.0))
        lower = e2.number_input("Lower external %", min_value=0.0, max_value=100.0, step=1.0, value=get_setting_float("stage_exterior_lower_external_percent", 45.0))
        ext_touch = e3.number_input("External touch ups %", min_value=0.0, max_value=100.0, step=1.0, value=get_setting_float("stage_exterior_touchups_percent", 10.0))
        save = st.form_submit_button("Save stage builder defaults", type="primary")
    if save:
        _save_settings(
            {
                "default_internal_weight_percent": internal_weight,
                "default_external_weight_percent": external_weight,
                "default_dwelling_count": dwellings,
                "stage_interior_prep_seal_percent": prep,
                "stage_interior_finish_coats_percent": finish,
                "stage_interior_cut_roll_doors_percent": cut_roll,
                "stage_interior_doors_trims_percent": doors,
                "stage_interior_touchups_percent": int_touch,
                "stage_exterior_upper_scaff_percent": upper,
                "stage_exterior_lower_external_percent": lower,
                "stage_exterior_touchups_percent": ext_touch,
            }
        )
        _success("Stage builder defaults saved.")
        _safe_rerun(st)


def _render_crews_tab(st: Any) -> None:
    employees = _employees()
    crews = _crews()
    with st.expander("Create crew", expanded=crews is None or getattr(crews, "empty", True)):
        with st.form("jobhub_create_crew_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            crew_name = c1.text_input("Crew name", placeholder="e.g. Crew A / Internal crew / Spray crew")
            crew_rate = c2.number_input("Default crew hourly rate", min_value=0.0, step=5.0, value=get_setting_float("default_staff_hourly_rate", 60.0))
            notes = st.text_area("Crew notes")
            create = st.form_submit_button("Create crew", type="primary")
        if create:
            if not crew_name.strip():
                _error("Enter a crew name first.")
            else:
                try:
                    _execute(
                        """
                        INSERT INTO jobhub_crews (crew_name, default_hourly_rate, active, notes, created_at, updated_at)
                        VALUES (?, ?, 1, ?, ?, ?)
                        ON CONFLICT(crew_name) DO UPDATE SET
                            default_hourly_rate=excluded.default_hourly_rate,
                            notes=excluded.notes,
                            active=1,
                            updated_at=excluded.updated_at
                        """,
                        (crew_name.strip(), float(crew_rate or 0), notes.strip(), _now(), _now()),
                    )
                    _success(f"Crew saved: {crew_name.strip()}.")
                    _safe_rerun(st)
                except Exception as exc:
                    _error(f"Could not save crew: {exc}")

    crews = _crews()
    if crews is None or getattr(crews, "empty", True):
        st.info("No crews saved yet.")
        return
    st.dataframe(crews, width="stretch", hide_index=True)

    crew_options = {str(row["crew_name"]): int(row["id"]) for _, row in crews.iterrows()}
    selected_crew_name = st.selectbox("Edit crew members", list(crew_options), key="jobhub_setup_edit_crew")
    selected_crew_id = crew_options[selected_crew_name]

    if employees is None or getattr(employees, "empty", True):
        st.info("Add employees first, then you can place people into crews.")
        return

    employee_options = {f"{row['name']} ({row['status']})": int(row["id"]) for _, row in employees.iterrows()}
    current_ids = set(_crew_member_ids(selected_crew_id))
    default_labels = [label for label, emp_id in employee_options.items() if emp_id in current_ids]
    selected_members = st.multiselect(
        "Crew members",
        list(employee_options),
        default=default_labels,
        key=f"jobhub_setup_crew_members_{selected_crew_id}",
    )
    if st.button("Save crew members", type="primary", key=f"save_crew_members_{selected_crew_id}"):
        chosen_ids = [employee_options[label] for label in selected_members]
        try:
            _execute("DELETE FROM jobhub_crew_members WHERE crew_id=?", (int(selected_crew_id),))
            for employee_id in chosen_ids:
                _execute(
                    """
                    INSERT INTO jobhub_crew_members (crew_id, employee_id, crew_role, active, created_at, updated_at)
                    VALUES (?, ?, 'Member', 1, ?, ?)
                    """,
                    (int(selected_crew_id), int(employee_id), _now(), _now()),
                )
            _success(f"Crew members saved for {selected_crew_name}.")
            _safe_rerun(st)
        except Exception as exc:
            _error(f"Could not save crew members: {exc}")


def render_setup_defaults_page() -> None:
    st = _st()
    if st is None:
        return
    _ensure_schema()
    st.header("JobHub Setup / Edit Defaults")
    st.caption("Admin controls for the defaults JobHub starts with: estimating rates, progress assumptions, stage splits and crews.")
    tab_rates, tab_stages, tab_crews = st.tabs(["Rates & forecast", "Stage defaults", "Crews"])
    with tab_rates:
        _render_rates_tab(st)
    with tab_stages:
        _render_stage_tab(st)
    with tab_crews:
        _render_crews_tab(st)


def _show_page(st: Any) -> None:
    st.session_state[SETUP_STATE_KEY] = True
    render_setup_defaults_page()
    st.stop()


def _labels(options: Any) -> list[str]:
    try:
        return [str(item) for item in list(options)]
    except Exception:
        return []


def _should_inject(label: Any, key: Any, options: Any) -> bool:
    labels = set(_labels(options))
    if SETUP_LABEL in labels:
        return True
    label_text = str(label or "")
    key_text = str(key or "")
    # Setup lives under Management only so the top-level menu stays clean.
    if key_text == "management_menu" or label_text == "Management Section":
        return True
    return False


def _patch_radio(owner: Any, st: Any) -> bool:
    original = getattr(owner, "radio", None)
    if original is None or getattr(original, "_pb_setup_defaults_guard", False):
        return False

    def wrapper(*args: Any, **kwargs: Any):
        arg_list = list(args)
        options_index = None
        label = kwargs.get("label", "")
        if len(arg_list) >= 2 and isinstance(arg_list[0], str):
            label = arg_list[0]
            options_index = 1
        elif len(arg_list) >= 3:
            label = arg_list[1]
            options_index = 2
        elif "options" in kwargs and args:
            label = args[0]
        options = arg_list[options_index] if options_index is not None else kwargs.get("options")
        if _should_inject(label, kwargs.get("key"), options):
            try:
                labels = _labels(options)
                if SETUP_LABEL not in labels:
                    new_options = list(options)
                    new_options.append(SETUP_LABEL)
                    if options_index is not None:
                        arg_list[options_index] = new_options
                    else:
                        kwargs["options"] = new_options
            except Exception:
                pass
        result = original(*tuple(arg_list), **kwargs)
        if str(result) == SETUP_LABEL:
            _show_page(st)
        return result

    wrapper._pb_setup_defaults_guard = True
    wrapper._pb_original_radio = original
    setattr(owner, "radio", wrapper)
    return True


def install_setup_defaults_guard() -> bool:
    st = _st()
    if st is None:
        return False
    installed = _patch_radio(st, st)
    try:
        delta_module = sys.modules.get("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_radio(delta_cls, st) or installed
    return installed
