import unittest

from jobhub_v2.config import load_runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def test_staging_requires_database_url(self):
        config = load_runtime_config({"JOBHUB_ENV": "staging"})
        self.assertIn(
            "DATABASE_URL is required outside development/test.",
            config.validate(),
        )

    def test_email_settings_are_required_only_when_enabled(self):
        disabled = load_runtime_config(
            {"JOBHUB_ENV": "test", "JOBHUB_EMAIL_NOTIFICATIONS_ENABLED": "false"}
        )
        self.assertEqual(disabled.validate(), [])

        enabled = load_runtime_config(
            {"JOBHUB_ENV": "test", "JOBHUB_EMAIL_NOTIFICATIONS_ENABLED": "true"}
        )
        self.assertEqual(len(enabled.validate()), 2)

    def test_boolean_environment_values_are_normalised(self):
        config = load_runtime_config(
            {
                "JOBHUB_ENV": "test",
                "JOBHUB_OFFLINE_SYNC_ENABLED": "YES",
                "JOBHUB_EMAIL_NOTIFICATIONS_ENABLED": "1",
                "JOBHUB_EMAIL_PROVIDER": "smtp",
                "JOBHUB_EMAIL_FROM": "jobhub@example.com",
            }
        )
        self.assertTrue(config.offline_sync_enabled)
        self.assertTrue(config.email_notifications_enabled)
        self.assertEqual(config.validate(), [])


if __name__ == "__main__":
    unittest.main()
