from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

import requests


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jobhub_notification_freeze_guard_test",
    ROOT / "jobhub" / "notification_freeze_guard.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class NotificationFreezeGuardTests(unittest.TestCase):
    def setUp(self):
        self.original_post = requests.post

    def tearDown(self):
        requests.post = self.original_post

    def test_automatic_overdue_push_is_skipped_without_network_wait(self):
        calls = []

        def fake_post(url, *args, **kwargs):
            calls.append((url, kwargs))
            raise AssertionError("automatic overdue push reached the network")

        requests.post = fake_post
        self.assertTrue(MODULE.install_notification_freeze_guard())

        def notify_overdue_staff_requests():
            return requests.post(
                MODULE.ONESIGNAL_NOTIFICATIONS_URL,
                json={"app_id": "test"},
                timeout=12,
            )

        response = notify_overdue_staff_requests()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.ok)
        self.assertIn("in-app JobHub notification", response.text)
        self.assertEqual(calls, [])

    def test_explicit_push_still_uses_original_requests_post(self):
        calls = []
        sentinel = object()

        def fake_post(url, *args, **kwargs):
            calls.append((url, kwargs))
            return sentinel

        requests.post = fake_post
        MODULE.install_notification_freeze_guard()

        result = requests.post(
            MODULE.ONESIGNAL_NOTIFICATIONS_URL,
            json={"app_id": "test"},
            timeout=12,
        )
        self.assertIs(result, sentinel)
        self.assertEqual(len(calls), 1)

    def test_non_onesignal_requests_are_unchanged_inside_overdue_check(self):
        calls = []
        sentinel = object()

        def fake_post(url, *args, **kwargs):
            calls.append(url)
            return sentinel

        requests.post = fake_post
        MODULE.install_notification_freeze_guard()

        def notify_overdue_staff_requests():
            return requests.post("https://example.invalid/internal", timeout=1)

        self.assertIs(notify_overdue_staff_requests(), sentinel)
        self.assertEqual(calls, ["https://example.invalid/internal"])

    def test_install_is_idempotent(self):
        requests.post = lambda *args, **kwargs: None
        self.assertTrue(MODULE.install_notification_freeze_guard())
        guarded = requests.post
        self.assertFalse(MODULE.install_notification_freeze_guard())
        self.assertIs(requests.post, guarded)

    def test_guard_is_installed_before_main_app_runs(self):
        init_source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn(
            "from .notification_freeze_guard import install_notification_freeze_guard",
            init_source,
        )
        self.assertIn("install_notification_freeze_guard()", init_source)


if __name__ == "__main__":
    unittest.main()
