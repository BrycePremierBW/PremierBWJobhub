from __future__ import annotations

import re
from datetime import date

import pandas as pd
import streamlit as st

from .auth import can_manage
from .common import AppContext, Field, _clean, _float, _int, render_crud
from .ui import header, rerun_success


def _show_frame(frame: pd.DataFrame, empty_message: str, *, height: int | None = None) -> None:
    if frame is None or frame.empty:
        st.caption(empty_message)
        return
    kwargs = {"hide_index": True, "width": "stretch"}
    if height is not None:
        kwargs["height"] = height
    st.dataframe(frame, **kwargs)


def dashboard_page(ctx: AppContext) -> None:
    header("Dashboard", "Live operational summary without full-app refresh loops.")
    today_text = date.today().isoformat()
    active_jobs = _int(ctx.db.scalar("SELECT COUNT(*) FROM jobs WHERE LOWER(COALESCE(status,'')) IN ('active','booked','not started')", default=0))
    pending_timesheets = _int(ctx.db.scalar("SELECT COUNT(*) FROM timesheet_entries WHERE LOWER(COALESCE(status,'')) IN ('submitted','pending')", default=0))
    staff = _int(ctx.db.scalar("SELECT COUNT(*) FROM employees WHERE LOWER(COALESCE(status,'Active'))='active'", default=0))
    contract = _float(ctx.db.scalar("SELECT COALESCE(SUM(contract_value),0) FROM jobs WHERE LOWER(COALESCE(status,'')) NOT IN ('archived','cancelled')", default=0))
    cols = st.columns(4)
    cols[0].metric("Active / booked jobs", active_jobs)
    cols[1].metric("Pending timesheets", pending_timesheets)
    cols[2].metric("Active staff", staff)
    cols[3].metric("Open contract value", f"${contract:,.0f}")

    st.markdown("### Crucial Jobs")
    crucial = ctx.db.query(
        """
        SELECT j.job_no AS "Job",j.job_name AS "Name",COALESCE(b.name,'') AS "Builder",
               COALESCE(j.status,'') AS "Status",j.start_date AS "Start",j.end_date AS "Finish",
               COALESCE(j.leading_hand,'') AS "Leading Hand"
        FROM jobs j LEFT JOIN builders_clients b ON b.id=j.builder_client_id
        WHERE LOWER(COALESCE(j.status,'')) NOT IN ('completed','paid','archived','cancelled')
        ORDER BY CASE WHEN COALESCE(j.end_date,'')<>'' AND j.end_date < ? THEN 0 ELSE 1 END,
                 CASE WHEN LOWER(COALESCE(j.status,''))='on hold' THEN 0 ELSE 1 END,
                 j.end_date,j.start_date
        LIMIT 20
        """,
        (today_text,),
    )
    _show_frame(crucial, "No open jobs need attention.")

    left, right = st.columns(2)
    with left:
        st.subheader("Paint to Order")
        paint = ctx.db.query(
            """
            SELECT COALESCE(j.job_no,'') AS "Job",
                   COALESCE(p.product_name,m.custom_product_name,'Unspecified product') AS "Product",
                   COALESCE(NULLIF(m.custom_supplier,''),NULLIF(m.supplier,''),p.supplier,'') AS "Supplier",
                   ROUND(CAST(COALESCE(m.qty_required,0)-COALESCE(m.qty_received,0) AS numeric),2) AS "Still Required",
                   COALESCE(p.unit,m.custom_unit,'') AS "Unit"
            FROM material_entries m
            LEFT JOIN jobs j ON j.id=m.job_id
            LEFT JOIN products p ON p.id=m.product_id
            WHERE COALESCE(m.qty_required,0)>COALESCE(m.qty_received,0)
            ORDER BY j.job_no,m.id LIMIT 25
            """
        )
        _show_frame(paint, "No outstanding paint or material quantities.")

    with right:
        st.subheader("Today’s Staff")
        if ctx.db.table_exists("staff_schedule"):
            schedule = ctx.db.query(
                """
                SELECT COALESCE(e.name,'') AS "Employee",COALESCE(j.job_no,'') AS "Job",
                       COALESCE(j.job_name,'') AS "Job Name",COALESCE(s.start_time,'') AS "Start",
                       COALESCE(s.finish_time,'') AS "Finish",COALESCE(s.site_role,'') AS "Role"
                FROM staff_schedule s
                LEFT JOIN employees e ON e.id=s.employee_id
                LEFT JOIN jobs j ON j.id=s.job_id
                WHERE s.schedule_date=? ORDER BY e.name
                """,
                (today_text,),
            )
            _show_frame(schedule, "No staff are scheduled today.")
        else:
            st.caption("The scheduler has not been initialised yet.")

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Tasks to Complete")
        if ctx.db.table_exists("app_notifications"):
            tasks = ctx.db.query(
                """
                SELECT title AS "Task",message AS "Details",created_at AS "Created"
                FROM app_notifications
                WHERE recipient_user_id=? AND COALESCE(read_at,'')=''
                ORDER BY id DESC LIMIT 20
                """,
                (_int(ctx.user.get("id")),),
            )
            _show_frame(tasks, "No unread management tasks.")
        else:
            st.caption("No management tasks are waiting.")
    with c2:
        st.subheader("Timesheets")
        pending = ctx.db.query(
            """
            SELECT t.work_date AS "Date",COALESCE(e.name,'') AS "Employee",
                   COALESCE(j.job_no,'') AS "Job",COALESCE(t.total_hours,0) AS "Hours",
                   COALESCE(t.status,'') AS "Status"
            FROM timesheet_entries t
            LEFT JOIN employees e ON e.id=t.employee_id
            LEFT JOIN jobs j ON j.id=t.job_id
            WHERE LOWER(COALESCE(t.status,'')) IN ('submitted','pending')
            ORDER BY t.work_date DESC,t.id DESC LIMIT 20
            """
        )
        _show_frame(pending, "No timesheets are waiting for review.")

    p1, p2 = st.columns(2)
    with p1:
        st.subheader("Job Progress")
        if ctx.db.table_exists("job_progress_snapshots"):
            progress = ctx.db.query(
                """
                SELECT COALESCE(j.job_no,'') AS "Job",COALESCE(j.job_name,'') AS "Name",
                       ROUND(CAST(COALESCE(p.physical_progress_percent,0) AS numeric),1) AS "Progress %",
                       COALESCE(p.forecast_remaining_labour_hours,0) AS "Forecast Hours Remaining",
                       COALESCE(p.forecast_completion_date,'') AS "Forecast Completion"
                FROM job_progress_snapshots p
                JOIN jobs j ON j.id=p.job_id
                JOIN (SELECT job_id,MAX(id) AS max_id FROM job_progress_snapshots GROUP BY job_id) latest
                  ON latest.max_id=p.id
                ORDER BY "Progress %",j.job_no LIMIT 20
                """
            )
            _show_frame(progress, "No progress snapshots have been entered.")
        else:
            st.caption("Open Job Progress to initialise progress tracking.")
    with p2:
        st.subheader("Active Site Blockers")
        if ctx.db.table_exists("field_forms"):
            blockers = ctx.db.query(
                """
                SELECT COALESCE(j.job_no,'') AS "Job",f.form_type AS "Form",
                       f.form_date AS "Date",COALESCE(f.status,'') AS "Status"
                FROM field_forms f LEFT JOIN jobs j ON j.id=f.job_id
                WHERE LOWER(COALESCE(f.status,'')) NOT IN ('approved','completed','closed')
                ORDER BY f.form_date DESC,f.id DESC LIMIT 20
                """
            )
            _show_frame(blockers, "No active site blockers are recorded.")
        else:
            st.caption("No active site blockers are recorded.")

    f1, f2 = st.columns(2)
    with f1:
        st.markdown("### Overhead & Profit")
        wages = _float(ctx.db.scalar(
            "SELECT COALESCE(SUM(COALESCE(hours,0)*COALESCE(NULLIF(hourly_rate_snapshot,0),hourly_rate,0)),0) FROM wage_entries",
            default=0,
        ))
        materials_value = _float(ctx.db.scalar(
            """
            SELECT COALESCE(SUM(COALESCE(m.qty_required,0)*COALESCE(NULLIF(p.price_ex_gst,0),m.custom_unit_price,0)),0)
            FROM material_entries m LEFT JOIN products p ON p.id=m.product_id
            """,
            default=0,
        ))
        remaining = contract - wages - materials_value
        m1, m2, m3 = st.columns(3)
        m1.metric("Recorded wages", f"${wages:,.0f}")
        m2.metric("Material commitment", f"${materials_value:,.0f}")
        m3.metric("Contract less recorded cost", f"${remaining:,.0f}")
        st.caption("This is an operational bridge, not final accounting profit. Use Xero for the ledger result.")
    with f2:
        st.subheader("Overdue Claims")
        if ctx.db.table_exists("invoice_claims"):
            overdue = ctx.db.query(
                """
                SELECT COALESCE(j.job_no,'') AS "Job",COALESCE(i.claim_no,'') AS "Claim",
                       COALESCE(i.amount_ex_gst,0) AS "Amount Ex GST",COALESCE(i.due_date,'') AS "Due",
                       COALESCE(i.status,'') AS "Status"
                FROM invoice_claims i LEFT JOIN jobs j ON j.id=i.job_id
                WHERE COALESCE(i.due_date,'')<>'' AND i.due_date<?
                  AND LOWER(COALESCE(i.status,'')) NOT IN ('paid','cancelled')
                ORDER BY i.due_date LIMIT 20
                """,
                (today_text,),
            )
            _show_frame(overdue, "No overdue claims.")
        else:
            st.caption("No overdue claims.")


def builders_page(ctx: AppContext) -> None:
    def safe_delete(record_id: int) -> tuple[bool, str]:
        count = _int(ctx.db.scalar("SELECT COUNT(*) FROM jobs WHERE builder_client_id=?", (record_id,), 0))
        return (count == 0, f"This record is linked to {count} job(s). Reassign those jobs first.")

    render_crud(
        ctx,
        title="Builders & Clients",
        subtitle="One clean contact register for builders, clients and suppliers.",
        table="builders_clients",
        fields=(
            Field("type", "Type", "select", "Builder", ("Builder", "Client", "Supplier", "Other")),
            Field("name", "Company / client name", required=True),
            Field("contact_name", "Contact name"), Field("phone", "Phone"), Field("email", "Email"),
            Field("address", "Address", "textarea"), Field("qbcc", "QBCC"), Field("abn", "ABN"),
            Field("terms", "Payment terms"), Field("notes", "Notes", "textarea"),
        ),
        display_columns=("type", "name", "contact_name", "phone", "email", "terms"),
        order_by="name", search_columns=("name", "contact_name", "phone", "email"), key="contacts",
        can_delete=safe_delete,
    )


def employees_page(ctx: AppContext) -> None:
    def safe_delete(record_id: int) -> tuple[bool, str]:
        queries = [("timesheet_entries", "employee_id"), ("wage_entries", "employee_id"), ("app_users", "employee_id")]
        count = sum(_int(ctx.db.scalar(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (record_id,), 0)) for table, column in queries if ctx.db.table_exists(table))
        return (count == 0, f"This employee has {count} linked record(s). Mark them Inactive instead.")

    render_crud(
        ctx,
        title="Employees",
        subtitle="Staff details and costing rates. Estimating rates remain separate from actual job cost.",
        table="employees",
        fields=(
            Field("name", "Name", required=True), Field("role", "Role"), Field("phone", "Phone"),
            Field("email", "Email"), Field("base_hourly_rate", "Base hourly rate", "number", 0),
            Field("rate_plus_10", "Rate plus 10%", "number", 0),
            Field("status", "Status", "select", "Active", ("Active", "Inactive", "On Leave")),
            Field("notes", "Notes", "textarea"),
        ),
        display_columns=("name", "role", "phone", "email", "base_hourly_rate", "rate_plus_10", "status"),
        order_by="name", search_columns=("name", "role", "phone", "email"), key="employees",
        can_delete=safe_delete,
    )


def products_page(ctx: AppContext) -> None:
    render_crud(
        ctx,
        title="Products",
        subtitle="Shared product and supplier price library.",
        table="products",
        fields=(
            Field("product_code", "Product code", required=True), Field("product_name", "Product name", required=True),
            Field("supplier", "Supplier"), Field("unit", "Unit", "select", "Each", ("Each", "L", "4L", "10L", "15L", "20L")),
            Field("price_ex_gst", "Price ex GST", "number", 0), Field("notes", "Notes", "textarea"),
        ),
        display_columns=("product_code", "product_name", "supplier", "unit", "price_ex_gst", "notes"),
        order_by="product_code", search_columns=("product_code", "product_name", "supplier"), key="products",
    )

    if can_manage():
        with st.expander("CSV import / export"):
            export = ctx.db.query("SELECT product_code,product_name,supplier,unit,price_ex_gst,notes FROM products ORDER BY product_code")
            st.download_button("Download product list", export.to_csv(index=False).encode(), "products.csv", "text/csv")
            upload = st.file_uploader("Upload CSV", type=["csv"], key="product_csv")
            if upload is not None:
                try:
                    frame = pd.read_csv(upload).fillna("")
                    aliases = {re.sub(r"[^a-z0-9]", "", str(c).lower()): c for c in frame.columns}
                    expected = {
                        "product_code": ("productcode", "code"), "product_name": ("productname", "name", "description"),
                        "supplier": ("supplier", "brand"), "unit": ("unit", "pack"),
                        "price_ex_gst": ("priceexgst", "price", "cost"), "notes": ("notes",),
                    }
                    prepared = pd.DataFrame()
                    for target, candidates in expected.items():
                        source = next((aliases[c] for c in candidates if c in aliases), None)
                        prepared[target] = frame[source] if source else ""
                    st.dataframe(prepared.head(50), hide_index=True, width="stretch")
                    if st.button("Import products", type="primary"):
                        rows = []
                        for _, row in prepared.iterrows():
                            code = _clean(row["product_code"])
                            name = _clean(row["product_name"])
                            if code and name:
                                rows.append((code, name, _clean(row["supplier"]), _clean(row["unit"]), _float(row["price_ex_gst"]), _clean(row["notes"])))
                        for row in rows:
                            ctx.db.execute(
                                """
                                INSERT INTO products(product_code,product_name,supplier,unit,price_ex_gst,notes)
                                VALUES (?,?,?,?,?,?)
                                ON CONFLICT(product_code) DO UPDATE SET
                                product_name=excluded.product_name,supplier=excluded.supplier,
                                unit=excluded.unit,price_ex_gst=excluded.price_ex_gst,notes=excluded.notes
                                """,
                                row,
                            )
                        ctx.audit("import", "products", None, f"{len(rows)} rows")
                        rerun_success(f"Imported {len(rows)} products.")
                except Exception as exc:
                    st.error(str(exc))