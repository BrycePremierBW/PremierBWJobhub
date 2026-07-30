"""Signed, expiring OAuth state values for Xero callback protection."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


class OAuthStateSigner:
    def __init__(self, secret: str, max_age_seconds: int = 600) -> None:
        if len(secret) < 32:
            raise ValueError("OAuth state secret must contain at least 32 characters.")
        self.secret = secret.encode("utf-8")
        self.max_age_seconds = max_age_seconds

    @staticmethod
    def _encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    def issue(self, user_id: str, nonce: str, now: int | None = None) -> str:
        if not user_id or not nonce:
            raise ValueError("OAuth state requires a user and nonce.")
        payload = {
            "user_id": str(user_id),
            "nonce": str(nonce),
            "issued_at": int(time.time() if now is None else now),
        }
        encoded = self._encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = self._encode(
            hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
        )
        return f"{encoded}.{signature}"

    def verify(self, state: str, now: int | None = None) -> dict[str, Any]:
        try:
            encoded, supplied_signature = state.split(".", 1)
        except ValueError as exc:
            raise ValueError("Invalid OAuth state format.") from exc
        expected_signature = self._encode(
            hmac.new(self.secret, encoded.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("Invalid OAuth state signature.")
        payload = json.loads(self._decode(encoded).decode("utf-8"))
        check_time = int(time.time() if now is None else now)
        age = check_time - int(payload["issued_at"])
        if age < 0 or age > self.max_age_seconds:
            raise ValueError("OAuth state has expired.")
        return payload


class OAuthNonceStore:
    """One-use OAuth nonce register shared across Streamlit reruns."""

    def __init__(self, connect, use_postgres: bool = False) -> None:
        self.connect = connect
        self.use_postgres = use_postgres

    @staticmethod
    def _hash(nonce: str) -> str:
        return hashlib.sha256(nonce.encode("utf-8")).hexdigest()

    def register(
        self,
        user_id: str,
        nonce: str,
        *,
        created_at: str,
        expires_at: str,
    ) -> None:
        placeholder = "%s" if self.use_postgres else "?"
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                INSERT INTO xero_oauth_nonces
                (nonce_hash, user_id, expires_at, consumed_at, created_at)
                VALUES ({placeholder},{placeholder},{placeholder},NULL,{placeholder})
                """,
                (self._hash(nonce), str(user_id), expires_at, created_at),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def consume(self, user_id: str, nonce: str, *, consumed_at: str) -> bool:
        placeholder = "%s" if self.use_postgres else "?"
        conn = self.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT user_id, expires_at, consumed_at
                FROM xero_oauth_nonces
                WHERE nonce_hash = {placeholder}
                """,
                (self._hash(nonce),),
            )
            row = cursor.fetchone()
            if not row or str(row[0]) != str(user_id) or row[2]:
                return False
            if datetime_from_iso(str(row[1])) < datetime_from_iso(consumed_at):
                return False
            cursor.execute(
                f"""
                UPDATE xero_oauth_nonces
                SET consumed_at = {placeholder}
                WHERE nonce_hash = {placeholder} AND consumed_at IS NULL
                """,
                (consumed_at, self._hash(nonce)),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return False
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def datetime_from_iso(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
