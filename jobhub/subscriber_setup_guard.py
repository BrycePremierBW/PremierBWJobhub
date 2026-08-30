"""Commercial subscriber setup panel for JobHub.

The panel is appended to the existing admin Setup / Edit Defaults screen. It
provides company identity, setup-health and safe preview-before-commit imports
for employees, builders/clients and product pricing. The implementation uses
JobHub's existing database helpers at render time so importing this module never
opens a database connection or renders Streamlit UI.
"""

from __future__ import annotations

import base64
import io
import sys
from typing import Any

import pandas as pd

from . import setup_defaults_guard
from .subscriber_onboarding import preview_import, setup_completion_percent, setup_health


PATCH_MARKER = "_pb_subscriber_setup_guard"
SETTING_PREFIX = "subscriber_"
MAX_LOGO_BYTES = 1_500_000


def _st() -> Any:
    return sys.modules.get("streamlit")


def _app_attr(name: str, default: Any = None) -> Any:
    for module_name in ("__main__", "pb_jobhub_app"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, name):
            return getattr(module, name)
    return default


def _execute(sql: str, params: tuple[Any, ...] = ()) -> Any:
    fn = _app_attr("execute")
    if callable(fn):
        return fn(sql, params)
    raise RuntimeError("JobHub database execute function is not available yet.")


def _df_query(sql: str, params: tuple[Any, ...] = ()) -> Any:
    fn = _app_attr("df_query") or _app_attr("safe_df_query")
    if callable(fn):
        return fn(sql, params)
    raise RuntimeError("JobHub database query function is not available yet.")


def _use_postgres() -> bool:
    return bool(_app_attr("USE_POSTGRES", False))


def _ensure_schema() -> None:
    _execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT
        )
        """
    )
    try:
        if _use_postgres():
            _execute("ALTER TABLE employees ADD COLUMN IF NOT EXISTS email TEXT")
        else:
            columns = _df_query("PRAGMA table_info(employees)")
            names = {str(value) for value in columns.iloc[:, 1].tolist()} if columns is not None and not columns.empty else set()
            if "email" not in names:
                _execute("ALTER TABLE employees ADD COLUMN email TEXT")
    except Exception:
        pass


def _setting_key(name: str) -> str:
    return f"{SETTING_PREFIX}{name}"


def _get_setting(name: str, default: str = "") -> str:
    try:
        df = _df_query(
            "SELECT setting_value FROM app_settings WHERE setting_key=? LIMIT 1",
            (_setting_key(name),),
        )
        if df is not None and not getattr(df, "empty", True):
            return str(df.iloc[0]["setting_value"] or "")
    except Exception:
        pass
    return default


def _set_setting(name: str, value: Any) -> None:
    _execute(
        """
        INSERT INTO app_settings (setting_key, setting_value)
        VALUES (?, ?)
        ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value
        """,
        (_setting_key(name), str(value or "")),
    )


def _count(table: str) -> int:
    allowed = {"employees", "builders_clients", "products"}
    if table not in allowed:
        return 0
    try:
        df = _df_query(f"SELECT COUNT(*) AS count_value FROM {table}")
        if df is not None and not getattr(df, "empty", True):
            return int(df.iloc[0]["count_value"] or 0)
    except Exception:
        pass
    return 0


def _setting_enabled(name: str) -> bool:
    value = _get_setting(name, "").strip().casefold()
    return value in {"1", "true", "yes", "connected", "configured", "on"}


def _load_brand_into_session(st: Any) -> None:
    company_name = _get_setting("company_name", "Premier Brushworks").strip() or "Premier Brushworks"
    subtitle = _get_setting("company_subtitle", "Jobs, site operations and estimating").strip() or "Jobs, site operations and estimating"
    logo_data_uri = _get_setting("company_logo_data_uri", "").strip()
    st.session_state["jobhub_company_name"] = company_name
    st.session_state["jobhub_company_subtitle"] = subtitle
    if logo_data_uri:
        st.session_state["jobhub_company_logo_data_uri"] = logo_data_uri
    else:
        try:
            st.session_state.pop("jobhub_company_logo_data_uri", None)
        except Exception:
            pass


def _safe_float(value: Any) -> float:
    try:
        if value in (None, "") or pd.isna(value):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _read_upload(uploaded: Any) -> pd.DataFrame:
    if uploaded is None:
        return pd.DataFrame()
    name = str(getattr(uploaded, "name", "") or "").lower()
    data = uploaded.getvalue()
    if name.endswith(".csv"):
        return pd.read_csv(io.BytesIO(data))
    if name.endswith(".xlsx"):
        return pd.read_excel(io.BytesIO(data), engine="openpyxl")
    raise ValueError("Upload a CSV or XLSX file.")


def _upsert_employees(rows: list[dict[str, Any]]) -> int:
    saved = 0
    for row in rows:
        name = str(row.get("name", "") or "").strip()
        if not name:
            continue
        _execute(
            """
            INSERT INTO employees (name, role, phone, email, base_hourly_rate, status, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                role=excluded.role,
                phone=excluded.phone,
                email=excluded.email,
                base_hourly_rate=excluded.base_hourly_rate,
                status=excluded.status,
                notes=excluded.notes
            """,
            (
                name,
                str(row.get("role", "") or "").strip(),
                str(row.get("phone", "") or "").strip(),
                str(row.get("email", "") or "").strip(),
                _safe_float(row.get("base_hourly_rate")),
                str(row.get("status", "") or "Active").strip() or "Active",
                str(row.get("notes", "") or "").strip(),
            ),
        )
        saved += 1
    return saved


def _upsert_builders_clients(rows: list[dict[str, Any]]) -> int:
    saved = 0
    for row in rows:
        name = str(row.get("name", "") or "").strip()
        if not name:
            continue
        _execute(
            """
            INSERT INTO builders_clients
            (type, name, contact_name, phone, email, address, qbcc, abn, terms, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                type=excluded.type,
                contact_name=excluded.contact_name,
                phone=excluded.phone,
                email=excluded.email,
                address=excluded.address,
                qbcc=excluded.qbcc,
                abn=excluded.abn,
                terms=excluded.terms,
                notes=excluded.notes
            """,
            (
                str(row.get("type", "") or "").strip(),
                name,
                str(row.get("contact_name", "") or "").strip(),
                str(row.get("phone", "") or "").strip(),
                str(row.get("email", "") or "").strip(),
                str(row.get("address", "") or "").strip(),
                str(row.get("qbcc", "") or "").strip(),
                str(row.get("abn", "") or "").strip(),
                str(row.get("terms", "") or "").strip(),
                str(row.get("notes", "") or "").strip(),
            ),
        )
        saved += 1
    return saved


def _upsert_products(rows: list[dict[str, Any]]) -> int:
    saved = 0
    for row in rows:
        product_name = str(row.get("product_name", "") or "").strip()
        if not product_name:
            continue
        product_code = str(row.get("product_code", "") or "").strip() or None
        params = (
            product_code,
            product_name,
            str(row.get("supplier", "") or "").strip(),
            str(row.get("unit", "") or "").strip(),
            _safe_float(row.get("price_ex_gst")),
            str(row.get("notes", "") or "").strip(),
        )
        if product_code:
            _execute(
                """
                INSERT INTO products (product_code, product_name, supplier, unit, price_ex_gst, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_code) DO UPDATE SET
                    product_name=excluded.product_name,
                    supplier=excluded.supplier,
                    unit=excluded.unit,
                    price_ex_gst=excluded.price_ex_gst,
                    notes=excluded.notes
                """,
                params,
            )
        else:
            _execute(
                """
                INSERT INTO products (product_code, product_name, supplier, unit, price_ex_gst, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                params,
            )
        saved += 1
    return saved


def _save_import(entity: str, rows: list[dict[str, Any]]) -> int:
    if entity == "employees":
        return _upsert_employees(rows)
    if entity == "builders_clients":
        return _upsert_builders_clients(rows)
    if entity == "products":
        return _upsert_products(rows)
    raise ValueError(f"Unsupported import entity: {entity}")


def _render_company_profile(st: Any) -> None:
    st.markdown("#### Company profile & branding")
    st.caption("These details personalise JobHub for this business. Premier Brushworks remains the default until changed.")
    with st.form("subscriber_company_profile_form"):
        c1, c2 = st.columns(2)
        company_name = c1.text_input("Business / trading name", value=_get_setting("company_name", "Premier Brushworks"))
        abn = c2.text_input("ABN", value=_get_setting("company_abn", ""))
        c3, c4 = st.columns(2)
        phone = c3.text_input("Business phone", value=_get_setting("company_phone", ""))
        email = c4.text_input("Business email", value=_get_setting("company_email", ""))
        address = st.text_input("Business address", value=_get_setting("company_address", ""))
        subtitle = st.text_input(
            "JobHub sidebar description",
            value=_get_setting("company_subtitle", "Jobs, site operations and estimating"),
        )
        save = st.form_submit_button("Save company profile", type="primary")
    if save:
        if not company_name.strip():
            st.error("Enter the business or trading name first.")
        else:
            _set_setting("company_name", company_name.strip())
            _set_setting("company_abn", abn.strip())
            _set_setting("company_phone", phone.strip())
            _set_setting("company_email", email.strip())
            _set_setting("company_address", address.strip())
            _set_setting("company_subtitle", subtitle.strip())
            _load_brand_into_session(st)
            st.success("Company profile saved.")

    logo = st.file_uploader(
        "Company logo",
        type=["png", "jpg", "jpeg"],
        key="subscriber_company_logo_upload",
        help="PNG or JPG up to 1.5 MB.",
    )
    if logo is not None:
        raw = logo.getvalue()
        if len(raw) > MAX_LOGO_BYTES:
            st.error("Logo is too large. Use an image smaller than 1.5 MB.")
        else:
            mime = "image/png" if str(logo.name).lower().endswith(".png") else "image/jpeg"
            data_uri = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
            st.image(raw, width=180)
            if st.button("Use this company logo", type="primary", key="subscriber_save_logo"):
                _set_setting("company_logo_data_uri", data_uri)
                st.session_state["jobhub_company_logo_data_uri"] = data_uri
                st.success("Company logo saved.")

    if _get_setting("company_logo_data_uri", "") and st.button("Remove saved logo", key="subscriber_remove_logo"):
        _set_setting("company_logo_data_uri", "")
        st.session_state.pop("jobhub_company_logo_data_uri", None)
        st.success("Company logo removed.")


def _render_health(st: Any) -> None:
    profile = {
        "company_name": _get_setting("company_name", "Premier Brushworks"),
        "logo_present": bool(_get_setting("company_logo_data_uri", "")),
    }
    get_app_setting = _app_attr("get_app_setting")
    rates_configured = bool(get_app_setting("default_staff_hourly_rate", "")) if callable(get_app_setting) else False
    stages_configured = bool(get_app_setting("default_internal_weight_percent", "")) if callable(get_app_setting) else False
    health = setup_health(
        company_profile=profile,
        employee_count=_count("employees"),
        builder_client_count=_count("builders_clients"),
        product_count=_count("products"),
        xero_connected=_setting_enabled("xero_connected"),
        rates_configured=rates_configured,
        stages_configured=stages_configured,
        notifications_configured=_setting_enabled("notifications_configured"),
    )
    percent = setup_completion_percent(health)
    st.markdown("#### Setup health")
    st.progress(percent / 100.0, text=f"{percent}% configured")
    labels = {
        "company_profile": "Company profile",
        "logo": "Company logo",
        "employees": "Employees",
        "builders_clients": "Builders / clients",
        "products": "Product pricing",
        "xero": "Xero connection",
        "rates": "Rates / estimating defaults",
        "job_stages": "Job stage defaults",
        "notifications": "Notifications",
    }
    columns = st.columns(3)
    for index, (key, label) in enumerate(labels.items()):
        columns[index % 3].markdown(f"{'✅' if health.get(key) else '⚪'} **{label}**")


def _render_import_panel(st: Any, entity: str, label: str) -> None:
    st.markdown(f"#### {label}")
    st.caption("Upload CSV or XLSX. JobHub maps common column names, validates required fields and previews changes before saving.")
    uploaded = st.file_uploader(
        f"Upload {label.lower()} file",
        type=["csv", "xlsx"],
        key=f"subscriber_import_{entity}",
    )
    if uploaded is None:
        return
    try:
        frame = _read_upload(uploaded)
        preview = preview_import(entity, frame)
    except Exception as exc:
        st.error(f"Could not read file: {exc}")
        return

    if preview.mapped_columns:
        mapping_df = pd.DataFrame(
            [{"Uploaded column": source, "JobHub field": target} for source, target in preview.mapped_columns.items()]
        )
        st.dataframe(mapping_df, width="stretch", hide_index=True)
    if preview.issues:
        for issue in preview.issues[:20]:
            location = "File" if issue.row_number == 0 else f"Row {issue.row_number}"
            st.error(f"{location}: {issue.message}")
    if preview.duplicate_rows:
        st.warning(
            f"{len(preview.duplicate_rows)} duplicate row(s) detected. Existing records with matching unique keys will be updated where supported."
        )
    if preview.rows:
        st.dataframe(pd.DataFrame(preview.rows).head(100), width="stretch", hide_index=True)
        st.caption(f"Previewing up to 100 rows. {len(preview.rows)} non-empty row(s) detected.")

    can_import = bool(preview.rows) and not preview.issues
    if st.button(
        f"Import {len(preview.rows)} {label.lower()} row(s)",
        type="primary",
        disabled=not can_import,
        key=f"subscriber_commit_{entity}",
    ):
        try:
            saved = _save_import(entity, preview.rows)
            st.success(f"Saved {saved} {label.lower()} row(s).")
        except Exception as exc:
            st.error(f"Import failed before completion: {exc}")


def render_subscriber_setup() -> None:
    st = _st()
    if st is None:
        return
    _ensure_schema()
    _load_brand_into_session(st)
    st.divider()
    st.subheader("Company & subscriber onboarding")
    st.caption("Set up JobHub for a new business without editing source code or manually loading its core lists.")
    _render_health(st)
    profile_tab, employees_tab, contacts_tab, products_tab, integrations_tab = st.tabs(
        ["Company", "Employees", "Builders & clients", "Products & pricing", "Integrations"]
    )
    with profile_tab:
        _render_company_profile(st)
    with employees_tab:
        _render_import_panel(st, "employees", "Employees")
    with contacts_tab:
        _render_import_panel(st, "builders_clients", "Builders & clients")
    with products_tab:
        _render_import_panel(st, "products", "Products & pricing")
    with integrations_tab:
        st.markdown("#### Xero")
        if _setting_enabled("xero_connected"):
            st.success("Xero is marked as connected for this JobHub account.")
        else:
            st.info("Xero connection is not yet configured. The secure OAuth connection flow is the next integration step; credentials and tokens will not be stored in the browser.")
        st.markdown("#### PlanReader")
        st.caption("PlanReader connection settings will be tenant-scoped as commercial account isolation is introduced.")


def install_subscriber_setup_guard() -> bool:
    original = getattr(setup_defaults_guard, "render_setup_defaults_page", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def wrapped_render_setup_defaults_page() -> None:
        original()
        render_subscriber_setup()

    wrapped_render_setup_defaults_page._pb_subscriber_setup_guard = True
    wrapped_render_setup_defaults_page._pb_original = original
    setup_defaults_guard.render_setup_defaults_page = wrapped_render_setup_defaults_page
    return True
