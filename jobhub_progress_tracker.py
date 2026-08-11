from __future__ import annotations

from datetime import datetime
from jobhub_time import jobhub_now
from io import BytesIO

import pandas as pd
import streamlit as st

from jobhub_progress_rules import (
    EXTERNAL_STAGES,
    INTERNAL_STAGES,
    STATUS_OPTIONS,
    combine_internal_progress,
    weighted_percent,
)
from jobhub_production import expected_progress, line_production_metrics


def _now():
    return jobhub_now().isoformat(timespec="seconds")


def _weighted_percent(row, stages):
    return weighted_percent(row, stages)


def _ensure_progress_column(context, table, column, definition):
    """Add one portable progress column without rebuilding a live table."""
    if context.get("USE_POSTGRES"):
        context["execute"](
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"
        )
        return
    columns = context["df_query"](f"PRAGMA table_info({table})")
    existing = set(columns.get("name", pd.Series(dtype=str)).astype(str).tolist())
    if column not in existing:
        context["execute"](f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


@st.cache_resource(show_spinner=False)
def ensure_progress_schema(_context):
    """Create progress tables and indexes once per running app process."""
    context = _context
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
            prepped_sealed TEXT DEFAULT 'Not started',
            prep_spray_finished TEXT DEFAULT 'Not started',
            cut_rolled TEXT DEFAULT 'Not started',
            defects TEXT DEFAULT 'Not started',
            is_custom INTEGER DEFAULT 0,
            scope_percent REAL DEFAULT 0,
            notes TEXT,
            updated_at TEXT,
            updated_by TEXT,
            UNIQUE(job_id, dwelling_no),
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """
    )
    for column, definition in (
        ("prepped_sealed", "TEXT DEFAULT 'Not started'"),
        ("prep_spray_finished", "TEXT DEFAULT 'Not started'"),
        ("cut_rolled", "TEXT DEFAULT 'Not started'"),
        ("defects", "TEXT DEFAULT 'Not started'"),
        ("is_custom", "INTEGER DEFAULT 0"),
        ("scope_percent", "REAL DEFAULT 0"),
    ):
        _ensure_progress_column(context, "job_dwelling_progress", column, definition)
    context["execute"](
        """
        UPDATE job_dwelling_progress
        SET prepped_sealed=COALESCE(NULLIF(sealer,''),'Not started'),
            prep_spray_finished=CASE
                WHEN LOWER(COALESCE(spray_walls,'Not started'))='complete'
                 AND LOWER(COALESCE(spray_ceilings,'Not started'))='complete'
                 AND LOWER(COALESCE(spray_gloss,'Not started'))='complete' THEN 'Complete'
                WHEN LOWER(COALESCE(spray_walls,'Not started'))<>'not started'
                  OR LOWER(COALESCE(spray_ceilings,'Not started'))<>'not started'
                  OR LOWER(COALESCE(spray_gloss,'Not started'))<>'not started' THEN 'In progress'
                ELSE 'Not started'
            END,
            cut_rolled=COALESCE(NULLIF(pc,''),'Not started'),
            defects=COALESCE(NULLIF(touchups,''),'Not started')
        WHERE LOWER(COALESCE(prepped_sealed,'Not started'))='not started'
          AND LOWER(COALESCE(prep_spray_finished,'Not started'))='not started'
          AND LOWER(COALESCE(cut_rolled,'Not started'))='not started'
          AND LOWER(COALESCE(defects,'Not started'))='not started'
          AND (
              LOWER(COALESCE(sealer,'Not started'))<>'not started'
              OR LOWER(COALESCE(spray_walls,'Not started'))<>'not started'
              OR LOWER(COALESCE(spray_ceilings,'Not started'))<>'not started'
              OR LOWER(COALESCE(spray_gloss,'Not started'))<>'not started'
              OR LOWER(COALESCE(pc,'Not started'))<>'not started'
              OR LOWER(COALESCE(touchups,'Not started'))<>'not started'
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
    execute(
        "CREATE INDEX IF NOT EXISTS idx_dwelling_progress_job_custom "
        "ON job_dwelling_progress(job_id, is_custom, dwelling_no)"
    )
    execute("CREATE INDEX IF NOT EXISTS idx_external_progress_job ON job_external_progress(job_id)")
    execute(
        "CREATE INDEX IF NOT EXISTS idx_external_progress_job_estimate "
        "ON job_external_progress(job_id, estimate_line_id)"
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_progress_settings_linked_estimate "
        "ON job_progress_settings(linked_estimate_id)"
    )


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
        """
        SELECT dwelling_no, floor_m2
        FROM job_dwelling_progress
        WHERE job_id=? AND COALESCE(is_custom,0)=0
        ORDER BY dwelling_no
        """,
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
        """
        DELETE FROM job_dwelling_progress
        WHERE job_id=? AND dwelling_no>? AND COALESCE(is_custom,0)=0
        """,
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
        """
        SELECT estimate_line_id, COALESCE(area_name, '') AS area_name,
               COALESCE(substrate, '') AS substrate,
               COALESCE(measured_m2, 0) AS measured_m2
        FROM job_external_progress
        WHERE job_id=? AND estimate_line_id IS NOT NULL
        """,
        (job_id,),
    )
    existing = {}
    if not current.empty:
        existing = {
            int(row["estimate_line_id"]): {
                "area_name": str(row["area_name"] or ""),
                "substrate": str(row["substrate"] or ""),
                "measured_m2": float(row["measured_m2"] or 0),
            }
            for _, row in current.iterrows()
        }
    now = _now()
    updates = []
    inserts = []
    added = 0
    for _, row in source.iterrows():
        line_id = int(row["id"])
        area_name = str(row["description"] or "External area")
        substrate = str(row["substrate"] or "Needs review")
        measured_m2 = float(row["qty"] or 0)
        if line_id in existing:
            saved = existing[line_id]
            if (
                saved["area_name"] != area_name
                or saved["substrate"] != substrate
                or abs(saved["measured_m2"] - measured_m2) > 0.0001
            ):
                updates.append(
                    (area_name, substrate, measured_m2, now, username, job_id, line_id)
                )
            continue
        inserts.append(
            (
                job_id,
                line_id,
                area_name,
                substrate,
                measured_m2,
                now,
                username,
            )
        )
        added += 1
    execute_many = context.get("execute_many")
    if updates:
        sql = """
            UPDATE job_external_progress
            SET area_name=?, substrate=?, measured_m2=?, updated_at=?, updated_by=?
            WHERE job_id=? AND estimate_line_id=?
        """
        if execute_many:
            execute_many(sql, updates)
        else:
            for params in updates:
                context["execute"](sql, params)
    if inserts:
        sql = """
            INSERT INTO job_external_progress
            (job_id,estimate_line_id,area_name,substrate,measured_m2,updated_at,updated_by)
            VALUES (?,?,?,?,?,?,?)
        """
        if execute_many:
            execute_many(sql, inserts)
        else:
            for params in inserts:
                context["execute"](sql, params)
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
        """
        SELECT * FROM job_dwelling_progress
        WHERE job_id=?
        ORDER BY COALESCE(is_custom,0), dwelling_no
        """,
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
    internal = combine_internal_progress(dwellings.to_dict("records"))
    internal_m2 = float(internal["internal_m2"])
    internal_done = float(internal["internal_done"])
    external_m2 = float(external["measured_m2"].fillna(0).sum()) if not external.empty else 0.0
    external_done = (
        float((external["measured_m2"].fillna(0) * external["progress_percent"] / 100).sum())
        if not external.empty else 0.0
    )
    internal_pct = float(internal["internal_percent"])
    external_pct = external_done / external_m2 * 100 if external_m2 else 0.0
    iw = float(settings.get("internal_weight_percent") or 65)
    ew = float(settings.get("external_weight_percent") or 35)
    has_internal_scope = bool(internal_m2 or internal["custom_item_count"])
    active_weight = (iw if has_internal_scope else 0) + (ew if external_m2 else 0)
    overall = ((internal_pct * iw) + (external_pct * ew)) / active_weight if active_weight else 0.0
    return dwellings, external, {
        "internal_m2": internal_m2,
        "internal_done": internal_done,
        "internal_pct": internal_pct,
        "internal_floor_pct": float(internal["internal_floor_percent"]),
        "internal_custom_items": int(internal["custom_item_count"]),
        "internal_custom_weight": float(internal["custom_weight_percent"]),
        "external_m2": external_m2,
        "external_done": external_done,
        "external_pct": external_pct,
        "overall_pct": overall,
    }


def _timesheet_expected_progress(context, job_id, estimate_id):
    """Calculate the progress that submitted hours should have earned on the take-off."""
    baseline = context["df_query"](
        """
        SELECT id,estimate_id,COALESCE(production_day_hours,8) AS day_hours,
               COALESCE(production_value_target,1000) AS value_target
        FROM estimate_baselines
        WHERE job_id=? AND COALESCE(active,1)=1
        ORDER BY locked_at DESC,id DESC LIMIT 1
        """,
        (int(job_id),),
    )
    baseline_id = int(baseline.iloc[0]["id"]) if not baseline.empty else None
    if not estimate_id and not baseline_id:
        return None
    if baseline_id:
        production = {
            "day_hours": float(baseline.iloc[0]["day_hours"] or 8),
            "value_low": 800.0,
            "value_target": float(baseline.iloc[0]["value_target"] or 1000),
            "value_high": 1000.0,
        }
        lines = context["df_query"](
            """
            SELECT COALESCE(qty,0) AS qty,COALESCE(unit,'item') AS unit,
                   COALESCE(unit_rate,0) AS unit_rate,COALESCE(line_total,0) AS line_total,
                   COALESCE(estimated_labour_hours,0) AS estimated_labour_hours
            FROM estimate_baseline_lines
            WHERE baseline_id=? AND COALESCE(production_tracking_enabled,1)=1
            """,
            (baseline_id,),
        )
    else:
        settings_df = context["df_query"](
            """
            SELECT COALESCE(production_day_hours,8) AS day_hours,
                   COALESCE(production_value_low,800) AS value_low,
                   COALESCE(production_value_target,1000) AS value_target,
                   COALESCE(production_value_high,1000) AS value_high
            FROM estimate_working_sheets WHERE id=?
            """,
            (int(estimate_id),),
        )
        if settings_df.empty:
            return None
        production = settings_df.iloc[0]
        lines = context["df_query"](
            """
            SELECT COALESCE(qty,0) AS qty,COALESCE(unit,'item') AS unit,
                   COALESCE(unit_rate,0) AS unit_rate,COALESCE(line_total,0) AS line_total,
                   COALESCE(estimated_labour_hours,0) AS estimated_labour_hours
            FROM estimate_line_items
            WHERE estimate_id=? AND COALESCE(production_tracking_enabled,1)=1
            """,
            (int(estimate_id),),
        )
    target_hours = 0.0
    for _, line in lines.iterrows():
        metrics = line_production_metrics(
            quantity=line["qty"], unit_rate=line["unit_rate"], line_total=line["line_total"],
            unit=line["unit"], day_hours=production["day_hours"],
            value_low=production["value_low"], value_target=production["value_target"],
            value_high=production["value_high"],
        )
        target_hours += float(
            metrics["labour_hours_at_target"] or line["estimated_labour_hours"] or 0
        )
    if target_hours <= 0:
        return None
    actual_df = context["df_query"](
        """
        SELECT COALESCE(SUM(total_hours),0) AS actual_hours
        FROM timesheet_entries
        WHERE job_id=? AND COALESCE(status,'Submitted') <> 'Rejected'
        """,
        (int(job_id),),
    )
    actual_hours = float(actual_df.iloc[0]["actual_hours"] or 0) if not actual_df.empty else 0.0
    result = expected_progress(actual_hours, target_hours)
    result["estimate_id"] = int(estimate_id) if estimate_id else None
    result["baseline_id"] = baseline_id
    result["source"] = "Locked baseline" if baseline_id else "Current estimate"
    return result


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


def _render_custom_internal_items(context, custom, job_id, username):
    """Create and update stairs or other separately weighted internal items."""
    st.markdown("#### Separate internal items")
    st.caption(
        "Add stairs, feature joinery or other quoted internal work separately. Its internal "
        "scope percentage is reserved from the floor-m² work; use 0% for tracking only."
    )
    existing_weight = (
        float(custom["scope_percent"].fillna(0).astype(float).sum())
        if not custom.empty else 0.0
    )
    with st.expander("Add a separate internal item", expanded=custom.empty):
        with st.form(f"add_custom_internal_{job_id}", clear_on_submit=True):
            a1, a2 = st.columns(2)
            item_name = a1.text_input("Item name", placeholder="e.g. Stairs")
            scope_percent = a2.number_input(
                "Share of internal scope %",
                min_value=0.0,
                max_value=100.0,
                step=1.0,
                value=0.0,
                help="The remaining internal percentage stays allocated to the floor-m² work.",
            )
            item_notes = st.text_area("Item notes")
            add_item = st.form_submit_button("Add separate item", type="primary")
        if add_item:
            clean_name = item_name.strip()
            if not clean_name:
                context["pb_error"]("Enter a name for the separate internal item.")
            elif existing_weight + float(scope_percent) > 100.0001:
                context["pb_error"]("Separate internal item percentages cannot total more than 100%.")
            else:
                duplicate = context["df_query"](
                    """
                    SELECT id FROM job_dwelling_progress
                    WHERE job_id=? AND COALESCE(is_custom,0)=1
                      AND LOWER(COALESCE(dwelling_name,''))=LOWER(?)
                    LIMIT 1
                    """,
                    (int(job_id), clean_name),
                )
                if not duplicate.empty:
                    context["pb_error"]("That separate internal item already exists.")
                else:
                    minimum = context["df_query"](
                        "SELECT COALESCE(MIN(dwelling_no),0) AS minimum_no "
                        "FROM job_dwelling_progress WHERE job_id=?",
                        (int(job_id),),
                    )
                    current_minimum = int(minimum.iloc[0]["minimum_no"] or 0)
                    custom_number = min(-1, current_minimum - 1)
                    context["execute"](
                        """
                        INSERT INTO job_dwelling_progress
                        (job_id,dwelling_no,dwelling_name,floor_m2,is_custom,scope_percent,
                         notes,updated_at,updated_by)
                        VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            int(job_id), custom_number, clean_name, 0.0, 1,
                            float(scope_percent), item_notes.strip(), _now(), username,
                        ),
                    )
                    context["pb_success"](f"Added separate internal item: {clean_name}.")
                    context["pb_rerun"]()

    if custom.empty:
        st.info("No separate internal items have been added.")
        return

    display_columns = [stage[0] for stage in INTERNAL_STAGES]
    editor = custom[
        ["id", "dwelling_name", "scope_percent", *display_columns, "notes"]
    ].copy()
    editor = editor.rename(
        columns={
            "dwelling_name": "Item",
            "scope_percent": "Internal Scope %",
            "notes": "Notes",
        }
    )
    config = {
        stage[0]: st.column_config.SelectboxColumn(
            stage[1], options=STATUS_OPTIONS, required=True,
        )
        for stage in INTERNAL_STAGES
    }
    config.update({
        "id": None,
        "Internal Scope %": st.column_config.NumberColumn(
            "Internal Scope %", min_value=0.0, max_value=100.0, step=1.0,
        ),
    })
    edited = st.data_editor(
        editor,
        width="stretch",
        hide_index=True,
        disabled=["id"],
        column_config=config,
        key=f"custom_internal_editor_{job_id}",
    )
    if st.button(
        "Save separate-item progress",
        type="primary",
        key=f"save_custom_internal_{job_id}",
    ):
        edited = edited.copy()
        edited["Internal Scope %"] = pd.to_numeric(
            edited["Internal Scope %"], errors="coerce"
        ).fillna(0.0)
        clean_names = edited["Item"].fillna("").astype(str).str.strip()
        total_weight = float(edited["Internal Scope %"].sum())
        if total_weight > 100.0001:
            context["pb_error"]("Separate internal item percentages cannot total more than 100%.")
        elif (edited["Internal Scope %"] < 0).any():
            context["pb_error"]("Separate internal item percentages cannot be negative.")
        elif (clean_names == "").any():
            context["pb_error"]("Every separate internal item needs a name.")
        elif clean_names.str.casefold().duplicated().any():
            context["pb_error"]("Separate internal item names must be unique.")
        else:
            for row_index, row in edited.iterrows():
                notes_value = row["Notes"]
                clean_notes = "" if pd.isna(notes_value) else str(notes_value)
                context["execute"](
                    """
                    UPDATE job_dwelling_progress
                    SET dwelling_name=?,scope_percent=?,prepped_sealed=?,
                        prep_spray_finished=?,cut_rolled=?,defects=?,notes=?,
                        updated_at=?,updated_by=?
                    WHERE id=? AND job_id=? AND COALESCE(is_custom,0)=1
                    """,
                    (
                        clean_names.loc[row_index],
                        float(row["Internal Scope %"]),
                        row["prepped_sealed"], row["prep_spray_finished"],
                        row["cut_rolled"], row["defects"], clean_notes,
                        _now(), username, int(row["id"]), int(job_id),
                    ),
                )
            context["pb_success"]("Separate internal item progress saved.")
            context["pb_rerun"]()

    item_options = {
        str(row["dwelling_name"] or f"Item {int(row['id'])}"): int(row["id"])
        for _, row in custom.iterrows()
    }
    remove_name = st.selectbox(
        "Separate item to remove",
        list(item_options.keys()),
        key=f"remove_custom_internal_select_{job_id}",
    )
    remove_id = item_options[remove_name]
    confirm_remove = st.checkbox(
        f"Confirm removal of {remove_name}",
        key=f"confirm_remove_custom_internal_{job_id}_{remove_id}",
    )
    if st.button(
        "Remove selected separate item",
        disabled=not confirm_remove,
        key=f"remove_custom_internal_{job_id}_{remove_id}",
    ):
        context["execute"](
            """
            DELETE FROM job_dwelling_progress
            WHERE id=? AND job_id=? AND COALESCE(is_custom,0)=1
            """,
            (remove_id, int(job_id)),
        )
        context["pb_success"](f"Removed separate internal item: {remove_name}.")
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
    production_estimate_id = (
        int(settings.get("linked_estimate_id"))
        if settings.get("linked_estimate_id")
        else (int(estimates.iloc[0]["id"]) if not estimates.empty else None)
    )
    productivity = _timesheet_expected_progress(context, job_id, production_estimate_id)
    if productivity:
        st.markdown("### Timesheet productivity check")
        p1, p2, p3 = st.columns(3)
        p1.metric("Take-off Target Hours", f"{productivity['budget_hours']:,.1f}")
        p2.metric("Timesheet Hours Used", f"{productivity['actual_hours']:,.1f}")
        p3.metric("Should Be Complete", f"{productivity['raw_expected_percent']:,.1f}%")
        st.caption(
            f"Calculated from tracked take-off lines using the {productivity['source'].lower()} "
            "and its completed-work value target per 8-hour painter-day."
        )
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
    regular_dwellings = dwellings[
        dwellings["is_custom"].fillna(0).astype(int) == 0
    ].copy() if not dwellings.empty else dwellings.copy()
    custom_internal = dwellings[
        dwellings["is_custom"].fillna(0).astype(int) == 1
    ].copy() if not dwellings.empty else dwellings.copy()
    estimate_value = float(job.get("contract_value") or 0)
    baseline_value = context["df_query"](
        """
        SELECT COALESCE(total_ex_gst,0) AS total_ex_gst
        FROM estimate_baselines
        WHERE job_id=? AND COALESCE(active,1)=1
        ORDER BY locked_at DESC,id DESC LIMIT 1
        """,
        (int(job_id),),
    )
    if not baseline_value.empty and float(baseline_value.iloc[0]["total_ex_gst"] or 0) > 0:
        estimate_value = float(baseline_value.iloc[0]["total_ex_gst"] or 0)
    elif settings.get("linked_estimate_id"):
        chosen = estimates[estimates["id"] == int(settings["linked_estimate_id"])]
        if not chosen.empty:
            estimate_value = float(chosen.iloc[0]["total_ex_gst"] or estimate_value)
    earned_value = estimate_value * totals["overall_pct"] / 100

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Overall Progress", f"{totals['overall_pct']:.1f}%")
    internal_detail = f"{totals['internal_done']:.1f} / {totals['internal_m2']:.1f} floor m²"
    if totals["internal_custom_items"]:
        internal_detail += f" + {totals['internal_custom_items']} separate item(s)"
    c2.metric("Internal", f"{totals['internal_pct']:.1f}%", internal_detail)
    c3.metric("External", f"{totals['external_pct']:.1f}%", f"{totals['external_done']:.1f} / {totals['external_m2']:.1f} substrate m²")
    c4.metric("Earned Value", f"${earned_value:,.0f}")
    c5.metric("Remaining Value", f"${max(0, estimate_value-earned_value):,.0f}")
    st.progress(min(max(totals["overall_pct"] / 100, 0.0), 1.0))
    if productivity:
        progress_variance = totals["overall_pct"] - productivity["raw_expected_percent"]
        if progress_variance >= -2:
            comparison = "ahead of" if progress_variance >= 0 else "within tolerance of"
            st.success(
                f"Recorded physical progress is {abs(progress_variance):.1f} percentage points "
                f"{comparison} the timesheet expectation."
            )
        else:
            st.warning(
                f"Recorded physical progress is {abs(progress_variance):.1f} percentage points behind "
                "what the used hours should have completed."
            )

    internal_tab, external_tab, summary_tab = st.tabs(
        ["Internal Dwellings", "External Substrates", "Summary / Export"]
    )
    with internal_tab:
        st.caption(
            "Internal floor-m² progress uses: Prepped and sealed 30%, Prep and spray "
            "finished 30%, Cut and rolled 30%, and Defects 10%."
        )
        _render_status_editor(
            context, regular_dwellings, "job_dwelling_progress", "id",
            INTERNAL_STAGES, username, f"internal_{job_id}",
        )
        _render_custom_internal_items(context, custom_internal, job_id, username)
        if not dwellings.empty:
            chart = dwellings[["dwelling_name", "progress_percent"]].rename(
                columns={"dwelling_name": "Internal Item", "progress_percent": "Progress %"}
            ).set_index("Internal Item")
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
                ["Internal floor-only progress %", totals["internal_floor_pct"]],
                ["Internal completed floor m²", totals["internal_done"]],
                ["Internal remaining floor m²", totals["internal_m2"] - totals["internal_done"]],
                ["Separate internal items", totals["internal_custom_items"]],
                ["Separate internal scope weighting %", totals["internal_custom_weight"]],
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
            dwellings.to_excel(writer, index=False, sheet_name="Internal Progress")
            external.to_excel(writer, index=False, sheet_name="External Substrates")
        output.seek(0)
        st.download_button(
            "Download progress workbook",
            data=output.getvalue(),
            file_name=f"{job['job_no']}_Job_Progress.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
