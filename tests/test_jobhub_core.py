import hashlib
import unittest

from jobhub_core import (
    calculate_estimate_pricing,
    calculate_shift_hours,
    hash_password,
    is_known_default_password_hash,
    is_public_ip_address,
    next_scoped_number,
    password_needs_rehash,
    password_strength_errors,
    validate_public_http_url,
    verify_password,
)


class PasswordTests(unittest.TestCase):
    def test_pbkdf2_hash_is_salted_and_verifies(self):
        first = hash_password("Strong!Pass123")
        second = hash_password("Strong!Pass123")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("Strong!Pass123", first))
        self.assertFalse(verify_password("wrong", first))
        self.assertFalse(password_needs_rehash(first))

    def test_legacy_hash_can_be_verified_and_requires_upgrade(self):
        legacy = hashlib.sha256(b"Legacy!Pass123").hexdigest()
        self.assertTrue(verify_password("Legacy!Pass123", legacy))
        self.assertTrue(password_needs_rehash(legacy))

    def test_known_defaults_and_strength_policy(self):
        legacy_default = hashlib.sha256(b"admin123").hexdigest()
        self.assertTrue(is_known_default_password_hash(legacy_default))
        self.assertTrue(password_strength_errors("admin123", "admin"))
        self.assertFalse(password_strength_errors("Long!UniquePass42", "admin"))


class PricingTests(unittest.TestCase):
    def test_target_margin_is_not_markup(self):
        result = calculate_estimate_pricing(
            line_total=100,
            labour_hours=0,
            labour_rate=0,
            material_allowance=0,
            access_equipment_allowance=0,
            subcontractor_allowance=0,
            sundries_allowance=0,
            pricing_percent=20,
            contingency_percent=0,
            gst_percent=10,
            pricing_method="Target Gross Margin",
        )
        self.assertEqual(result["total_ex_gst"], 125.00)
        self.assertEqual(result["achieved_margin_percent"], 20.00)
        self.assertEqual(result["total_inc_gst"], 137.50)

    def test_markup_preserves_legacy_meaning(self):
        result = calculate_estimate_pricing(
            line_total=100,
            labour_hours=0,
            labour_rate=0,
            material_allowance=0,
            access_equipment_allowance=0,
            subcontractor_allowance=0,
            sundries_allowance=0,
            pricing_percent=20,
            contingency_percent=0,
            gst_percent=10,
            pricing_method="Markup",
        )
        self.assertEqual(result["total_ex_gst"], 120.00)
        self.assertEqual(result["achieved_margin_percent"], 16.67)


class SafetyAndNumberingTests(unittest.TestCase):
    def test_private_and_local_addresses_are_blocked(self):
        self.assertFalse(is_public_ip_address("127.0.0.1"))
        self.assertFalse(is_public_ip_address("10.0.0.1"))
        allowed, _ = validate_public_http_url("http://127.0.0.1/admin")
        self.assertFalse(allowed)
        allowed, _ = validate_public_http_url("file:///etc/passwd")
        self.assertFalse(allowed)

    def test_next_number_uses_highest_existing_suffix(self):
        self.assertEqual(
            next_scoped_number(["VAR-001", "VAR-007", "other"], "VAR"),
            "VAR-008",
        )


class TimesheetCalculationTests(unittest.TestCase):
    def test_same_day_shift_less_break(self):
        self.assertEqual(calculate_shift_hours("07:00", "15:30", 30), 8.0)

    def test_overnight_shift(self):
        self.assertEqual(calculate_shift_hours("22:00", "06:00", 30), 7.5)

    def test_break_cannot_exceed_shift(self):
        with self.assertRaises(ValueError):
            calculate_shift_hours("07:00", "08:00", 90)

    def test_invalid_time_is_rejected(self):
        with self.assertRaises(ValueError):
            calculate_shift_hours("25:00", "08:00", 0)


if __name__ == "__main__":
    unittest.main()
