"""Premier Brushworks JobHub enterprise operations module.

This module keeps new operational workflows out of the already-large main
Streamlit file.  It adds restart-safe database tables and user interfaces for:

* live forecast-to-complete job control
* purchase orders and supplier-invoice matching
* mobile field clocking, photos and timesheet submission
* digital pre-start, safety, quality and completion forms
* audit/error visibility, backups and Xero-ready exports

The module deliberately accepts a context dictionary from pb_jobhub_app.py so
it can reuse JobHub's existing database adapters, permissions, notification
system and storage rules without importing the main app (which would create a
circular import).
"""

from __future__ import annotations

import json
import re
import traceback
import zipfile
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import streamlit as st

from jobhub_production import remaining_contract_labour

ENTERPRISE_BUILD = "2026.07.27-enterprise-foundation-v1"
PLANNING_LABOUR_RATE = 60.0


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return date.today().isoformat()


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "" or pd.isna(value):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _money(value: Any) -> str:
    return f"${_f(value):,.2f}"


def _clean(value: Any, max_len: int = 500) -> str:
    return str(value or "").strip()[:max_len]


def _slug(value: Any, fallback: str = "record") -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "_", _clean(value, 120)).strip("._")
    return result or fallback


def _user(ctx: dict[str, Any]) -> dict[str, Any]:
    user = ctx["get_current_user"]() or {}
    return dict(user)


def _role(ctx: dict[str, Any]) -> str:
    return str(_user(ctx).get("role", "")).lower()


def _management(ctx: dict[str, Any]) -> bool:
    return _role(ctx) in {"admin", "manager"}


def _query(ctx: dict[str, Any], sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    return ctx["df_query"](sql, params)


def _execute(ctx: dict[str, Any], sql: str, params: tuple[Any, ...] = ()) -> Any:
    return ctx["execute"](sql, params)


def _notify_management(
    ctx: dict[str, Any],
    event_type: str,
    title: str,
    message: str,
    job_id: int | None = None,
    entity_type: str = "",
    entity_id: Any = "",
) -> None:
    notify = ctx.get("create_management_notifications")
    if callable(notify):
        try:
            notify(
                event_type=event_type,
                title=title,
                message=message,
                job_id=job_id,
                entity_type=entity_type,
                entity_id=entity_id,
            )
        except TypeError:
            # Compatibility with an earlier positional signature.
            notify(event_type, title, message, job_id, entity_type, entity_id)


def _audit(
    ctx: dict[str, Any], action: str, entity_type: str, entity_id: Any = "", details: Any = None
) -> None:
    recorder = ctx.get("record_audit_event")
    if callable(recorder):
        try:
            recorder(action, entity_type, entity_id, details or {})
        except TypeError:
            recorder(action, entity_type, entity_id)


def ensure_enterprise_schema(connect: Callable[[], Any]) -> bool:
    """Create enterprise tables and indexes in SQLite or PostgreSQL.

    JobHub's PostgreSQL adapter converts SQLite AUTOINCREMENT and question-mark
    placeholders, so the DDL remains portable across local testing and Render.
    """
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_error_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                area TEXT,
                error_type TEXT,
                message TEXT,
                traceback_text TEXT,
                context_json TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by TEXT,
                resolution_notes TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS field_clock_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                job_id INTEGER NOT NULL,
                clock_in TEXT NOT NULL,
                clock_out TEXT,
                break_minutes REAL DEFAULT 0,
                travel_minutes REAL DEFAULT 0,
                total_hours REAL DEFAULT 0,
                work_type TEXT DEFAULT 'Painting',
                notes TEXT,
                submitted_timesheet_id INTEGER,
                status TEXT DEFAULT 'Active',
                created_by TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(employee_id) REFERENCES employees(id),
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(submitted_timesheet_id) REFERENCES timesheet_entries(id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS job_progress_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                snapshot_date TEXT NOT NULL,
                physical_progress_percent REAL DEFAULT 0,
                forecast_remaining_labour_hours REAL DEFAULT 0,
                forecast_completion_date TEXT,
                notes TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS purchase_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                po_no TEXT UNIQUE NOT NULL,
                job_id INTEGER NOT NULL,
                supplier TEXT NOT NULL,
                status TEXT DEFAULT 'Requested',
                order_date TEXT,
                expected_date TEXT,
                requested_by TEXT,
                approved_by TEXT,
                approved_at TEXT,
                subtotal_ex_gst REAL DEFAULT 0,
                gst_amount REAL DEFAULT 0,
                total_inc_gst REAL DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS purchase_order_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                purchase_order_id INTEGER NOT NULL,
                material_entry_id INTEGER,
                product_id INTEGER,
                product_code TEXT,
                description TEXT NOT NULL,
                colour TEXT,
                qty REAL DEFAULT 0,
                unit TEXT,
                unit_price_ex_gst REAL DEFAULT 0,
                line_total_ex_gst REAL DEFAULT 0,
                received_qty REAL DEFAULT 0,
                notes TEXT,
                FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id),
                FOREIGN KEY(material_entry_id) REFERENCES material_entries(id),
                FOREIGN KEY(product_id) REFERENCES products(id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS supplier_invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_no TEXT NOT NULL,
                supplier TEXT NOT NULL,
                job_id INTEGER NOT NULL,
                purchase_order_id INTEGER,
                invoice_date TEXT,
                due_date TEXT,
                status TEXT DEFAULT 'Received',
                subtotal_ex_gst REAL DEFAULT 0,
                gst_amount REAL DEFAULT 0,
                total_inc_gst REAL DEFAULT 0,
                variance_ex_gst REAL DEFAULT 0,
                file_path TEXT,
                notes TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(supplier, invoice_no),
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS supplier_invoice_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplier_invoice_id INTEGER NOT NULL,
                matched_po_line_id INTEGER,
                product_code TEXT,
                description TEXT NOT NULL,
                qty REAL DEFAULT 0,
                unit_price_ex_gst REAL DEFAULT 0,
                line_total_ex_gst REAL DEFAULT 0,
                po_line_total_ex_gst REAL DEFAULT 0,
                variance_ex_gst REAL DEFAULT 0,
                notes TEXT,
                FOREIGN KEY(supplier_invoice_id) REFERENCES supplier_invoices(id),
                FOREIGN KEY(matched_po_line_id) REFERENCES purchase_order_lines(id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS field_forms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                employee_id INTEGER,
                form_type TEXT NOT NULL,
                form_date TEXT NOT NULL,
                status TEXT DEFAULT 'Submitted',
                answers_json TEXT,
                signature_name TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                approved_by TEXT,
                approved_at TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id),
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS backup_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                backup_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                size_bytes INTEGER DEFAULT 0,
                status TEXT DEFAULT 'Completed',
                created_by TEXT,
                created_at TEXT NOT NULL,
                notes TEXT
            )
            """
        )

        for statement in [
            "CREATE INDEX IF NOT EXISTS idx_error_events_created ON app_error_events(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_clock_employee_status ON field_clock_entries(employee_id, status, clock_in)",
            "CREATE INDEX IF NOT EXISTS idx_clock_job ON field_clock_entries(job_id, clock_in)",
            "CREATE INDEX IF NOT EXISTS idx_progress_job_date ON job_progress_snapshots(job_id, snapshot_date)",
            "CREATE INDEX IF NOT EXISTS idx_po_job_status ON purchase_orders(job_id, status, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_po_supplier_status ON purchase_orders(supplier, status)",
            "CREATE INDEX IF NOT EXISTS idx_po_lines_po ON purchase_order_lines(purchase_order_id)",
            "CREATE INDEX IF NOT EXISTS idx_supplier_invoice_job ON supplier_invoices(job_id, invoice_date)",
            "CREATE INDEX IF NOT EXISTS idx_supplier_invoice_po ON supplier_invoices(purchase_order_id)",
            "CREATE INDEX IF NOT EXISTS idx_field_forms_job_date ON field_forms(job_id, form_date)",
            "CREATE INDEX IF NOT EXISTS idx_backup_runs_created ON backup_runs(created_at)",
        ]:
            cur.execute(statement)

        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def log_error(
    connect: Callable[[], Any],
    username: str,
    area: str,
    exc: BaseException,
    context: dict[str, Any] | None = None,
) -> None:
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO app_error_events
            (username, area, error_type, message, traceback_text, context_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _clean(username, 120),
                _clean(area, 160),
                type(exc).__name__,
                _clean(exc, 2000),
                traceback.format_exc()[-12000:],
                json.dumps(context or {}, default=str, sort_keys=True)[:12000],
                _now(),
            ),
        )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def _job_options(ctx: dict[str, Any], include_closed: bool = True) -> dict[str, int]:
    where = "" if include_closed else "WHERE COALESCE(j.status, '') NOT IN ('Complete', 'Completed', 'Archived', 'Cancelled')"
    df = _query(
        ctx,
        f"""
        SELECT j.id, j.job_no, j.job_name, COALESCE(b.name, '') AS builder_name
        FROM jobs j
        LEFT JOIN builders_clients b ON b.id = j.builder_client_id
        {where}
        ORDER BY j.job_no
        """,
    )
    return {
        f"{_clean(row['job_no'])} — {_clean(row['job_name'])}"
        + (f" ({_clean(row['builder_name'])})" if _clean(row["builder_name"]) else ""): int(row["id"])
        for _, row in df.iterrows()
    }


def _employee_options(ctx: dict[str, Any]) -> dict[str, int]:
    df = _query(
        ctx,
        """
        SELECT id, name
        FROM employees
        WHERE COALESCE(status, 'Active') <> 'Inactive'
        ORDER BY name
        """,
    )
    return {_clean(row["name"]): int(row["id"]) for _, row in df.iterrows()}


def _latest_progress(ctx: dict[str, Any]) -> pd.DataFrame:
    return _query(
        ctx,
        """
        SELECT p.*
        FROM job_progress_snapshots p
        JOIN (
            SELECT job_id, MAX(id) AS max_id
            FROM job_progress_snapshots
            GROUP BY job_id
        ) latest ON latest.max_id = p.id
        """,
    )


def enterprise_job_cost_dataframe(ctx: dict[str, Any]) -> pd.DataFrame:
    """Return a connected forecast-to-complete view for every job."""
    jobs = _query(
        ctx,
        """
        SELECT j.id AS job_id, j.job_no AS "Job No", j.job_name AS "Job Name",
               COALESCE(b.name, '') AS "Builder / Client", COALESCE(j.status, '') AS "Status",
               COALESCE(j.leading_hand, '') AS "Leading Hand",
               COALESCE(j.contract_value, 0) AS "Original Contract",
               j.start_date AS "Start Date", j.end_date AS "End Date"
        FROM jobs j
        LEFT JOIN builders_clients b ON b.id = j.builder_client_id
        ORDER BY j.job_no
        """,
    )
    if jobs.empty:
        return jobs

    variations = _query(
        ctx,
        """
        SELECT job_id,
               COALESCE(SUM(CASE WHEN status = 'Approved' THEN amount_ex_gst ELSE 0 END), 0) AS approved_variations,
               COALESCE(SUM(CASE WHEN status IN ('Draft', 'Sent', 'Pending') THEN amount_ex_gst ELSE 0 END), 0) AS pending_variations
        FROM job_variations
        GROUP BY job_id
        """,
    )
    claims = _query(
        ctx,
        """
        SELECT job_id,
               COALESCE(SUM(CASE WHEN status NOT IN ('Draft', 'Cancelled') THEN amount_ex_gst ELSE 0 END), 0) AS claimed,
               COALESCE(SUM(CASE WHEN status = 'Paid' OR COALESCE(paid_date, '') <> '' THEN amount_ex_gst ELSE 0 END), 0) AS paid
        FROM invoice_claims
        GROUP BY job_id
        """,
    )
    budget = _query(
        ctx,
        """
        SELECT job_id,
               COALESCE(quoted_labour_hours, 0) AS budget_labour_hours,
               COALESCE(quoted_labour_cost, 0) AS budget_labour_cost,
               COALESCE(quoted_materials, 0) AS budget_materials,
               COALESCE(quoted_access_equipment, 0) AS budget_access,
               COALESCE(quoted_subcontractors, 0) AS budget_subcontractors,
               COALESCE(quoted_sundries, 0) AS budget_sundries,
               COALESCE(target_gp_percent, 35) AS target_gp
        FROM job_budgets
        """,
    )
    estimates = _query(
        ctx,
        """
        SELECT e.job_id,
               COALESCE(e.labour_hours, 0) AS estimate_labour_hours,
               COALESCE(e.labour_hours, 0) * 60.0 AS estimate_labour_cost,
               COALESCE(e.material_allowance, 0) AS estimate_materials,
               COALESCE(e.access_equipment_allowance, 0) AS estimate_access,
               COALESCE(e.subcontractor_allowance, 0) AS estimate_subcontractors,
               COALESCE(e.sundries_allowance, 0) AS estimate_sundries
        FROM estimate_working_sheets e
        JOIN (
            SELECT job_id, MAX(id) AS max_id
            FROM estimate_working_sheets
            WHERE COALESCE(archived, 0) = 0
            GROUP BY job_id
        ) latest ON latest.max_id = e.id
        """,
    )
    labour = _query(
        ctx,
        """
        SELECT w.job_id,
               COALESCE(SUM(w.hours), 0) AS actual_labour_hours,
               COALESCE(SUM(COALESCE(w.hours, 0) * COALESCE(NULLIF(w.hourly_rate_snapshot, 0), e.rate_plus_10, e.base_hourly_rate, 0)), 0) AS actual_labour_cost
        FROM wage_entries w
        LEFT JOIN employees e ON e.id = w.employee_id
        GROUP BY w.job_id
        """,
    )
    materials = _query(
        ctx,
        """
        SELECT m.job_id,
               COALESCE(SUM(COALESCE(m.qty_required, 0) * COALESCE(m.custom_unit_price, p.price_ex_gst, 0)), 0) AS committed_material_cost,
               COALESCE(SUM(COALESCE(m.qty_received, 0) * COALESCE(m.custom_unit_price, p.price_ex_gst, 0)), 0) AS received_material_cost
        FROM material_entries m
        LEFT JOIN products p ON p.id = m.product_id
        GROUP BY m.job_id
        """,
    )
    po = _query(
        ctx,
        """
        SELECT job_id,
               COALESCE(SUM(CASE WHEN status NOT IN ('Cancelled', 'Rejected') THEN subtotal_ex_gst ELSE 0 END), 0) AS po_committed,
               COALESCE(SUM(CASE WHEN status IN ('Approved', 'Ordered', 'Part Received', 'Received', 'Closed') THEN subtotal_ex_gst ELSE 0 END), 0) AS po_approved
        FROM purchase_orders
        GROUP BY job_id
        """,
    )
    supplier_invoices = _query(
        ctx,
        """
        SELECT job_id,
               COALESCE(SUM(CASE WHEN status NOT IN ('Rejected', 'Cancelled') THEN subtotal_ex_gst ELSE 0 END), 0) AS supplier_invoiced
        FROM supplier_invoices
        GROUP BY job_id
        """,
    )
    progress = _latest_progress(ctx)
    if not progress.empty:
        progress = progress.rename(
            columns={
                "physical_progress_percent": "physical_progress",
                "forecast_remaining_labour_hours": "manual_remaining_hours",
                "forecast_completion_date": "manual_finish_date",
            }
        )
        progress = progress[["job_id", "physical_progress", "manual_remaining_hours", "manual_finish_date", "notes"]]

    result = jobs.copy()
    for frame in (variations, claims, budget, estimates, labour, materials, po, supplier_invoices, progress):
        if frame is not None and not frame.empty:
            result = result.merge(frame, on="job_id", how="left")

    numeric = [
        "Original Contract", "approved_variations", "pending_variations", "claimed", "paid",
        "budget_labour_hours", "budget_labour_cost", "budget_materials", "budget_access",
        "budget_subcontractors", "budget_sundries", "target_gp", "estimate_labour_hours",
        "estimate_labour_cost", "estimate_materials", "estimate_access", "estimate_subcontractors",
        "estimate_sundries", "actual_labour_hours", "actual_labour_cost", "committed_material_cost",
        "received_material_cost", "po_committed", "po_approved", "supplier_invoiced",
        "physical_progress", "manual_remaining_hours",
    ]
    for col in numeric:
        if col not in result:
            result[col] = 0.0
        result[col] = pd.to_numeric(result[col], errors="coerce").fillna(0.0)

    result["Revised Contract"] = result["Original Contract"] + result["approved_variations"]
    result["Budget Labour Hours"] = result["budget_labour_hours"].where(
        result["budget_labour_hours"] > 0, result["estimate_labour_hours"]
    )
    result["Budget Labour Cost"] = result["Budget Labour Hours"] * PLANNING_LABOUR_RATE
    result["Budget Materials"] = result["budget_materials"].where(
        result["budget_materials"] > 0, result["estimate_materials"]
    )
    result["Budget Access"] = result["budget_access"].where(
        result["budget_access"] > 0, result["estimate_access"]
    )
    result["Budget Subcontractors"] = result["budget_subcontractors"].where(
        result["budget_subcontractors"] > 0, result["estimate_subcontractors"]
    )
    result["Budget Sundries"] = result["budget_sundries"].where(
        result["budget_sundries"] > 0, result["estimate_sundries"]
    )
    result["Budget Direct Cost"] = (
        result["Budget Labour Cost"] + result["Budget Materials"] + result["Budget Access"]
        + result["Budget Subcontractors"] + result["Budget Sundries"]
    )
    result["Actual Material Cost"] = result[["received_material_cost", "supplier_invoiced"]].max(axis=1)
    result["Material Commitment"] = result[[
        "Budget Materials", "committed_material_cost", "received_material_cost",
        "po_committed", "po_approved", "supplier_invoiced",
    ]].max(axis=1)
    result["Cost to Date"] = result["actual_labour_cost"] + result["Actual Material Cost"]

    progress_fraction = (result["physical_progress"] / 100).clip(lower=0, upper=1)
    contract_labour = result.apply(
        lambda row: remaining_contract_labour(
            contract_value=row["Revised Contract"],
            actual_labour_hours=row["actual_labour_hours"],
            material_commitment=row["Material Commitment"],
            access_allowance=row["Budget Access"],
            subcontractor_allowance=row["Budget Subcontractors"],
            sundries_allowance=row["Budget Sundries"],
        ),
        axis=1,
    )
    result["Contract Labour Hours"] = contract_labour.map(lambda item: item["allowed_labour_hours"])
    result["Contract Labour Work Value"] = contract_labour.map(lambda item: item["labour_work_value"])
    result["Used Labour Work Value"] = contract_labour.map(lambda item: item["used_labour_work_value"])
    result["Remaining Labour Work Value"] = contract_labour.map(lambda item: item["remaining_labour_work_value"])
    result["Forecast Remaining Labour Hours"] = contract_labour.map(lambda item: item["remaining_labour_hours"])
    result["Hours Over Contract Allowance"] = contract_labour.map(lambda item: item["hours_over_allowance"])
    result["Forecast Labour Cost"] = (
        result["actual_labour_cost"]
        + result["Forecast Remaining Labour Hours"] * PLANNING_LABOUR_RATE
    )
    progress_forecast_material = result["Actual Material Cost"] / progress_fraction.replace(0, float("nan"))
    result["Forecast Material Cost"] = progress_forecast_material.fillna(
        result[["Actual Material Cost", "Budget Materials", "po_committed"]].max(axis=1)
    )
    result["Forecast Final Direct Cost"] = (
        result["Forecast Labour Cost"] + result["Forecast Material Cost"]
        + result["Budget Access"] + result["Budget Subcontractors"] + result["Budget Sundries"]
    )
    result["Forecast Profit"] = result["Revised Contract"] - result["Forecast Final Direct Cost"]
    result["Forecast GP %"] = (
        result["Forecast Profit"] / result["Revised Contract"].replace(0, float("nan")) * 100
    ).fillna(0)
    result["Labour Used %"] = (
        result["actual_labour_hours"] / result["Contract Labour Hours"].replace(0, float("nan")) * 100
    ).fillna(0)
    result["Billing %"] = (
        result["claimed"] / result["Revised Contract"].replace(0, float("nan")) * 100
    ).fillna(0)
    result["Cash Collected %"] = (
        result["paid"] / result["Revised Contract"].replace(0, float("nan")) * 100
    ).fillna(0)
    result["Cost Variance"] = result["Budget Direct Cost"] - result["Forecast Final Direct Cost"]

    def risk(row: pd.Series) -> str:
        if _f(row["Revised Contract"]) <= 0:
            return "Needs contract value"
        if _f(row["physical_progress"]) <= 0 and _f(row["actual_labour_hours"]) > 0:
            return "Needs progress update"
        if _f(row["Forecast GP %"]) < 20:
            return "Critical"
        if _f(row["Forecast GP %"]) < _f(row["target_gp"], 35):
            return "Watch"
        if _f(row["Labour Used %"]) > _f(row["physical_progress"]) + 15 and _f(row["physical_progress"]) > 0:
            return "Labour ahead of progress"
        return "On track"

    result["Risk"] = result.apply(risk, axis=1)
    return result


def _save_progress(ctx: dict[str, Any], job_id: int, progress: float, remaining_hours: float, finish: str, notes: str) -> None:
    user = _user(ctx)
    _execute(
        ctx,
        """
        INSERT INTO job_progress_snapshots
        (job_id, snapshot_date, physical_progress_percent, forecast_remaining_labour_hours,
         forecast_completion_date, notes, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, _today(), progress, remaining_hours, finish, notes, user.get("username", ""), _now()),
    )
    _audit(ctx, "job_progress_updated", "job", job_id, {"progress": progress, "remaining_hours": remaining_hours})


def render_job_control(ctx: dict[str, Any]) -> None:
    st.subheader("Live Job Control & Forecast-to-Complete")
    st.caption("Connected contract, variation, labour, materials, purchasing, billing and physical-progress control.")
    df = enterprise_job_cost_dataframe(ctx)
    if df.empty:
        st.info("No jobs are available yet.")
        return

    active = df[~df["Status"].astype(str).str.lower().isin(["complete", "completed", "archived", "cancelled"])]
    total_contract = active["Revised Contract"].sum()
    total_forecast_cost = active["Forecast Final Direct Cost"].sum()
    total_profit = total_contract - total_forecast_cost
    total_gp = total_profit / total_contract * 100 if total_contract else 0
    critical = int(active["Risk"].isin(["Critical", "Needs progress update", "Needs contract value"]).sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active revised contracts", _money(total_contract))
    c2.metric("Forecast final direct cost", _money(total_forecast_cost))
    c3.metric("Forecast gross profit", _money(total_profit), f"{total_gp:.1f}%")
    c4.metric("Jobs needing attention", critical)

    labels = {f"{r['Job No']} — {r['Job Name']}": int(r["job_id"]) for _, r in df.iterrows()}
    selected_label = st.selectbox("Review job", list(labels), key="enterprise_job_control_job")
    job_id = labels[selected_label]
    row = df[df["job_id"].astype(int) == job_id].iloc[0]

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Revised contract", _money(row["Revised Contract"]))
    m2.metric("Cost to date", _money(row["Cost to Date"]))
    m3.metric("Forecast final cost", _money(row["Forecast Final Direct Cost"]), _money(row["Cost Variance"]))
    m4.metric("Forecast GP", f"{_f(row['Forecast GP %']):.1f}%")
    m5.metric("Risk", _clean(row["Risk"]))

    h1, h2, h3, h4, h5 = st.columns(5)
    h1.metric("Hours remaining", f"{_f(row['Forecast Remaining Labour Hours']):,.1f} h")
    h2.metric("Contract labour allowance", f"{_f(row['Contract Labour Hours']):,.1f} h")
    h3.metric("Timesheet hours used", f"{_f(row['actual_labour_hours']):,.1f} h")
    h4.metric("Materials reserved", _money(row["Material Commitment"]))
    h5.metric("Work target", "$125 / hour")

    if row["Risk"] in {"Critical", "Needs contract value"}:
        st.error(f"This job is flagged: {row['Risk']}.")
    elif row["Risk"] in {"Watch", "Labour ahead of progress", "Needs progress update"}:
        st.warning(f"This job needs review: {row['Risk']}.")
    else:
        st.success("The current forecast is on track.")

    left, right = st.columns([1, 1])
    with left:
        st.markdown("#### Update physical progress")
        with st.form("enterprise_progress_form"):
            progress = st.slider(
                "Physical work complete",
                0.0,
                100.0,
                float(max(0, min(100, _f(row["physical_progress"])))),
                1.0,
                format="%.0f%%",
            )
            remaining = float(max(0, _f(row["Forecast Remaining Labour Hours"])))
            st.metric("Automatically calculated labour hours remaining", f"{remaining:,.1f} h")
            st.caption(
                "Revised contract, less the strongest known material commitment and other "
                "non-labour allowances, divided by $125 per painter-hour, less timesheet hours used."
            )
            finish = st.date_input("Forecast completion date", value=date.today()).isoformat()
            notes = st.text_area("Progress notes / recovery actions")
            submitted = st.form_submit_button("Save Progress Forecast", width="stretch")
        if submitted:
            try:
                _save_progress(ctx, job_id, progress, remaining, finish, notes)
                ctx["pb_success"]("Progress and forecast updated.")
                ctx["pb_rerun"]()
            except Exception as exc:
                log_error(ctx["connect"], _user(ctx).get("username", ""), "job_control_save_progress", exc, {"job_id": job_id})
                ctx["pb_error"](f"Progress could not be saved: {exc}")

    with right:
        st.markdown("#### Connected job position")
        position = pd.DataFrame(
            [
                ["Original contract", row["Original Contract"]],
                ["Approved variations", row["approved_variations"]],
                ["Pending variations", row["pending_variations"]],
                ["Claims issued", row["claimed"]],
                ["Cash received", row["paid"]],
                ["Actual labour cost", row["actual_labour_cost"]],
                ["Actual/verified material cost", row["Actual Material Cost"]],
                ["Material commitment used in hours", row["Material Commitment"]],
                ["PO commitments", row["po_committed"]],
                ["Contract labour work value", row["Contract Labour Work Value"]],
                ["Timesheet work value used at $125/hour", row["Used Labour Work Value"]],
                ["Remaining labour work value", row["Remaining Labour Work Value"]],
                ["Budget direct cost", row["Budget Direct Cost"]],
                ["Forecast final direct cost", row["Forecast Final Direct Cost"]],
            ],
            columns=["Measure", "Amount Ex GST"],
        )
        st.dataframe(
            position,
            hide_index=True,
            width="stretch",
            column_config={"Amount Ex GST": st.column_config.NumberColumn(format="$%.2f")},
        )

    st.markdown("#### Portfolio risk register")
    display_cols = [
        "Job No", "Job Name", "Builder / Client", "Status", "Leading Hand", "Risk",
        "physical_progress", "Labour Used %", "Revised Contract", "Cost to Date",
        "Material Commitment", "Forecast Remaining Labour Hours", "Forecast Final Direct Cost",
        "Cost Variance", "Forecast GP %", "Billing %", "Cash Collected %",
    ]
    portfolio = df[[col for col in display_cols if col in df.columns]].copy()
    portfolio = portfolio.rename(columns={"physical_progress": "Physical Progress %"})
    st.dataframe(
        portfolio,
        hide_index=True,
        width="stretch",
        column_config={
            "Revised Contract": st.column_config.NumberColumn(format="$%.2f"),
            "Cost to Date": st.column_config.NumberColumn(format="$%.2f"),
            "Material Commitment": st.column_config.NumberColumn(format="$%.2f"),
            "Forecast Remaining Labour Hours": st.column_config.NumberColumn(format="%.1f h"),
            "Forecast Final Direct Cost": st.column_config.NumberColumn(format="$%.2f"),
            "Cost Variance": st.column_config.NumberColumn(format="$%.2f"),
            "Forecast GP %": st.column_config.NumberColumn(format="%.1f%%"),
            "Physical Progress %": st.column_config.NumberColumn(format="%.0f%%"),
            "Labour Used %": st.column_config.NumberColumn(format="%.1f%%"),
            "Billing %": st.column_config.NumberColumn(format="%.1f%%"),
            "Cash Collected %": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )


def _next_po_number(ctx: dict[str, Any], job_id: int) -> str:
    job = _query(ctx, "SELECT job_no FROM jobs WHERE id = ?", (job_id,))
    job_no = _slug(job.iloc[0]["job_no"] if not job.empty else job_id, "JOB")
    count = _query(ctx, "SELECT COUNT(*) AS c FROM purchase_orders WHERE job_id = ?", (job_id,))
    next_no = int(count.iloc[0]["c"] or 0) + 1 if not count.empty else 1
    return f"PO-{job_no}-{next_no:03d}"


def _material_request_lines(ctx: dict[str, Any], job_id: int, supplier: str = "") -> pd.DataFrame:
    supplier_clause = ""
    params: list[Any] = [job_id]
    if supplier:
        supplier_clause = "AND LOWER(TRIM(COALESCE(m.custom_supplier, p.supplier, m.supplier, ''))) = LOWER(TRIM(?))"
        params.append(supplier)
    df = _query(
        ctx,
        f"""
        SELECT m.id AS material_entry_id, p.id AS product_id,
               COALESCE(m.custom_product_code, p.product_code, '') AS "Product Code",
               COALESCE(m.custom_product_name, p.product_name, '') AS "Description",
               COALESCE(m.custom_colour, '') AS "Colour",
               CASE
                   WHEN COALESCE(m.qty_required, 0) - COALESCE(m.qty_received, 0) > 0
                   THEN COALESCE(m.qty_required, 0) - COALESCE(m.qty_received, 0)
                   ELSE 0
               END AS "Qty",
               COALESCE(m.custom_unit, p.unit, 'Each') AS "Unit",
               COALESCE(m.custom_unit_price, p.price_ex_gst, 0) AS "Unit Price Ex GST",
               COALESCE(m.custom_supplier, p.supplier, m.supplier, '') AS supplier
        FROM material_entries m
        LEFT JOIN products p ON p.id = m.product_id
        WHERE m.job_id = ?
          AND COALESCE(m.qty_required, 0) > COALESCE(m.qty_received, 0)
          {supplier_clause}
        ORDER BY m.id
        """,
        tuple(params),
    )
    if df.empty:
        return pd.DataFrame(columns=[
            "Include", "material_entry_id", "product_id", "Product Code", "Description",
            "Colour", "Qty", "Unit", "Unit Price Ex GST", "Line Total Ex GST", "Notes"
        ])
    df.insert(0, "Include", True)
    df["Line Total Ex GST"] = df["Qty"].astype(float) * df["Unit Price Ex GST"].astype(float)
    df["Notes"] = ""
    return df.drop(columns=["supplier"], errors="ignore")


def _supplier_options(ctx: dict[str, Any], job_id: int | None = None) -> list[str]:
    values: list[str] = []
    if job_id:
        job = _query(ctx, "SELECT allowed_material_suppliers FROM jobs WHERE id = ?", (job_id,))
        if not job.empty:
            raw = _clean(job.iloc[0]["allowed_material_suppliers"], 1000)
            values.extend([part.strip() for part in re.split(r"[,;|\n]+", raw) if part.strip()])
    products = _query(
        ctx,
        """
        SELECT supplier
        FROM (
            SELECT TRIM(COALESCE(supplier, '')) AS supplier
            FROM products
            WHERE TRIM(COALESCE(supplier, '')) <> ''
            GROUP BY TRIM(COALESCE(supplier, ''))
        ) suppliers
        ORDER BY LOWER(supplier), supplier
        """,
    )
    if not products.empty:
        values.extend(products["supplier"].astype(str).tolist())
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(value.strip())
    return result


def _create_purchase_order(
    ctx: dict[str, Any], job_id: int, supplier: str, po_no: str, order_date: str,
    expected_date: str, status: str, notes: str, lines: pd.DataFrame,
) -> int:
    user = _user(ctx)
    cleaned: list[dict[str, Any]] = []
    for _, row in lines.iterrows():
        if not bool(row.get("Include", True)):
            continue
        description = _clean(row.get("Description"), 500)
        qty = _f(row.get("Qty"))
        price = _f(row.get("Unit Price Ex GST"))
        if not description or qty <= 0:
            continue
        cleaned.append({
            "material_entry_id": int(row["material_entry_id"]) if pd.notna(row.get("material_entry_id")) and _f(row.get("material_entry_id")) else None,
            "product_id": int(row["product_id"]) if pd.notna(row.get("product_id")) and _f(row.get("product_id")) else None,
            "product_code": _clean(row.get("Product Code"), 120),
            "description": description,
            "colour": _clean(row.get("Colour"), 160),
            "qty": qty,
            "unit": _clean(row.get("Unit"), 80),
            "unit_price": price,
            "line_total": round(qty * price, 2),
            "notes": _clean(row.get("Notes"), 500),
        })
    if not cleaned:
        raise ValueError("At least one line with a description and quantity is required.")

    subtotal = round(sum(item["line_total"] for item in cleaned), 2)
    gst = round(subtotal * 0.10, 2)
    total = round(subtotal + gst, 2)
    approved_by = user.get("username", "") if status != "Requested" else ""
    approved_at = _now() if approved_by else ""

    conn = ctx["connect"]()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO purchase_orders
            (po_no, job_id, supplier, status, order_date, expected_date, requested_by,
             approved_by, approved_at, subtotal_ex_gst, gst_amount, total_inc_gst,
             notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (po_no, job_id, supplier, status, order_date, expected_date, user.get("username", ""),
             approved_by, approved_at, subtotal, gst, total, notes, _now(), _now()),
        )
        po_id = int(getattr(cur, "lastrowid", 0) or 0)
        if not po_id:
            cur.execute("SELECT id FROM purchase_orders WHERE po_no = ?", (po_no,))
            po_id = int(cur.fetchone()[0])
        for item in cleaned:
            cur.execute(
                """
                INSERT INTO purchase_order_lines
                (purchase_order_id, material_entry_id, product_id, product_code, description,
                 colour, qty, unit, unit_price_ex_gst, line_total_ex_gst, received_qty, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (po_id, item["material_entry_id"], item["product_id"], item["product_code"],
                 item["description"], item["colour"], item["qty"], item["unit"],
                 item["unit_price"], item["line_total"], 0, item["notes"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _audit(ctx, "purchase_order_created", "purchase_order", po_id, {"po_no": po_no, "job_id": job_id, "subtotal": subtotal})
    _notify_management(
        ctx,
        "purchase_order_created",
        f"Purchase order {po_no}",
        f"{user.get('username', 'A user')} created {po_no} for {supplier} — {_money(subtotal)} ex GST.",
        job_id,
        "purchase_order",
        po_id,
    )
    return po_id


def render_procurement(ctx: dict[str, Any]) -> None:
    st.subheader("Purchasing, Deliveries & Supplier Invoices")
    st.caption("Convert job material requirements into controlled purchase orders and match supplier invoices before job costing.")
    tab_create, tab_register, tab_invoice, tab_export = st.tabs(
        ["Create Purchase Order", "PO Register / Receiving", "Supplier Invoice Match", "Export"]
    )

    with tab_create:
        jobs = _job_options(ctx, include_closed=False)
        if not jobs:
            st.info("No active jobs are available.")
        else:
            selected = st.selectbox("Job", list(jobs), key="enterprise_po_job")
            job_id = jobs[selected]
            suppliers = _supplier_options(ctx, job_id)
            if not suppliers:
                suppliers = ["Haymes", "Dulux", "Taubmans", "Other"]
            supplier = st.selectbox("Supplier", suppliers, key="enterprise_po_supplier")
            source_lines = _material_request_lines(ctx, job_id, supplier)
            if source_lines.empty:
                source_lines = pd.DataFrame([{
                    "Include": True, "material_entry_id": None, "product_id": None,
                    "Product Code": "", "Description": "", "Colour": "", "Qty": 1.0,
                    "Unit": "Each", "Unit Price Ex GST": 0.0, "Line Total Ex GST": 0.0, "Notes": "",
                }])
                st.info("No open material requests matched this supplier. Add manual lines below.")
            edited = st.data_editor(
                source_lines,
                num_rows="dynamic",
                hide_index=True,
                width="stretch",
                key="enterprise_po_lines",
                disabled=["material_entry_id", "product_id", "Line Total Ex GST"],
                column_config={
                    "Include": st.column_config.CheckboxColumn(default=True),
                    "Qty": st.column_config.NumberColumn(min_value=0.0, step=1.0),
                    "Unit Price Ex GST": st.column_config.NumberColumn(min_value=0.0, format="$%.2f"),
                    "Line Total Ex GST": st.column_config.NumberColumn(format="$%.2f"),
                },
            )
            estimated_subtotal = sum(
                _f(r.get("Qty")) * _f(r.get("Unit Price Ex GST"))
                for _, r in edited.iterrows() if bool(r.get("Include", True))
            )
            st.metric("Purchase order subtotal", _money(estimated_subtotal))
            with st.form("enterprise_po_header"):
                po_no = st.text_input("PO Number", value=_next_po_number(ctx, job_id))
                c1, c2 = st.columns(2)
                order_date = c1.date_input("Order date", value=date.today()).isoformat()
                expected_date = c2.date_input("Required / expected date", value=date.today()).isoformat()
                status_options = ["Requested", "Approved", "Ordered"] if _management(ctx) else ["Requested"]
                status = st.selectbox("Initial status", status_options)
                notes = st.text_area("Delivery instructions / notes")
                submit_po = st.form_submit_button("Create Purchase Order", width="stretch")
            if submit_po:
                try:
                    po_id = _create_purchase_order(
                        ctx, job_id, supplier, po_no.strip(), order_date, expected_date, status, notes, edited
                    )
                    ctx["pb_success"](f"Purchase order {po_no} was created successfully.")
                    st.session_state["enterprise_selected_po_id"] = po_id
                    ctx["pb_rerun"]()
                except Exception as exc:
                    log_error(ctx["connect"], _user(ctx).get("username", ""), "create_purchase_order", exc, {"job_id": job_id})
                    ctx["pb_error"](f"Purchase order was not created: {exc}")

    with tab_register:
        po_df = _query(
            ctx,
            """
            SELECT po.id, po.po_no AS "PO Number", j.job_no AS "Job No", j.job_name AS "Job Name",
                   po.supplier AS "Supplier", po.status AS "Status", po.order_date AS "Order Date",
                   po.expected_date AS "Expected", po.subtotal_ex_gst AS "Subtotal Ex GST",
                   po.total_inc_gst AS "Total Inc GST", po.requested_by AS "Requested By",
                   po.approved_by AS "Approved By", po.created_at AS "Created"
            FROM purchase_orders po
            JOIN jobs j ON j.id = po.job_id
            ORDER BY po.id DESC
            """,
        )
        if po_df.empty:
            st.info("No purchase orders have been created yet.")
        else:
            st.dataframe(
                po_df.drop(columns=["id"]), hide_index=True, width="stretch",
                column_config={
                    "Subtotal Ex GST": st.column_config.NumberColumn(format="$%.2f"),
                    "Total Inc GST": st.column_config.NumberColumn(format="$%.2f"),
                },
            )
            po_labels = {f"{r['PO Number']} — {r['Supplier']} — {r['Job No']}": int(r["id"]) for _, r in po_df.iterrows()}
            chosen = st.selectbox("Open purchase order", list(po_labels), key="enterprise_po_register_select")
            po_id = po_labels[chosen]
            lines = _query(
                ctx,
                """
                SELECT id, product_code AS "Product Code", description AS "Description", colour AS "Colour",
                       qty AS "Ordered Qty", received_qty AS "Received Qty", unit AS "Unit",
                       unit_price_ex_gst AS "Unit Price Ex GST", line_total_ex_gst AS "Line Total Ex GST"
                FROM purchase_order_lines
                WHERE purchase_order_id = ?
                ORDER BY id
                """,
                (po_id,),
            )
            edited_lines = st.data_editor(
                lines,
                hide_index=True,
                width="stretch",
                key=f"enterprise_receive_po_{po_id}",
                disabled=["id", "Product Code", "Description", "Colour", "Ordered Qty", "Unit", "Unit Price Ex GST", "Line Total Ex GST"],
                column_config={
                    "Received Qty": st.column_config.NumberColumn(min_value=0.0, step=1.0),
                    "Unit Price Ex GST": st.column_config.NumberColumn(format="$%.2f"),
                    "Line Total Ex GST": st.column_config.NumberColumn(format="$%.2f"),
                },
            )
            status = st.selectbox(
                "PO status", ["Requested", "Approved", "Ordered", "Part Received", "Received", "Closed", "Cancelled"],
                key=f"enterprise_po_status_{po_id}",
            )
            if st.button("Save Receiving / Status", key=f"enterprise_po_save_{po_id}", width="stretch"):
                try:
                    conn = ctx["connect"]()
                    cur = conn.cursor()
                    for _, line in edited_lines.iterrows():
                        cur.execute(
                            "UPDATE purchase_order_lines SET received_qty = ? WHERE id = ?",
                            (_f(line["Received Qty"]), int(line["id"])),
                        )
                    user = _user(ctx)
                    if status in {"Approved", "Ordered", "Part Received", "Received", "Closed"}:
                        cur.execute(
                            """
                            UPDATE purchase_orders
                            SET status = ?, approved_by = COALESCE(NULLIF(approved_by, ''), ?),
                                approved_at = COALESCE(NULLIF(approved_at, ''), ?), updated_at = ?
                            WHERE id = ?
                            """,
                            (status, user.get("username", ""), _now(), _now(), po_id),
                        )
                    else:
                        cur.execute("UPDATE purchase_orders SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), po_id))
                    conn.commit()
                    conn.close()
                    _audit(ctx, "purchase_order_receiving_updated", "purchase_order", po_id, {"status": status})
                    ctx["pb_success"]("Purchase order receiving and status were updated.")
                    ctx["pb_rerun"]()
                except Exception as exc:
                    try:
                        conn.rollback()
                        conn.close()
                    except Exception:
                        pass
                    log_error(ctx["connect"], _user(ctx).get("username", ""), "update_purchase_order", exc, {"po_id": po_id})
                    ctx["pb_error"](f"Purchase order could not be updated: {exc}")

    with tab_invoice:
        po_choices_df = _query(
            ctx,
            """
            SELECT po.id, po.po_no, po.job_id, po.supplier, j.job_no, j.job_name, po.subtotal_ex_gst
            FROM purchase_orders po
            JOIN jobs j ON j.id = po.job_id
            WHERE po.status NOT IN ('Cancelled', 'Rejected')
            ORDER BY po.id DESC
            """,
        )
        if po_choices_df.empty:
            st.info("Create a purchase order before matching a supplier invoice.")
        else:
            labels = {
                f"{r['po_no']} — {r['supplier']} — {r['job_no']} {r['job_name']}": int(r["id"])
                for _, r in po_choices_df.iterrows()
            }
            label = st.selectbox("Purchase order", list(labels), key="enterprise_invoice_po")
            po_id = labels[label]
            po_row = po_choices_df[po_choices_df["id"].astype(int) == po_id].iloc[0]
            po_lines = _query(
                ctx,
                """
                SELECT id AS matched_po_line_id, product_code AS "Product Code", description AS "Description",
                       qty AS "Qty", unit_price_ex_gst AS "Unit Price Ex GST",
                       line_total_ex_gst AS "PO Line Total Ex GST",
                       line_total_ex_gst AS "Invoice Line Total Ex GST", '' AS "Notes"
                FROM purchase_order_lines
                WHERE purchase_order_id = ?
                ORDER BY id
                """,
                (po_id,),
            )
            invoice_lines = st.data_editor(
                po_lines,
                num_rows="dynamic",
                hide_index=True,
                width="stretch",
                key=f"enterprise_invoice_lines_{po_id}",
                disabled=["matched_po_line_id", "PO Line Total Ex GST"],
                column_config={
                    "Qty": st.column_config.NumberColumn(min_value=0.0, step=1.0),
                    "Unit Price Ex GST": st.column_config.NumberColumn(min_value=0.0, format="$%.2f"),
                    "PO Line Total Ex GST": st.column_config.NumberColumn(format="$%.2f"),
                    "Invoice Line Total Ex GST": st.column_config.NumberColumn(min_value=0.0, format="$%.2f"),
                },
            )
            invoice_subtotal = sum(_f(r.get("Invoice Line Total Ex GST")) for _, r in invoice_lines.iterrows())
            variance = invoice_subtotal - _f(po_row["subtotal_ex_gst"])
            x1, x2, x3 = st.columns(3)
            x1.metric("PO subtotal", _money(po_row["subtotal_ex_gst"]))
            x2.metric("Invoice subtotal", _money(invoice_subtotal))
            x3.metric("Variance", _money(variance))
            with st.form("enterprise_invoice_header"):
                invoice_no = st.text_input("Supplier invoice number")
                d1, d2 = st.columns(2)
                invoice_date = d1.date_input("Invoice date", value=date.today()).isoformat()
                due_date = d2.date_input("Due date", value=date.today()).isoformat()
                status = st.selectbox("Invoice status", ["Received", "Matched", "Approved", "Paid", "Disputed"])
                uploaded = st.file_uploader("Supplier invoice PDF", type=["pdf"], key="enterprise_supplier_invoice_pdf")
                notes = st.text_area("Invoice notes")
                submit_invoice = st.form_submit_button("Save Supplier Invoice Match", width="stretch")
            if submit_invoice:
                try:
                    if not invoice_no.strip():
                        raise ValueError("Supplier invoice number is required.")
                    file_path = ""
                    if uploaded is not None:
                        job = _query(ctx, "SELECT job_no FROM jobs WHERE id = ?", (int(po_row["job_id"]),))
                        job_no = _slug(job.iloc[0]["job_no"] if not job.empty else po_row["job_id"])
                        invoice_dir = Path(ctx["JOB_FILES_DIR"]) / job_no / "supplier_invoices"
                        invoice_dir.mkdir(parents=True, exist_ok=True)
                        target = invoice_dir / f"{_slug(invoice_no)}_{_slug(uploaded.name, 'invoice.pdf')}"
                        target.write_bytes(uploaded.getvalue())
                        file_path = str(target)
                    conn = ctx["connect"]()
                    cur = conn.cursor()
                    gst = round(invoice_subtotal * 0.10, 2)
                    cur.execute(
                        """
                        INSERT INTO supplier_invoices
                        (invoice_no, supplier, job_id, purchase_order_id, invoice_date, due_date, status,
                         subtotal_ex_gst, gst_amount, total_inc_gst, variance_ex_gst, file_path, notes,
                         created_by, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (invoice_no.strip(), po_row["supplier"], int(po_row["job_id"]), po_id,
                         invoice_date, due_date, status, invoice_subtotal, gst, invoice_subtotal + gst,
                         variance, file_path, notes, _user(ctx).get("username", ""), _now()),
                    )
                    invoice_id = int(getattr(cur, "lastrowid", 0) or 0)
                    if not invoice_id:
                        cur.execute(
                            "SELECT id FROM supplier_invoices WHERE supplier = ? AND invoice_no = ?",
                            (po_row["supplier"], invoice_no.strip()),
                        )
                        invoice_id = int(cur.fetchone()[0])
                    for _, line in invoice_lines.iterrows():
                        description = _clean(line.get("Description"), 500)
                        if not description:
                            continue
                        po_total = _f(line.get("PO Line Total Ex GST"))
                        invoice_total = _f(line.get("Invoice Line Total Ex GST"))
                        matched_id = int(line["matched_po_line_id"]) if pd.notna(line.get("matched_po_line_id")) and _f(line.get("matched_po_line_id")) else None
                        cur.execute(
                            """
                            INSERT INTO supplier_invoice_lines
                            (supplier_invoice_id, matched_po_line_id, product_code, description, qty,
                             unit_price_ex_gst, line_total_ex_gst, po_line_total_ex_gst, variance_ex_gst, notes)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (invoice_id, matched_id, _clean(line.get("Product Code"), 120), description,
                             _f(line.get("Qty")), _f(line.get("Unit Price Ex GST")), invoice_total,
                             po_total, invoice_total - po_total, _clean(line.get("Notes"), 500)),
                        )
                    conn.commit()
                    conn.close()
                    _audit(ctx, "supplier_invoice_matched", "supplier_invoice", invoice_id, {"po_id": po_id, "variance": variance})
                    _notify_management(
                        ctx,
                        "supplier_invoice_received",
                        f"Supplier invoice {invoice_no.strip()}",
                        f"Invoice {invoice_no.strip()} from {po_row['supplier']} was matched to {po_row['po_no']}. Variance: {_money(variance)}.",
                        int(po_row["job_id"]),
                        "supplier_invoice",
                        invoice_id,
                    )
                    ctx["pb_success"](f"Supplier invoice {invoice_no.strip()} was saved and matched.")
                    ctx["pb_rerun"]()
                except Exception as exc:
                    try:
                        conn.rollback()
                        conn.close()
                    except Exception:
                        pass
                    log_error(ctx["connect"], _user(ctx).get("username", ""), "supplier_invoice_match", exc, {"po_id": po_id})
                    ctx["pb_error"](f"Supplier invoice could not be saved: {exc}")

            invoice_register = _query(
                ctx,
                """
                SELECT si.invoice_no AS "Invoice", si.supplier AS "Supplier", j.job_no AS "Job No",
                       po.po_no AS "PO Number", si.invoice_date AS "Invoice Date", si.status AS "Status",
                       si.subtotal_ex_gst AS "Subtotal Ex GST", si.variance_ex_gst AS "Variance Ex GST",
                       si.created_by AS "Entered By", si.created_at AS "Created"
                FROM supplier_invoices si
                JOIN jobs j ON j.id = si.job_id
                LEFT JOIN purchase_orders po ON po.id = si.purchase_order_id
                ORDER BY si.id DESC
                """,
            )
            if not invoice_register.empty:
                st.markdown("#### Supplier invoice register")
                st.dataframe(
                    invoice_register, hide_index=True, width="stretch",
                    column_config={
                        "Subtotal Ex GST": st.column_config.NumberColumn(format="$%.2f"),
                        "Variance Ex GST": st.column_config.NumberColumn(format="$%.2f"),
                    },
                )

    with tab_export:
        po_export = _query(ctx, "SELECT * FROM purchase_orders ORDER BY id")
        line_export = _query(ctx, "SELECT * FROM purchase_order_lines ORDER BY id")
        invoice_export = _query(ctx, "SELECT * FROM supplier_invoices ORDER BY id")
        invoice_line_export = _query(ctx, "SELECT * FROM supplier_invoice_lines ORDER BY id")
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            po_export.to_excel(writer, index=False, sheet_name="Purchase Orders")
            line_export.to_excel(writer, index=False, sheet_name="PO Lines")
            invoice_export.to_excel(writer, index=False, sheet_name="Supplier Invoices")
            invoice_line_export.to_excel(writer, index=False, sheet_name="Invoice Lines")
        st.download_button(
            "Download Purchasing Register",
            output.getvalue(),
            file_name=f"PB_JobHub_Purchasing_{_today()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch",
        )


def _form_fields(form_type: str) -> list[tuple[str, str, list[str] | None]]:
    definitions: dict[str, list[tuple[str, str, list[str] | None]]] = {
        "Daily Pre-Start": [
            ("weather", "Weather / site conditions", None),
            ("planned_work", "Planned work today", None),
            ("hazards", "Hazards identified", None),
            ("controls", "Controls put in place", None),
            ("ppe", "Required PPE checked", ["Yes", "No", "Not applicable"]),
            ("access", "Access equipment inspected", ["Yes", "No", "Not applicable"]),
            ("swms", "SWMS reviewed and understood", ["Yes", "No", "Not applicable"]),
        ],
        "Hazard / Incident Report": [
            ("event", "What happened / hazard identified", None),
            ("location", "Exact location", None),
            ("immediate_action", "Immediate action taken", None),
            ("severity", "Potential severity", ["Low", "Moderate", "High", "Critical"]),
            ("medical", "Medical treatment required", ["No", "First aid", "Doctor", "Emergency"]),
        ],
        "Quality Inspection": [
            ("area", "Area / elevation / room", None),
            ("substrate", "Substrate", None),
            ("preparation", "Preparation completed", None),
            ("coating_system", "Coating system / coats applied", None),
            ("finish", "Finish acceptable", ["Yes", "No", "Requires rectification"]),
            ("defects", "Defects / rectification required", None),
        ],
        "Practical Completion": [
            ("area", "Area / stage completed", None),
            ("scope_complete", "Scope complete", ["Yes", "No"]),
            ("defects_complete", "Defects rectified", ["Yes", "No", "Not applicable"]),
            ("cleaned", "Area cleaned and waste removed", ["Yes", "No"]),
            ("handover_notes", "Handover / client notes", None),
        ],
    }
    return definitions[form_type]


def _render_form_submission(ctx: dict[str, Any], key_prefix: str, default_job_id: int | None = None) -> None:
    jobs = _job_options(ctx, include_closed=True)
    if not jobs:
        st.info("No jobs are available.")
        return
    labels = list(jobs)
    default_index = 0
    if default_job_id:
        for i, label in enumerate(labels):
            if jobs[label] == default_job_id:
                default_index = i
                break
    job_label = st.selectbox("Job", labels, index=default_index, key=f"{key_prefix}_job")
    job_id = jobs[job_label]
    form_type = st.selectbox(
        "Form type",
        ["Daily Pre-Start", "Hazard / Incident Report", "Quality Inspection", "Practical Completion"],
        key=f"{key_prefix}_type",
    )
    with st.form(f"{key_prefix}_form"):
        answers: dict[str, str] = {}
        for field_key, label, options in _form_fields(form_type):
            if options:
                answers[field_key] = st.selectbox(label, options, key=f"{key_prefix}_{field_key}")
            else:
                answers[field_key] = st.text_area(label, key=f"{key_prefix}_{field_key}")
        signature = st.text_input("Submitted / signed by", value=_user(ctx).get("employee_name") or _user(ctx).get("username", ""))
        acknowledgement = st.checkbox("I confirm this record is accurate.")
        submitted = st.form_submit_button("Submit Form", width="stretch")
    if submitted:
        try:
            if not acknowledgement:
                raise ValueError("Confirm that the record is accurate before submitting.")
            user = _user(ctx)
            employee_id = user.get("employee_id")
            _execute(
                ctx,
                """
                INSERT INTO field_forms
                (job_id, employee_id, form_type, form_date, status, answers_json,
                 signature_name, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, employee_id, form_type, _today(), "Submitted", json.dumps(answers, sort_keys=True),
                 signature, user.get("username", ""), _now()),
            )
            form_id_df = _query(ctx, "SELECT MAX(id) AS id FROM field_forms")
            form_id = int(form_id_df.iloc[0]["id"] or 0) if not form_id_df.empty else 0
            _audit(ctx, "field_form_submitted", "field_form", form_id, {"job_id": job_id, "form_type": form_type})
            if form_type == "Hazard / Incident Report":
                _notify_management(
                    ctx,
                    "hazard_incident_submitted",
                    f"{form_type} submitted",
                    f"{signature or user.get('username', 'A user')} submitted a {form_type} for {job_label}.",
                    job_id,
                    "field_form",
                    form_id,
                )
            ctx["pb_success"](f"{form_type} was submitted successfully.")
            ctx["pb_rerun"]()
        except Exception as exc:
            log_error(ctx["connect"], _user(ctx).get("username", ""), "field_form_submit", exc, {"job_id": job_id, "form_type": form_type})
            ctx["pb_error"](f"Form was not submitted: {exc}")


def render_compliance(ctx: dict[str, Any]) -> None:
    st.subheader("Digital Site, Safety & Quality Forms")
    st.caption("Job-linked pre-starts, hazards, quality inspections and completion records with employee, date and approval history.")
    tab_submit, tab_register = st.tabs(["Submit Form", "Form Register / Approval"])
    with tab_submit:
        _render_form_submission(ctx, "enterprise_compliance")
    with tab_register:
        forms = _query(
            ctx,
            """
            SELECT f.id, j.job_no AS "Job No", j.job_name AS "Job Name", f.form_type AS "Form Type",
                   f.form_date AS "Date", COALESCE(e.name, f.signature_name, f.created_by) AS "Submitted By",
                   f.status AS "Status", f.created_at AS "Created", f.approved_by AS "Approved By",
                   f.answers_json
            FROM field_forms f
            JOIN jobs j ON j.id = f.job_id
            LEFT JOIN employees e ON e.id = f.employee_id
            ORDER BY f.id DESC
            """,
        )
        if forms.empty:
            st.info("No digital forms have been submitted yet.")
            return
        st.dataframe(forms.drop(columns=["id", "answers_json"]), hide_index=True, width="stretch")
        labels = {f"#{int(r['id'])} — {r['Form Type']} — {r['Job No']} — {r['Date']}": int(r["id"]) for _, r in forms.iterrows()}
        selected = st.selectbox("Open form", list(labels), key="enterprise_form_register")
        form_id = labels[selected]
        row = forms[forms["id"].astype(int) == form_id].iloc[0]
        form_type = str(row["Form Type"])
        try:
            answers = json.loads(row["answers_json"] or "{}")
        except Exception:
            answers = {}
        if not isinstance(answers, dict):
            answers = {"record": answers}

        if _management(ctx):
            editable_fields = _form_fields(form_type)
            with st.form(f"enterprise_form_edit_{form_id}"):
                st.caption(
                    f"Edit the {form_type} answers before approving. Dropdown fields use "
                    "fixed choices; free-text fields can be corrected as needed."
                )
                edited_answers: dict[str, str] = {}
                for field_key, label, options in editable_fields:
                    current = str(answers.get(field_key, "") or "")
                    if options:
                        options_for_field = (
                            list(options)
                            if current in options
                            else [current] + list(options)
                        )
                        edited_answers[field_key] = st.selectbox(
                            label,
                            options_for_field,
                            index=0,
                            key=f"enterprise_form_answer_{form_id}_{field_key}",
                        )
                    else:
                        edited_answers[field_key] = st.text_area(
                            label,
                            value=current,
                            key=f"enterprise_form_answer_{form_id}_{field_key}",
                        )
                save_answers = st.form_submit_button("Save Answers", width="stretch")
            if save_answers:
                user = _user(ctx)
                cleaned_answers = {
                    k: str(v or "").strip() for k, v in edited_answers.items()
                }
                try:
                    _execute(
                        ctx,
                        """
                        UPDATE field_forms
                        SET answers_json = ?
                        WHERE id = ?
                        """,
                        (json.dumps(cleaned_answers, sort_keys=True), form_id),
                    )
                except Exception as exc:
                    log_error(ctx["connect"], user.get("username", ""), "field_form_answer_update", exc, {"form_id": form_id})
                    ctx["pb_error"](f"Answers were not saved: {exc}")
                else:
                    _audit(
                        ctx,
                        "field_form_answers_updated",
                        "field_form",
                        form_id,
                        {"form_type": form_type, "edited_by": user.get("username", "")},
                    )
                    ctx["pb_success"](f"{form_type} answers were updated.")
                    ctx["pb_rerun"]()
        else:
            st.json(answers)

        if _management(ctx):
            status = st.selectbox("Approval status", ["Submitted", "Approved", "Requires Action", "Closed"], key=f"enterprise_form_status_{form_id}")
            if st.button("Save Form Status", key=f"enterprise_form_status_save_{form_id}", width="stretch"):
                user = _user(ctx)
                _execute(
                    ctx,
                    """
                    UPDATE field_forms
                    SET status = ?, approved_by = ?, approved_at = ?
                    WHERE id = ?
                    """,
                    (status, user.get("username", ""), _now(), form_id),
                )
                _audit(ctx, "field_form_status_updated", "field_form", form_id, {"status": status})
                ctx["pb_success"]("Form status was updated.")
                ctx["pb_rerun"]()


def render_job_field_forms_panel(ctx: dict[str, Any], job_id: int) -> None:
    """Show every digital form submitted against one job, editable for management.

    Mirrors the Form Register / Approval view but scoped to a single job so the
    Job Folder and the approval register stay in sync on the same records.
    """
    st.markdown("### Site Safety & Quality Forms")
    st.caption(
        "Digital pre-starts, hazard reports, quality inspections and completion "
        "records for this job. Approvals are managed in the Operations Hub."
    )
    forms = _query(
        ctx,
        """
        SELECT f.id, f.form_type AS "Form Type",
               f.form_date AS "Date",
               COALESCE(e.name, f.signature_name, f.created_by) AS "Submitted By",
               f.status AS "Status", f.approved_by AS "Approved By",
               f.approved_at AS "Approved At",
               f.answers_json
        FROM field_forms f
        LEFT JOIN employees e ON e.id = f.employee_id
        WHERE f.job_id = ?
        ORDER BY f.id DESC
        """,
        (int(job_id),),
    )
    if forms.empty:
        st.info("No digital forms have been submitted for this job yet.")
        return

    management = _management(ctx)
    for _, row in forms.iterrows():
        form_id = int(row["id"])
        header = f"#{form_id} — {row['Form Type']} — {row['Date']} — {row['Status']}"
        with st.expander(header):
            try:
                answers = json.loads(row["answers_json"] or "{}")
            except Exception:
                answers = {}
            if not isinstance(answers, dict):
                answers = {"record": answers}
            st.caption(f"Submitted by {row['Submitted By']}")

            if not management:
                st.json(answers)
                continue

            try:
                editable_fields = _form_fields(str(row["Form Type"]))
            except KeyError:
                editable_fields = [
                    (key, key, None)
                    for key in (answers or {})
                ] or [("record", "Record", None)]

            with st.form(f"job_folder_form_edit_{form_id}"):
                st.caption(
                    "Edit answers before approving. Dropdown fields use fixed "
                    "choices; free-text fields can be corrected as needed."
                )
                edited_answers: dict[str, str] = {}
                for field_key, label, options in editable_fields:
                    current = str(answers.get(field_key, "") or "")
                    if options:
                        options_for_field = (
                            list(options)
                            if current in options
                            else [current] + list(options)
                        )
                        edited_answers[field_key] = st.selectbox(
                            label,
                            options_for_field,
                            index=0,
                            key=f"job_folder_form_answer_{form_id}_{field_key}",
                        )
                    else:
                        edited_answers[field_key] = st.text_area(
                            label,
                            value=current,
                            key=f"job_folder_form_answer_{form_id}_{field_key}",
                        )
                save_answers = st.form_submit_button("Save Answers", width="stretch")
            if save_answers:
                user = _user(ctx)
                cleaned_answers = {
                    k: str(v or "").strip() for k, v in edited_answers.items()
                }
                try:
                    _execute(
                        ctx,
                        """
                        UPDATE field_forms
                        SET answers_json = ?
                        WHERE id = ?
                        """,
                        (json.dumps(cleaned_answers, sort_keys=True), form_id),
                    )
                except Exception as exc:
                    log_error(ctx["connect"], user.get("username", ""), "field_form_answer_update", exc, {"form_id": form_id})
                    ctx["pb_error"](f"Answers were not saved: {exc}")
                else:
                    _audit(
                        ctx,
                        "field_form_answers_updated",
                        "field_form",
                        form_id,
                        {"form_type": str(row["Form Type"]), "edited_by": user.get("username", "")},
                    )
                    ctx["pb_success"](f"Form #{form_id} answers were updated.")
                    ctx["pb_rerun"]()

            if management:
                status = st.selectbox(
                    "Approval status",
                    ["Submitted", "Approved", "Requires Action", "Closed"],
                    index=["Submitted", "Approved", "Requires Action", "Closed"].index(str(row["Status"]))
                    if str(row["Status"]) in ["Submitted", "Approved", "Requires Action", "Closed"]
                    else 0,
                    key=f"job_folder_form_status_{form_id}",
                )
                if st.button(
                    "Save Form Status",
                    key=f"job_folder_form_status_save_{form_id}",
                    width="stretch",
                ):
                    user = _user(ctx)
                    _execute(
                        ctx,
                        """
                        UPDATE field_forms
                        SET status = ?, approved_by = ?, approved_at = ?
                        WHERE id = ?
                        """,
                        (status, user.get("username", ""), _now(), form_id),
                    )
                    _audit(ctx, "field_form_status_updated", "field_form", form_id, {"status": status})
                    ctx["pb_success"]("Form status was updated.")
                    ctx["pb_rerun"]()


def _active_clock(ctx: dict[str, Any], employee_id: int) -> pd.DataFrame:
    return _query(
        ctx,
        """
        SELECT c.*, j.job_no, j.job_name, j.site_address
        FROM field_clock_entries c
        JOIN jobs j ON j.id = c.job_id
        WHERE c.employee_id = ? AND c.status = 'Active' AND COALESCE(c.clock_out, '') = ''
        ORDER BY c.id DESC
        LIMIT 1
        """,
        (employee_id,),
    )


def _clock_hours(clock_in: str, clock_out: datetime, break_minutes: float) -> float:
    start = datetime.fromisoformat(str(clock_in).replace(" ", "T"))
    return round(max(0.0, (clock_out - start).total_seconds() / 3600 - _f(break_minutes) / 60), 2)


def render_field_mode(ctx: dict[str, Any]) -> None:
    st.header("Field Mode")
    st.caption("A simplified phone-first view for today’s job, time, photos, safety and job information.")
    user = _user(ctx)
    employee_id = user.get("employee_id")
    if not employee_id:
        st.error("This JobHub user is not linked to an employee record. An administrator must link the account first.")
        return

    today_schedule = _query(
        ctx,
        """
        SELECT s.id, j.id AS job_id, j.job_no AS "Job No", j.job_name AS "Job Name",
               j.site_address AS "Site Address", s.start_time AS "Start", s.finish_time AS "Finish",
               s.site_role AS "Role", s.notes AS "Notes"
        FROM staff_schedule s
        JOIN jobs j ON j.id = s.job_id
        WHERE s.employee_id = ? AND s.schedule_date = ?
        ORDER BY s.start_time, j.job_no
        """,
        (employee_id, _today()),
    )
    st.subheader("Today")
    if today_schedule.empty:
        st.info("No schedule entries are assigned to you today.")
    else:
        st.dataframe(today_schedule.drop(columns=["id", "job_id"]), hide_index=True, width="stretch")

    active = _active_clock(ctx, employee_id)
    if active.empty:
        jobs = _job_options(ctx, include_closed=False)
        if not jobs:
            st.warning("No active jobs are available for clocking.")
            return
        scheduled_job_ids = set(today_schedule["job_id"].astype(int).tolist()) if not today_schedule.empty else set()
        ordered_labels = sorted(jobs, key=lambda label: (jobs[label] not in scheduled_job_ids, label.lower()))
        selected = st.selectbox("Clock onto job", ordered_labels, key="field_mode_clock_job")
        work_type = st.selectbox("Work type", ["Painting", "Preparation", "Travel", "Supervision", "Touch-ups", "Other"], key="field_mode_work_type")
        clock_notes = st.text_input("Clock-in note", key="field_mode_clock_note")
        if st.button("▶ Clock On", key="field_mode_clock_on", width="stretch"):
            try:
                job_id = jobs[selected]
                _execute(
                    ctx,
                    """
                    INSERT INTO field_clock_entries
                    (employee_id, job_id, clock_in, work_type, notes, status, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, 'Active', ?, ?)
                    """,
                    (employee_id, job_id, _now(), work_type, clock_notes, user.get("username", ""), _now()),
                )
                _audit(ctx, "field_clock_started", "job", job_id, {"employee_id": employee_id})
                ctx["pb_success"](f"Clocked onto {selected}.")
                ctx["pb_rerun"]()
            except Exception as exc:
                log_error(ctx["connect"], user.get("username", ""), "field_clock_on", exc)
                ctx["pb_error"](f"Clock-on failed: {exc}")
    else:
        row = active.iloc[0]
        started = str(row["clock_in"])
        st.success(f"Clocked on: {row['job_no']} — {row['job_name']} at {started}")
        st.caption(_clean(row["site_address"]))
        with st.form("field_mode_clock_off_form"):
            break_minutes = st.number_input("Unpaid break minutes", min_value=0.0, max_value=240.0, value=0.0, step=5.0)
            travel_minutes = st.number_input("Travel minutes included", min_value=0.0, max_value=600.0, value=0.0, step=5.0)
            notes = st.text_area("Daily work completed / notes")
            submit_clock = st.form_submit_button("■ Clock Off & Submit Timesheet", width="stretch")
        if submit_clock:
            try:
                now_dt = datetime.now()
                total_hours = _clock_hours(started, now_dt, break_minutes)
                start_dt = datetime.fromisoformat(started.replace(" ", "T"))
                conn = ctx["connect"]()
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO timesheet_entries
                    (job_id, employee_id, work_date, start_time, finish_time, break_minutes,
                     total_hours, work_type, submitted_by, submitted_at, status, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Submitted', ?)
                    """,
                    (int(row["job_id"]), employee_id, start_dt.date().isoformat(), start_dt.strftime("%H:%M"),
                     now_dt.strftime("%H:%M"), break_minutes, total_hours, row["work_type"],
                     user.get("username", ""), _now(), notes),
                )
                timesheet_id = int(getattr(cur, "lastrowid", 0) or 0)
                if not timesheet_id:
                    cur.execute("SELECT MAX(id) FROM timesheet_entries WHERE employee_id = ?", (employee_id,))
                    timesheet_id = int(cur.fetchone()[0])
                cur.execute(
                    """
                    UPDATE field_clock_entries
                    SET clock_out = ?, break_minutes = ?, travel_minutes = ?, total_hours = ?,
                        notes = ?, submitted_timesheet_id = ?, status = 'Submitted'
                    WHERE id = ?
                    """,
                    (_now(), break_minutes, travel_minutes, total_hours, notes, timesheet_id, int(row["id"])),
                )
                conn.commit()
                conn.close()
                _audit(ctx, "field_clock_timesheet_submitted", "timesheet", timesheet_id, {"clock_id": int(row["id"]), "hours": total_hours})
                _notify_management(
                    ctx,
                    "timesheet_submitted",
                    "New timesheet submitted",
                    f"{user.get('employee_name') or user.get('username')} submitted {total_hours:.2f} hours for {row['job_no']} — {row['job_name']}.",
                    int(row["job_id"]),
                    "timesheet",
                    timesheet_id,
                )
                ctx["pb_success"](f"Clocked off and submitted {total_hours:.2f} hours successfully.")
                ctx["pb_rerun"]()
            except Exception as exc:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
                log_error(ctx["connect"], user.get("username", ""), "field_clock_off", exc, {"clock_id": int(row["id"])})
                ctx["pb_error"](f"Clock-off failed: {exc}")

        job_id = int(row["job_id"])
        details = _query(
            ctx,
            """
            SELECT job_no, job_name, site_address, leading_hand, notes,
                   COALESCE(allowed_material_suppliers, '') AS allowed_material_suppliers,
                   COALESCE(restrict_material_products, 0) AS restrict_material_products
            FROM jobs WHERE id = ?
            """,
            (job_id,),
        )
        if not details.empty:
            d = details.iloc[0]
            with st.expander("Job scope, colours and approved products", expanded=True):
                st.write(f"**Site:** {_clean(d['site_address'])}")
                st.write(f"**Leading hand:** {_clean(d['leading_hand']) or 'Not assigned'}")
                st.write(f"**Job notes / scope:** {_clean(d['notes'], 5000) or 'No job notes entered.'}")
                if int(_f(d["restrict_material_products"])):
                    st.write(f"**Approved paint supplier/brand:** {_clean(d['allowed_material_suppliers']) or 'Restriction enabled but no supplier recorded'}")
                colours = _query(
                    ctx,
                    """
                    SELECT section AS "Area / Section", work_location AS "Location", substrate AS "Substrate",
                           coating_system AS "Coating System", colour_finish AS "Colour / Finish"
                    FROM estimate_line_items li
                    JOIN estimate_working_sheets e ON e.id = li.estimate_id
                    WHERE e.job_id = ? AND TRIM(COALESCE(li.colour_finish, '')) <> ''
                    ORDER BY li.id
                    """,
                    (job_id,),
                )
                if not colours.empty:
                    st.dataframe(colours, hide_index=True, width="stretch")

        st.markdown("#### Quick progress photos")
        uploaded_photos = st.file_uploader(
            "Select photos", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True,
            key=f"field_mode_photos_{job_id}",
        )
        photo_category = st.selectbox("Photo category", ["Progress", "Before", "After", "Defect", "Safety", "Delivery"], key=f"field_mode_photo_category_{job_id}")
        photo_caption = st.text_input("Caption", key=f"field_mode_photo_caption_{job_id}")
        if st.button("Save Photos to Job", key=f"field_mode_save_photos_{job_id}", width="stretch"):
            if not uploaded_photos:
                ctx["pb_error"]("Select at least one photo first.")
            else:
                saved = 0
                failures: list[str] = []
                for photo in uploaded_photos:
                    try:
                        ctx["save_job_photo"](job_id, photo, photo_category, photo_caption, "Uploaded from Field Mode")
                        saved += 1
                    except Exception as exc:
                        failures.append(f"{photo.name}: {exc}")
                if saved:
                    ctx["pb_success"](f"{saved} photo{'s were' if saved != 1 else ' was'} successfully added to {row['job_no']} — {row['job_name']}.")
                if failures:
                    ctx["pb_error"]("Some photos failed: " + "; ".join(failures[:3]))
                if saved:
                    ctx["pb_rerun"]()

        st.markdown("#### Daily site form")
        _render_form_submission(ctx, f"field_mode_form_{job_id}", default_job_id=job_id)


def _database_table_names(ctx: dict[str, Any]) -> list[str]:
    if ctx.get("USE_POSTGRES"):
        df = _query(
            ctx,
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
        )
        return df["table_name"].astype(str).tolist() if not df.empty else []
    df = _query(
        ctx,
        """
        SELECT name AS table_name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """,
    )
    return df["table_name"].astype(str).tolist() if not df.empty else []


def create_backup(ctx: dict[str, Any], include_job_files: bool, backup_type: str, created_by: str) -> Path:
    backup_dir = Path(ctx["DATA_DIR"]) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"PB_JobHub_{_slug(backup_type)}_{stamp}.zip"
    tables = _database_table_names(ctx)
    manifest: dict[str, Any] = {
        "created_at": _now(),
        "created_by": created_by,
        "backup_type": backup_type,
        "database": "PostgreSQL" if ctx.get("USE_POSTGRES") else "SQLite",
        "tables": {},
        "job_files_included": bool(include_job_files),
        "enterprise_build": ENTERPRISE_BUILD,
    }
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for table in tables:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                continue
            frame = _query(ctx, f"SELECT * FROM {table}")
            csv_bytes = frame.to_csv(index=False).encode("utf-8-sig")
            zf.writestr(f"database/{table}.csv", csv_bytes)
            manifest["tables"][table] = int(len(frame))
        if include_job_files:
            root = Path(ctx["JOB_FILES_DIR"])
            if root.exists():
                for path in root.rglob("*"):
                    if path.is_file():
                        try:
                            zf.write(path, arcname=f"job_files/{path.relative_to(root).as_posix()}")
                        except (OSError, ValueError):
                            continue
        zf.writestr("backup_manifest.json", json.dumps(manifest, indent=2, default=str))
    size = target.stat().st_size
    _execute(
        ctx,
        """
        INSERT INTO backup_runs
        (backup_type, file_path, size_bytes, status, created_by, created_at, notes)
        VALUES (?, ?, ?, 'Completed', ?, ?, ?)
        """,
        (backup_type, str(target), size, created_by, _now(), f"{len(tables)} tables exported"),
    )
    return target


def ensure_daily_backup(ctx: dict[str, Any]) -> None:
    """Create one lightweight database CSV backup per calendar day."""
    today_prefix = f"PB_JobHub_Daily_Data_{date.today().strftime('%Y%m%d')}"
    backup_dir = Path(ctx["DATA_DIR"]) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    if any(path.name.startswith(today_prefix) for path in backup_dir.glob("*.zip")):
        return
    # Use a deterministic daily name to prevent concurrent duplicates.
    temp_target = create_backup(ctx, False, "Daily_Data", "system")
    final_target = backup_dir / f"{today_prefix}.zip"
    if final_target.exists():
        temp_target.unlink(missing_ok=True)
    else:
        temp_target.replace(final_target)
        _execute(ctx, "UPDATE backup_runs SET file_path = ? WHERE file_path = ?", (str(final_target), str(temp_target)))


def _xero_export_zip(ctx: dict[str, Any]) -> bytes:
    exports: dict[str, pd.DataFrame] = {
        "contacts.csv": _query(
            ctx,
            """
            SELECT name AS "ContactName", contact_name AS "FirstName", email AS "EmailAddress",
                   phone AS "PhoneNumber", address AS "AddressLine1", abn AS "TaxNumber",
                   terms AS "PaymentTerms", notes AS "Notes"
            FROM builders_clients ORDER BY name
            """,
        ),
        "sales_claims.csv": _query(
            ctx,
            """
            SELECT c.claim_no AS "InvoiceNumber", b.name AS "ContactName", c.invoice_date AS "InvoiceDate",
                   c.due_date AS "DueDate", c.description AS "Description", c.amount_ex_gst AS "UnitAmount",
                   c.status AS "Status", j.job_no AS "TrackingName"
            FROM invoice_claims c
            JOIN jobs j ON j.id = c.job_id
            LEFT JOIN builders_clients b ON b.id = j.builder_client_id
            ORDER BY c.id
            """,
        ),
        "supplier_bills.csv": _query(
            ctx,
            """
            SELECT si.invoice_no AS "InvoiceNumber", si.supplier AS "ContactName",
                   si.invoice_date AS "InvoiceDate", si.due_date AS "DueDate",
                   si.subtotal_ex_gst AS "UnitAmount", si.status AS "Status",
                   j.job_no AS "TrackingName", si.notes AS "Reference"
            FROM supplier_invoices si
            JOIN jobs j ON j.id = si.job_id
            ORDER BY si.id
            """,
        ),
        "approved_timesheets.csv": _query(
            ctx,
            """
            SELECT e.name AS "Employee", t.work_date AS "Date", j.job_no AS "TrackingName",
                   t.total_hours AS "Hours", t.work_type AS "EarningsRate", t.notes AS "Description"
            FROM timesheet_entries t
            JOIN employees e ON e.id = t.employee_id
            JOIN jobs j ON j.id = t.job_id
            WHERE t.status IN ('Approved', 'Paid', 'Processed')
            ORDER BY t.work_date, e.name
            """,
        ),
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, frame in exports.items():
            zf.writestr(name, frame.to_csv(index=False).encode("utf-8-sig"))
        zf.writestr(
            "README.txt",
            "Xero-ready review exports generated by Premier Brushworks JobHub. Review field mapping and tax codes before importing into Xero. JobHub remains the operational source; Xero remains the accounting source of truth.\n",
        )
    return output.getvalue()


def render_system_control(ctx: dict[str, Any]) -> None:
    st.subheader("System Health, Audit, Backups & Accounting Export")
    st.caption("Administrator visibility for errors, changes, backup evidence and Xero-ready operational data.")
    tab_health, tab_audit, tab_backup, tab_xero = st.tabs(["System Health", "Audit Trail", "Backups", "Xero-Ready Export"])

    with tab_health:
        tables = _database_table_names(ctx)
        counts: list[dict[str, Any]] = []
        for table in tables:
            try:
                count_df = _query(ctx, f"SELECT COUNT(*) AS c FROM {table}")
                counts.append({"Table": table, "Rows": int(count_df.iloc[0]["c"] or 0)})
            except Exception:
                counts.append({"Table": table, "Rows": "Error"})
        h1, h2, h3 = st.columns(3)
        h1.metric("Database", "PostgreSQL" if ctx.get("USE_POSTGRES") else "SQLite")
        h2.metric("Tables", len(tables))
        unresolved_df = _query(ctx, "SELECT COUNT(*) AS c FROM app_error_events WHERE COALESCE(resolved_at, '') = ''")
        unresolved = int(unresolved_df.iloc[0]["c"] or 0) if not unresolved_df.empty else 0
        h3.metric("Unresolved logged errors", unresolved)
        st.dataframe(pd.DataFrame(counts), hide_index=True, width="stretch")
        errors = _query(
            ctx,
            """
            SELECT id, created_at AS "Created", username AS "User", area AS "Area",
                   error_type AS "Type", message AS "Message", resolved_at AS "Resolved",
                   resolved_by AS "Resolved By", resolution_notes AS "Resolution"
            FROM app_error_events
            ORDER BY id DESC
            LIMIT 200
            """,
        )
        if errors.empty:
            st.success("No application errors have been recorded by the new enterprise workflows.")
        else:
            st.dataframe(errors.drop(columns=["id"]), hide_index=True, width="stretch")
            if _role(ctx) == "admin":
                labels = {f"#{int(r['id'])} — {r['Area']} — {r['Message'][:70]}": int(r["id"]) for _, r in errors.iterrows()}
                selected = st.selectbox("Resolve logged error", list(labels), key="enterprise_error_resolve")
                resolution = st.text_area("Resolution notes", key="enterprise_error_resolution")
                if st.button("Mark Error Resolved", key="enterprise_error_resolve_button", width="stretch"):
                    error_id = labels[selected]
                    _execute(
                        ctx,
                        "UPDATE app_error_events SET resolved_at = ?, resolved_by = ?, resolution_notes = ? WHERE id = ?",
                        (_now(), _user(ctx).get("username", ""), resolution, error_id),
                    )
                    ctx["pb_success"]("Error was marked as resolved.")
                    ctx["pb_rerun"]()

    with tab_audit:
        audit = _query(
            ctx,
            """
            SELECT created_at AS "Created", username AS "User", action AS "Action",
                   entity_type AS "Record Type", entity_id AS "Record ID", details AS "Details"
            FROM audit_events
            ORDER BY id DESC
            LIMIT 1000
            """,
        )
        if audit.empty:
            st.info("No audit events are available.")
        else:
            action_filter = st.text_input("Filter audit trail", key="enterprise_audit_filter")
            if action_filter.strip():
                haystack = audit.astype(str).agg(" ".join, axis=1).str.lower()
                audit = audit[haystack.str.contains(action_filter.strip().lower(), na=False)]
            st.dataframe(audit, hide_index=True, width="stretch")
            st.download_button(
                "Download Audit Trail CSV",
                audit.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"PB_JobHub_Audit_Trail_{_today()}.csv",
                mime="text/csv",
                width="stretch",
            )

    with tab_backup:
        st.info("A lightweight database export is created automatically once per day when JobHub is used. Full backups include all stored job files and can be much larger.")
        b1, b2 = st.columns(2)
        if b1.button("Create Data Backup Now", key="enterprise_backup_data", width="stretch"):
            try:
                path = create_backup(ctx, False, "Manual_Data", _user(ctx).get("username", ""))
                ctx["pb_success"](f"Data backup created: {path.name}")
                ctx["pb_rerun"]()
            except Exception as exc:
                log_error(ctx["connect"], _user(ctx).get("username", ""), "manual_data_backup", exc)
                ctx["pb_error"](f"Backup failed: {exc}")
        if b2.button("Create Full Backup Including Job Files", key="enterprise_backup_full", width="stretch"):
            try:
                path = create_backup(ctx, True, "Manual_Full", _user(ctx).get("username", ""))
                ctx["pb_success"](f"Full backup created: {path.name}")
                ctx["pb_rerun"]()
            except Exception as exc:
                log_error(ctx["connect"], _user(ctx).get("username", ""), "manual_full_backup", exc)
                ctx["pb_error"](f"Full backup failed: {exc}")
        backups = _query(
            ctx,
            """
            SELECT id, backup_type AS "Type", file_path AS "File", size_bytes AS "Bytes",
                   status AS "Status", created_by AS "Created By", created_at AS "Created", notes AS "Notes"
            FROM backup_runs ORDER BY id DESC LIMIT 100
            """,
        )
        if not backups.empty:
            st.dataframe(backups.drop(columns=["id"]), hide_index=True, width="stretch")
            labels = {f"{r['Created']} — {r['Type']} — {Path(str(r['File'])).name}": str(r["File"]) for _, r in backups.iterrows()}
            selected = st.selectbox("Download existing backup", list(labels), key="enterprise_backup_download_select")
            backup_path = Path(labels[selected])
            if backup_path.exists():
                st.download_button(
                    "Download Selected Backup",
                    backup_path.read_bytes(),
                    file_name=backup_path.name,
                    mime="application/zip",
                    width="stretch",
                )
            else:
                st.warning("The selected backup record exists, but the physical file is no longer on this server.")

    with tab_xero:
        st.warning("This is a controlled export, not a live Xero API connection. Review tax codes, account codes and tracking mappings before import.")
        st.download_button(
            "Download Xero-Ready Export ZIP",
            _xero_export_zip(ctx),
            file_name=f"PB_JobHub_Xero_Ready_{_today()}.zip",
            mime="application/zip",
            width="stretch",
        )


def render_operations_hub(ctx: dict[str, Any]) -> None:
    st.header("Operations Hub")
    st.caption("Premier Brushworks control centre for forecast, purchasing, field compliance and system reliability.")
    section = st.radio(
        "Operations section",
        ["Job Control", "Purchasing", "Compliance & QA", "System / Backups"],
        horizontal=True,
        key="enterprise_operations_section",
    )
    try:
        if section == "Job Control":
            render_job_control(ctx)
        elif section == "Purchasing":
            render_procurement(ctx)
        elif section == "Compliance & QA":
            render_compliance(ctx)
        else:
            if not _management(ctx):
                st.error("System and backup controls are limited to managers and administrators.")
                return
            render_system_control(ctx)
    except Exception as exc:
        log_error(ctx["connect"], _user(ctx).get("username", ""), f"operations_hub:{section}", exc)
        st.error("This Operations Hub section encountered an error. The error has been logged for administrator review.")
        st.exception(exc)
