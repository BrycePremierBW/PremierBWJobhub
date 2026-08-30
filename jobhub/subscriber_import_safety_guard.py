"""Safer commercial onboarding imports for JobHub.

Replaces the first-pass subscriber import panel with a preview-first workflow
that provides downloadable templates, distinguishes existing records from
within-file duplicates and defaults to adding new records without overwriting
existing company data.
"""

from __future__ import annotations

import sys
from typing import Any

import pandas as pd

from . import subscriber_setup_guard
from .subscriber_onboarding import IMPORT_SCHEMAS, import_template, preview_import


PATCH_MARKER = "_pb_subscriber_import_safety_guard"


def _st() -> Any:
    return sys.modules.get("streamlit")


def _normalise(value: Any) -> str:
    return str(value or "").strip().casefold()


def template_csv_bytes(entity: str) -> bytes:
    return import_template(entity).to_csv(index=False).encode("utf-8")


def _existing_keys(entity: str) -> set[str]:
    try:
        if entity == "employees":
            df = subscriber_setup_guard._df_query("SELECT name FROM employees")
            return {_normalise(value) for value in df["name"].tolist() if _normalise(value)}
        if entity == "builders_clients":
            df = subscriber_setup_guard._df_query("SELECT name FROM builders_clients")
            return {_normalise(value) for value in df["name"].tolist() if _normalise(value)}
        if entity == "products":
            df = subscriber_setup_guard._df_query("SELECT product_code, product_name FROM products")
            result: set[str] = set()
            for _, row in df.iterrows():
                code = _normalise(row.get("product_code", ""))
                name = _normalise(row.get("product_name", ""))
                if code:
                    result.add(code)
                elif name:
                    result.add(name)
            return result
    except Exception:
        return set()
    return set()


def _row_identity(entity: str, row: dict[str, Any]) -> str:
    schema = IMPORT_SCHEMAS[entity]
    key = str(schema["duplicate_key"])
    value = _normalise(row.get(key, ""))
    if entity == "products" and not value:
        value = _normalise(row.get("product_name", ""))
    return value


def partition_rows(
    entity: str,
    rows: list[dict[str, Any]],
    existing_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[int]]:
    """Return new rows, existing rows and 1-based duplicate positions in upload data."""
    existing = {_normalise(value) for value in (existing_keys or set()) if _normalise(value)}
    seen: set[str] = set()
    new_rows: list[dict[str, Any]] = []
    matched_rows: list[dict[str, Any]] = []
    duplicate_positions: list[int] = []

    for position, row in enumerate(rows, start=1):
        identity = _row_identity(entity, row)
        if identity and identity in seen:
            duplicate_positions.append(position)
            continue
        if identity:
            seen.add(identity)
        if identity and identity in existing:
            matched_rows.append(row)
        else:
            new_rows.append(row)
    return new_rows, matched_rows, duplicate_positions


def _render_import_panel(st: Any, entity: str, label: str) -> None:
    st.markdown(f"#### {label}")
    st.caption(
        "Use the template or upload your existing CSV/XLSX. JobHub maps common headings, validates the file and shows exactly what will be added or updated before saving."
    )
    st.download_button(
        f"Download {label.lower()} import template",
        data=template_csv_bytes(entity),
        file_name=f"jobhub_{entity}_import_template.csv",
        mime="text/csv",
        key=f"subscriber_template_{entity}",
    )
    uploaded = st.file_uploader(
        f"Upload {label.lower()} file",
        type=["csv", "xlsx"],
        key=f"subscriber_import_{entity}",
    )
    if uploaded is None:
        return

    try:
        frame = subscriber_setup_guard._read_upload(uploaded)
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

    existing = _existing_keys(entity)
    new_rows, matched_rows, within_file_duplicates = partition_rows(entity, preview.rows, existing)
    if within_file_duplicates:
        st.error(
            "The upload contains duplicate unique records at import positions: "
            + ", ".join(str(value) for value in within_file_duplicates[:20])
            + ". Remove or combine duplicates before importing."
        )

    c1, c2, c3 = st.columns(3)
    c1.metric("New", len(new_rows))
    c2.metric("Already in JobHub", len(matched_rows))
    c3.metric("Rows in file", len(preview.rows))

    mode = st.radio(
        "How should existing matching records be handled?",
        ["Add new only", "Update matching records too"],
        horizontal=True,
        key=f"subscriber_import_mode_{entity}",
        help="Add new only is safest. Choose update only when this file is intended to refresh existing company data or pricing.",
    )
    rows_to_save = list(new_rows)
    if mode == "Update matching records too":
        rows_to_save.extend(matched_rows)

    if preview.rows:
        preview_frame = pd.DataFrame(preview.rows).head(100)
        st.dataframe(preview_frame, width="stretch", hide_index=True)
        st.caption(f"Previewing up to 100 rows. {len(preview.rows)} non-empty row(s) detected.")

    if matched_rows and mode == "Add new only":
        st.info(f"{len(matched_rows)} existing matching record(s) will be left unchanged.")
    elif matched_rows:
        st.warning(f"{len(matched_rows)} existing matching record(s) will be updated with values from this upload.")

    can_import = bool(rows_to_save) and not preview.issues and not within_file_duplicates
    if st.button(
        f"Import {len(rows_to_save)} {label.lower()} row(s)",
        type="primary",
        disabled=not can_import,
        key=f"subscriber_commit_{entity}",
    ):
        try:
            saved = subscriber_setup_guard._save_import(entity, rows_to_save)
            skipped = len(preview.rows) - len(rows_to_save)
            message = f"Saved {saved} {label.lower()} row(s)."
            if skipped:
                message += f" Left {skipped} existing record(s) unchanged."
            st.success(message)
        except Exception as exc:
            st.error(f"Import failed: {exc}")


def install_subscriber_import_safety_guard() -> bool:
    original = getattr(subscriber_setup_guard, "_render_import_panel", None)
    if original is None or getattr(original, PATCH_MARKER, False):
        return False

    def safe_render_import_panel(st: Any, entity: str, label: str) -> None:
        return _render_import_panel(st, entity, label)

    safe_render_import_panel._pb_subscriber_import_safety_guard = True
    safe_render_import_panel._pb_original = original
    subscriber_setup_guard._render_import_panel = safe_render_import_panel
    return True
