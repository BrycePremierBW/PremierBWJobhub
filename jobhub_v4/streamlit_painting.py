"""Streamlit workspace for JobHub V4 painting-specific operations."""

from __future__ import annotations

from datetime import datetime, timezone
from jobhub_time import jobhub_now
import json
from typing import Any
from uuid import uuid4

import pandas as pd
import streamlit as st

from .handover import build_handover_manifest, build_handover_zip
from .measurements import (
    EXTERNAL_SUBSTRATE_AREA,
    INTERNAL_FLOOR_AREA,
    MEASUREMENT_BASIS_OPTIONS,
    recommended_measurement_basis,
    work_unit_for_measurement_basis,
)
from .paint import calculate_paint_quantity, colour_order_allowed, optimise_pack_mix
from .revisions import compare_revisions
from .schema import ensure_v4_schema
from jobhub_production import (
    DEFAULT_DAY_HOURS,
    DEFAULT_VALUE_HIGH,
    DEFAULT_VALUE_LOW,
    DEFAULT_VALUE_TARGET,
    expected_progress,
    line_production_metrics,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _current_user(ctx: dict[str, Any]) -> dict[str, Any]:
    return ctx["get_current_user"]() or {}


def _current_user_name(ctx: dict[str, Any]) -> str:
    user = _current_user(ctx)
    return str(user.get("username") or user.get("name") or user.get("id") or "unknown")


def _job_picker(ctx: dict[str, Any]) -> tuple[int, dict[str, Any]] | tuple[None, None]:
    jobs = ctx["df_query"](
        """
        SELECT j.id, j.job_no, j.job_name, j.site_address,
               COALESCE(bc.name, '') AS builder_client
        FROM jobs j
        LEFT JOIN builders_clients bc ON bc.id = j.builder_client_id
        WHERE COALESCE(j.archived_at, '') = ''
        ORDER BY j.job_no
        """
    )
    if jobs.empty:
        st.info("Create a job before using Painting Intelligence.")
        return None, None
    options = {
        f"{row['job_no']} — {row['job_name']}": row.to_dict()
        for _, row in jobs.iterrows()
    }
    selected = st.selectbox("Job", list(options), key="v4_job")
    job = options[selected]
    return int(job["id"]), job


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _takeoff_lines_for_job(ctx: dict[str, Any], job_id: int) -> pd.DataFrame:
    """Use the latest imported take-off estimate, falling back to the latest job estimate."""
    imported = ctx["df_query"](
        """
        SELECT estimate_id
        FROM takeoff_pack_imports
        WHERE job_id=? AND estimate_id IS NOT NULL
        ORDER BY imported_at DESC, id DESC
        LIMIT 1
        """,
        (job_id,),
    )
    if not imported.empty:
        estimate_id = int(imported.iloc[0]["estimate_id"])
    else:
        estimates = ctx["df_query"](
            """
            SELECT id
            FROM estimate_working_sheets
            WHERE job_id=? AND COALESCE(archived,0)=0
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id,),
        )
        if estimates.empty:
            return pd.DataFrame()
        estimate_id = int(estimates.iloc[0]["id"])
    return ctx["df_query"](
        """
        SELECT li.id, li.estimate_id, li.job_stage_id,
               COALESCE(js.stage_name,'Whole Job') AS stage_name,
               COALESCE(li.section,'Take-off') AS section,
               COALESCE(li.item_description,'') AS item_description,
               COALESCE(li.qty,0) AS qty, COALESCE(li.unit,'item') AS unit,
               COALESCE(li.unit_rate,0) AS unit_rate,
               COALESCE(li.line_total,0) AS line_total,
               COALESCE(li.estimated_labour_hours,0) AS estimated_labour_hours,
               COALESCE(li.substrate,'') AS substrate,
               COALESCE(li.work_location,'') AS work_location,
               COALESCE(li.coating_system,'') AS coating_system,
               COALESCE(li.colour_finish,'') AS colour_finish,
               COALESCE(li.production_tracking_enabled,1) AS production_tracking_enabled,
               COALESCE(e.production_day_hours,8) AS production_day_hours,
               COALESCE(e.production_value_low,800) AS production_value_low,
               COALESCE(e.production_value_target,1000) AS production_value_target,
               COALESCE(e.production_value_high,1000) AS production_value_high
        FROM estimate_line_items li
        JOIN estimate_working_sheets e ON e.id=li.estimate_id
        LEFT JOIN job_stages js ON js.id=li.job_stage_id
        WHERE li.estimate_id=?
        ORDER BY li.id
        """,
        (estimate_id,),
    )


def _job_stage_options(ctx: dict[str, Any], job_id: int) -> dict[str, int | None]:
    stages = ctx["df_query"](
        """
        SELECT id, stage_name
        FROM job_stages
        WHERE job_id=?
        ORDER BY sequence_order, id
        """,
        (job_id,),
    )
    options: dict[str, int | None] = {"Whole Job": None}
    for _, row in stages.iterrows():
        options[str(row["stage_name"])] = int(row["id"])
    return options


def _render_required_work_status(
    ctx: dict[str, Any],
    job_id: int,
    takeoff_lines: pd.DataFrame,
    selected_line: dict[str, Any] | None,
    stage_id: int | None,
    stage_name: str,
    work_quantity: float,
    work_unit: str,
    unit_rate: float,
) -> dict[str, float]:
    """Show the quantity that should be complete for hours already submitted."""
    if selected_line:
        day_hours = float(selected_line.get("production_day_hours") or DEFAULT_DAY_HOURS)
        value_low = float(selected_line.get("production_value_low") or DEFAULT_VALUE_LOW)
        value_target = float(selected_line.get("production_value_target") or DEFAULT_VALUE_TARGET)
        value_high = float(selected_line.get("production_value_high") or DEFAULT_VALUE_HIGH)
    else:
        day_hours = DEFAULT_DAY_HOURS
        value_low = DEFAULT_VALUE_LOW
        value_target = DEFAULT_VALUE_TARGET
        value_high = DEFAULT_VALUE_HIGH

    selected_metrics = line_production_metrics(
        quantity=work_quantity,
        unit_rate=unit_rate,
        unit=work_unit,
        day_hours=day_hours,
        value_low=value_low,
        value_target=value_target,
        value_high=value_high,
    )
    scope = takeoff_lines[
        takeoff_lines["production_tracking_enabled"].fillna(1).astype(int) == 1
    ].copy() if not takeoff_lines.empty else pd.DataFrame()
    if stage_id is not None and not scope.empty:
        scope = scope[scope["job_stage_id"].fillna(0).astype(int) == int(stage_id)]

    target_hours = 0.0
    selected_id = int(selected_line["id"]) if selected_line else None
    selected_found = False
    for _, line in scope.iterrows():
        is_selected = selected_id is not None and int(line["id"]) == selected_id
        metrics = line_production_metrics(
            quantity=work_quantity if is_selected else line["qty"],
            unit_rate=unit_rate if is_selected else line["unit_rate"],
            line_total=None if is_selected else line["line_total"],
            unit=work_unit if is_selected else line["unit"], day_hours=day_hours, value_low=value_low,
            value_target=value_target, value_high=value_high,
        )
        line_hours = float(metrics["labour_hours_at_target"])
        if line_hours <= 0:
            line_hours = float(line["estimated_labour_hours"] or 0)
        target_hours += line_hours
        selected_found = selected_found or is_selected
    if not selected_found:
        target_hours += float(selected_metrics["labour_hours_at_target"])

    if stage_id is None:
        hours = ctx["df_query"](
            """
            SELECT COALESCE(SUM(total_hours),0) AS actual_hours
            FROM timesheet_entries
            WHERE job_id=? AND COALESCE(status,'Submitted') <> 'Rejected'
            """,
            (job_id,),
        )
    else:
        hours = ctx["df_query"](
            """
            SELECT COALESCE(SUM(total_hours),0) AS actual_hours
            FROM timesheet_entries
            WHERE job_id=? AND job_stage_id=?
              AND COALESCE(status,'Submitted') <> 'Rejected'
            """,
            (job_id, stage_id),
        )
    actual_hours = float(hours.iloc[0]["actual_hours"] or 0) if not hours.empty else 0.0
    progress = expected_progress(actual_hours, target_hours)
    expected_quantity = float(work_quantity) * progress["expected_percent"] / 100.0

    st.markdown("#### Required-work check")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric(f"Target {work_unit} / 8h", f"{float(selected_metrics['units_per_day_target']):,.2f}")
    p2.metric(f"{stage_name} Target Hours", f"{target_hours:,.1f}")
    p3.metric("Timesheet Hours Used", f"{actual_hours:,.1f}")
    p4.metric("Should Be Complete", f"{progress['raw_expected_percent']:,.1f}%")
    if target_hours > 0:
        st.info(
            f"Based on ${value_target:,.0f} of completed work per {day_hours:g}-hour painter-day, "
            f"about {expected_quantity:,.2f} of {work_quantity:,.2f} {work_unit} should be complete by now."
        )
    elif unit_rate <= 0:
        st.warning("Enter a unit rate above $0 to calculate the required daily quantity and expected progress.")
    return {
        "target_hours": target_hours,
        "actual_hours": actual_hours,
        "expected_percent": float(progress["raw_expected_percent"]),
    }


def _paint_calculator(ctx: dict[str, Any], job_id: int) -> None:
    st.subheader("Paint quantity and pack optimisation")
    st.caption(
        "Select measured work from this job's take-off, or choose Manual entry. Both "
        "routes calculate coating litres, pack mix and the work progress expected from timesheets."
    )

    takeoff_lines = _takeoff_lines_for_job(ctx, job_id)
    source_options: dict[str, dict[str, Any] | None] = {}
    if not takeoff_lines.empty:
        for _, line in takeoff_lines.iterrows():
            location = str(line["work_location"] or "").strip()
            description = str(line["item_description"] or "").strip()
            label_name = location or description or f"Take-off line {int(line['id'])}"
            source_options[
                f"{label_name} · {float(line['qty'] or 0):,.2f} {line['unit']} · "
                f"{line['stage_name']} (line #{int(line['id'])})"
            ] = line.to_dict()
    source_options["Manual entry"] = None
    selected_source = st.selectbox(
        "Work source",
        list(source_options.keys()),
        key=f"v4_work_source_{job_id}",
        help="Imported take-off lines are listed first. Choose Manual entry to add work not included in the take-off.",
    )
    selected_line = source_options[selected_source]
    source_key = str(int(selected_line["id"])) if selected_line else "manual"

    def clean_text(value: Any, fallback: str = "") -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return fallback
        text = str(value).strip()
        return fallback if text.casefold() == "nan" else text

    stage_options = _job_stage_options(ctx, job_id)
    if len(stage_options) == 1:
        st.info(
            "This job has no named stages yet, so only Whole Job is available. "
            "Add stages under Job Lookup / Links → Stages / POs, then return here."
        )
    original_stage_name = (
        clean_text(selected_line.get("stage_name"), "Whole Job")
        if selected_line else "Whole Job"
    )
    if original_stage_name not in stage_options:
        original_stage_name = "Whole Job"
    selected_stage_name = st.selectbox(
        "Job stage for progress tracking",
        list(stage_options.keys()),
        index=list(stage_options.keys()).index(original_stage_name),
        key=f"v4_stage_{job_id}_{source_key}",
        help=(
            "Choose the stage whose timesheet hours should be compared with this work. "
            "Saving a take-off coating system also saves this stage against its take-off line."
        ),
    )
    selected_stage_id = stage_options[selected_stage_name]
    if selected_line:
        st.caption(
            f"Take-off source: {clean_text(selected_line.get('section'), 'Take-off')} · "
            f"currently assigned to {original_stage_name}. Values can be adjusted before saving."
        )

    left, right = st.columns(2)
    area_name = left.text_input(
        "Area / location",
        value=(
            clean_text(selected_line.get("work_location"))
            or clean_text(selected_line.get("item_description"))
            if selected_line else ""
        ),
        key=f"v4_area_name_{job_id}_{source_key}",
    )
    substrate = left.text_input(
        "Substrate",
        value=clean_text(selected_line.get("substrate")) if selected_line else "",
        key=f"v4_substrate_{job_id}_{source_key}",
    )
    product = left.text_input(
        "Paint product",
        value=(
            clean_text(selected_line.get("coating_system"))
            or clean_text(selected_line.get("item_description"))
            if selected_line else ""
        ),
        key=f"v4_product_{job_id}_{source_key}",
    )
    colour = left.text_input(
        "Colour",
        value=clean_text(selected_line.get("colour_finish")) if selected_line else "",
        key=f"v4_colour_{job_id}_{source_key}",
    )

    measurement_context = " ".join(
        clean_text(selected_line.get(field))
        for field in ("section", "item_description", "work_location")
    ) if selected_line else area_name
    default_measurement_basis = recommended_measurement_basis(
        selected_line.get("unit") if selected_line else "m²",
        stage_name=selected_stage_name,
        context=measurement_context,
    )
    measurement_basis = right.selectbox(
        "Measurement basis",
        list(MEASUREMENT_BASIS_OPTIONS),
        index=list(MEASUREMENT_BASIS_OPTIONS).index(default_measurement_basis),
        key=f"v4_measurement_basis_{job_id}_{source_key}_{selected_stage_id or 'whole'}",
        help=(
            "Internal work normally uses the building's floor m². External work normally "
            "uses the actual painted substrate m². You can override the suggested basis."
        ),
    )
    work_unit = work_unit_for_measurement_basis(measurement_basis)
    default_quantity = float(selected_line.get("qty") or 0) if selected_line else 0.0
    quantity_label = {
        EXTERNAL_SUBSTRATE_AREA: "External substrate area (m²)",
        INTERNAL_FLOOR_AREA: "Internal floor area (m²)",
        "Lineal m": "Length (lineal m)",
        "Item": "Number of items",
    }[measurement_basis]
    work_quantity = float(right.number_input(
        quantity_label,
        min_value=0.0,
        value=default_quantity,
        step=1.0 if measurement_basis == "Item" else 10.0,
        key=f"v4_work_qty_{job_id}_{source_key}",
    ))
    if measurement_basis == EXTERNAL_SUBSTRATE_AREA:
        area_sqm = work_quantity
    elif measurement_basis == INTERNAL_FLOOR_AREA:
        area_sqm = float(right.number_input(
            "Painted substrate area for litre calculation (m²)",
            min_value=0.0,
            value=0.0,
            step=10.0,
            key=f"v4_floor_painted_area_{job_id}_{source_key}",
            help=(
                "Floor area can calculate the work value and target hours, but paint litres "
                "must use the actual combined wall, ceiling and other painted surface area."
            ),
        ))
        right.caption(
            "The floor m² rate drives the $1,000/day progress target; the painted substrate m² drives paint litres."
        )
    elif measurement_basis == "Lineal m":
        painted_width = float(right.number_input(
            "Painted width / height per lineal metre (m)",
            min_value=0.01,
            value=1.0,
            step=0.05,
            key=f"v4_lineal_width_{job_id}_{source_key}",
        ))
        area_sqm = work_quantity * painted_width
        right.caption(f"Converted painted area: {area_sqm:,.2f} m²")
    else:
        area_sqm = float(right.number_input(
            "Painted area for litre calculation (m²)",
            min_value=0.0,
            value=0.0,
            step=10.0,
            key=f"v4_item_area_{job_id}_{source_key}",
        ))
    unit_rate = float(right.number_input(
        f"Sell rate per {work_unit} ex GST",
        min_value=0.0,
        value=float(selected_line.get("unit_rate") or 0) if selected_line else 0.0,
        step=1.0,
        key=f"v4_unit_rate_{job_id}_{source_key}",
        help="Used to convert the $1,000 completed-work target into the required daily quantity.",
    ))
    coats = right.number_input(
        "Coats",
        min_value=1,
        max_value=10,
        value=2,
        step=1,
        key=f"v4_coats_{job_id}_{source_key}",
    )
    coverage = right.number_input(
        "Coverage (m²/L/coat)",
        min_value=0.1,
        value=12.0,
        step=0.5,
        key=f"v4_coverage_{job_id}_{source_key}",
    )
    waste = right.number_input(
        "Waste allowance (%)",
        min_value=0.0,
        max_value=100.0,
        value=10.0,
        step=1.0,
        key=f"v4_waste_{job_id}_{source_key}",
    )

    tracking = _render_required_work_status(
        ctx, job_id, takeoff_lines, selected_line, selected_stage_id,
        selected_stage_name, work_quantity, work_unit, unit_rate,
    )

    st.markdown("#### Warehouse stock and supplier pricing")
    pack_columns = st.columns(3)
    stock: dict[int, int] = {}
    prices: dict[int, float] = {}
    for column, size in zip(pack_columns, (4, 10, 15)):
        with column:
            st.markdown(f"**{size} L**")
            stock[size] = int(
                st.number_input(
                    "Warehouse packs",
                    min_value=0,
                    step=1,
                    key=f"v4_stock_{job_id}_{source_key}_{size}",
                )
            )
            prices[size] = float(
                st.number_input(
                    "Supplier price ex GST",
                    min_value=0.0,
                    step=1.0,
                    key=f"v4_price_{job_id}_{source_key}_{size}",
                )
            )

    calculation_fingerprint = json.dumps(
        {
            "source_key": source_key, "area_name": area_name, "substrate": substrate,
            "product": product, "colour": colour, "area_sqm": area_sqm,
            "work_quantity": work_quantity, "work_unit": work_unit,
            "measurement_basis": measurement_basis, "unit_rate": unit_rate,
            "coats": int(coats), "coverage": float(coverage), "waste": float(waste),
            "stock": stock, "prices": prices, "job_stage_id": selected_stage_id,
        },
        sort_keys=True,
        default=str,
    )

    if st.button(
        "Calculate paint, packs and required work",
        type="primary",
        key=f"v4_calculate_{job_id}_{source_key}",
    ):
        try:
            quantity = calculate_paint_quantity(
                area_sqm=area_sqm,
                coats=coats,
                coverage_sqm_per_litre=coverage,
                waste_percent=waste,
            )
            plan = optimise_pack_mix(
                required_litres=quantity["required_litres"],
                warehouse_stock=stock,
                supplier_prices=prices,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state["v4_last_calculation"] = {
                "job_id": job_id,
                "source_key": source_key,
                "input_fingerprint": calculation_fingerprint,
                "quantity": quantity,
                "plan": plan,
                "form": {
                    "area_name": area_name, "substrate": substrate, "product": product,
                    "colour": colour, "area_sqm": area_sqm, "coats": int(coats),
                    "coverage": float(coverage), "waste": float(waste),
                    "work_quantity": work_quantity, "work_unit": work_unit,
                    "measurement_basis": measurement_basis,
                    "unit_rate": unit_rate, "job_stage_id": selected_stage_id,
                    "stage_name": selected_stage_name,
                    "source_line_id": int(selected_line["id"]) if selected_line else None,
                    "estimate_id": int(selected_line["estimate_id"]) if selected_line else None,
                    "tracking": tracking,
                },
            }

    calculation = st.session_state.get("v4_last_calculation")
    if (
        not calculation
        or int(calculation.get("job_id") or 0) != int(job_id)
        or str(calculation.get("source_key") or "") != source_key
        or calculation.get("input_fingerprint") != calculation_fingerprint
    ):
        return
    quantity = calculation["quantity"]
    plan = calculation["plan"]
    saved_form = calculation["form"]
    metric_columns = st.columns(4)
    metric_columns[0].metric("Required", f"{quantity['required_litres']:.2f} L")
    metric_columns[1].metric("Supplied", f"{plan['supplied_litres']:.0f} L")
    metric_columns[2].metric("Excess", f"{plan['excess_litres']:.2f} L")
    metric_columns[3].metric("Purchase ex GST", f"${plan['purchase_cost']:,.2f}")
    st.dataframe(pd.DataFrame(plan["lines"]), hide_index=True, width="stretch")

    approval = ctx["df_query"](
        """
        SELECT status, approved_by, approved_at
        FROM colour_approvals
        WHERE job_id = ? AND LOWER(colour_name) = LOWER(?)
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (job_id, saved_form["colour"]),
    )
    if approval.empty:
        allowed, reason = False, "No colour approval is recorded for this colour."
    else:
        row = approval.iloc[0].to_dict()
        allowed, reason = colour_order_allowed(
            row.get("status", ""),
            approved_by=row.get("approved_by", ""),
            approved_at=row.get("approved_at", ""),
        )
    if allowed:
        st.success("Colour gate passed. The material order may be prepared.")
    else:
        st.warning(reason)

    add_manual_to_tracking = False
    if saved_form.get("source_line_id") is None:
        add_manual_to_tracking = st.checkbox(
            "Add this manual work to the job's estimate / take-off tracking",
            value=True,
            key=f"v4_add_manual_tracking_{job_id}_{source_key}",
            help="The item will appear in this dropdown next time and continue comparing timesheet hours with required work.",
        )

    if st.button("Save coating system", key=f"v4_save_system_{job_id}_{source_key}"):
        if not str(saved_form["area_name"]).strip() or not str(saved_form["product"]).strip():
            st.error("Area / location and paint product are required.")
            return
        timestamp = _now()
        system_id = str(uuid4())
        ctx["execute"](
            """
            INSERT INTO paint_systems
            (id, job_id, area_name, substrate, product_name, colour_name,
             area_sqm, coat_count, coverage_sqm_per_litre, waste_percent,
             required_litres, pack_plan_json, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id,
                job_id,
                str(saved_form["area_name"]).strip(),
                str(saved_form["substrate"]).strip(),
                str(saved_form["product"]).strip(),
                str(saved_form["colour"]).strip(),
                float(saved_form["area_sqm"]),
                int(saved_form["coats"]),
                float(saved_form["coverage"]),
                float(saved_form["waste"]),
                quantity["required_litres"],
                json.dumps(plan),
                _current_user_name(ctx),
                timestamp,
                timestamp,
            ),
        )
        if saved_form.get("source_line_id") is not None:
            ctx["execute"](
                """
                UPDATE estimate_line_items
                SET job_stage_id=?, unit=?
                WHERE id=? AND estimate_id=?
                """,
                (
                    saved_form.get("job_stage_id"), saved_form["work_unit"],
                    int(saved_form["source_line_id"]), int(saved_form["estimate_id"]),
                ),
            )
        ctx["record_audit_event"](
            "paint_system_created",
            "paint_system",
            system_id,
            {
                "job_id": job_id,
                "required_litres": quantity["required_litres"],
                "source_line_id": saved_form.get("source_line_id"),
                "job_stage_id": saved_form.get("job_stage_id"),
                "measurement_basis": saved_form.get("measurement_basis"),
                "expected_progress_percent": saved_form.get("tracking", {}).get("expected_percent", 0),
            },
        )
        if add_manual_to_tracking and saved_form.get("source_line_id") is None:
            estimate_id = saved_form.get("estimate_id")
            if estimate_id is None:
                latest = ctx["df_query"](
                    """
                    SELECT id FROM estimate_working_sheets
                    WHERE job_id=? AND COALESCE(archived,0)=0
                    ORDER BY id DESC LIMIT 1
                    """,
                    (job_id,),
                )
                estimate_id = int(latest.iloc[0]["id"]) if not latest.empty else None
            if estimate_id is None:
                job_row = ctx["df_query"]("SELECT job_no FROM jobs WHERE id=?", (job_id,))
                job_no = str(job_row.iloc[0]["job_no"] or f"JOB-{job_id}") if not job_row.empty else f"JOB-{job_id}"
                conn = ctx["connect"]()
                try:
                    cur = conn.cursor()
                    insert_sql = """
                        INSERT INTO estimate_working_sheets
                        (job_id,estimate_no,estimate_date,revision,status,created_at,updated_at,notes)
                        VALUES (?,?,?,?,?,?,?,?)
                    """
                    if ctx.get("USE_POSTGRES"):
                        insert_sql += " RETURNING id"
                    cur.execute(insert_sql, (
                        job_id, f"{job_no}-PI-01", jobhub_now().date().isoformat(),
                        "Painting Intelligence", "Draft", timestamp, timestamp,
                        "Created from a manual Painting Intelligence work item.",
                    ))
                    estimate_id = int(cur.fetchone()[0]) if ctx.get("USE_POSTGRES") else int(cur.lastrowid)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
            target_metrics = line_production_metrics(
                quantity=saved_form["work_quantity"], unit_rate=saved_form["unit_rate"],
                unit=saved_form["work_unit"],
            )
            ctx["execute"](
                """
                INSERT INTO estimate_line_items
                (estimate_id,job_stage_id,production_tracking_enabled,section,item_description,
                 qty,unit,unit_rate,line_total,estimated_labour_hours,substrate,work_location,
                 coating_system,colour_finish,source_pack,notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(estimate_id), saved_form.get("job_stage_id"), 1, "Painting Intelligence",
                    f"{saved_form['area_name']} — {saved_form['product']}",
                    float(saved_form["work_quantity"]), saved_form["work_unit"],
                    float(saved_form["unit_rate"]),
                    round(float(saved_form["work_quantity"]) * float(saved_form["unit_rate"]), 2),
                    float(target_metrics["labour_hours_at_target"]), saved_form["substrate"],
                    saved_form["area_name"], saved_form["product"], saved_form["colour"],
                    "Manual Painting Intelligence", "Saved from Paint & packs.",
                ),
            )
            recalc = ctx.get("recalc_estimate_totals")
            if recalc:
                recalc(int(estimate_id))
        ctx["pb_success"]("Coating system and pack plan saved.")
        ctx["pb_rerun"]()


def _colour_approvals(ctx: dict[str, Any], job_id: int) -> None:
    st.subheader("Colour approval gate")
    st.caption("Orders remain blocked until an approved colour, approver and date are recorded.")
    with st.form("v4_colour_approval_form", clear_on_submit=True):
        area_name = st.text_input("Area / location")
        colour_name = st.text_input("Colour name")
        colour_code = st.text_input("Colour code")
        product_name = st.text_input("Product")
        status = st.selectbox("Status", ["pending", "requested", "approved", "rejected"])
        approval_reference = st.text_input("Approval reference")
        notes = st.text_area("Notes")
        submitted = st.form_submit_button("Save colour approval", type="primary")
    if submitted:
        if not area_name.strip() or not colour_name.strip():
            st.error("Area / location and colour name are required.")
        else:
            timestamp = _now()
            user_name = _current_user_name(ctx)
            approval_id = str(uuid4())
            approved_by = user_name if status == "approved" else ""
            approved_at = timestamp if status == "approved" else ""
            ctx["execute"](
                """
                INSERT INTO colour_approvals
                (id, job_id, area_name, colour_name, colour_code, product_name,
                 status, requested_by, requested_at, approved_by, approved_at,
                 approval_reference, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    job_id,
                    area_name.strip(),
                    colour_name.strip(),
                    colour_code.strip(),
                    product_name.strip(),
                    status,
                    user_name,
                    timestamp,
                    approved_by,
                    approved_at,
                    approval_reference.strip(),
                    notes.strip(),
                    timestamp,
                    timestamp,
                ),
            )
            ctx["record_audit_event"](
                "colour_approval_recorded",
                "colour_approval",
                approval_id,
                {"job_id": job_id, "status": status},
            )
            ctx["pb_success"]("Colour approval saved.")
            ctx["pb_rerun"]()

    approvals = ctx["df_query"](
        """
        SELECT area_name AS "Area", colour_name AS "Colour",
               colour_code AS "Code", product_name AS "Product",
               status AS "Status", approved_by AS "Approved by",
               approved_at AS "Approved at", approval_reference AS "Reference"
        FROM colour_approvals
        WHERE job_id = ?
        ORDER BY updated_at DESC
        """,
        (job_id,),
    )
    st.dataframe(approvals, hide_index=True, width="stretch")


def _plan_evidence(ctx: dict[str, Any], job_id: int) -> None:
    st.subheader("Plan-linked evidence")
    st.caption("Link progress, defect and close-out evidence to a plan and revision reference.")
    with st.form("v4_evidence_form", clear_on_submit=True):
        plan_reference = st.text_input("Plan / specification reference")
        revision = st.text_input("Revision")
        location = st.text_input("Location / grid reference")
        evidence_type = st.selectbox(
            "Evidence type",
            [
                "progress_photo",
                "completion_photo",
                "defect",
                "defect_closeout",
                "warranty",
                "other",
            ],
        )
        title = st.text_input("Title")
        description = st.text_area("Description")
        photo_id = st.number_input("Existing JobHub photo ID (optional)", min_value=0, step=1)
        document_id = st.number_input(
            "Existing JobHub document ID (optional)",
            min_value=0,
            step=1,
        )
        submitted = st.form_submit_button("Link evidence", type="primary")
    if submitted:
        if not plan_reference.strip() or not title.strip():
            st.error("Plan reference and title are required.")
        elif not int(photo_id) and not int(document_id) and evidence_type not in {"warranty", "other"}:
            st.error("Select an existing photo or document for this evidence.")
        else:
            evidence_id = str(uuid4())
            timestamp = _now()
            ctx["execute"](
                """
                INSERT INTO plan_evidence
                (id, job_id, plan_reference, revision, location_reference,
                 evidence_type, title, description, photo_id, document_id,
                 status, captured_by, captured_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    evidence_id,
                    job_id,
                    plan_reference.strip(),
                    revision.strip(),
                    location.strip(),
                    evidence_type,
                    title.strip(),
                    description.strip(),
                    int(photo_id) or None,
                    int(document_id) or None,
                    _current_user_name(ctx),
                    timestamp,
                    timestamp,
                ),
            )
            ctx["record_audit_event"](
                "plan_evidence_linked",
                "plan_evidence",
                evidence_id,
                {"job_id": job_id, "plan_reference": plan_reference.strip()},
            )
            ctx["pb_success"]("Evidence linked to the plan reference.")
            ctx["pb_rerun"]()

    evidence = ctx["df_query"](
        """
        SELECT plan_reference AS "Plan", revision AS "Revision",
               location_reference AS "Location", evidence_type AS "Type",
               title AS "Title", photo_id AS "Photo ID",
               document_id AS "Document ID", captured_by AS "Captured by",
               captured_at AS "Captured"
        FROM plan_evidence
        WHERE job_id = ? AND status <> 'void'
        ORDER BY created_at DESC
        """,
        (job_id,),
    )
    st.dataframe(evidence, hide_index=True, width="stretch")


def _revision_compare(ctx: dict[str, Any], job_id: int) -> None:
    st.subheader("Revision comparison and draft variation detection")
    document_name = st.text_input("Drawing / specification name", key="v4_revision_document")
    previous_revision = st.text_input("Previous revision", key="v4_previous_revision")
    current_revision = st.text_input("Current revision", key="v4_current_revision")
    previous_text = st.text_area(
        "Previous extracted text",
        height=180,
        key="v4_previous_text",
    )
    current_text = st.text_area(
        "Current extracted text",
        height=180,
        key="v4_current_text",
    )
    if not st.button("Compare revisions", type="primary", key="v4_compare"):
        return
    try:
        comparison = compare_revisions(
            previous_text,
            current_text,
            previous_label=previous_revision or "previous",
            current_label=current_revision or "current",
        )
    except ValueError as exc:
        st.error(str(exc))
        return

    st.session_state["v4_last_revision_comparison"] = comparison
    metrics = st.columns(3)
    metrics[0].metric("Similarity", f"{comparison['similarity_percent']:.1f}%")
    metrics[1].metric("Added lines", len(comparison["added_lines"]))
    metrics[2].metric("Variation risk", f"{comparison['variation_risk_score']}/100")
    if comparison["likely_variation"]:
        st.warning("Likely scope change detected. A draft variation will be created for review.")
    else:
        st.info("No strong variation signal detected. Review the diff before closing.")
    st.code(comparison["diff"] or "No textual changes.", language="diff")

    timestamp = _now()
    revision_id = str(uuid4())
    ctx["execute"](
        """
        INSERT INTO drawing_revisions
        (id, job_id, document_name, previous_revision, current_revision,
         similarity_percent, variation_risk_score, comparison_json,
         compared_by, compared_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            revision_id,
            job_id,
            document_name.strip() or "Drawing / specification",
            previous_revision.strip(),
            current_revision.strip(),
            comparison["similarity_percent"],
            comparison["variation_risk_score"],
            json.dumps(comparison),
            _current_user_name(ctx),
            timestamp,
        ),
    )
    if comparison["likely_variation"]:
        suggestion_id = str(uuid4())
        ctx["execute"](
            """
            INSERT INTO variation_suggestions
            (id, job_id, drawing_revision_id, title, reason, risk_score,
             status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?)
            """,
            (
                suggestion_id,
                job_id,
                revision_id,
                f"Review scope change — {document_name.strip() or current_revision or 'revision'}",
                "; ".join(comparison["added_lines"][:8])
                or "Revision comparison identified a likely scope change.",
                comparison["variation_risk_score"],
                _current_user_name(ctx),
                timestamp,
                timestamp,
            ),
        )
        ctx["create_management_notifications"](
            event_type="draft_variation_detected",
            title="Drawing revision may require a variation",
            message=f"{document_name or 'A drawing/specification'} has a risk score of {comparison['variation_risk_score']}/100.",
            job_id=job_id,
            entity_type="variation_suggestion",
            entity_id=suggestion_id,
        )
    ctx["record_audit_event"](
        "drawing_revisions_compared",
        "drawing_revision",
        revision_id,
        {"job_id": job_id, "risk_score": comparison["variation_risk_score"]},
    )


def _handover(ctx: dict[str, Any], job_id: int, job: dict[str, Any]) -> None:
    st.subheader("Builder close-out and handover pack")
    evidence = ctx["df_query"](
        """
        SELECT plan_reference, revision, location_reference, evidence_type,
               title, description, photo_id, document_id, status,
               captured_by, captured_at
        FROM plan_evidence
        WHERE job_id = ? AND status <> 'void'
        ORDER BY created_at
        """,
        (job_id,),
    )
    colours = ctx["df_query"](
        """
        SELECT area_name, colour_name, colour_code, product_name, status,
               approved_by, approved_at, approval_reference, notes
        FROM colour_approvals
        WHERE job_id = ?
        ORDER BY created_at
        """,
        (job_id,),
    )
    manifest = build_handover_manifest(
        job=job,
        evidence=_records(evidence),
        colour_approvals=_records(colours),
    )
    completed = sum(1 for item in manifest["checklist"] if item["complete"])
    st.progress(completed / max(1, len(manifest["checklist"])))
    st.write(f"{completed} of {len(manifest['checklist'])} close-out requirements complete.")
    checklist_frame = pd.DataFrame(manifest["checklist"]).rename(
        columns={"label": "Requirement", "complete": "Complete"}
    )
    st.dataframe(checklist_frame[["Requirement", "Complete"]], hide_index=True)
    if manifest["ready"]:
        st.success("The handover evidence checklist is complete.")
    else:
        st.warning("Missing: " + ", ".join(manifest["missing_requirements"]))

    if st.button("Generate handover pack", type="primary", key="v4_handover"):
        pack_id = str(uuid4())
        timestamp = _now()
        manifest["generated_at"] = timestamp
        ctx["execute"](
            """
            INSERT INTO handover_packs
            (id, job_id, status, manifest_json, generated_by, generated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                pack_id,
                job_id,
                "ready" if manifest["ready"] else "draft",
                json.dumps(manifest),
                _current_user_name(ctx),
                timestamp,
            ),
        )
        ctx["record_audit_event"](
            "handover_pack_generated",
            "handover_pack",
            pack_id,
            {"job_id": job_id, "ready": manifest["ready"]},
        )
        st.session_state["v4_handover_download"] = {
            "name": f"{job.get('job_no', 'job')}_handover_pack.zip",
            "data": build_handover_zip(manifest),
        }

    download = st.session_state.get("v4_handover_download")
    if download:
        st.download_button(
            "Download handover pack",
            data=download["data"],
            file_name=download["name"],
            mime="application/zip",
            type="primary",
        )


def render_painting_intelligence(ctx: dict[str, Any]) -> None:
    st.header("Painting Intelligence")
    st.caption(
        "Coating quantities, pack optimisation, colour gates, plan evidence, "
        "revision risk and builder handover."
    )
    ensure_v4_schema(ctx["connect"])
    job_id, job = _job_picker(ctx)
    if not job_id:
        return
    tabs = st.tabs(
        [
            "Paint & packs",
            "Colour approvals",
            "Plan evidence",
            "Revision compare",
            "Handover",
        ]
    )
    with tabs[0]:
        _paint_calculator(ctx, job_id)
    with tabs[1]:
        _colour_approvals(ctx, job_id)
    with tabs[2]:
        _plan_evidence(ctx, job_id)
    with tabs[3]:
        _revision_compare(ctx, job_id)
    with tabs[4]:
        _handover(ctx, job_id, job)
