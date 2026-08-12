import os
import unittest
from unittest.mock import patch

import requests

import jobhub.blip_integration_guard as blip


class _Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("request failed", response=self)

    def json(self):
        return self.payload


class _Session:
    def __init__(self):
        self.calls = []
        self.employee_calls = 0
        self.clocking_calls = 0

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/connect/token"):
            return _Response({"access_token": "token-x"})
        if "/employees/" in url:
            self.employee_calls += 1
            if self.employee_calls == 1:
                return _Response(
                    {
                        "items": [
                            {
                                "id": "emp-1",
                                "name": {"givenName": "Alex", "familyName": "Painter"},
                                "email": "alex@example.com",
                            }
                        ],
                        "continuationToken": "emp-next",
                    }
                )
            return _Response({"items": [], "continuationToken": None})
        if "/blip/" in url:
            self.clocking_calls += 1
            return _Response(
                {
                    "items": [
                        {
                            "id": "clock-1",
                            "employeeId": "emp-1",
                            "start": "2026-08-12T00:00:00Z",
                            "end": "2026-08-12T08:00:00Z",
                        }
                    ],
                    "continuationToken": None,
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")


class _422Session(_Session):
    def post(self, url, **kwargs):
        if url.endswith("/connect/token"):
            return _Response({"access_token": "token-x"})
        if "/employees/" in url:
            return _Response({"errors": {"$": ["The value could not be read as the expected type."]}}, 422)
        return super().post(url, **kwargs)


class _Retry500Session(_Session):
    def __init__(self):
        super().__init__()
        self.failures = 0

    def post(self, url, **kwargs):
        if url.endswith("/connect/token"):
            return _Response({"access_token": "token-x"})
        if "/employees/" in url and self.failures < 2:
            self.calls.append((url, kwargs))
            self.failures += 1
            return _Response(
                {
                    "title": "Internal Server Error",
                    "detail": "A temporary server error occurred.",
                    "status": 500,
                },
                500,
            )
        return super().post(url, **kwargs)


class _RepeatingTokenEmployeeSession(_Session):
    def post(self, url, **kwargs):
        if url.endswith("/connect/token"):
            return _Response({"access_token": "token-x"})
        if "/employees/" in url:
            return _Response(
                {
                    "items": [
                        {
                            "id": "emp-1",
                            "name": {"givenName": "Alex", "familyName": "Painter"},
                            "email": "alex@example.com",
                        }
                    ],
                    "continuationToken": "emp-next",
                }
            )
        return super().post(url, **kwargs)


class _RepeatingTokenClockSession(_Session):
    def post(self, url, **kwargs):
        if url.endswith("/connect/token"):
            return _Response({"access_token": "token-x"})
        if "/blip/" in url:
            return _Response(
                {
                    "items": [
                        {
                            "id": "clock-1",
                            "employeeId": "emp-1",
                            "start": "2026-08-12T00:00:00Z",
                            "end": "2026-08-12T08:00:00Z",
                        }
                    ],
                    "continuationToken": "clk-next",
                }
            )
        return super().post(url, **kwargs)


class _Permanent500Session(_Session):
    def post(self, url, **kwargs):
        if url.endswith("/connect/token"):
            return _Response({"access_token": "token-x"})
        if "/employees/" in url:
            return _Response(
                {
                    "title": "Internal Server Error",
                    "detail": "Unable to process employee query.",
                    "status": 500,
                },
                500,
            )
        return super().post(url, **kwargs)


class BrightHRRequestCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            blip.ENV_CLIENT_ID: "client-id",
            blip.ENV_CLIENT_SECRET: "client-secret",
            blip.ENV_TOKEN_URL: "https://login.brighthr.com/connect/token",
            blip.ENV_EMPLOYEES_URL: "https://api.bright.hr/employees/v1/query",
            blip.ENV_ATTENDANCE_URL: "https://api.bright.hr/blip/v1/clockings/query",
            blip.ENV_SYNC_FROM: "2026-08-01",
            blip.ENV_SYNC_TO: "2026-08-12",
        }

    def test_employee_first_request_has_no_body_and_next_has_only_token(self):
        session = _Session()
        with patch.dict(os.environ, self.env, clear=False):
            token = blip._request_token(session)
            employees = blip._fetch_employees_with_token(session, token)

        self.assertEqual(len(employees), 1)
        employee_calls = [(url, kwargs) for url, kwargs in session.calls if "/employees/" in url]
        self.assertEqual(len(employee_calls), 2)
        self.assertNotIn("json", employee_calls[0][1])
        self.assertNotIn("data", employee_calls[0][1])
        self.assertNotIn("Content-Type", employee_calls[0][1]["headers"])
        self.assertEqual(employee_calls[1][1]["json"], {"continuationToken": "emp-next"})
        self.assertNotIn("pageSize", employee_calls[1][1]["json"])

    def test_blip_query_uses_filters_without_page_size(self):
        session = _Session()
        with patch.dict(os.environ, self.env, clear=False):
            rows = list(blip._iter_attendance_records(session))

        self.assertEqual(len(rows), 1)
        clocking_calls = [(url, kwargs) for url, kwargs in session.calls if "/blip/" in url]
        self.assertEqual(len(clocking_calls), 1)
        body = clocking_calls[0][1]["json"]
        self.assertNotIn("pageSize", body)
        self.assertEqual(body["filters"]["employeeId"], "emp-1")
        self.assertEqual(body["filters"]["from"], "2026-08-01T00:00:00Z")
        self.assertEqual(body["filters"]["to"], "2026-08-12T00:00:00Z")

    def test_422_identifies_employee_query_phase(self):
        session = _422Session()
        with patch.dict(os.environ, self.env, clear=False):
            token = blip._request_token(session)
            with self.assertRaisesRegex(RuntimeError, r"BrightHR employee query failed with HTTP 422"):
                blip._fetch_employees_with_token(session, token)

    def test_employee_query_retries_transient_500_then_recovers(self):
        session = _Retry500Session()
        with patch.dict(os.environ, self.env, clear=False), patch(
            "jobhub.blip_request_compat_guard.time.sleep", return_value=None
        ) as sleeper:
            token = blip._request_token(session)
            employees = blip._fetch_employees_with_token(session, token)

        self.assertEqual(len(employees), 1)
        self.assertEqual(session.failures, 2)
        self.assertEqual(sleeper.call_count, 2)
        employee_calls = [(url, kwargs) for url, kwargs in session.calls if "/employees/" in url]
        self.assertNotIn("json", employee_calls[0][1])
        self.assertNotIn("json", employee_calls[1][1])
        self.assertNotIn("json", employee_calls[2][1])

    def test_permanent_500_surfaces_problem_title_and_detail(self):
        session = _Permanent500Session()
        with patch.dict(os.environ, self.env, clear=False), patch(
            "jobhub.blip_request_compat_guard.time.sleep", return_value=None
        ):
            token = blip._request_token(session)
            with self.assertRaisesRegex(
                RuntimeError,
                r"BrightHR employee query failed with HTTP 500.*Internal Server Error.*Unable to process employee query",
            ):
                blip._fetch_employees_with_token(session, token)

    def test_employee_query_stops_when_token_repeats(self):
        session = _RepeatingTokenEmployeeSession()
        with patch.dict(os.environ, self.env, clear=False):
            token = blip._request_token(session)
            with self.assertRaisesRegex(RuntimeError, r"same continuationToken"):
                blip._fetch_employees_with_token(session, token)

    def test_clocking_query_stops_when_token_repeats(self):
        session = _RepeatingTokenClockSession()
        with patch.dict(os.environ, self.env, clear=False):
            with self.assertRaisesRegex(RuntimeError, r"same continuationToken"):
                list(blip._iter_attendance_records(session))


if __name__ == "__main__":
    unittest.main()
