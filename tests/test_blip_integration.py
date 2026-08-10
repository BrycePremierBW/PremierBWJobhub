import os
import unittest
from unittest.mock import patch

from jobhub.blip_integration_guard import (
    ENV_ATTENDANCE_URL,
    ENV_CLIENT_ID,
    ENV_CLIENT_SECRET,
    ENV_TOKEN_AUTH_MODE,
    ENV_TOKEN_URL,
    _request_token,
    _safe_error,
    attendance_status,
    configuration_ready,
    normalise_blip_record,
    normalise_blip_records,
    source_hash,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            response = self
            raise requests.HTTPError("sensitive https://example.invalid/token", response=response)

    def json(self):
        return self._payload


class _Session:
    def __init__(self):
        self.post_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _Response({"access_token": "secret-access-token"})


class BrightHRBlipIntegrationTests(unittest.TestCase):
    def test_normalises_nested_attendance(self):
        row = normalise_blip_record(
            {
                "attendanceId": "att-1",
                "employee": {"id": "emp-2", "name": "Alex Painter", "email": "a@example.com"},
                "location": {"id": "site-3", "name": "King St"},
                "clockIn": "2026-08-10T07:05:00+10:00",
                "clockOut": "2026-08-10T15:31:00+10:00",
                "breakMinutes": 30,
            }
        )
        self.assertEqual(row["provider_event_id"], "att-1")
        self.assertEqual(row["provider_employee_id"], "emp-2")
        self.assertEqual(row["provider_location_id"], "site-3")
        self.assertEqual(row["work_date"], "2026-08-10")
        self.assertEqual(row["start_time"], "07:05")
        self.assertEqual(row["end_time"], "15:31")
        self.assertEqual(row["break_minutes"], 30)

    def test_wrapper_payload_is_supported(self):
        rows = normalise_blip_records(
            {
                "data": {
                    "items": [
                        {
                            "id": "evt-1",
                            "employeeId": "emp-1",
                            "siteId": "job-location",
                            "startTime": "2026-08-10T08:00:00Z",
                            "endTime": "2026-08-10T16:00:00Z",
                        }
                    ]
                }
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider_employee_id"], "emp-1")

    def test_missing_employee_is_rejected(self):
        with self.assertRaises(ValueError):
            normalise_blip_record({"clockIn": "2026-08-10T08:00:00Z"})

    def test_status_gate_prevents_unmapped_or_open_publish(self):
        self.assertEqual(attendance_status(end_time="", employee_id=1, job_id=2), "Open")
        self.assertEqual(
            attendance_status(end_time="16:00", employee_id=None, job_id=2),
            "Needs mapping",
        )
        self.assertEqual(
            attendance_status(end_time="16:00", employee_id=1, job_id=None),
            "Needs mapping",
        )
        self.assertEqual(attendance_status(end_time="16:00", employee_id=1, job_id=2), "Ready")
        self.assertEqual(
            attendance_status(end_time="16:00", employee_id=1, job_id=2, published_timesheet_id=99),
            "Published",
        )

    def test_source_hash_is_stable(self):
        record = normalise_blip_record(
            {
                "id": "evt",
                "employeeId": "emp",
                "startTime": "2026-08-10T08:00:00Z",
                "endTime": "2026-08-10T16:00:00Z",
            }
        )
        self.assertEqual(source_hash(record), source_hash(record))
        self.assertEqual(len(source_hash(record)), 64)

    def test_configuration_requires_all_secret_and_endpoint_inputs(self):
        keys = [ENV_CLIENT_ID, ENV_CLIENT_SECRET, ENV_TOKEN_URL, ENV_ATTENDANCE_URL]
        clean = {key: "" for key in keys}
        with patch.dict(os.environ, clean, clear=False):
            self.assertFalse(configuration_ready())
        complete = {
            ENV_CLIENT_ID: "id",
            ENV_CLIENT_SECRET: "secret",
            ENV_TOKEN_URL: "https://token.invalid",
            ENV_ATTENDANCE_URL: "https://attendance.invalid",
        }
        with patch.dict(os.environ, complete, clear=False):
            self.assertTrue(configuration_ready())

    def test_token_request_uses_client_credentials_and_returns_token(self):
        session = _Session()
        env = {
            ENV_CLIENT_ID: "client-id",
            ENV_CLIENT_SECRET: "client-secret",
            ENV_TOKEN_URL: "https://token.invalid",
            ENV_TOKEN_AUTH_MODE: "body",
        }
        with patch.dict(os.environ, env, clear=False):
            token = _request_token(session)
        self.assertEqual(token, "secret-access-token")
        _, kwargs = session.post_calls[0]
        self.assertEqual(kwargs["data"]["grant_type"], "client_credentials")
        self.assertEqual(kwargs["data"]["client_id"], "client-id")
        self.assertEqual(kwargs["data"]["client_secret"], "client-secret")

    def test_safe_error_redacts_endpoint(self):
        message = _safe_error(RuntimeError("failed at https://secret.example/path token=abc"))
        self.assertNotIn("secret.example", message)
        self.assertNotIn("token=abc", message)


if __name__ == "__main__":
    unittest.main()
