"""Encrypted persistence for Xero access and rotating refresh tokens."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .xero_client import XeroToken


class TokenCipher(Protocol):
    def encrypt(self, value: str) -> str: ...
    def decrypt(self, value: str) -> str: ...


class FernetTokenCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("XERO_TOKEN_ENCRYPTION_KEY is required.")
        try:
            from cryptography.fernet import Fernet
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "The cryptography package is required for Xero token storage."
            ) from exc
        self._fernet = Fernet(key.encode("ascii"))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")


class XeroTokenStore:
    def __init__(self, connect, cipher: TokenCipher, use_postgres: bool = False) -> None:
        self.connect = connect
        self.cipher = cipher
        self.use_postgres = use_postgres

    def save(
        self,
        *,
        tenant_id: str,
        tenant_name: str,
        token: XeroToken,
        connected_by: str,
        now: datetime,
    ) -> None:
        if not tenant_id:
            raise ValueError("Xero tenant ID is required.")
        values = (
            tenant_id,
            tenant_name,
            self.cipher.encrypt(token.access_token),
            self.cipher.encrypt(token.refresh_token),
            token.expires_at.isoformat(),
            token.scope,
            connected_by,
            now.isoformat(),
            now.isoformat(),
        )
        conn = self.connect()
        try:
            cursor = conn.cursor()
            if self.use_postgres:
                cursor.execute(
                    """
                    INSERT INTO xero_connections
                    (tenant_id, tenant_name, encrypted_access_token,
                     encrypted_refresh_token, token_expires_at, scopes,
                     connected_by, connected_at, updated_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tenant_id) DO UPDATE SET
                        tenant_name = EXCLUDED.tenant_name,
                        encrypted_access_token = EXCLUDED.encrypted_access_token,
                        encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                        token_expires_at = EXCLUDED.token_expires_at,
                        scopes = EXCLUDED.scopes,
                        connected_by = EXCLUDED.connected_by,
                        updated_at = EXCLUDED.updated_at
                    """,
                    values,
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO xero_connections
                    (tenant_id, tenant_name, encrypted_access_token,
                     encrypted_refresh_token, token_expires_at, scopes,
                     connected_by, connected_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (tenant_id) DO UPDATE SET
                        tenant_name = excluded.tenant_name,
                        encrypted_access_token = excluded.encrypted_access_token,
                        encrypted_refresh_token = excluded.encrypted_refresh_token,
                        token_expires_at = excluded.token_expires_at,
                        scopes = excluded.scopes,
                        connected_by = excluded.connected_by,
                        updated_at = excluded.updated_at
                    """,
                    values,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def load(self, tenant_id: str) -> XeroToken | None:
        conn = self.connect()
        try:
            cursor = conn.cursor()
            placeholder = "%s" if self.use_postgres else "?"
            cursor.execute(
                f"""
                SELECT encrypted_access_token, encrypted_refresh_token,
                       token_expires_at, scopes
                FROM xero_connections
                WHERE tenant_id = {placeholder}
                """,
                (tenant_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return XeroToken(
                access_token=self.cipher.decrypt(str(row[0])),
                refresh_token=self.cipher.decrypt(str(row[1])),
                expires_at=datetime.fromisoformat(str(row[2])),
                scope=str(row[3] or ""),
            )
        finally:
            conn.close()
