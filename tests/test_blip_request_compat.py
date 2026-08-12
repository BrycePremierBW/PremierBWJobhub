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
        self.calls.append(("POST", url, kwargs))
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

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        raise AssertionError(f"Unexpected GET URL: {url}")


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
            self.calls.append(("POST", url, kwargs))
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


class _GetFallbackSession(_Session):
    def post(self, url, **kwargs):
        if url.endswith("/connect/token"):
            return _Response({"access_token": "token-x"})
        if "/employees/" in url:
            self.calls.append(("POST", url, kwargs))
            return _Response({}, 500)
        return super().post(url, **kwargs)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if "/employees/" in url:
            return _Response(
                {
                    "items": [
                        {
                            "id": "emp-get-1",
                            "name": {"givenName": "GET", "familyName": "Fallback"},
                            "email": "get@example.com",
                        }
                    ],
                    "continuationToken": None,
                }
            )
        raise AssertionError(f"Unexpected GET URL: {url}")


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
            self.calls.append(("POST", url, kwargs))
            return _Response(
                {
                    "title": "Internal Server Error",
                    "detail": "Unable to process employee query.",
                    "status": 500,
                },
                500,
            )
        return super().post(url, **kwargs)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return _Response(
            {
                "title": "Service Unavailable",
                "detail": "Employee API unavailable.",
                "status": 503,
            },
            503,
        )


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

    def _employee_calls(self, session):
        return [call for call in session.calls if "/employees/" in call[1]]

    def test_employee_first_request_has_no_body_and_next_has_only_token(self):
        session = _Session()
        with patch.dict(os.environ, self.env, clear=False):
            token = blip._request_token(session)
            employees = blip._fetch_employees_with_token(session, token)

        self.assertEqual(len(employees), 1)
        employee_calls = self._employee_calls(session)
        self.assertEqual(len(employee_calls), 2)
        self.assertEqual(employee_calls[0][0], "POST")
        self.assertNotIn("json", employee_calls[0][2])
        self.assertNotIn("data", employee_calls[0][2])
        self.assertNotIn("Content-Type", employee_calls[0][2]["headers"])
        self.assertEqual(employee_calls[1][2]["json"], {"continuationToken": "emp-next"})
        self.assertNotIn("pageSize", employee_calls[1][2]["json"])

    def test_blip_query_uses_filters_without_page_size(self):
        session = _Session()
        with patch.dict(os.environ, self.env, clear=False):
            rows = list(blip._iter_attendance_records(session))

        self.assertEqual(len(rows), 1)
        clocking_calls = [call for call in session.calls if "/blip/" in call[1]]
        self.assertEqual(len(clocking_calls), 1)
        body = clocking_calls[0][2]["json"]
        self.assertNotIn("pageSize", body)
        self.assertEqual(body["filters"]["employeeId"], "emp-1")
        self.assertEqual(body["filters"]["from"], "2026-08-01T00:00:00Z")
        self.assertEqual(body["filters"]["to"], "2026-08-12T00:00:00Z")

    def test_422_identifies_employee_query_phase_without_get_fallback(self):
        session = _422Session()
        with patch.dict(os.environ, self.env, clear=False):
            token = blip._request_token(session)
            with self.assertRaisesRegex(RuntimeError, r"BrightHR employee query POST failed with HTTP 422"):
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
        employee_calls = self._employee_calls(session)
        self.assertTrue(all(call[0] == "POST" for call in employee_calls))
        self.assertNotIn("json", employee_calls[0][2])
        self.assertNotIn("json", employee_calls[1][2])
        self.assertNotIn("json", employee_calls[2][2])

    def test_repeated_post_500_uses_api_catalogue_get_fallback(self):
        session = _GetFallbackSession()
        with patch.dict(os.environ, self.env, clear=False), patch(
            "jobhub.blip_request_compat_guard.time.sleep", return_value=None
        ):
            token = blip._request_token(session)
            employees = blip._fetch_employees_with_token(session, token)

        self.assertEqual([item["id"] for item in employees], ["emp-get-1"])
        employee_calls = self._employee_calls(session)
        self.assertEqual([call[0] for call in employee_calls], ["POST", "POST", "POST", "GET"])
        self.assertNotIn("json", employee_calls[-1][2])

    def test_post_and_get_server_failures_identify_provisioning_boundary(self):
        session = _Permanent500Session()
        with patch.dict(os.environ, self.env, clear=False), patch(
            "jobhub.blip_request_compat_guard.time.sleep", return_value=None
        ):
            token = blip._request_token(session)
            with self.assertRaisesRegex(
                RuntimeError,
                r"employee query POST failed with HTTP 500.*GET fallback also failed.*HTTP 503.*OAuth token acquisition succeeded",
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
