"""Idempotent outbox and retry rules for critical email notifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Callable, Iterable
from uuid import uuid4

from .schema import ensure_v2_schema


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _normalise_recipients(recipients: Iterable[str]) -> list[str]:
    normalised = sorted(
        {
            str(value or "").strip().casefold()
            for value in recipients
            if str(value or "").strip()
        }
    )
    if not normalised:
        raise ValueError("At least one email recipient is required.")
    if any("@" not in value or value.startswith("@") or value.endswith("@") for value in normalised):
        raise ValueError("Every email recipient must be a valid address.")
    return normalised


def build_message_key(
    *,
    event_type: str,
    recipients: Iterable[str],
    subject: str,
    entity_id: str = "",
) -> str:
    payload = {
        "event_type": str(event_type or "").strip().casefold(),
        "recipients": _normalise_recipients(recipients),
        "subject": str(subject or "").strip(),
        "entity_id": str(entity_id or "").strip(),
    }
    if not payload["event_type"] or not payload["subject"]:
        raise ValueError("Event type and subject are required.")
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class EmailDelivery:
    provider_message_id: str


class CriticalEmailOutbox:
    def __init__(self, connection_factory) -> None:
        self.connection_factory = connection_factory

    def ensure_schema(self) -> None:
        ensure_v2_schema(self.connection_factory)

    def enqueue(
        self,
        *,
        event_type: str,
        recipients: Iterable[str],
        subject: str,
        text_body: str,
        html_body: str = "",
        entity_id: str = "",
    ) -> dict[str, Any]:
        recipient_list = _normalise_recipients(recipients)
        subject = str(subject or "").strip()
        text_body = str(text_body or "").strip()
        if not subject or not text_body:
            raise ValueError("Email subject and text body are required.")
        message_key = build_message_key(
            event_type=event_type,
            recipients=recipient_list,
            subject=subject,
            entity_id=entity_id,
        )
        message_id = str(uuid4())
        timestamp = _iso(_now())
        connection = self.connection_factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO critical_email_outbox
                (id, message_key, event_type, recipient_json, subject,
                 text_body, html_body, status, attempt_count,
                 next_attempt_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
                ON CONFLICT(message_key) DO NOTHING
                """,
                (
                    message_id,
                    message_key,
                    str(event_type or "").strip().casefold(),
                    json.dumps(recipient_list),
                    subject,
                    text_body,
                    str(html_body or ""),
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            inserted = int(cursor.rowcount or 0) > 0
            cursor.execute(
                """
                SELECT id, status, attempt_count
                FROM critical_email_outbox
                WHERE message_key = ?
                """,
                (message_key,),
            )
            row = cursor.fetchone()
            connection.commit()
            return {
                "message_id": str(row[0]),
                "message_key": message_key,
                "status": str(row[1]),
                "attempt_count": int(row[2] or 0),
                "duplicate": not inserted,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()

    def deliver_pending(
        self,
        sender: Callable[[dict[str, Any]], EmailDelivery | str],
        *,
        limit: int = 25,
        max_attempts: int = 5,
        now: datetime | None = None,
    ) -> dict[str, int]:
        check_time = now or _now()
        connection = self.connection_factory()
        cursor = connection.cursor()
        summary = {"sent": 0, "retry": 0, "failed": 0}
        try:
            cursor.execute(
                """
                SELECT id, event_type, recipient_json, subject, text_body,
                       html_body, attempt_count
                FROM critical_email_outbox
                WHERE status IN ('pending', 'retry')
                  AND (next_attempt_at IS NULL OR next_attempt_at = '' OR next_attempt_at <= ?)
                ORDER BY created_at
                LIMIT ?
                """,
                (_iso(check_time), int(limit)),
            )
            rows = cursor.fetchall()
            for row in rows:
                message = {
                    "id": str(row[0]),
                    "event_type": str(row[1]),
                    "recipients": json.loads(row[2] or "[]"),
                    "subject": str(row[3]),
                    "text_body": str(row[4]),
                    "html_body": str(row[5] or ""),
                }
                attempt = int(row[6] or 0) + 1
                try:
                    delivery = sender(message)
                    provider_id = (
                        delivery.provider_message_id
                        if isinstance(delivery, EmailDelivery)
                        else str(delivery or "")
                    )
                    sent_at = _iso(check_time)
                    cursor.execute(
                        """
                        UPDATE critical_email_outbox
                        SET status = 'sent', attempt_count = ?,
                            provider_message_id = ?, last_error = '',
                            sent_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (attempt, provider_id, sent_at, sent_at, message["id"]),
                    )
                    summary["sent"] += 1
                except Exception as exc:
                    terminal = attempt >= max_attempts
                    delay_minutes = min(60, 2 ** max(0, attempt - 1))
                    next_attempt = _iso(check_time + timedelta(minutes=delay_minutes))
                    cursor.execute(
                        """
                        UPDATE critical_email_outbox
                        SET status = ?, attempt_count = ?, last_error = ?,
                            next_attempt_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            "failed" if terminal else "retry",
                            attempt,
                            str(exc)[:2000],
                            next_attempt,
                            _iso(check_time),
                            message["id"],
                        ),
                    )
                    summary["failed" if terminal else "retry"] += 1
            connection.commit()
            return summary
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
