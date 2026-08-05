"""Make JobHub stage setup easier to understand and always visible.

The original dwelling builder was injected immediately before the selectable
Current Stages dataframe.  Jobs with no stages return before that dataframe is
rendered, which hid the builder precisely when it was most useful.  This guard
renders the builder near the top of every Job Stages panel and prevents the old
dataframe hook from drawing it a second time.

It also replaces technical stage wording with plain labels while preserving the
same database fields, percentages and calculation behaviour.
"""

from __future__ import annotations

import inspect
import sys
from typing import Any

from . import stage_dwelling_builder_guard as dwelling_builder


PATCH_MARKER = "_pb_stage_setup_simplifier_guard"
INTRO_CAPTION = (
    "Split this job into any named work stages. Existing jobs remain available as "
    "Whole Job until stages are added."
)

MODE_LABELS = {
    "builder": "Add stages for dwellings",
    "preset": "Use a saved template",
    "custom": "Add one custom stage",
}

STEP_LABELS = {
    "Prep and seal": "Prepare and seal",
    "Finish coats": "Spray finish coats",
    "Cut and roll walls and paint doors": "Cut in, roll walls and paint doors",
    "Doors and trims": "Doors and trims",
    "Interior touch ups / defects": "Interior touch-ups and defects",
    "Upper scaff work": "Upper exterior (scaffold)",
    "Lower external": "Lower exterior",
    "External touch ups": "Exterior touch-ups",
    "Whole job": "Whole job",
    "Site establishment": "Site setup",
    "Final defects / handover": "Final defects and handover",
}

LABELS = {
    INTRO_CAPTION: (
        "Break the job into clear work stages. The stage percentages should add up "
        "to 100% of the whole job."
    ),
    "Stage add method": "How do you want to add stages?",
    "Stage area": "Work area",
    "Dwelling / unit": "Dwelling",
    "Work step": "Painting stage",
    "Quick add dwelling / estimate stages": "Add stages for multiple dwellings",
    "Create stages without typing names manually. Example: Interior - Dwelling 6 - Prep and seal.": (
        "Use this for multi-dwelling jobs. JobHub will divide the selected share "
        "of the whole job across the dwellings and painting stages."
    ),
    "From dwelling": "First dwelling",
    "To dwelling": "Last dwelling",
    "Work steps": "Painting stages",
    "Internal % of job": "Interior share of whole job (%)",
    "External % of job": "Exterior share of whole job (%)",
    "Dwellings used for %": "Number of dwellings",
    "Optional estimate line to link (only when creating one stage)": "Link an estimate item (optional)",
    "Stage notes": "Notes",
    "Create dwelling stages": "Create stages",
    "Stage Name": "Stage name",
    "Job %": "Share of whole job (%)",
    "Budget Hours Override": "Budget hours (optional)",
    "Planned Start": "Start date",
    "Planned Finish": "Finish date",
    "Purchase Order": "Purchase order",
    "Add a stage": "Add one stage",
    "### Current Stages": "### Job stages",
    "No stages have been added. Scheduling and timesheets will continue to use Whole Job.": (
        "No stages yet. Use Add stages for multiple dwellings above, or add one "
        "custom stage."
    ),
}

_RENDERED_JOBS: set[int] = set()
_TARGET_CAPTION_ACTIVE = False


def _streamlit() -> Any:
    return sys.modules.get("streamlit")


def _simple_label(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return LABELS.get(value, value)


def _job_id_from_stack() -> int | None:
    frame = inspect.currentframe()
    try:
        for _ in range(30):
            frame = frame.f_back if frame is not None else None
            if frame is None:
                return None
            if frame.f_code.co_name != "render_job_stages_panel":
                continue
            value = frame.f_locals.get("job_id")
            try:
                return int(value)
            except Exception:
                return None
    finally:
        del frame
    return None


def _patch_builder_once() -> bool:
    original = getattr(dwelling_builder, "_render_bulk_dwelling_stage_builder", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def render_once(st: Any, job_id: int) -> Any:
        job_id = int(job_id)
        if job_id in _RENDERED_JOBS:
            return None
        _RENDERED_JOBS.add(job_id)
        try:
            return original(st, job_id)
        except Exception:
            _RENDERED_JOBS.discard(job_id)
            raise

    setattr(render_once, PATCH_MARKER, True)
    render_once._pb_original_builder = original
    dwelling_builder._render_bulk_dwelling_stage_builder = render_once
    return True


def _inject_builder(st: Any) -> None:
    job_id = _job_id_from_stack()
    if job_id is None:
        return
    # A new Job Stages panel render marks a new opportunity to draw this job's
    # builder.  The wrapped builder then suppresses the old dataframe injection.
    _RENDERED_JOBS.discard(job_id)
    try:
        dwelling_builder._render_bulk_dwelling_stage_builder(st, job_id)
    except Exception:
        # Do not take down the entire Job Folder page if the optional helper has
        # a runtime problem.  For non-empty jobs the older dataframe hook remains
        # available as a fallback because render_once discarded the marker.
        _RENDERED_JOBS.discard(job_id)


def _patch_label_method(owner: Any, st: Any, method_name: str) -> bool:
    original = getattr(owner, method_name, None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapper(first: Any, *args: Any, **kwargs: Any):
        global _TARGET_CAPTION_ACTIVE
        raw = first
        mapped = _simple_label(first)
        is_intro = method_name == "caption" and str(raw) == INTRO_CAPTION
        if not is_intro or _TARGET_CAPTION_ACTIVE:
            return original(mapped, *args, **kwargs)

        _TARGET_CAPTION_ACTIVE = True
        try:
            result = original(mapped, *args, **kwargs)
            _inject_builder(st)
            return result
        finally:
            _TARGET_CAPTION_ACTIVE = False

    setattr(wrapper, PATCH_MARKER, True)
    wrapper._pb_original_method = original
    setattr(owner, method_name, wrapper)
    return True


def _simplify_builder_values() -> None:
    dwelling_builder.MODE_BUILDER = MODE_LABELS["builder"]
    dwelling_builder.MODE_PRESET = MODE_LABELS["preset"]
    dwelling_builder.MODE_CUSTOM = MODE_LABELS["custom"]

    old_keys = dict(dwelling_builder.STEP_SETTING_KEYS)
    for old_label, new_label in STEP_LABELS.items():
        if old_label in old_keys:
            dwelling_builder.STEP_SETTING_KEYS[new_label] = old_keys[old_label]

    dwelling_builder.INTERIOR_STEPS = [
        STEP_LABELS.get(label, label) for label in dwelling_builder.INTERIOR_STEPS
    ]
    dwelling_builder.EXTERIOR_STEPS = [
        STEP_LABELS.get(label, label) for label in dwelling_builder.EXTERIOR_STEPS
    ]
    dwelling_builder.WHOLE_JOB_STEPS = [
        STEP_LABELS.get(label, label) for label in dwelling_builder.WHOLE_JOB_STEPS
    ]


def install_stage_setup_simplifier_guard() -> bool:
    """Install plain labels and a reliable multi-dwelling stage panel."""
    st = _streamlit()
    if st is None:
        return False

    _simplify_builder_values()
    installed = _patch_builder_once()
    methods = (
        "caption",
        "selectbox",
        "number_input",
        "multiselect",
        "text_input",
        "text_area",
        "date_input",
        "form_submit_button",
        "expander",
        "markdown",
        "info",
    )
    for method_name in methods:
        installed = _patch_label_method(st, st, method_name) or installed

    try:
        delta_module = sys.modules.get("streamlit.delta_generator")
        delta_cls = getattr(delta_module, "DeltaGenerator", None) if delta_module is not None else None
    except Exception:
        delta_cls = None
    if delta_cls is not None:
        for method_name in methods:
            installed = _patch_label_method(delta_cls, st, method_name) or installed
    return installed
