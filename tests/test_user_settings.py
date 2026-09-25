"""Tests for local search and matching settings."""

import tempfile
import unittest
from pathlib import Path

import yaml

from job_finder.matching.user_settings import EXAMPLE_SETTINGS_PATH, load_user_settings


class UserSettingsTests(unittest.TestCase):
    def test_public_example_is_valid(self):
        settings = load_user_settings(EXAMPLE_SETTINGS_PATH)

        self.assertTrue(settings["search"]["local_location"])
        self.assertGreater(settings["search"]["local_radius_km"], 0)
        self.assertTrue(settings["matching"]["local_places"])
        self.assertIsNone(settings["matching"]["salary_target_eur"])
        self.assertIsNone(settings["matching"]["salary_minimum_eur"])

    def test_older_settings_with_removed_role_preferences_still_load(self):
        settings = load_user_settings(EXAMPLE_SETTINGS_PATH)
        settings["matching"]["preferred_role_groups"] = ["software_development"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yaml"
            path.write_text(yaml.safe_dump(settings), encoding="utf-8")
            loaded = load_user_settings(path)

        self.assertEqual(loaded["search"], settings["search"])


if __name__ == "__main__":
    unittest.main()
