from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "jobhub" / "integration_health_guard.py"


class IntegrationHealthGuardTests(unittest.TestCase):
    def test_source_parses(self):
        ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))

    def test_guard_is_installed_after_system_health_and_before_mobile_navigation(self):
        source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("from .integration_health_guard import install_integration_health_guard", source)
        self.assertIn("install_integration_health_guard()", source)
        self.assertLess(
            source.index("install_system_health_guard()"),
            source.index("install_integration_health_guard()"),
        )
        self.assertLess(
            source.index("install_integration_health_guard()"),
            source.index("install_mobile_top_navigation_guard()"),
        )

    def test_required_live_configuration_checks_are_present(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        required = [
            "Production database configuration",
            "Public JobHub URL",
            "Device push notifications",
            "Email notifications",
            "Offline synchronisation",
            "External AI",
            "Self-editing code",
            "ONESIGNAL_APP_ID",
            "ONESIGNAL_REST_API_KEY",
            "JOBHUB_PUBLIC_URL",
            "JOBHUB_EMAIL_NOTIFICATIONS_ENABLED",
            "JOBHUB_OFFLINE_SYNC_ENABLED",
            "JOBHUB_ALLOW_EXTERNAL_AI",
            "JOBHUB_ENABLE_SELF_EDIT",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_report_only_exposes_configuration_state(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        required_safe_outputs = [
            '"Yes" if one_signal_app else "No"',
            '"Yes" if one_signal_key else "No"',
            '"Yes" if public_url else "No"',
            "Secret values are never",
        ]
        for marker in required_safe_outputs:
            self.assertIn(marker, source)
        self.assertNotIn("metrics[\"OneSignal server key\"] = os.getenv", source)
        self.assertNotIn("metrics[\"Database URL\"] = os.getenv", source)

    def test_partial_push_configuration_is_critical(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("elif one_signal_app or one_signal_key", source)
        self.assertIn('push_status = "Critical"', source)
        self.assertIn("both the app ID and REST API key are required", source)

    def test_health_status_is_recalculated_after_integrations_are_added(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("def _recalculate_status", source)
        self.assertIn('overall = "Critical" if status_counts["Critical"]', source)
        self.assertIn("report[\"checks\"] = list(report.get(\"checks\") or []) + integration_checks", source)
        self.assertIn("return _recalculate_status(report)", source)


if __name__ == "__main__":
    unittest.main()
