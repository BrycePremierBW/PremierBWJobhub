import unittest

from jobhub_progress_rules import (
    INTERNAL_STAGES,
    combine_internal_progress,
    weighted_percent,
)


class InternalMilestoneTests(unittest.TestCase):
    def test_internal_milestones_use_30_30_30_10_weights(self):
        self.assertEqual(
            [stage[2] for stage in INTERNAL_STAGES],
            [30.0, 30.0, 30.0, 10.0],
        )
        self.assertEqual(
            weighted_percent(
                {
                    "prepped_sealed": "Complete",
                    "prep_spray_finished": "Complete",
                    "cut_rolled": "In progress",
                    "defects": "Not started",
                },
                INTERNAL_STAGES,
            ),
            75.0,
        )

    def test_separate_stairs_item_uses_nominated_internal_share(self):
        result = combine_internal_progress(
            [
                {
                    "is_custom": 0,
                    "floor_m2": 100,
                    "progress_percent": 50,
                    "scope_percent": 0,
                },
                {
                    "is_custom": 1,
                    "floor_m2": 0,
                    "progress_percent": 100,
                    "scope_percent": 10,
                },
            ]
        )
        self.assertEqual(result["internal_floor_percent"], 50.0)
        self.assertEqual(result["custom_weight_percent"], 10.0)
        self.assertEqual(result["internal_percent"], 55.0)

    def test_tracking_only_custom_item_does_not_change_progress(self):
        result = combine_internal_progress(
            [
                {"is_custom": 0, "floor_m2": 80, "progress_percent": 60},
                {
                    "is_custom": 1,
                    "progress_percent": 100,
                    "scope_percent": 0,
                },
            ]
        )
        self.assertEqual(result["internal_percent"], 60.0)
        self.assertEqual(result["custom_item_count"], 1.0)


if __name__ == "__main__":
    unittest.main()
