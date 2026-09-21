"""Tests for local search and matching settings."""

import tempfile
import unittest
from pathlib import Path

import yaml

from job_finder.matching.user_settings import EXAMPLE_SETTINGS_PATH, load_user_settings


class UserSettingsTests(unittest.TestCase):
    def load_with_preferred_roles(self, roles, *, omit=False):
        settings = load_user_settings(EXAMPLE_SETTINGS_PATH)
        if omit:
            settings["matching"].pop("preferred_role_groups", None)
        else:
            settings["matching"]["preferred_role_groups"] = roles
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yaml"
            path.write_text(yaml.safe_dump(settings), encoding="utf-8")
            return load_user_settings(path)

    def test_older_settings_without_role_preferences_remain_usable(self):
        settings = self.load_with_preferred_roles(None, omit=True)
        self.assertNotIn("preferred_role_groups", settings["matching"])

    def test_empty_and_known_role_preferences_are_accepted(self):
        for roles in [[], ["software_development", "general_it"]]:
            with self.subTest(roles=roles):
                settings = self.load_with_preferred_roles(roles)
                self.assertEqual(settings["matching"]["preferred_role_groups"], roles)

    def test_misspelled_and_invalid_role_preferences_fail_at_loading(self):
        for roles in [["sofware_development"], "software_development", None, [42]]:
            with self.subTest(roles=roles):
                with self.assertRaisesRegex(ValueError, "preferred_role_groups"):
                    self.load_with_preferred_roles(roles)

    def test_public_example_is_valid(self):
        settings = load_user_settings(EXAMPLE_SETTINGS_PATH)

        self.assertTrue(settings["search"]["local_location"])
        self.assertGreater(settings["search"]["local_radius_km"], 0)
        self.assertTrue(settings["matching"]["local_places"])
        self.assertIsNone(settings["matching"]["salary_target_eur"])
        self.assertIsNone(settings["matching"]["salary_minimum_eur"])


if __name__ == "__main__":
    unittest.main()
