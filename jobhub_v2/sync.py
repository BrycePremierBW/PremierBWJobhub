"""Transaction-safe processing for queued Field Mode events."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable
from uuid import uuid4

from .idempotency import build_idempotency_key, normalise_sync_payload
from .schema import ensure_v2_schema


SyncHandler = Callable[[dict[str, Any], Any], dict[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OfflineSyncProcessor:
    """Persist and process each mobile event at most once."""

    def __init__(
        self,
        connection_factory,
        handlers: dict[str, SyncHandler],
    ) -> None:
        self.connection_factory = connection_factory
        self.handlers = dict(handlers)

    def ensure_schema(self) -> None:
        ensure_v2_schema(self.connection_factory)

    def process(
        self,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        normalised = normalise_sync_payload(payload)
        event_type = normalised["event_type"]
        if event_type not in self.handlers:
            raise ValueError(f"No server handler is registered for {event_type}.")
        expected_key = build_idempotency_key(normalised)
        supplied_key = str(idempotency_key or expected_key).strip()
        if supplied_key != expected_key:
            raise ValueError("The idempotency key does not match the event payload.")

        connection = self.connection_factory()
        cursor = connection.cursor()
        timestamp = _now()
        event_id = str(uuid4())
        payload_json = json.dumps(
            normalised,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        attempt_count = 1
        try:
            cursor.execute(
                """
                INSERT INTO offline_sync_events
                (id, idempotency_key, event_type, employee_id, job_id,
                 occurred_at, payload_json, status, attempt_count,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (
                    event_id,
                    supplied_key,
                    event_type,
                    normalised["employee_id"],
                    normalised["job_id"],
                    normalised["occurred_at"],
                    payload_json,
                    timestamp,
                    timestamp,
                ),
            )
            inserted = int(cursor.rowcount or 0) > 0
            if not inserted:
                cursor.execute(
                    """
                    SELECT id, status, result_json, last_error, attempt_count
                    FROM offline_sync_events
                    WHERE idempotency_key = ?
                    """,
                    (supplied_key,),
                )
                existing = cursor.fetchone()
                if not existing:
                    raise RuntimeError("The duplicate sync event could not be loaded.")
                status = str(existing[1] or "")
                if status == "completed":
                    connection.commit()
                    return {
                        "event_id": str(existing[0]),
                        "status": status,
                        "duplicate": True,
                        "result": json.loads(existing[2] or "{}"),
                        "attempt_count": int(existing[4] or 0),
                    }
                if status in {"pending", "processing"}:
                    connection.commit()
                    return {
                        "event_id": str(existing[0]),
                        "status": status,
                        "duplicate": True,
                        "result": None,
                        "attempt_count": int(existing[4] or 0),
                    }
                event_id = str(existing[0])
                attempt_count = int(existing[4] or 0) + 1

            cursor.execute(
                """
                UPDATE offline_sync_events
                SET status = 'processing', attempt_count = attempt_count + 1,
                    last_error = '', updated_at = ?
                WHERE id = ?
                """,
                (_now(), event_id),
            )
            # Make the idempotency claim visible before invoking a handler.
            # Handler database writes and the completed marker then commit
            # together; a handler failure is rolled back and recorded safely.
            connection.commit()
            result = self.handlers[event_type](normalised, connection)
            if not isinstance(result, dict):
                raise TypeError("A sync handler must return an object.")
            completed_at = _now()
            cursor.execute(
                """
                UPDATE offline_sync_events
                SET status = 'completed', result_json = ?, last_error = '',
                    completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(result, sort_keys=True, ensure_ascii=False, default=str),
                    completed_at,
                    completed_at,
                    event_id,
                ),
            )
            connection.commit()
            return {
                "event_id": event_id,
                "status": "completed",
                "duplicate": False,
                "result": result,
                "attempt_count": attempt_count,
            }
        except Exception as exc:
            try:
                connection.rollback()
                failure_cursor = connection.cursor()
                failure_cursor.execute(
                    """
                    UPDATE offline_sync_events
                    SET status = 'failed', last_error = ?, updated_at = ?
                    WHERE idempotency_key = ?
                    """,
                    (str(exc)[:2000], _now(), supplied_key),
                )
                connection.commit()
                failure_cursor.close()
            except Exception:
                connection.rollback()
            raise
        finally:
            cursor.close()
            connection.close()
