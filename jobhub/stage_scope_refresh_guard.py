"""Keep dwelling-stage options aligned with the selected work area.

Streamlit forms do not rerun when an ordinary widget inside the form changes.
The original builder placed the work-area selector inside the form, so changing
Interior to Exterior left the Painting stages multiselect on its previous list.
This guard places Work area outside the form and gives each area its own widget
state, so the available painting stages update immediately and cannot retain
choices from another area.
"""

from __future__ import annotations

from typing import Any

from . import stage_dwelling_builder_guard as builder


PATCH_MARKER = "_pb_stage_scope_refresh_guard"


def _scope_widget_suffix(scope: str) -> str:
    return "_".join(str(scope or "whole_job").strip().casefold().split())


def _render_bulk_dwelling_stage_builder(st: Any, job_id: int) -> None:
    with st.expander("Add stages for multiple dwellings", expanded=False):
        st.caption(
            "Use this for multi-dwelling jobs. JobHub will divide the selected "
            "share of the whole job across the dwellings and painting stages."
        )
        line_options = builder._estimate_line_options(job_id)

        # This control must stay outside the form. Changing it then reruns the
        # page immediately, allowing the Painting stages options to be rebuilt.
        scope = str(
            st.selectbox(
                "Work area",
                builder.SCOPE_OPTIONS,
                key=f"bulk_stage_scope_{job_id}",
            )
        )
        steps = builder._stage_steps(scope)
        step_key = f"bulk_stage_steps_{job_id}_{_scope_widget_suffix(scope)}"
        default_dwellings = max(
            1,
            builder._safe_int(builder._setting("default_dwelling_count"), 1),
        )

        with st.form(f"bulk_dwelling_stage_builder_{job_id}", clear_on_submit=False):
            c1, c2 = st.columns(2)
            from_dwelling = c1.number_input(
                "First dwelling",
                min_value=1,
                max_value=500,
                step=1,
                value=1,
            )
            to_dwelling = c2.number_input(
                "Last dwelling",
                min_value=1,
                max_value=500,
                step=1,
                value=default_dwellings,
            )

            selected_steps = st.multiselect(
                "Painting stages",
                steps,
                default=steps[:1],
                key=step_key,
            )

            p1, p2 = st.columns(2)
            if scope == "Interior":
                internal_weight = p1.number_input(
                    "Interior share of whole job (%)",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.5,
                    value=builder._setting("default_internal_weight_percent"),
                )
                external_weight = 0.0
            elif scope == "Exterior":
                internal_weight = 0.0
                external_weight = p1.number_input(
                    "Exterior share of whole job (%)",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.5,
                    value=builder._setting("default_external_weight_percent"),
                )
            else:
                internal_weight = 0.0
                external_weight = 0.0
                p1.info("Whole job stages use 100% of the job.")

            percent_dwellings = p2.number_input(
                "Number of dwellings",
                min_value=1,
                max_value=500,
                step=1,
                value=default_dwellings,
            )

            line_label = st.selectbox(
                "Link an estimate item (optional)",
                list(line_options),
                key=f"bulk_stage_estimate_line_{job_id}",
            )
            notes = st.text_area(
                "Notes",
                value="Created from the multi-dwelling stage builder.",
            )
            submitted = st.form_submit_button("Create stages", type="primary")

        if not submitted:
            return
        if int(to_dwelling) < int(from_dwelling):
            builder._error("Last dwelling must be the same as or higher than First dwelling.")
            return
        if not selected_steps:
            builder._error("Choose at least one painting stage.")
            return

        try:
            created: list[int] = []
            skipped = 0
            sequence = builder._next_sequence(job_id)
            dwelling_range = (
                [None]
                if scope == "Whole job"
                else list(range(int(from_dwelling), int(to_dwelling) + 1))
            )
            for dwelling_no in dwelling_range:
                dwelling_label = (
                    "All dwellings" if dwelling_no is None else f"Dwelling {dwelling_no}"
                )
                for step in selected_steps:
                    stage_name = builder._stage_name(scope, dwelling_label, str(step))
                    percent = builder._job_percent(
                        scope,
                        dwelling_label,
                        str(step),
                        int(percent_dwellings),
                        internal_weight,
                        external_weight,
                    )
                    stage_id = builder._insert_stage(
                        job_id,
                        stage_name,
                        percent,
                        sequence,
                        notes.strip(),
                    )
                    if stage_id:
                        created.append(stage_id)
                        sequence += 1
                    else:
                        skipped += 1

            selected_line_id = int(line_options.get(line_label, 0) or 0)
            if selected_line_id and len(created) == 1:
                builder._execute(
                    "UPDATE estimate_line_items SET job_stage_id=? WHERE id=?",
                    (int(created[0]), selected_line_id),
                )
            elif selected_line_id and len(created) != 1:
                builder._error(
                    "An estimate item can only be linked when exactly one stage is created. "
                    "The stages were created without the estimate link."
                )

            builder._success(
                f"Created {len(created)} stage(s). "
                f"Skipped {skipped} existing duplicate(s)."
            )
            builder._safe_rerun(st)
        except Exception as exc:
            builder._error(f"Could not create dwelling stages: {exc}")


def install_stage_scope_refresh_guard() -> bool:
    original = getattr(builder, "_render_bulk_dwelling_stage_builder", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False
    setattr(_render_bulk_dwelling_stage_builder, PATCH_MARKER, True)
    _render_bulk_dwelling_stage_builder._pb_original_builder = original
    builder._render_bulk_dwelling_stage_builder = _render_bulk_dwelling_stage_builder
    return True
