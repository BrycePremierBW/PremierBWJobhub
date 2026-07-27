from datetime import datetime, timedelta, timezone
import sqlite3
import unittest

from jobhub_v2.email_delivery import (
    CriticalEmailOutbox,
    EmailDelivery,
    build_message_key,
)
from jobhub_v2.idempotency import build_idempotency_key
from jobhub_v2.schema import ensure_v2_schema
from jobhub_v2.sync import OfflineSyncProcessor


class SharedDatabase:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")

    def connect(self):
        connection = self.connection

        class Wrapper:
            def cursor(self):
                return connection.cursor()

            def commit(self):
                return connection.commit()

            def rollback(self):
                return connection.rollback()

            def close(self):
                pass

        return Wrapper()


def sync_payload():
    return {
        "event_type": "timesheet",
        "employee_id": 7,
        "job_id": 11,
        "occurred_at": "2026-07-27T10:00:00+00:00",
        "data": {"hours": 7.6, "notes": "Preparation"},
    }


class V2SchemaTests(unittest.TestCase):
    def test_schema_is_restart_safe(self):
        database = SharedDatabase()
        ensure_v2_schema(database.connect)
        ensure_v2_schema(database.connect)
        tables = {
            row[0]
            for row in database.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        self.assertIn("offline_sync_events", tables)
        self.assertIn("critical_email_outbox", tables)


class OfflineSyncProcessorTests(unittest.TestCase):
    def setUp(self):
        self.database = SharedDatabase()
        ensure_v2_schema(self.database.connect)
        self.calls = []

        def handle_timesheet(payload, connection):
            self.calls.append(payload)
            return {"timesheet_id": 91}

        self.processor = OfflineSyncProcessor(
            self.database.connect,
            {"timesheet": handle_timesheet},
        )

    def test_duplicate_event_returns_original_result_without_second_write(self):
        payload = sync_payload()
        key = build_idempotency_key(payload)
        first = self.processor.process(payload, idempotency_key=key)
        second = self.processor.process(payload, idempotency_key=key)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["result"], {"timesheet_id": 91})
        self.assertEqual(len(self.calls), 1)

    def test_mismatched_key_is_rejected_before_handler(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.processor.process(sync_payload(), idempotency_key="wrong")
        self.assertEqual(self.calls, [])

    def test_failure_is_recorded_and_can_be_retried(self):
        attempts = []

        def flaky(payload, connection):
            attempts.append(payload)
            if len(attempts) == 1:
                raise RuntimeError("temporary failure")
            return {"ok": True}

        processor = OfflineSyncProcessor(self.database.connect, {"timesheet": flaky})
        payload = sync_payload()
        with self.assertRaisesRegex(RuntimeError, "temporary"):
            processor.process(payload)
        failed = self.database.connection.execute(
            "SELECT status, attempt_count FROM offline_sync_events"
        ).fetchone()
        self.assertEqual(failed, ("failed", 1))
        retried = processor.process(payload)
        self.assertEqual(retried["status"], "completed")
        self.assertEqual(retried["attempt_count"], 2)


class CriticalEmailOutboxTests(unittest.TestCase):
    def setUp(self):
        self.database = SharedDatabase()
        ensure_v2_schema(self.database.connect)
        self.outbox = CriticalEmailOutbox(self.database.connect)

    def test_enqueue_is_idempotent(self):
        values = {
            "event_type": "timesheet_rejected",
            "recipients": ["Manager@Example.com"],
            "subject": "Timesheet requires review",
            "text_body": "Review timesheet 42.",
            "entity_id": "42",
        }
        first = self.outbox.enqueue(**values)
        second = self.outbox.enqueue(**values)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["message_id"], second["message_id"])

    def test_delivery_marks_message_sent(self):
        self.outbox.enqueue(
            event_type="critical_alert",
            recipients=["ops@example.com"],
            subject="JobHub alert",
            text_body="A critical alert occurred.",
        )
        delivered = []

        def sender(message):
            delivered.append(message)
            return EmailDelivery("provider-123")

        summary = self.outbox.deliver_pending(sender)
        self.assertEqual(summary["sent"], 1)
        row = self.database.connection.execute(
            "SELECT status, provider_message_id, attempt_count FROM critical_email_outbox"
        ).fetchone()
        self.assertEqual(row, ("sent", "provider-123", 1))
        self.assertEqual(delivered[0]["recipients"], ["ops@example.com"])

    def test_delivery_failure_uses_backoff_and_terminal_limit(self):
        self.outbox.enqueue(
            event_type="critical_alert",
            recipients=["ops@example.com"],
            subject="JobHub alert",
            text_body="A critical alert occurred.",
        )
        now = datetime.now(timezone.utc) + timedelta(seconds=1)

        def failing_sender(_message):
            raise RuntimeError("provider unavailable")

        first = self.outbox.deliver_pending(
            failing_sender,
            max_attempts=2,
            now=now,
        )
        self.assertEqual(first["retry"], 1)
        second = self.outbox.deliver_pending(
            failing_sender,
            max_attempts=2,
            now=now + timedelta(minutes=2),
        )
        self.assertEqual(second["failed"], 1)
        status = self.database.connection.execute(
            "SELECT status, attempt_count FROM critical_email_outbox"
        ).fetchone()
        self.assertEqual(status, ("failed", 2))

    def test_message_key_normalises_recipient_case_and_order(self):
        first = build_message_key(
            event_type="alert",
            recipients=["B@example.com", "a@example.com"],
            subject="Review",
        )
        second = build_message_key(
            event_type="ALERT",
            recipients=["A@example.com", "b@example.com"],
            subject="Review",
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
