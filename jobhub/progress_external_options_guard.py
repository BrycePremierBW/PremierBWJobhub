"""External progress option presets for the JobHub progress tracker.

The existing tracker already records external substrate progress, but it only
used coating-step columns.  This guard adds Premier Brushworks external progress
options without rewriting the live tracker: simple External 100%, upper/lower
external split, and the original coating-step workflow.
"""

from __future__ import annotations

import sys
from typing import Any, Iterable, Sequence


EXTERNAL_SIMPLE_MODE = "External — 100%"
EXTERNAL_SECTION_MODE = "Upper scaff / lower / touch-ups"
EXTERNAL_COATING_MODE = "Coating steps"
EXTERNAL_MODE_OPTIONS = (EXTERNAL_SIMPLE_MODE, EXTERNAL_SECTION_MODE, EXTERNAL_COATING_MODE)
EXTERNAL_MODE_KEY = "pb_progress_external_tracking_mode"

EXTERNAL_SIMPLE_STAGES = (("external_overall", "External", 100.0),)
EXTERNAL_SECTION_STAGES = (
    ("upper_scaff_work", "upper scaff work", 45.0),
    ("lower_external", "lower", 45.0),
    ("touchups", "touch ups", 10.0),
)


def _tracker_module() -> Any:
    return sys.modules.get("jobhub_progress_tracker")


def _rules_module() -> Any:
    return sys.modules.get("jobhub_progress_rules")


def _streamlit() -> Any:
    return sys.modules.get("streamlit")


def _default_coating_stages() -> tuple[tuple[str, str, float], ...]:
    tracker = _tracker_module()
    original = getattr(tracker, "_pb_original_external_stages", None) if tracker else None
    if original:
        return tuple(original)
    return (
        ("prep", "Preparation", 15.0),
        ("primer", "Primer / Sealer", 20.0),
        ("first_coat", "First Coat", 25.0),
        ("final_coat", "Final Coat", 30.0),
        ("touchups", "Touch-ups", 10.0),
    )


def _current_mode() -> str:
    st = _streamlit()
    if st is None:
        return EXTERNAL_SECTION_MODE
    try:
        mode = str(st.session_state.get(EXTERNAL_MODE_KEY) or EXTERNAL_SECTION_MODE)
    except Exception:
        mode = EXTERNAL_SECTION_MODE
    return mode if mode in EXTERNAL_MODE_OPTIONS else EXTERNAL_SECTION_MODE


def _stages_for_mode(mode: str | None = None) -> tuple[tuple[str, str, float], ...]:
    selected = mode or _current_mode()
    if selected == EXTERNAL_SIMPLE_MODE:
        return EXTERNAL_SIMPLE_STAGES
    if selected == EXTERNAL_COATING_MODE:
        return _default_coating_stages()
    return EXTERNAL_SECTION_STAGES


class DynamicExternalStages(Sequence[tuple[str, str, float]]):
    """Sequence facade so existing tracker code uses the chosen external mode."""

    def _items(self) -> tuple[tuple[str, str, float], ...]:
        return _stages_for_mode()

    def __iter__(self) -> Iterable[tuple[str, str, float]]:
        return iter(self._items())

    def __len__(self) -> int:
        return len(self._items())

    def __getitem__(self, index: int) -> tuple[str, str, float]:
        return self._items()[index]


def _install_external_schema_guard(tracker: Any) -> bool:
    original = getattr(tracker, "ensure_progress_schema", None)
    if original is None or getattr(original, "_pb_external_options_schema_guard", False):
        return False

    def ensure_progress_schema_with_external_options(context: dict[str, Any]) -> None:
        original(context)
        ensure_column = getattr(tracker, "_ensure_progress_column", None)
        if callable(ensure_column):
            for column in ("external_overall", "upper_scaff_work", "lower_external"):
                ensure_column(context, "job_external_progress", column, "TEXT DEFAULT 'Not started'")
        else:
            for column in ("external_overall", "upper_scaff_work", "lower_external"):
                try:
                    if context.get("USE_POSTGRES"):
                        context["execute"](
                            f"ALTER TABLE job_external_progress ADD COLUMN IF NOT EXISTS {column} TEXT DEFAULT 'Not started'"
                        )
                    else:
                        cols = context["df_query"]("PRAGMA table_info(job_external_progress)")
                        names = set(cols.get("name", []).astype(str).tolist()) if hasattr(cols, "get") else set()
                        if column not in names:
                            context["execute"](
                                f"ALTER TABLE job_external_progress ADD COLUMN {column} TEXT DEFAULT 'Not started'"
                            )
                except Exception:
                    pass

    ensure_progress_schema_with_external_options._pb_external_options_schema_guard = True
    ensure_progress_schema_with_external_options._pb_original_ensure_progress_schema = original
    tracker.ensure_progress_schema = ensure_progress_schema_with_external_options
    return True


def _install_external_editor_guard(tracker: Any) -> bool:
    original = getattr(tracker, "_render_status_editor", None)
    if original is None or getattr(original, "_pb_external_options_editor_guard", False):
        return False

    def render_status_editor_with_external_options(
        context: dict[str, Any],
        df: Any,
        table: str,
        id_column: str,
        stages: Any,
        username: str,
        key_prefix: str,
    ) -> Any:
        st = _streamlit()
        if st is not None and table == "job_external_progress":
            st.markdown("#### External progress options")
            st.caption(
                "Choose how this job's external progress should be tracked. Use External 100% "
                "for simple jobs, or upper/lower/touch-ups for scaffold jobs."
            )
            try:
                current = _current_mode()
                mode = st.radio(
                    "External tracking mode",
                    EXTERNAL_MODE_OPTIONS,
                    index=EXTERNAL_MODE_OPTIONS.index(current),
                    horizontal=True,
                    key=EXTERNAL_MODE_KEY,
                )
            except Exception:
                mode = EXTERNAL_SECTION_MODE
            stages = _stages_for_mode(str(mode))
        return original(context, df, table, id_column, stages, username, key_prefix)

    render_status_editor_with_external_options._pb_external_options_editor_guard = True
    render_status_editor_with_external_options._pb_original_render_status_editor = original
    tracker._render_status_editor = render_status_editor_with_external_options
    return True


def install_progress_external_options_guard() -> bool:
    tracker = _tracker_module()
    if tracker is None:
        return False

    if not hasattr(tracker, "_pb_original_external_stages"):
        tracker._pb_original_external_stages = tuple(getattr(tracker, "EXTERNAL_STAGES", ()) or ())

    dynamic = DynamicExternalStages()
    tracker.EXTERNAL_STAGES = dynamic
    rules = _rules_module()
    if rules is not None:
        rules.EXTERNAL_STAGES = dynamic

    schema_installed = _install_external_schema_guard(tracker)
    editor_installed = _install_external_editor_guard(tracker)
    return bool(schema_installed or editor_installed)
