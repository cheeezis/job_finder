"""Tests for local search and matching settings."""

import unittest

from job_agent.user_settings import EXAMPLE_SETTINGS_PATH, load_user_settings


class UserSettingsTests(unittest.TestCase):
    def test_public_example_is_valid(self):
        settings = load_user_settings(EXAMPLE_SETTINGS_PATH)

        self.assertTrue(settings["search"]["local_location"])
        self.assertGreater(settings["search"]["local_radius_km"], 0)
        self.assertTrue(settings["matching"]["local_places"])
        self.assertIsNone(settings["matching"]["salary_target_eur"])
        self.assertIsNone(settings["matching"]["salary_minimum_eur"])


if __name__ == "__main__":
    unittest.main()
