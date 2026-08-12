import os
import unittest
from unittest.mock import patch

from jobhub.blip_integration_guard import (
    ENV_ATTENDANCE_URL,
    ENV_CLIENT_ID,
    ENV_CLIENT_SECRET,
    ENV_EMPLOYEES_URL,
    ENV_TOKEN_AUTH_MODE,
    ENV_TOKEN_URL,
    _fetch_attendance_payload,
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


class _ApiSession:
    def __init__(self, employee_pages, clocking_pages_by_employee):
        self._employee_pages = employee_pages
        self._clocking_pages_by_employee = clocking_pages_by_employee
        self._emp_index = 0
        self._clk_indexes = {}
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/connect/token"):
            return _Response({"access_token": "token-x"})
        if "/employees/" in url:
            page = self._employee_pages[min(self._emp_index, len(self._employee_pages) - 1)]
            last = self._emp_index >= len(self._employee_pages) - 1
            self._emp_index += 1
            result = {"items": page}
            if not last:
                result["continuationToken"] = "emp-next"
            return _Response(result)
        if "/blip/" in url:
            body = kwargs.get("json", {})
            employee_id = (body.get("filters") or {}).get("employeeId")
            pages = self._clocking_pages_by_employee.get(employee_id) or [[]]
            index = self._clk_indexes.get(employee_id, 0)
            page = pages[min(index, len(pages) - 1)]
            last = index >= len(pages) - 1
            self._clk_indexes[employee_id] = index + 1
            result = {"items": page}
            if not last:
                result["continuationToken"] = "clk-next"
            return _Response(result)
        return _Response({"items": []})


class BrightHRBlipIntegrationTests(unittest.TestCase):
    def test_normalises_documented_clocking_shape(self):
        row = normalise_blip_record(
            {
                "id": "clock-1",
                "employeeId": "emp-2",
                "start": "2026-08-10T21:05:00Z",
                "end": "2026-08-11T05:31:00Z",
                "startTimeZone": "Australia/Sydney",
                "endTimeZone": "Australia/Sydney",
                "breaks": [
                    {"start": "2026-08-11T00:05:00Z", "end": "2026-08-11T00:35:00Z"}
                ],
                "note": "Touch up paint in lobby",
            }
        )
        self.assertEqual(row["provider_event_id"], "clock-1")
        self.assertEqual(row["provider_employee_id"], "emp-2")
        self.assertEqual(row["provider_location_id"], "")
        self.assertEqual(row["work_date"], "2026-08-11")
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
                            "start": "2026-08-10T08:00:00Z",
                            "end": "2026-08-10T16:00:00Z",
                        }
                    ]
                }
            }
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["provider_employee_id"], "emp-1")

    def test_missing_employee_is_rejected(self):
        with self.assertRaises(ValueError):
            normalise_blip_record({"start": "2026-08-10T08:00:00Z"})

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
                "start": "2026-08-10T08:00:00Z",
                "end": "2026-08-10T16:00:00Z",
            }
        )
        self.assertEqual(source_hash(record), source_hash(record))
        self.assertEqual(len(source_hash(record)), 64)

    def test_configuration_requires_all_secret_and_endpoint_inputs(self):
        keys = [ENV_CLIENT_ID, ENV_CLIENT_SECRET, ENV_TOKEN_URL, ENV_EMPLOYEES_URL, ENV_ATTENDANCE_URL]
        clean = {key: "" for key in keys}
        with patch.dict(os.environ, clean, clear=False):
            self.assertFalse(configuration_ready())
        complete = {
            ENV_CLIENT_ID: "id",
            ENV_CLIENT_SECRET: "secret",
            ENV_TOKEN_URL: "https://token.invalid",
            ENV_EMPLOYEES_URL: "https://employees.invalid",
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

    def test_fetch_attendance_payload_pages_employees_and_clockings(self):
        env = {
            ENV_CLIENT_ID: "id",
            ENV_CLIENT_SECRET: "secret",
            ENV_TOKEN_URL: "https://login.brighthr.com/connect/token",
            ENV_EMPLOYEES_URL: "https://api.bright.hr/employees/v1/query",
            ENV_ATTENDANCE_URL: "https://api.bright.hr/blip/v1/clockings/query",
        }
        clocking = {
            "id": "clock-a1",
            "employeeId": "emp-1",
            "start": "2026-08-10T21:05:00Z",
            "end": "2026-08-11T05:31:00Z",
            "startTimeZone": "Australia/Sydney",
            "endTimeZone": "Australia/Sydney",
            "breaks": [{"start": "2026-08-11T00:05:00Z", "end": "2026-08-11T00:35:00Z"}],
        }
        session = _ApiSession(
            employee_pages=[
                [
                    {
                        "id": "emp-1",
                        "name": {"givenName": "Alex", "familyName": "Painter"},
                        "email": "a@example.com",
                    }
                ]
            ],
            clocking_pages_by_employee={"emp-1": [[clocking], []]},
        )
        with patch.dict(os.environ, env, clear=False):
            records = _fetch_attendance_payload(session)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "clock-a1")
        self.assertEqual(records[0]["employee"]["id"], "emp-1")
        self.assertEqual(records[0]["employee"]["name"], "Alex Painter")

        clocking_calls = [kwargs for url, kwargs in session.calls if "/blip/" in url]
        self.assertEqual(len(clocking_calls), 2)
        self.assertEqual(clocking_calls[0]["json"]["filters"]["employeeId"], "emp-1")
        self.assertEqual(clocking_calls[0]["json"]["pageSize"], 100)
        self.assertEqual(clocking_calls[1]["json"]["continuationToken"], "clk-next")

    def test_safe_error_redacts_endpoint(self):
        message = _safe_error(RuntimeError("failed at https://secret.example/path token=abc"))
        self.assertNotIn("secret.example", message)
        self.assertNotIn("token=abc", message)


if __name__ == "__main__":
    unittest.main()
