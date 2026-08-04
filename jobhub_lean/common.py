from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd
import streamlit as st

from .auth import can_manage, current_user, hash_password, is_admin
from .db import Database
from .ui import header, rerun_error, rerun_success, selected_row


@dataclass(frozen=True)
class AppContext:
    db: Database
    data_dir: Path
    job_files_dir: Path
    startup_warnings: tuple[str, ...] = ()

    @property
    def user(self) -> dict[str, Any]:
        return current_user()

    def audit(self, action: str, entity_type: str, entity_id: int | None, details: str = "") -> None:
        self.db.execute(
            """
            INSERT INTO audit_events
            (user_id,username,action,entity_type,entity_id,details,created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                self.user.get("id"),
                self.user.get("username", ""),
                action,
                entity_type,
                entity_id,
                details,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

    def enterprise_context(self) -> dict[str, Any]:
        from .estimating import recalc_estimate

        return {
            "connect": self.db.connect,
            "df_query": self.db.query,
            "execute": self.db.execute,
            "execute_many": self.db.execute_many,
            "record_audit_event": lambda action, entity_type="", entity_id=None, details="": self.audit(
                action, entity_type, entity_id, details
            ),
            "recalc_estimate_totals": lambda estimate_id: recalc_estimate(self, int(estimate_id)),
            "create_management_notifications": lambda *args, **kwargs: None,
            "get_current_user": lambda: self.user,
            "save_job_photo": lambda *args, **kwargs: None,
            "pb_success": st.success,
            "pb_error": st.error,
            "pb_rerun": st.rerun,
            "DATA_DIR": str(self.data_dir),
            "JOB_FILES_DIR": str(self.job_files_dir),
            "USE_POSTGRES": self.db.postgres,
        }


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = "text"
    default: Any = ""
    options: tuple[str, ...] = ()
    required: bool = False


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    text = _clean(value)
    return text[:10] if text else ""


def _date_value(value: Any, fallback: date | None = None) -> date:
    try:
        return pd.to_datetime(value).date()
    except Exception:
        return fallback or date.today()


def _time_text(value: Any, fallback: str = "07:00") -> str:
    text = _clean(value)
    return text[:5] if re.match(r"^\d{1,2}:\d{2}", text) else fallback


def _time_value(value: Any, fallback: time = time(7, 0)) -> time:
    try:
        return datetime.strptime(_time_text(value), "%H:%M").time()
    except Exception:
        return fallback


def shift_hours(start: time, finish: time, break_hours: float) -> float:
    start_dt = datetime.combine(date.today(), start)
    finish_dt = datetime.combine(date.today(), finish)
    if finish_dt <= start_dt:
        finish_dt += timedelta(days=1)
    return max(0.0, round((finish_dt - start_dt).total_seconds() / 3600 - break_hours, 2))


def _option_map(frame: pd.DataFrame, id_col: str, label_cols: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    if frame is None or frame.empty:
        return result
    for _, row in frame.iterrows():
        label = " — ".join(_clean(row.get(col)) for col in label_cols if _clean(row.get(col)))
        if label:
            result[label] = _int(row.get(id_col))
    return result


def job_options(ctx: AppContext, include_archived: bool = False) -> dict[str, int]:
    where = "" if include_archived else "WHERE LOWER(COALESCE(j.status,'')) <> 'archived'"
    return _option_map(
        ctx.db.query(
            f"""
            SELECT j.id,j.job_no,j.job_name,COALESCE(b.name,'') AS builder
            FROM jobs j LEFT JOIN builders_clients b ON b.id=j.builder_client_id
            {where}
            ORDER BY j.job_no,j.job_name
            """
        ),
        "id",
        ("job_no", "job_name", "builder"),
    )


def employee_options(ctx: AppContext, active_only: bool = True) -> dict[str, int]:
    where = "WHERE LOWER(COALESCE(status,'Active'))='active'" if active_only else ""
    return _option_map(
        ctx.db.query(f"SELECT id,name,role FROM employees {where} ORDER BY name"),
        "id",
        ("name", "role"),
    )


def builder_options(ctx: AppContext) -> dict[str, int]:
    return _option_map(
        ctx.db.query("SELECT id,name,type FROM builders_clients ORDER BY name"),
        "id",
        ("name", "type"),
    )


def product_options(ctx: AppContext) -> dict[str, int]:
    return _option_map(
        ctx.db.query("SELECT id,product_code,product_name,supplier FROM products ORDER BY product_code"),
        "id",
        ("product_code", "product_name", "supplier"),
    )


def _widget(field: Field, value: Any, key: str) -> Any:
    if field.kind == "textarea":
        return st.text_area(field.label, value=_clean(value), key=key)
    if field.kind == "number":
        return st.number_input(field.label, value=_float(value), step=1.0, key=key)
    if field.kind == "integer":
        return st.number_input(field.label, value=_int(value), step=1, key=key)
    if field.kind == "date":
        return st.date_input(field.label, value=_date_value(value), key=key).isoformat()
    if field.kind == "select":
        options = list(field.options)
        current = _clean(value) or _clean(field.default)
        if current and current not in options:
            options.append(current)
        index = options.index(current) if current in options else 0
        return st.selectbox(field.label, options, index=index, key=key)
    return st.text_input(field.label, value=_clean(value), key=key)


def render_crud(
    ctx: AppContext,
    *,
    title: str,
    subtitle: str,
    table: str,
    fields: tuple[Field, ...],
    display_columns: tuple[str, ...],
    order_by: str,
    search_columns: tuple[str, ...],
    key: str,
    can_delete: Callable[[int], tuple[bool, str]] | None = None,
) -> None:
    header(title, subtitle)
    search = st.text_input("Search", key=f"{key}_search").strip()
    sql = f"SELECT id,{','.join(display_columns)} FROM {table}"
    params: tuple[Any, ...] = ()
    if search:
        conditions = " OR ".join(f"LOWER(COALESCE({column},'')) LIKE ?" for column in search_columns)
        sql += f" WHERE {conditions}"
        params = tuple(f"%{search.lower()}%" for _ in search_columns)
    sql += f" ORDER BY {order_by}"
    frame = ctx.db.query(sql, params)
    row = selected_row(frame, key=f"{key}_table")
    selected_key = f"{key}_selected_id"
    if row:
        st.session_state[selected_key] = _int(row.get("id"))
    selected_id = _int(st.session_state.get(selected_key))

    with st.expander(f"Add {title.rstrip('s')}", expanded=frame.empty):
        with st.form(f"{key}_add"):
            values = {field.name: _widget(field, field.default, f"{key}_add_{field.name}") for field in fields}
            submitted = st.form_submit_button("Save", type="primary")
        if submitted:
            missing = [field.label for field in fields if field.required and not _clean(values[field.name])]
            if missing:
                st.error("Required: " + ", ".join(missing))
            else:
                columns = [field.name for field in fields]
                try:
                    new_id = ctx.db.insert_id(
                        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                        tuple(values[column] for column in columns),
                    )
                    ctx.audit("create", table, new_id, json.dumps(values, default=str))
                    rerun_success(f"{title.rstrip('s')} saved.")
                except Exception as exc:
                    st.error(str(exc))

    if selected_id:
        detail = ctx.db.query(f"SELECT * FROM {table} WHERE id=?", (selected_id,))
        if detail.empty:
            st.session_state.pop(selected_key, None)
            st.rerun()
        data = detail.iloc[0].to_dict()
        with st.expander("Edit selected record", expanded=True):
            with st.form(f"{key}_edit_{selected_id}"):
                values = {field.name: _widget(field, data.get(field.name, field.default), f"{key}_edit_{selected_id}_{field.name}") for field in fields}
                save = st.form_submit_button("Update", type="primary")
            if save:
                missing = [field.label for field in fields if field.required and not _clean(values[field.name])]
                if missing:
                    st.error("Required: " + ", ".join(missing))
                else:
                    assignments = ",".join(f"{field.name}=?" for field in fields)
                    try:
                        ctx.db.execute(
                            f"UPDATE {table} SET {assignments} WHERE id=?",
                            (*[values[field.name] for field in fields], selected_id),
                        )
                        ctx.audit("update", table, selected_id, json.dumps(values, default=str))
                        rerun_success("Record updated.")
                    except Exception as exc:
                        st.error(str(exc))

            if can_manage():
                allowed, reason = can_delete(selected_id) if can_delete else (True, "")
                confirm = st.checkbox("I understand this permanently deletes the selected record", key=f"{key}_confirm_{selected_id}")
                if st.button("Delete selected record", disabled=not (allowed and confirm), key=f"{key}_delete_{selected_id}"):
                    ctx.db.execute(f"DELETE FROM {table} WHERE id=?", (selected_id,))
                    ctx.audit("delete", table, selected_id)
                    st.session_state.pop(selected_key, None)
                    rerun_success("Record deleted.")
                if not allowed and reason:
                    st.info(reason)
