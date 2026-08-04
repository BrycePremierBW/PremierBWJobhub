from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import types
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "jobhub" / "permission_policy_guard.py"
SPEC = importlib.util.spec_from_file_location("jobhub_permission_policy_guard_test", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class PermissionPolicyGuardTests(unittest.TestCase):
    def test_source_parses(self):
        ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))

    def test_existing_roles_keep_expected_permissions(self):
        self.assertTrue(MODULE.has_permission("admin", "users.manage"))
        self.assertTrue(MODULE.has_permission("admin", "permissions.audit"))
        self.assertTrue(MODULE.has_permission("manager", "system.health"))
        self.assertTrue(MODULE.has_permission("manager", "setup.manage"))
        self.assertFalse(MODULE.has_permission("manager", "users.manage"))
        self.assertTrue(MODULE.has_permission("employee", "field.use"))
        self.assertFalse(MODULE.has_permission("employee", "system.health"))
        self.assertFalse(MODULE.has_permission("unknown", "field.use"))

    def test_sensitive_routes_are_protected(self):
        self.assertFalse(MODULE.can_access_route("manager", "User Access"))
        self.assertFalse(MODULE.can_access_route("employee", "System Health"))
        self.assertFalse(MODULE.can_access_route("employee", MODULE.PERMISSIONS_LABEL))
        self.assertTrue(MODULE.can_access_route("manager", "System Health"))
        self.assertTrue(MODULE.can_access_route("employee", "Employee Portal"))
        self.assertEqual(MODULE.safe_route_for_role("employee", "User Access"), "Employee Portal")
        self.assertEqual(MODULE.safe_route_for_role("manager", "User Access"), "Dashboard")

    def test_permissions_page_is_only_injected_for_admin(self):
        options = ["Builders & Clients", "Employees", "Products"]
        self.assertTrue(MODULE._should_inject("Management Section", "management_menu", options, role="admin"))
        self.assertFalse(MODULE._should_inject("Management Section", "management_menu", options, role="manager"))
        self.assertFalse(MODULE._should_inject("Management Section", "management_menu", options, role="employee"))

    def test_account_audit_flags_invalid_and_unlinked_accounts(self):
        frame = pd.DataFrame(
            [
                {
                    "id": 1,
                    "username": "admin",
                    "role": "admin",
                    "active": 1,
                    "employee_id": None,
                    "employee_name": "",
                    "employee_status": "",
                    "has_password": 1,
                },
                {
                    "id": 2,
                    "username": "worker",
                    "role": "employee",
                    "active": 1,
                    "employee_id": None,
                    "employee_name": "",
                    "employee_status": "",
                    "has_password": 1,
                },
                {
                    "id": 3,
                    "username": "mystery",
                    "role": "owner",
                    "active": 1,
                    "employee_id": None,
                    "employee_name": "",
                    "employee_status": "",
                    "has_password": 0,
                },
            ]
        )
        accounts, findings, counts = MODULE._analyse_accounts(frame)
        self.assertEqual(counts["admin"], 1)
        self.assertEqual(counts["employee"], 1)
        self.assertEqual(counts["invalid"], 1)
        worker = next(row for row in accounts if row["Username"] == "worker")
        mystery = next(row for row in accounts if row["Username"] == "mystery")
        self.assertEqual(worker["Audit Status"], "Warning")
        self.assertIn("not linked", worker["Findings"])
        self.assertEqual(mystery["Audit Status"], "Critical")
        self.assertTrue(any(item["Severity"] == "Critical" for item in findings))

    def test_navigation_pop_blocks_direct_sensitive_route(self):
        class FakeState(dict):
            pass

        state = FakeState(user={"role": "employee"}, go_to_menu="User Access")
        st = types.SimpleNamespace(session_state=state)
        self.assertTrue(MODULE._install_session_navigation_guard(st))
        self.assertEqual(state.pop("go_to_menu", None), "Employee Portal")
        self.assertEqual(state.get("_pb_permission_denied_route"), "User Access")

    def test_guard_install_order_precedes_other_injected_management_pages(self):
        source = (ROOT / "jobhub" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("from .permission_policy_guard import install_permission_policy_guard", source)
        self.assertIn("install_permission_policy_guard()", source)
        self.assertLess(
            source.index("install_permission_policy_guard()"),
            source.index("install_system_health_guard()"),
        )
        self.assertLess(
            source.index("install_permission_policy_guard()"),
            source.index("install_mobile_top_navigation_guard()"),
        )

    def test_no_secret_fields_are_rendered(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("Password hashes, passwords, connection details and API secrets are never displayed", source)
        self.assertNotIn('"Password Hash"', source)
        self.assertNotIn('"password_hash":', source)


if __name__ == "__main__":
    unittest.main()
