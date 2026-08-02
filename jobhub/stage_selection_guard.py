"""Stage selection helpers for JobHub's stage/PO/control panels.

The main JobHub file already has stage tables, progress summaries and claim
editors.  This guard keeps the change small and safe by wrapping Streamlit's
existing table functions so a user can select stages from either the visible
row/table or a clear dropdown/multiselect control.
"""

from __future__ import annotations

from types import SimpleNamespace
import re
import sys
from typing import Any, Mapping

try:
    import pandas as pd
except Exception:  # pragma: no cover - Streamlit runtime always has pandas
    pd = None  # type: ignore[assignment]


_STAGE_CURRENT_PREFIX = "selectable_job_stages_"
_STAGE_PROGRESS_PREFIX = "job_stage_progress_summary_"
_STAGE_CLAIM_PREFIX = "stage_claim_editor_"


def _job_id_from_key(key: Any, prefix: str) -> str:
    text = str(key or "")
    if text.startswith(prefix):
        return text[len(prefix):]
    return ""


def _selection_rows(selection: Any) -> list[int]:
    if selection is None:
        return []
    rows = getattr(selection, "rows", None)
    if rows is None and isinstance(selection, Mapping):
        rows = selection.get("rows")
    if rows is None:
        return []
    result: list[int] = []
    try:
        iterable = list(rows)
    except TypeError:
        iterable = []
    for row in iterable:
        try:
            result.append(int(row))
        except (TypeError, ValueError):
            continue
    return result


def _set_rows(event: Any, rows: list[int]) -> Any:
    selection = getattr(event, "selection", None)
    if selection is not None:
        try:
            selection.rows = rows
            return event
        except Exception:
            pass
        if isinstance(selection, dict):
            try:
                selection["rows"] = rows
                return event
            except Exception:
                pass
    return SimpleNamespace(selection=SimpleNamespace(rows=rows, columns=[]))


def _as_dataframe(data: Any):
    if pd is None:
        return None
    if data is None:
        return None
    if hasattr(data, "copy") and hasattr(data, "columns"):
        try:
            return data.copy()
        except Exception:
            return data
    try:
        return pd.DataFrame(data)
    except Exception:
        return None


def _stage_label(row: Any, *, include_context: bool = False) -> str:
    get = row.get if hasattr(row, "get") else lambda key, default=None: default
    stage = str(get("Stage Name", get("Stage", "")) or "").strip()
    if not stage:
        stage = "Unnamed stage"
    if not include_context:
        return stage
    parts = [stage]
    po = str(get("Purchase Order", get("PO", "")) or "").strip()
    if po:
        parts.append(f"PO {po}")
    percent = get("Job %", "")
    try:
        percent_float = float(percent or 0)
        if percent_float:
            parts.append(f"{percent_float:g}%")
    except Exception:
        pass
    return " — ".join(parts)


def _stage_selection_key(job_id: str) -> str:
    return f"pb_selected_stage_for_job_{job_id}"


def _claim_selection_key(job_id: str) -> str:
    return f"pb_selected_claim_stages_for_job_{job_id}"


def _valid_stage_labels(df: Any, *, include_context: bool = False) -> list[str]:
    if df is None or getattr(df, "empty", True):
        return []
    labels: list[str] = []
    seen: set[str] = set()
    for _, row in df.iterrows():
        label = _stage_label(row, include_context=include_context)
        if label and label not in seen:
            labels.append(label)
            seen.add(label)
    return labels


def _label_matches(label: str, row: Any) -> bool:
    plain = _stage_label(row, include_context=False)
    contextual = _stage_label(row, include_context=True)
    return label == plain or label == contextual or label.startswith(f"{plain} —")


def _update_selected_stage_from_event(streamlit_module: Any, job_id: str, df: Any, event: Any) -> None:
    rows = _selection_rows(getattr(event, "selection", None))
    if not rows or df is None or getattr(df, "empty", True):
        return
    row_index = rows[0]
    if row_index < 0 or row_index >= len(df):
        return
    try:
        label = _stage_label(df.iloc[row_index], include_context=False)
    except Exception:
        return
    if label:
        streamlit_module.session_state[_stage_selection_key(job_id)] = label


def _render_stage_selectbox(streamlit_module: Any, job_id: str, df: Any) -> str:
    labels = _valid_stage_labels(df, include_context=False)
    if not labels:
        return ""
    key = _stage_selection_key(job_id)
    stored = str(streamlit_module.session_state.get(key) or "")
    default_index = labels.index(stored) if stored in labels else 0
    selected = streamlit_module.selectbox(
        "Stage selection",
        labels,
        index=default_index,
        key=f"stage_selection_dropdown_{job_id}",
        help="Select a stage here, or click a stage row/tile below. The edit form will follow this selection.",
    )
    streamlit_module.session_state[key] = selected
    return selected


def _event_for_dropdown_selection(streamlit_module: Any, job_id: str, df: Any, event: Any) -> Any:
    rows = _selection_rows(getattr(event, "selection", None))
    if rows:
        _update_selected_stage_from_event(streamlit_module, job_id, df, event)
        return event
    selected_label = str(streamlit_module.session_state.get(_stage_selection_key(job_id)) or "")
    if not selected_label or df is None or getattr(df, "empty", True):
        return event
    for index, (_, row) in enumerate(df.iterrows()):
        if _label_matches(selected_label, row):
            return _set_rows(event, [index])
    return event


def _render_claim_stage_multiselect(streamlit_module: Any, job_id: str, df: Any):
    if df is None or getattr(df, "empty", True):
        return df
    labels = _valid_stage_labels(df, include_context=True)
    if not labels:
        return df
    stored = list(streamlit_module.session_state.get(_claim_selection_key(job_id)) or [])
    selected_stage = str(streamlit_module.session_state.get(_stage_selection_key(job_id)) or "")
    if stored:
        default = [label for label in stored if label in labels]
    elif selected_stage:
        default = [label for label in labels if label == selected_stage or label.startswith(f"{selected_stage} —")]
    else:
        default = []
    selected = streamlit_module.multiselect(
        "Stage selection for this claim",
        labels,
        default=default,
        key=f"stage_claim_selection_dropdown_{job_id}",
        help="Use this dropdown instead of tiny table checkboxes. You can still adjust the claim amount in the table below.",
    )
    streamlit_module.session_state[_claim_selection_key(job_id)] = list(selected)
    if not selected:
        return df
    work = df.copy()
    work["Include"] = [
        any(_label_matches(label, row) for label in selected)
        for _, row in work.iterrows()
    ]
    return work


def install_stage_selection_guard() -> bool:
    streamlit_module = sys.modules.get("streamlit")
    if streamlit_module is None:
        return False
    changed = False

    original_dataframe = getattr(streamlit_module, "dataframe", None)
    if original_dataframe is not None and not getattr(original_dataframe, "_pb_stage_selection_guard", False):
        def pb_stage_dataframe(data: Any = None, *args: Any, **kwargs: Any):
            key = kwargs.get("key")
            job_id_progress = _job_id_from_key(key, _STAGE_PROGRESS_PREFIX)
            job_id_current = _job_id_from_key(key, _STAGE_CURRENT_PREFIX)
            df = _as_dataframe(data)
            if job_id_progress:
                kwargs["on_select"] = "rerun"
                kwargs["selection_mode"] = "single-row"
                event = original_dataframe(data, *args, **kwargs)
                _update_selected_stage_from_event(streamlit_module, job_id_progress, df, event)
                return event
            if job_id_current:
                _render_stage_selectbox(streamlit_module, job_id_current, df)
                event = original_dataframe(data, *args, **kwargs)
                return _event_for_dropdown_selection(streamlit_module, job_id_current, df, event)
            return original_dataframe(data, *args, **kwargs)

        pb_stage_dataframe._pb_stage_selection_guard = True  # type: ignore[attr-defined]
        pb_stage_dataframe._pb_original_dataframe = original_dataframe  # type: ignore[attr-defined]
        streamlit_module.dataframe = pb_stage_dataframe
        changed = True

    original_data_editor = getattr(streamlit_module, "data_editor", None)
    if original_data_editor is not None and not getattr(original_data_editor, "_pb_stage_claim_guard", False):
        def pb_stage_data_editor(data: Any = None, *args: Any, **kwargs: Any):
            key = kwargs.get("key")
            job_id = _job_id_from_key(key, _STAGE_CLAIM_PREFIX)
            if not job_id:
                return original_data_editor(data, *args, **kwargs)
            df = _as_dataframe(data)
            data_for_editor = _render_claim_stage_multiselect(streamlit_module, job_id, df)
            edited = original_data_editor(data_for_editor if data_for_editor is not None else data, *args, **kwargs)
            edited_df = _as_dataframe(edited)
            if edited_df is not None and not getattr(edited_df, "empty", True) and "Include" in edited_df.columns:
                selected = [
                    _stage_label(row, include_context=True)
                    for _, row in edited_df[edited_df["Include"].fillna(False).astype(bool)].iterrows()
                ]
                streamlit_module.session_state[_claim_selection_key(job_id)] = selected
            return edited

        pb_stage_data_editor._pb_stage_claim_guard = True  # type: ignore[attr-defined]
        pb_stage_data_editor._pb_original_data_editor = original_data_editor  # type: ignore[attr-defined]
        streamlit_module.data_editor = pb_stage_data_editor
        changed = True

    return changed
