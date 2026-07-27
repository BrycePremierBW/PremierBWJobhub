import unittest

from jobhub_v2.idempotency import build_idempotency_key, normalise_sync_payload


def sample_payload():
    return {
        "event_type": "timesheet",
        "employee_id": 12,
        "job_id": 34,
        "occurred_at": "2026-07-27T05:00:00+00:00",
        "data": {"hours": 7.6, "notes": "External preparation"},
    }


class IdempotencyTests(unittest.TestCase):
    def test_idempotency_key_is_stable_for_equivalent_payloads(self):
        first = sample_payload()
        second = {
            "data": first["data"],
            "job_id": first["job_id"],
            "employee_id": first["employee_id"],
            "occurred_at": first["occurred_at"],
            "event_type": first["event_type"],
        }
        self.assertEqual(
            build_idempotency_key(first),
            build_idempotency_key(second),
        )

    def test_different_payloads_have_different_keys(self):
        first = sample_payload()
        second = sample_payload()
        second["data"] = {"hours": 8.0}
        self.assertNotEqual(
            build_idempotency_key(first),
            build_idempotency_key(second),
        )

    def test_unknown_event_type_is_rejected(self):
        payload = sample_payload()
        payload["event_type"] = "delete_database"
        with self.assertRaisesRegex(ValueError, "Unsupported sync event"):
            normalise_sync_payload(payload)

    def test_sync_data_must_be_an_object(self):
        payload = sample_payload()
        payload["data"] = "not-an-object"
        with self.assertRaisesRegex(ValueError, "must be an object"):
            normalise_sync_payload(payload)


if __name__ == "__main__":
    unittest.main()
