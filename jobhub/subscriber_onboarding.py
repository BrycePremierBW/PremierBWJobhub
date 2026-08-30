"""Commercial subscriber onboarding helpers for JobHub.

This module keeps import validation and setup-health logic separate from the
Streamlit UI so future tenant-aware onboarding screens can reuse the same rules.
It deliberately does not write to the database; callers review validated rows
before committing them through the normal JobHub data layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


IMPORT_SCHEMAS = {
    "employees": {
        "required": ["name"],
        "optional": ["role", "phone", "email", "base_hourly_rate", "status", "notes"],
        "aliases": {
            "employee": "name",
            "employee_name": "name",
            "full_name": "name",
            "mobile": "phone",
            "mobile_phone": "phone",
            "hourly_rate": "base_hourly_rate",
            "rate": "base_hourly_rate",
        },
        "duplicate_key": "name",
    },
    "builders_clients": {
        "required": ["name"],
        "optional": ["type", "contact_name", "phone", "email", "address", "qbcc", "abn", "terms", "notes"],
        "aliases": {
            "builder": "name",
            "client": "name",
            "company": "name",
            "business_name": "name",
            "contact": "contact_name",
            "mobile": "phone",
        },
        "duplicate_key": "name",
    },
    "products": {
        "required": ["product_name"],
        "optional": ["product_code", "supplier", "unit", "price_ex_gst", "notes"],
        "aliases": {
            "code": "product_code",
            "sku": "product_code",
            "product": "product_name",
            "description": "product_name",
            "price": "price_ex_gst",
            "cost": "price_ex_gst",
            "unit_price": "price_ex_gst",
        },
        "duplicate_key": "product_code",
    },
}


@dataclass(frozen=True)
class ImportIssue:
    row_number: int
    field: str
    message: str


@dataclass(frozen=True)
class ImportPreview:
    entity: str
    rows: list[dict[str, Any]]
    issues: list[ImportIssue]
    duplicate_rows: list[int]
    accepted_rows: list[int]
    source_columns: list[str]
    mapped_columns: dict[str, str]

    @property
    def ready_to_import(self) -> bool:
        return bool(self.accepted_rows) and not self.issues


def normalise_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    chars = []
    previous_underscore = False
    for char in text:
        if char.isalnum():
            chars.append(char)
            previous_underscore = False
        elif not previous_underscore:
            chars.append("_")
            previous_underscore = True
    return "".join(chars).strip("_")


def canonical_column_map(entity: str, columns: Iterable[Any]) -> dict[str, str]:
    schema = IMPORT_SCHEMAS.get(entity)
    if schema is None:
        raise ValueError(f"Unsupported import entity: {entity}")

    valid = set(schema["required"] + schema["optional"])
    aliases = schema["aliases"]
    mapped: dict[str, str] = {}
    used_targets: set[str] = set()

    for original in columns:
        normalised = normalise_header(original)
        target = aliases.get(normalised, normalised)
        if target in valid and target not in used_targets:
            mapped[str(original)] = target
            used_targets.add(target)
    return mapped


def _clean_cell(value: Any) -> Any:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    if isinstance(value, str):
        return value.strip()
    return value


def _normalise_identity(value: Any) -> str:
    return str(_clean_cell(value) or "").strip().casefold()


def preview_import(
    entity: str,
    dataframe: pd.DataFrame,
    existing_keys: Sequence[Any] | None = None,
) -> ImportPreview:
    schema = IMPORT_SCHEMAS.get(entity)
    if schema is None:
        raise ValueError(f"Unsupported import entity: {entity}")

    if dataframe is None:
        dataframe = pd.DataFrame()

    source_columns = [str(column) for column in dataframe.columns]
    mapping = canonical_column_map(entity, source_columns)
    missing_required = [field for field in schema["required"] if field not in mapping.values()]
    issues: list[ImportIssue] = []

    if missing_required:
        for field in missing_required:
            issues.append(ImportIssue(0, field, f"Required column '{field}' was not found."))
        return ImportPreview(entity, [], issues, [], [], source_columns, mapping)

    rows: list[dict[str, Any]] = []
    duplicate_rows: list[int] = []
    accepted_rows: list[int] = []
    seen = {_normalise_identity(value) for value in (existing_keys or []) if _normalise_identity(value)}
    duplicate_key = schema["duplicate_key"]

    for frame_index, (_, source_row) in enumerate(dataframe.iterrows(), start=2):
        row: dict[str, Any] = {}
        for source, target in mapping.items():
            row[target] = _clean_cell(source_row.get(source, ""))

        if not any(str(value or "").strip() for value in row.values()):
            continue

        row_has_issue = False
        for field in schema["required"]:
            if not str(row.get(field, "") or "").strip():
                issues.append(ImportIssue(frame_index, field, f"{field.replace('_', ' ').title()} is required."))
                row_has_issue = True

        duplicate_value = row.get(duplicate_key, "")
        if entity == "products" and not str(duplicate_value or "").strip():
            duplicate_value = row.get("product_name", "")

        identity = _normalise_identity(duplicate_value)
        if identity and identity in seen:
            duplicate_rows.append(frame_index)
        elif identity:
            seen.add(identity)

        rows.append(row)
        if not row_has_issue and frame_index not in duplicate_rows:
            accepted_rows.append(frame_index)

    return ImportPreview(entity, rows, issues, duplicate_rows, accepted_rows, source_columns, mapping)


def import_template(entity: str) -> pd.DataFrame:
    schema = IMPORT_SCHEMAS.get(entity)
    if schema is None:
        raise ValueError(f"Unsupported import entity: {entity}")
    return pd.DataFrame(columns=schema["required"] + schema["optional"])


def setup_health(
    company_profile: Mapping[str, Any] | None = None,
    employee_count: int = 0,
    builder_client_count: int = 0,
    product_count: int = 0,
    xero_connected: bool = False,
    rates_configured: bool = False,
    stages_configured: bool = False,
    notifications_configured: bool = False,
) -> dict[str, bool]:
    profile = dict(company_profile or {})
    return {
        "company_profile": bool(str(profile.get("company_name", "")).strip()),
        "logo": bool(profile.get("logo_present") or profile.get("logo_path") or profile.get("logo_url")),
        "employees": int(employee_count or 0) > 0,
        "builders_clients": int(builder_client_count or 0) > 0,
        "products": int(product_count or 0) > 0,
        "xero": bool(xero_connected),
        "rates": bool(rates_configured),
        "job_stages": bool(stages_configured),
        "notifications": bool(notifications_configured),
    }


def setup_completion_percent(health: Mapping[str, bool]) -> int:
    values = [bool(value) for value in health.values()]
    if not values:
        return 0
    return round(100 * sum(values) / len(values))
