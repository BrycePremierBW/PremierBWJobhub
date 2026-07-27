from __future__ import annotations

from datetime import datetime
from io import BytesIO

import pandas as pd
import streamlit as st


INTERNAL_STAGES = [
    ("sealer", "Sealer", 15.0),
    ("spray_walls", "Spray Walls", 25.0),
    ("spray_ceilings", "Spray Ceilings", 20.0),
    ("spray_gloss", "Spray Gloss", 15.0),
    ("pc", "PC", 15.0),
    ("touchups", "Touch-ups", 10.0),
]
EXTERNAL_STAGES = [
    ("prep", "Preparation", 15.0),
    ("primer", "Primer / Sealer", 20.0),
    ("first_coat", "First Coat", 25.0),
    ("final_coat", "Final Coat", 30.0),
    ("touchups", "Touch-ups", 10.0),
]
STATUS_FACTOR = {"Not started": 0.0, "In progress": 0.5, "Complete": 1.0}
STATUS_OPTIONS = list(STATUS_FACTOR)


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _status_factor(value):
    return STATUS_FACTOR.get(str(value or "Not started"), 0.0)


def _weighted_percent(row, stages):
    weight_total = sum(stage[2] for stage in stages) or 100.0
    earned = sum(_status_factor(row.get(stage[0])) * stage[2] for stage in stages)
    return round(earned / weight_total * 100.0, 2)


def ensure_progress_schema(context):
    execute = context["execute"]
    postgres = bool(context.get("USE_POSTGRES"))
    pk = "SERIAL PRIMARY KEY" if postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    execute(
        f"""
        CREATE TABLE IF NOT EXISTS job_progress_settings (
            id {pk},
            job_id INTEGER NOT NULL UNIQUE,
            dwelling_count INTEGER DEFAULT 1,
            internal_floor_m2 REAL DEFAULT 0,
            linked_estimate_id INTEGER,
            internal_weight_percent REAL DEFAULT 65,
            external_weight_percent REAL DEFAULT 35,
            updated_at TEXT,
            updated_by TEXT,
            notes TEXT,
            FOREIGN KEY(job_id) REFERENCES jobs(id),
            FOREIGN KEY(linked_estimate_id) REFERENCES estimate_working_sheets(id)
        )
        """
    )
    execute(
        f"""
        CREATE TABLE IF NOT EXISTS job_dwelling_progress (
            id {pk},
            job_id INTEGER NOT NULL,
            dwelling_no INTEGER NOT NULL,
            dwelling_name TEXT,
            floor_m2 REAL DEFAULT 0,
            sealer TEXT DEFAULT 'Not started',
            spray_walls TEXT DEFAULT 'Not started',
            spray_ceilings TEXT DEFAULT 'Not started',
            spray_gloss TEXT DEFAULT 'Not started',
            pc TEXT DEFAULT 'Not started',
            touchups TEXT DEFAULT 'Not started',
            notes TEXT,
            updated_at TEXT,
            updated_by TEXT,
            UNIQUE(job_id, dwelling_no),
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """
    )
    execute(
        f"""
        CREATE TABLE IF NOT EXISTS job_external_progress (
            id {pk},
            job_id INTEGER NOT NULL,
            estimate_line_id INTEGER,
            dwelling_no INTEGER DEFAULT 0,
            area_name TEXT,
            substrate TEXT,
            measured_m2 REAL DEFAULT 0,
            prep TEXT DEFAULT 'Not started',
            primer TEXT DEFAULT 'Not started',
            first_coat TEXT DEFAULT 'Not started',
            final_coat TEXT DEFAULT 'Not started',
            touchups TEXT DEFAULT 'Not started',
            notes TEXT,
            updated_at TEXT,
            updated_by TEXT,
            FOREIGN KEY(job_id) REFERENCES jobs(id),
            FOREIGN KEY(estimate_line_id) REFERENCES estimate_line_items(id)
        )
        """
    )
    execute("CREATE INDEX IF NOT EXISTS idx_dwelling_progress_job ON job_dwelling_progress(job_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_external_progress_job ON job_external_progress(job_id)")


def _job_options(context):
    return context["df_query"](
        """
        SELECT j.id, j.job_no, j.job_name, COALESCE(j.status,'') AS status,
               COALESCE(j.contract_value,0) AS contract_value,
               COALESCE(b.name,'') AS builder
        FROM jobs j
        LEFT JOIN builders_clients b ON b.id=j.builder_client_id
        WHERE LOWER(COALESCE(j.status,'')) NOT IN ('archived','cancelled','deleted')
        ORDER BY j.job_no, j.job_name
        """
    )


def _estimate_options(context, job_id):
    return context["df_query"](
        """
        SELECT id, estimate_no, revision, status, COALESCE(total_ex_gst,0) AS total_ex_gst,
               COALESCE(labour_hours,0) AS labour_hours, updated_at
        FROM estimate_working_sheets
        WHERE job_id=? AND COALESCE(archived,0)=0
        ORDER BY CASE LOWER(COALESCE(status,'')) WHEN 'approved' THEN 0 ELSE 1 END,
                 id DESC
        """,
        (job_id,),
    )


def _setting(context, job_id):
    df = context["df_query"]("SELECT * FROM job_progress_settings WHERE job_id=?", (job_id,))
    return {} if df.empty else df.iloc[0].to_dict()


def _sync_dwellings(context, job_id, count, total_floor_m2, username):
    existing = context["df_query"](
        "SELECT dwelling_no, floor_m2 FROM job_dwelling_progress WHERE job_id=? ORDER BY dwelling_no",
        (job_id,),
    )
    existing_numbers = set(existing["dwelling_no"].astype(int).tolist()) if not existing.empty else set()
    default_m2 = round(float(total_floor_m2 or 0) / max(int(count), 1), 2)
    for number in range(1, int(count) + 1):
        if number not in existing_numbers:
            context["execute"](
                """
                INSERT INTO job_dwelling_progress
                (job_id,dwelling_no,dwelling_name,floor_m2,updated_at,updated_by)
                VALUES (?,?,?,?,?,?)
                """,
                (job_id, number, f"Dwelling {number}", default_m2, _now(), username),
            )
    context["execute"](
        "DELETE FROM job_dwelling_progress WHERE job_id=? AND dwelling_no>?",
        (job_id, int(count)),
    )


def _estimate_external_lines(context, estimate_id):
    if not estimate_id:
        return pd.DataFrame()
    return context["df_query"](
        """
        SELECT id, COALESCE(section,'') AS section, COALESCE(item_description,'') AS description,
               COALESCE(qty,0) AS qty, LOWER(COALESCE(unit,'')) AS unit,
               COALESCE(substrate,'') AS substrate, COALESCE(work_location,'') AS work_location
        FROM estimate_line_items
        WHERE estimate_id=?
          AND (
              LOWER(COALESCE(work_location,'')) LIKE '%external%'
              OR LOWER(COALESCE(section,'')) LIKE '%external%'
              OR LOWER(COALESCE(item_description,'')) LIKE '%external%'
              OR LOWER(COALESCE(item_description,'')) LIKE '%soffit%'
              OR LOWER(COALESCE(item_description,'')) LIKE '%fascia%'
          )
          AND LOWER(COALESCE(unit,'')) IN ('m2','m²','sqm','sq m')
        ORDER BY id
        """,
        (estimate_id,),
    )


def _sync_external_from_estimate(context, job_id, estimate_id, username):
    source = _estimate_external_lines(context, estimate_id)
    if source.empty:
        return 0
    current = context["df_query"](
        "SELECT estimate_line_id FROM job_external_progress WHERE job_id=? AND estimate_line_id IS NOT NULL",
        (job_id,),
    )
    existing = set(current["estimate_line_id"].dropna().astype(int).tolist()) if not current.empty else set()
    added = 0
    for _, row in source.iterrows():
        line_id = int(row["id"])
        if line_id in existing:
            context["execute"](
                """
                UPDATE job_external_progress
                SET area_name=?, substrate=?, measured_m2=?, updated_at=?, updated_by=?
                WHERE job_id=? AND estimate_line_id=?
                """,
                (
                    str(row["description"] or "External area"),
                    str(row["substrate"] or "Needs review"),
                    float(row["qty"] or 0),
                    _now(),
                    username,
                    job_id,
                    line_id,
                ),
            )
            continue
        context["execute"](
            """
            INSERT INTO job_external_progress
            (job_id,estimate_line_id,area_name,substrate,measured_m2,updated_at,updated_by)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                job_id,
                line_id,
                str(row["description"] or "External area"),
                str(row["substrate"] or "Needs review"),
                float(row["qty"] or 0),
                _now(),
                username,
            ),
        )
        added += 1
    return added


def sync_all_linked_progress(context):
    """Refresh estimator-linked progress quantities for every configured job."""
    ensure_progress_schema(context)
    settings = context["df_query"](
        """
        SELECT job_id,linked_estimate_id
        FROM job_progress_settings
        WHERE linked_estimate_id IS NOT NULL
        """
    )
    changed = 0
    if settings.empty:
        return changed
    for _, row in settings.iterrows():
        changed += _sync_external_from_estimate(
            context,
            int(row["job_id"]),
            int(row["linked_estimate_id"]),
            "Automatic JobHub sync",
        )
    return changed


def _summary(context, job_id, settings):
    dwellings = context["df_query"](
        "SELECT * FROM job_dwelling_progress WHERE job_id=? ORDER BY dwelling_no",
        (job_id,),
    )
    external = context["df_query"](
        "SELECT * FROM job_external_progress WHERE job_id=? ORDER BY dwelling_no, substrate, area_name",
        (job_id,),
    )
    if not dwellings.empty:
        dwellings["progress_percent"] = dwellings.apply(
            lambda row: _weighted_percent(row, INTERNAL_STAGES), axis=1
        )
    if not external.empty:
        external["progress_percent"] = external.apply(
            lambda row: _weighted_percent(row, EXTERNAL_STAGES), axis=1
        )
    internal_m2 = float(dwellings["floor_m2"].fillna(0).sum()) if not dwellings.empty else 0.0
    internal_done = (
        float((dwellings["floor_m2"].fillna(0) * dwellings["progress_percent"] / 100).sum())
        if not dwellings.empty else 0.0
    )
    external_m2 = float(external["measured_m2"].fillna(0).sum()) if not external.empty else 0.0
    external_done = (
        float((external["measured_m2"].fillna(0) * external["progress_percent"] / 100).sum())
        if not external.empty else 0.0
    )
    internal_pct = internal_done / internal_m2 * 100 if internal_m2 else 0.0
    external_pct = external_done / external_m2 * 100 if external_m2 else 0.0
    iw = float(settings.get("internal_weight_percent") or 65)
    ew = float(settings.get("external_weight_percent") or 35)
    active_weight = (iw if internal_m2 else 0) + (ew if external_m2 else 0)
    overall = ((internal_pct * iw) + (external_pct * ew)) / active_weight if active_weight else 0.0
    return dwellings, external, {
        "internal_m2": internal_m2,
        "internal_done": internal_done,
        "internal_pct": internal_pct,
        "external_m2": external_m2,
        "external_done": external_done,
        "external_pct": external_pct,
        "overall_pct": overall,
    }


def _render_status_editor(context, df, table, id_column, stages, username, key_prefix):
    if df.empty:
        st.info("No rows have been created yet.")
        return
    display_columns = ["dwelling_name", "floor_m2"] if table == "job_dwelling_progress" else [
        "dwelling_no", "area_name", "substrate", "measured_m2"
    ]
    display_columns += [stage[0] for stage in stages] + ["notes"]
    editor = df[["id"] + display_columns].copy()
    labels = {
        "dwelling_name": "Dwelling",
        "floor_m2": "Floor m²",
        "dwelling_no": "Dwelling No",
        "area_name": "External Area",
        "measured_m2": "Measured m²",
        "notes": "Notes",
    }
    editor = editor.rename(columns=labels)
    config = {
        stage[0]: st.column_config.SelectboxColumn(stage[1], options=STATUS_OPTIONS, required=True)
        for stage in stages
    }
    config["id"] = None
    edited = st.data_editor(
        editor,
        width="stretch",
        hide_index=True,
        disabled=["id"],
        column_config=config,
        key=f"{key_prefix}_editor",
    )
    if st.button("Save progress updates", type="primary", key=f"{key_prefix}_save"):
        reverse_labels = {value: key for key, value in labels.items()}
        edited = edited.rename(columns=reverse_labels)
        for _, row in edited.iterrows():
            fields = [column for column in display_columns if column in row.index]
            assignments = ", ".join(f"{field}=?" for field in fields)
            params = [row[field] for field in fields] + [_now(), username, int(row["id"])]
            context["execute"](
                f"UPDATE {table} SET {assignments}, updated_at=?, updated_by=? WHERE {id_column}=?",
                tuple(params),
            )
        context["pb_success"]("Progress saved and percentages recalculated.")
        context["pb_rerun"]()


def render_progress_tracker(context):
    ensure_progress_schema(context)
    user = context["get_current_user"]() or {}
    username = str(user.get("username") or user.get("name") or "JobHub user")
    st.header("Job Progress Tracker")
    st.caption(
        "Estimator-linked dwelling progress, external substrate quantities, weighted completion, "
        "remaining work and progress-claim guidance."
    )
    jobs = _job_options(context)
    if jobs.empty:
        st.info("Create a job before setting up progress tracking.")
        return
    labels = {
        f"{row['job_no']} · {row['job_name']} · {row['builder']}": int(row["id"])
        for _, row in jobs.iterrows()
    }
    selected_label = st.selectbox("Job", list(labels), key="progress_tracker_job")
    job_id = labels[selected_label]
    job = jobs[jobs["id"] == job_id].iloc[0]
    settings = _setting(context, job_id)
    estimates = _estimate_options(context, job_id)

    with st.expander("Tracker setup and estimator link", expanded=not bool(settings)):
        estimate_labels = {"No linked estimate": 0}
        for _, row in estimates.iterrows():
            estimate_labels[
                f"{row['estimate_no']} · {row['revision']} · {row['status']} · "
                f"${float(row['total_ex_gst'] or 0):,.2f} ex GST"
            ] = int(row["id"])
        linked = int(settings.get("linked_estimate_id") or 0)
        current_estimate_index = list(estimate_labels.values()).index(linked) if linked in estimate_labels.values() else 0
        with st.form(f"progress_setup_{job_id}"):
            c1, c2, c3 = st.columns(3)
            dwelling_count = c1.number_input(
                "Number of dwellings / units", min_value=1, max_value=500,
                value=max(1, int(settings.get("dwelling_count") or 1)), step=1,
            )
            internal_floor_m2 = c2.number_input(
                "Total internal floor m²", min_value=0.0,
                value=float(settings.get("internal_floor_m2") or 0), step=1.0,
            )
            estimate_label = c3.selectbox(
                "Estimator source", list(estimate_labels), index=current_estimate_index,
            )
            c4, c5 = st.columns(2)
            internal_weight = c4.number_input(
                "Internal weighting %", min_value=0.0, max_value=100.0,
                value=float(settings.get("internal_weight_percent") or 65), step=5.0,
            )
            external_weight = c5.number_input(
                "External weighting %", min_value=0.0, max_value=100.0,
                value=float(settings.get("external_weight_percent") or 35), step=5.0,
            )
            notes = st.text_area("Tracker notes", value=str(settings.get("notes") or ""))
            submitted = st.form_submit_button("Save setup and create dwelling rows", type="primary")
        if submitted:
            estimate_id = estimate_labels[estimate_label] or None
            context["execute"](
                """
                INSERT INTO job_progress_settings
                (job_id,dwelling_count,internal_floor_m2,linked_estimate_id,
                 internal_weight_percent,external_weight_percent,updated_at,updated_by,notes)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(job_id) DO UPDATE SET
                    dwelling_count=excluded.dwelling_count,
                    internal_floor_m2=excluded.internal_floor_m2,
                    linked_estimate_id=excluded.linked_estimate_id,
                    internal_weight_percent=excluded.internal_weight_percent,
                    external_weight_percent=excluded.external_weight_percent,
                    updated_at=excluded.updated_at,updated_by=excluded.updated_by,notes=excluded.notes
                """,
                (
                    job_id, int(dwelling_count), float(internal_floor_m2), estimate_id,
                    float(internal_weight), float(external_weight), _now(), username, notes,
                ),
            )
            _sync_dwellings(context, job_id, dwelling_count, internal_floor_m2, username)
            added = _sync_external_from_estimate(context, job_id, estimate_id, username)
            context["pb_success"](
                f"Tracker setup saved. {int(dwelling_count)} dwelling rows are ready"
                + (f" and {added} external estimator rows were linked." if added else ".")
            )
            context["pb_rerun"]()

    settings = _setting(context, job_id)
    if not settings:
        st.info("Save the tracker setup above to create this job's dwelling rows.")
        return
    # Linked estimator quantities are refreshed on every tracker render. This
    # keeps progress, forecasting and schedule suggestions on one live dataset.
    if settings.get("linked_estimate_id"):
        _sync_external_from_estimate(
            context, job_id, int(settings["linked_estimate_id"]), username
        )
    dwellings, external, totals = _summary(context, job_id, settings)
    estimate_value = float(job.get("contract_value") or 0)
    if settings.get("linked_estimate_id"):
        chosen = estimates[estimates["id"] == int(settings["linked_estimate_id"])]
        if not chosen.empty:
            estimate_value = float(chosen.iloc[0]["total_ex_gst"] or estimate_value)
    earned_value = estimate_value * totals["overall_pct"] / 100

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Overall Progress", f"{totals['overall_pct']:.1f}%")
    c2.metric("Internal", f"{totals['internal_pct']:.1f}%", f"{totals['internal_done']:.1f} / {totals['internal_m2']:.1f} floor m²")
    c3.metric("External", f"{totals['external_pct']:.1f}%", f"{totals['external_done']:.1f} / {totals['external_m2']:.1f} substrate m²")
    c4.metric("Earned Value", f"${earned_value:,.0f}")
    c5.metric("Remaining Value", f"${max(0, estimate_value-earned_value):,.0f}")
    st.progress(min(max(totals["overall_pct"] / 100, 0.0), 1.0))

    internal_tab, external_tab, summary_tab = st.tabs(
        ["Internal Dwellings", "External Substrates", "Summary / Export"]
    )
    with internal_tab:
        st.caption(
            "Internal progress is calculated from floor m² and the confirmed stages: "
            "Sealer, Spray Walls, Spray Ceilings, Spray Gloss, PC and Touch-ups."
        )
        _render_status_editor(
            context, dwellings, "job_dwelling_progress", "id",
            INTERNAL_STAGES, username, f"internal_{job_id}",
        )
        if not dwellings.empty:
            chart = dwellings[["dwelling_name", "progress_percent"]].rename(
                columns={"dwelling_name": "Dwelling", "progress_percent": "Progress %"}
            ).set_index("Dwelling")
            st.bar_chart(chart)
    with external_tab:
        st.caption(
            "External progress uses measured estimator m² by substrate. Re-sync after changing "
            "external estimate line quantities."
        )
        if settings.get("linked_estimate_id") and st.button(
            "Re-sync external m² from estimator", key=f"resync_external_{job_id}"
        ):
            added = _sync_external_from_estimate(
                context, job_id, int(settings["linked_estimate_id"]), username
            )
            context["pb_success"](
                f"External estimator quantities refreshed. {added} new rows added."
            )
            context["pb_rerun"]()
        _render_status_editor(
            context, external, "job_external_progress", "id",
            EXTERNAL_STAGES, username, f"external_{job_id}",
        )
        if not external.empty:
            external_summary = external.copy()
            external_summary["completed_m2"] = (
                external_summary["measured_m2"] * external_summary["progress_percent"] / 100
            )
            grouped = external_summary.groupby("substrate", as_index=False).agg(
                measured_m2=("measured_m2", "sum"),
                completed_m2=("completed_m2", "sum"),
            )
            grouped["remaining_m2"] = grouped["measured_m2"] - grouped["completed_m2"]
            st.dataframe(grouped, width="stretch", hide_index=True)
    with summary_tab:
        summary = pd.DataFrame(
            [
                ["Overall progress %", totals["overall_pct"]],
                ["Internal progress %", totals["internal_pct"]],
                ["Internal completed floor m²", totals["internal_done"]],
                ["Internal remaining floor m²", totals["internal_m2"] - totals["internal_done"]],
                ["External progress %", totals["external_pct"]],
                ["External completed substrate m²", totals["external_done"]],
                ["External remaining substrate m²", totals["external_m2"] - totals["external_done"]],
                ["Estimated/contract value ex GST", estimate_value],
                ["Earned value", earned_value],
                ["Remaining value", max(0, estimate_value - earned_value)],
            ],
            columns=["Metric", "Value"],
        )
        st.dataframe(summary, width="stretch", hide_index=True)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            summary.to_excel(writer, index=False, sheet_name="Summary")
            dwellings.to_excel(writer, index=False, sheet_name="Internal Dwellings")
            external.to_excel(writer, index=False, sheet_name="External Substrates")
        output.seek(0)
        st.download_button(
            "Download progress workbook",
            data=output.getvalue(),
            file_name=f"{job['job_no']}_Job_Progress.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
