from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class SetupSchedulerCrewBridgeGuardTests(unittest.TestCase):
    def test_bridge_guard_source_parses(self):
        ast.parse(read("jobhub/setup_scheduler_crew_bridge_guard.py"))

    def test_bridge_reads_setup_crews_into_scheduler_picker(self):
        source = read("jobhub/setup_scheduler_crew_bridge_guard.py")
        required = [
            "jobhub_crews",
            "jobhub_crew_members",
            "scheduler.saved_crews = saved_crews_with_setup_bridge",
            "active_only",
            "Negative ids prevent clashes with scheduler_crews ids",
            "JobHub Setup",
            "member_ids",
            "member_names",
            "lead_employee_id",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_bridge_is_installed_after_setup_defaults(self):
        source = read("jobhub/__init__.py")
        self.assertIn("from .setup_scheduler_crew_bridge_guard import install_setup_scheduler_crew_bridge_guard", source)
        self.assertIn("install_setup_scheduler_crew_bridge_guard()", source)
        self.assertLess(
            source.index("install_setup_defaults_guard()"),
            source.index("install_setup_scheduler_crew_bridge_guard()"),
        )


if __name__ == "__main__":
    unittest.main()
