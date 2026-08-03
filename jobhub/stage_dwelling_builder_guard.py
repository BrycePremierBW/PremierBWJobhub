"""Quick-build dwelling and area stages for JobHub."""

from __future__ import annotations

from datetime import datetime
import sys
from typing import Any

from . import stage_preset_guard


PATCH_MARKER = "_pb_stage_dwelling_builder_guard"
MODE_BUILDER = "Build by dwelling / estimate line"
MODE_PRESET = "Use saved preset"
MODE_CUSTOM = "Type custom stage"

SCOPE_OPTIONS = ["Interior", "Exterior", "Whole job"]

SETTING_DEFAULTS: dict[str, float] = {
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

STEP_SETTING_KEYS: dict[str, str] = {
    "Prep and seal": "stage_interior_prep_seal_percent",
    "Finish coats": "stage_interior_finish_coats_percent",
    "Cut and roll walls and paint doors": "stage_interior_cut_roll_doors_percent",
    "Doors and trims": "stage_interior_doors_trims_percent",
    "Interior touch ups / defects": "stage_interior_touchups_percent",
    "Upper scaff work": "stage_exterior_upper_scaff_percent",
    "Lower external": "stage_exterior_lower_external_percent",
    "External touch ups": "stage_exterior_touchups_percent",
}

INTERIOR_STEPS = [
    "Prep and seal",
    "Finish coats",
    "Cut and roll walls and paint doors",
    "Doors and trims",
    "Interior touch ups / defects",
]
EXTERIOR_STEPS = ["Upper scaff work", "Lower external", "External touch ups"]
WHOLE_JOB_STEPS = ["Whole job", "Site establishment", "Final defects / handover"]

STAGE_BUILDER_MODE_KEY = "pb_dwelling_stage_builder_mode"
STAGE_BUILDER_PERCENT_KEY = "pb_dwelling_stage_builder_percent"
_STAGE_CURRENT_PREFIX = "selectable_job_stages_"


def _st() -> Any:
    return sys.modules.get("streamlit")


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _df_query(sql: str, params: tuple[Any, ...] = ()) -> Any:
    df_query = _app_attr("df_query") or _app_attr("safe_df_query")
    if callable(df_query):
        return df_query(sql, params)
    return None


def _execute(sql: str, params: tuple[Any, ...] = ()) -> Any:
    execute = _app_attr("execute")
    if callable(execute):
        return execute(sql, params)
    raise RuntimeError("JobHub database execute function is not available yet.")


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


def _get_setting_float(key: str, default: float) -> float:
    try:
        df = _df_query("SELECT setting_value FROM app_settings WHERE setting_key=? LIMIT 1", (key,))
        if df is not None and not getattr(df, "empty", True):
            return _safe_float(df.iloc[0]["setting_value"], default)
    except Exception:
        pass
    return float(default)


def _setting(key: str) -> float:
    return _get_setting_float(key, SETTING_DEFAULTS.get(key, 0.0))


def _stage_steps(scope: str) -> list[str]:
    if scope == "Interior":
        return INTERIOR_STEPS
    if scope == "Exterior":
        return EXTERIOR_STEPS
    return WHOLE_JOB_STEPS


def _scope_weight(scope: str, internal_weight: float | None = None, external_weight: float | None = None) -> float:
    if scope == "Interior":
        return _safe_float(internal_weight, _setting("default_internal_weight_percent"))
    if scope == "Exterior":
        return _safe_float(external_weight, _setting("default_external_weight_percent"))
    return 100.0


def _step_percent(step: str) -> float:
    key = STEP_SETTING_KEYS.get(step)
    if not key:
        return 100.0
    return _setting(key)


def _dwelling_labels(total: int) -> list[str]:
    count = max(1, min(max(int(total or 1), 30), 500))
    return ["All dwellings"] + [f"Dwelling {number}" for number in range(1, count + 1)]


def _dwelling_number(label: str) -> int | None:
    if "Dwelling" not in str(label):
        return None
    try:
        return int(str(label).split("Dwelling", 1)[1].strip().split()[0])
    except Exception:
        return None


def _job_percent(scope: str, dwelling: str, step: str, dwelling_count: int, internal_weight: float | None = None, external_weight: float | None = None) -> float:
    if scope == "Whole job":
        return round(_step_percent(step), 4)
    divisor = max(1, int(dwelling_count or 1)) if _dwelling_number(dwelling) is not None else 1
    return round(_scope_weight(scope, internal_weight, external_weight) * _step_percent(step) / 100.0 / divisor, 4)


def _stage_name(scope: str, dwelling: str, step: str, estimate_line: str = "") -> str:
    parts: list[str] = []
    if scope == "Whole job":
        parts = ["Whole job", step]
    else:
        parts = [scope, dwelling, step]
    clean_line = " ".join(str(estimate_line or "").strip().split())
    if clean_line and clean_line != "No estimate line link":
        parts.append(clean_line[:80])
    return " - ".join(part for part in parts if part and part != "All dwellings")[:160]


def _render_stage_name_selector_with_builder(
    st: Any,
    selectbox_fn: Any,
    text_input_fn: Any,
    checkbox_fn: Any,
    caption_fn: Any,
    original_render: Any,
) -> str:
    mode = selectbox_fn(
        "Stage add method",
        [MODE_BUILDER, MODE_PRESET, MODE_CUSTOM],
        key=STAGE_BUILDER_MODE_KEY,
        help="Use the builder for stages like Interior - Dwelling 6 - Prep and seal.",
    )
    mode = str(mode)
    if mode == MODE_PRESET:
        return original_render(st, selectbox_fn, text_input_fn, checkbox_fn, caption_fn)
    if mode == MODE_CUSTOM:
        st.session_state[STAGE_BUILDER_PERCENT_KEY] = 0.0
        return str(
            text_input_fn(
                "Custom Stage Name",
                value=str(st.session_state.get(stage_preset_guard._STAGE_CUSTOM_KEY, "") or ""),
                key=stage_preset_guard._STAGE_CUSTOM_KEY,
                placeholder="Type the custom stage name",
            )
            or ""
        )

    default_dwellings = max(1, _safe_int(_setting("default_dwelling_count"), 1))
    scope = str(selectbox_fn("Stage area", SCOPE_OPTIONS, key="pb_stage_builder_scope"))
    dwellings = _dwelling_labels(default_dwellings)
    dwelling = str(selectbox_fn("Dwelling / unit", dwellings, key="pb_stage_builder_dwelling"))
    steps = _stage_steps(scope)
    step = str(selectbox_fn("Work step", steps, key="pb_stage_builder_work_step"))
    calculated_percent = _job_percent(scope, dwelling, step, default_dwellings)
    st.session_state[STAGE_BUILDER_PERCENT_KEY] = float(calculated_percent)
    built_name = _stage_name(scope, dwelling, step)
    if callable(caption_fn):
        caption_fn(
            f"Stage name: {built_name}. Job % starts at {calculated_percent:g}% "
            "from your JobHub Setup defaults."
        )
    return built_name


def _patch_stage_preset_builder() -> bool:
    original_render = getattr(stage_preset_guard, "_render_stage_name_selector", None)
    original_percent = getattr(stage_preset_guard, "_stage_percent_kwargs", None)
    if original_render is None or getattr(original_render, PATCH_MARKER, False):
        return False

    def render_stage_name_selector(st: Any, selectbox_fn: Any, text_input_fn: Any, checkbox_fn: Any, caption_fn: Any) -> str:
        return _render_stage_name_selector_with_builder(
            st, selectbox_fn, text_input_fn, checkbox_fn, caption_fn, original_render
        )

    def stage_percent_kwargs(st: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        if str(st.session_state.get(STAGE_BUILDER_MODE_KEY) or "") == MODE_BUILDER:
            new_kwargs = dict(kwargs)
            new_kwargs.setdefault("key", stage_preset_guard._STAGE_PERCENT_KEY)
            new_kwargs["value"] = float(st.session_state.get(STAGE_BUILDER_PERCENT_KEY, 0.0) or 0.0)
            new_kwargs.setdefault("help", "Auto-filled by the dwelling stage builder. You can still change it before saving.")
            return new_kwargs
        if callable(original_percent):
            return original_percent(st, kwargs)
        return dict(kwargs)

    render_stage_name_selector._pb_stage_dwelling_builder_guard = True
    render_stage_name_selector._pb_original = original_render
    stage_preset_guard._render_stage_name_selector = render_stage_name_selector

    stage_percent_kwargs._pb_stage_dwelling_builder_guard = True
    stage_percent_kwargs._pb_original = original_percent
    stage_preset_guard._stage_percent_kwargs = stage_percent_kwargs
    return True


def _job_id_from_key(key: Any) -> str:
    text = str(key or "")
    return text[len(_STAGE_CURRENT_PREFIX):] if text.startswith(_STAGE_CURRENT_PREFIX) else ""


def _estimate_line_options(job_id: int) -> dict[str, int]:
    options = {"No estimate line link": 0}
    try:
        df = _df_query(
            """
            SELECT li.id, COALESCE(e.estimate_no,'Estimate') AS estimate_no,
                   COALESCE(li.section,'') AS section,
                   COALESCE(li.item_description,'') AS item_description,
                   COALESCE(li.qty,0) AS qty,
                   COALESCE(li.unit,'item') AS unit,
                   COALESCE(li.line_total,0) AS line_total
            FROM estimate_line_items li
            JOIN estimate_working_sheets e ON e.id=li.estimate_id
            WHERE e.job_id=? AND COALESCE(e.archived,0)=0
            ORDER BY e.id DESC, li.id
            LIMIT 250
            """,
            (int(job_id),),
        )
        if df is None or getattr(df, "empty", True):
            return options
        for _, row in df.iterrows():
            label = (
                f"{row['estimate_no']} | {row['section']} | {row['item_description']} | "
                f"{float(row['qty'] or 0):g} {row['unit']} | ${float(row['line_total'] or 0):,.2f}"
            )[:180]
            options[label] = int(row["id"])
    except Exception:
        pass
    return options


def _stage_exists(job_id: int, stage_name: str) -> bool:
    df = _df_query(
        "SELECT id FROM job_stages WHERE job_id=? AND LOWER(stage_name)=LOWER(?) LIMIT 1",
        (int(job_id), stage_name),
    )
    return df is not None and not getattr(df, "empty", True)


def _next_sequence(job_id: int) -> int:
    df = _df_query("SELECT COALESCE(MAX(sequence_order),0) AS max_seq FROM job_stages WHERE job_id=?", (int(job_id),))
    if df is None or getattr(df, "empty", True):
        return 1
    return _safe_int(df.iloc[0]["max_seq"], 0) + 1


def _insert_stage(job_id: int, stage_name: str, job_percent: float, sequence: int, notes: str = "") -> int | None:
    if _stage_exists(job_id, stage_name):
        return None
    _execute(
        """
        INSERT INTO job_stages (job_id, stage_name, sequence_order, job_percent, status, notes, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'Planned', ?, ?, ?)
        """,
        (int(job_id), stage_name, int(sequence), float(job_percent or 0), notes, _now(), _now()),
    )
    df = _df_query(
        "SELECT id FROM job_stages WHERE job_id=? AND LOWER(stage_name)=LOWER(?) ORDER BY id DESC LIMIT 1",
        (int(job_id), stage_name),
    )
    if df is not None and not getattr(df, "empty", True):
        return _safe_int(df.iloc[0]["id"], 0)
    return None


def _render_bulk_dwelling_stage_builder(st: Any, job_id: int) -> None:
    with st.expander("Quick add dwelling / estimate stages", expanded=False):
        st.caption("Create stages without typing names manually. Example: Interior - Dwelling 6 - Prep and seal.")
        line_options = _estimate_line_options(job_id)
        with st.form(f"bulk_dwelling_stage_builder_{job_id}", clear_on_submit=False):
            c1, c2, c3 = st.columns(3)
            scope = c1.selectbox("Stage area", SCOPE_OPTIONS, key=f"bulk_stage_scope_{job_id}")
            default_dwellings = max(1, _safe_int(_setting("default_dwelling_count"), 1))
            from_dwelling = c2.number_input("From dwelling", min_value=1, max_value=500, step=1, value=1)
            to_dwelling = c3.number_input("To dwelling", min_value=1, max_value=500, step=1, value=default_dwellings)

            steps = _stage_steps(str(scope))
            selected_steps = st.multiselect("Work steps", steps, default=steps[:1], key=f"bulk_stage_steps_{job_id}")

            p1, p2, p3 = st.columns(3)
            internal_weight = p1.number_input("Internal % of job", min_value=0.0, max_value=100.0, step=0.5, value=_setting("default_internal_weight_percent"))
            external_weight = p2.number_input("External % of job", min_value=0.0, max_value=100.0, step=0.5, value=_setting("default_external_weight_percent"))
            percent_dwellings = p3.number_input("Dwellings used for %", min_value=1, max_value=500, step=1, value=default_dwellings)

            line_label = st.selectbox(
                "Optional estimate line to link (only when creating one stage)",
                list(line_options),
                key=f"bulk_stage_estimate_line_{job_id}",
            )
            notes = st.text_area("Stage notes", value="Created from quick dwelling stage builder.")
            submitted = st.form_submit_button("Create dwelling stages", type="primary")

        if not submitted:
            return
        if int(to_dwelling) < int(from_dwelling):
            _error("To dwelling must be the same or higher than From dwelling.")
            return
        if not selected_steps:
            _error("Choose at least one work step.")
            return
        try:
            created: list[int] = []
            skipped = 0
            sequence = _next_sequence(job_id)
            dwelling_range = [None] if str(scope) == "Whole job" else list(range(int(from_dwelling), int(to_dwelling) + 1))
            for dwelling_no in dwelling_range:
                dwelling_label = "All dwellings" if dwelling_no is None else f"Dwelling {dwelling_no}"
                for step in selected_steps:
                    stage_name = _stage_name(str(scope), dwelling_label, str(step))
                    percent = _job_percent(str(scope), dwelling_label, str(step), int(percent_dwellings), internal_weight, external_weight)
                    stage_id = _insert_stage(job_id, stage_name, percent, sequence, notes.strip())
                    if stage_id:
                        created.append(stage_id)
                        sequence += 1
                    else:
                        skipped += 1
            selected_line_id = int(line_options.get(line_label, 0) or 0)
            if selected_line_id and len(created) == 1:
                _execute("UPDATE estimate_line_items SET job_stage_id=? WHERE id=?", (int(created[0]), selected_line_id))
            elif selected_line_id and len(created) != 1:
                _error("Estimate line linking only works when exactly one stage is created. The stages were created but the estimate line was not linked.")
            _success(f"Created {len(created)} stage(s). Skipped {skipped} existing duplicate(s).")
            _safe_rerun(st)
        except Exception as exc:
            _error(f"Could not create dwelling stages: {exc}")


def _patch_dataframe(owner: Any, st: Any) -> bool:
    original = getattr(owner, "dataframe", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def dataframe_with_dwelling_stage_builder(data: Any = None, *args: Any, **kwargs: Any):
        job_id_text = _job_id_from_key(kwargs.get("key"))
        if job_id_text:
            try:
                _render_bulk_dwelling_stage_builder(st, int(job_id_text))
            except Exception:
                pass
        return original(data, *args, **kwargs)

    dataframe_with_dwelling_stage_builder._pb_stage_dwelling_builder_guard = True
    dataframe_with_dwelling_stage_builder._pb_original_dataframe = original
    setattr(owner, "dataframe", dataframe_with_dwelling_stage_builder)
    return True


def install_stage_dwelling_builder_guard() -> bool:
    st = _st()
    if st is None:
        return False
    installed = _patch_stage_preset_builder()
    installed = _patch_dataframe(st, st) or installed
    try:
        delta_module = sys.modules.get("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        installed = _patch_dataframe(delta_cls, st) or installed
    return installed
