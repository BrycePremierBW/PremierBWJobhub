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


def _bootstrap_admin(db: Database) -> None:
    if int(db.scalar("SELECT COUNT(*) FROM app_users", default=0) or 0) > 0:
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
                VALUES (?,?, 'admin',1,0,?)
                """,
                (username.strip(), hash_password(password), datetime.now().isoformat()),
            )
            st.success("Administrator created. Sign in below.")
            st.rerun()


def login(db: Database) -> dict[str, Any]:
    user = st.session_state.get(SESSION_KEY)
    if isinstance(user, dict) and user.get("id"):
        return user

    st.title("Premier Brushworks JobHub")
    st.caption("Lean operations system")
    _bootstrap_admin(db)

    with st.form("jobhub_login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
    if submitted:
        frame = db.query(
            """
            SELECT u.id,u.username,u.password_hash,u.role,u.employee_id,
                   COALESCE(u.active,1) AS active,COALESCE(e.name,'') AS employee_name
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
                row.pop("password_hash", None)
                st.session_state[SESSION_KEY] = row
                st.rerun()
            else:
                st.error("Incorrect username or password.")
    st.stop()


def logout_button() -> None:
    if st.sidebar.button("Sign out", use_container_width=True):
        st.session_state.clear()
        st.rerun()


def current_user() -> dict[str, Any]:
    value = st.session_state.get(SESSION_KEY)
    return value if isinstance(value, dict) else {}


def is_admin() -> bool:
    return str(current_user().get("role") or "").lower() == "admin"


def can_manage() -> bool:
    return str(current_user().get("role") or "").lower() in {"admin", "manager"}
