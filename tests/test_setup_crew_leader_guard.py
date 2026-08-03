"""Smoke tests for setup crew leader support."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class SetupCrewLeaderGuardTests(unittest.TestCase):
    def test_sources_parse(self):
        for path in [
            "jobhub/setup_crew_leader_guard.py",
            "jobhub/setup_scheduler_crew_bridge_guard.py",
            "jobhub/__init__.py",
        ]:
            with self.subTest(path=path):
                ast.parse(read(path), filename=path)

    def test_setup_crew_leader_guard_markers(self):
        source = read("jobhub/setup_crew_leader_guard.py")
        required = [
            "lead_employee_id",
            "Crew leader",
            "Default hourly rate per person",
            "Save crew leader and members",
            "crew_role",
            "Leader",
            "install_setup_crew_leader_guard",
            "_render_crews_tab_with_leader",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_scheduler_bridge_uses_saved_setup_leader(self):
        source = read("jobhub/setup_scheduler_crew_bridge_guard.py")
        required = [
            "lead_employee_id",
            "configured_leader_id",
            "leader_name",
            "CASE WHEN e.id=? THEN 0 ELSE 1 END",
            "JobHub Setup",
            "saved_crews_with_setup_bridge",
        ]
        for marker in required:
            self.assertIn(marker, source)

    def test_startup_installs_leader_before_scheduler_bridge(self):
        source = read("jobhub/__init__.py")
        self.assertIn("from .setup_crew_leader_guard import install_setup_crew_leader_guard", source)
        self.assertIn("install_setup_crew_leader_guard()", source)
        self.assertIn("install_setup_scheduler_crew_bridge_guard()", source)
        self.assertLess(
            source.index("install_setup_crew_leader_guard()"),
            source.index("install_setup_scheduler_crew_bridge_guard()"),
        )


if __name__ == "__main__":
    unittest.main()
