"""Non-destructive organisation schema foundation for commercial JobHub.

This creates tenant/company metadata alongside the existing production schema
without changing the behaviour of current Premier Brushworks records. Data-table
scoping is intentionally a later migration so it can be rolled out with focused
backfill and query-isolation regressions rather than silently altering live
business queries.
"""

from __future__ import annotations

import re
import sys
from typing import Any


DEFAULT_ORGANIZATION_SLUG = "premier-brushworks"
DEFAULT_ORGANIZATION_NAME = "Premier Brushworks"
SCHEMA_VERSION = "1"


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


def slugify_organization_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:80]


def ensure_organization_schema() -> bool:
    pk = "SERIAL PRIMARY KEY" if _use_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    _execute(
        f"""
        CREATE TABLE IF NOT EXISTS organizations (
            id {pk},
            organization_slug TEXT NOT NULL UNIQUE,
            company_name TEXT NOT NULL,
            trading_name TEXT,
            abn TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            logo_data_uri TEXT,
            subscription_status TEXT NOT NULL DEFAULT 'Active',
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    _execute(
        f"""
        CREATE TABLE IF NOT EXISTS organization_settings (
            id {pk},
            organization_id INTEGER NOT NULL,
            setting_key TEXT NOT NULL,
            setting_value TEXT,
            updated_at TEXT,
            UNIQUE(organization_id, setting_key),
            FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE
        )
        """
    )
    _execute(
        f"""
        CREATE TABLE IF NOT EXISTS organization_integrations (
            id {pk},
            organization_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Disconnected',
            external_tenant_id TEXT,
            external_tenant_name TEXT,
            encrypted_token_payload TEXT,
            scopes TEXT,
            connected_at TEXT,
            refreshed_at TEXT,
            disconnected_at TEXT,
            notes TEXT,
            UNIQUE(organization_id, provider),
            FOREIGN KEY(organization_id) REFERENCES organizations(id) ON DELETE CASCADE
        )
        """
    )
    _execute(
        "CREATE INDEX IF NOT EXISTS idx_org_integrations_provider_status "
        "ON organization_integrations(provider, status)"
    )
    _execute(
        """
        INSERT INTO organizations (organization_slug, company_name, trading_name, subscription_status)
        VALUES (?, ?, ?, 'Active')
        ON CONFLICT(organization_slug) DO UPDATE SET
            company_name=excluded.company_name,
            trading_name=excluded.trading_name
        """,
        (DEFAULT_ORGANIZATION_SLUG, DEFAULT_ORGANIZATION_NAME, DEFAULT_ORGANIZATION_NAME),
    )
    _execute(
        """
        INSERT INTO app_settings (setting_key, setting_value)
        VALUES ('organization_schema_version', ?)
        ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value
        """,
        (SCHEMA_VERSION,),
    )
    return True


def get_organization_id(slug: str = DEFAULT_ORGANIZATION_SLUG) -> int | None:
    try:
        df = _df_query(
            "SELECT id FROM organizations WHERE organization_slug=? LIMIT 1",
            (str(slug or DEFAULT_ORGANIZATION_SLUG),),
        )
        if df is not None and not getattr(df, "empty", True):
            return int(df.iloc[0]["id"])
    except Exception:
        return None
    return None


def install_organization_schema_guard() -> bool:
    """Install after normal DB bootstrap; do no work during package import."""
    return True
