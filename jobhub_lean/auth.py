from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime
from typing import Any

import streamlit as st

from .db import Database

try:
    from jobhub_core import hash_password as _secure_hash
    from jobhub_core import verify_password as _secure_verify
except Exception:  # pragma: no cover
    _secure_hash = None
    _secure_verify = None


SESSION_KEY = "jobhub_user"
LEGACY_SESSION_KEY = "user"


def hash_password(password: str) -> str:
    if _secure_hash is not None:
        return str(_secure_hash(password))
    salt = os.urandom(16)
    rounds = 260_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds).hex()
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest}"


def verify_password(password: str, stored: str) -> bool:
    if _secure_verify is not None:
        try:
            return bool(_secure_verify(password, stored))
        except Exception:
            pass
    try:
        text = str(stored or "")
        if text.startswith("pbkdf2_sha256$"):
            _, rounds, salt, expected = text.split("$", 3)
            digest = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), bytes.fromhex(salt), int(rounds)
            ).hex()
            return hmac.compare_digest(digest, expected)
        return hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), text)
    except Exception:
        return False


def _store_user(user: dict[str, Any]) -> dict[str, Any]:
    """Keep the lean and legacy session keys aligned for retained modules."""
    clean = dict(user)
    clean.pop("password_hash", None)
    st.session_state[SESSION_KEY] = clean
    st.session_state[LEGACY_SESSION_KEY] = clean
    return clean


def _session_user() -> dict[str, Any]:
    lean = st.session_state.get(SESSION_KEY)
    legacy = st.session_state.get(LEGACY_SESSION_KEY)
    if isinstance(lean, dict) and lean.get("id"):
        if not isinstance(legacy, dict) or legacy.get("id") != lean.get("id"):
            st.session_state[LEGACY_SESSION_KEY] = dict(lean)
        return lean
    if isinstance(legacy, dict) and legacy.get("id"):
        st.session_state[SESSION_KEY] = dict(legacy)
        return legacy
    return {}


def _bootstrap_admin(db: Database) -> None:
    if int(db.scalar("SELECT COUNT(*) FROM app_users", default=0) or 0) > 0:
        return

    bootstrap_password = os.getenv("JOBHUB_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    bootstrap_username = os.getenv("JOBHUB_BOOTSTRAP_ADMIN_USERNAME", "admin").strip() or "admin"
    if bootstrap_password:
        db.execute(
            """
            INSERT INTO app_users
            (username,password_hash,role,active,must_change_password,password_changed_at,notes)
            VALUES (?,?,'admin',1,1,?,?)
            """,
            (
                bootstrap_username,
                hash_password(bootstrap_password),
                datetime.now().isoformat(timespec="seconds"),
                "Created from JOBHUB_BOOTSTRAP_ADMIN_PASSWORD.",
            ),
        )
        return

    st.warning("No JobHub user exists yet. Create the first administrator.")
    with st.form("bootstrap_admin"):
        username = st.text_input("Administrator username", value="admin")
        password = st.text_input("Administrator password", type="password")
        confirm = st.text_input("Confirm password", type="password")
        submitted = st.form_submit_button("Create administrator", type="primary")
    if submitted:
        if len(password) < 10:
            st.error("Use at least 10 characters.")
        elif password != confirm:
            st.error("Passwords do not match.")
        elif not username.strip():
            st.error("Enter a username.")
        else:
            db.execute(
                """
                INSERT INTO app_users
                (username,password_hash,role,active,must_change_password,password_changed_at)
                VALUES (?,?,'admin',1,0,?)
                """,
                (username.strip(), hash_password(password), datetime.now().isoformat(timespec="seconds")),
            )
            st.success("Administrator created. Sign in below.")
            st.rerun()


def login(db: Database) -> dict[str, Any]:
    user = _session_user()
    if user:
        return user

    _bootstrap_admin(db)
    st.title("Premier Brushworks JobHub")
    st.caption("Lean operations system")

    with st.form("jobhub_login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
    if submitted:
        frame = db.query(
            """
            SELECT u.id,u.username,u.password_hash,u.role,u.employee_id,
                   COALESCE(u.active,1) AS active,
                   COALESCE(u.must_change_password,0) AS must_change_password,
                   COALESCE(u.notes,'') AS notes,
                   COALESCE(e.name,'') AS employee_name
            FROM app_users u
            LEFT JOIN employees e ON e.id=u.employee_id
            WHERE LOWER(TRIM(u.username))=LOWER(TRIM(?))
            LIMIT 1
            """,
            (username,),
        )
        if frame.empty or not int(frame.iloc[0].get("active", 1) or 0):
            st.error("Incorrect username or password.")
        else:
            row = frame.iloc[0].to_dict()
            if verify_password(password, str(row.get("password_hash") or "")):
                _store_user(row)
                st.rerun()
            else:
                st.error("Incorrect username or password.")
    st.stop()


def logout_button() -> None:
    if st.sidebar.button("Sign out", use_container_width=True):
        st.session_state.pop(SESSION_KEY, None)
        st.session_state.pop(LEGACY_SESSION_KEY, None)
        st.rerun()


def current_user() -> dict[str, Any]:
    return _session_user()


def is_admin() -> bool:
    return str(current_user().get("role") or "").lower() == "admin"


def can_manage() -> bool:
    return str(current_user().get("role") or "").lower() in {"admin", "manager"}
