from __future__ import annotations

import re
from datetime import date

import pandas as pd
import streamlit as st

from .auth import can_manage
from .common import AppContext, Field, _clean, _float, _int, render_crud
from .ui import header, rerun_success


def dashboard_page(ctx: AppContext) -> None:
    header("Dashboard", "Live operational summary without full-app refresh loops.")
    active_jobs = _int(ctx.db.scalar("SELECT COUNT(*) FROM jobs WHERE LOWER(COALESCE(status,'')) IN ('active','booked','not started')", default=0))
    pending_timesheets = _int(ctx.db.scalar("SELECT COUNT(*) FROM timesheet_entries WHERE LOWER(COALESCE(status,'')) IN ('submitted','pending')", default=0))
    staff = _int(ctx.db.scalar("SELECT COUNT(*) FROM employees WHERE LOWER(COALESCE(status,'Active'))='active'", default=0))
    contract = _float(ctx.db.scalar("SELECT COALESCE(SUM(contract_value),0) FROM jobs WHERE LOWER(COALESCE(status,'')) NOT IN ('archived','cancelled')", default=0))
    cols = st.columns(4)
    cols[0].metric("Active / booked jobs", active_jobs)
    cols[1].metric("Pending timesheets", pending_timesheets)
    cols[2].metric("Active staff", staff)
    cols[3].metric("Open contract value", f"${contract:,.0f}")

    st.subheader("Jobs needing attention")
    risks = ctx.db.query(
        """
        SELECT j.job_no AS "Job",j.job_name AS "Name",COALESCE(b.name,'') AS "Builder",
               COALESCE(j.status,'') AS "Status",j.start_date AS "Start",j.end_date AS "Finish",
               COALESCE(j.leading_hand,'') AS "Leading Hand"
        FROM jobs j LEFT JOIN builders_clients b ON b.id=j.builder_client_id
        WHERE LOWER(COALESCE(j.status,'')) NOT IN ('completed','paid','archived','cancelled')
        ORDER BY CASE WHEN j.end_date<>'' AND j.end_date < ? THEN 0 ELSE 1 END,j.end_date,j.start_date
        LIMIT 20
        """,
        (date.today().isoformat(),),
    )
    st.dataframe(risks, hide_index=True, use_container_width=True)

    if ctx.db.table_exists("staff_schedule"):
        st.subheader("Today’s schedule")
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
            (date.today().isoformat(),),
        )
        st.dataframe(schedule, hide_index=True, use_container_width=True)


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
                    st.dataframe(prepared.head(50), hide_index=True, use_container_width=True)
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
