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
