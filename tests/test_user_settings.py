"""Tests for local search and matching settings."""

import re
import tempfile
import unittest
from pathlib import Path

import yaml
from settings_helpers import example_settings, with_settings

from job_finder.matching.user_settings import (
    EXAMPLE_SETTINGS_PATH,
    SETTINGS_ENV,
    SETTINGS_PATH,
    configured_user_settings,
    load_user_settings,
)


class UserSettingsTests(unittest.TestCase):
    def test_public_example_is_valid(self):
        settings = load_user_settings(EXAMPLE_SETTINGS_PATH)

        self.assertTrue(settings.search.local_location)
        self.assertGreater(settings.search.local_radius_km, 0)
        self.assertTrue(settings.matching.local_places)
        self.assertIsNone(settings.matching.salary_target_eur)
        self.assertIsNone(settings.matching.salary_minimum_eur)
        # Without own lists the built-in search terms and all company pages apply.
        self.assertIsNone(settings.search.terms)
        self.assertIsNone(settings.sources.companies)

    def test_container_settings_come_from_the_environment(self):
        text = EXAMPLE_SETTINGS_PATH.read_text(encoding="utf-8").replace("Musterstadt", "Envstadt")
        settings, source = configured_user_settings({SETTINGS_ENV: text})

        self.assertEqual(settings.search.local_location, "Envstadt")
        self.assertEqual(source, SETTINGS_ENV)
        self.assertEqual(configured_user_settings({})[1], SETTINGS_PATH.name)
        with self.assertRaisesRegex(ValueError, SETTINGS_ENV):
            configured_user_settings({SETTINGS_ENV: "search: ["})

    def test_older_settings_with_removed_role_preferences_still_load(self):
        values = load_user_settings(EXAMPLE_SETTINGS_PATH).mapping
        values["matching"]["preferred_role_groups"] = ["software_development"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.yaml"
            path.write_text(yaml.safe_dump(values), encoding="utf-8")
            loaded = load_user_settings(path)

        self.assertEqual(loaded.search.local_location, values["search"]["local_location"])

    def test_invalid_settings_name_the_field_before_anything_runs(self):
        cases = {
            "search.local_radius_km": {"search": {"local_radius_km": 0}},
            "search.local_postal_code": {"search": {"local_postal_code": 12345}},
            "matching.local_places": {"matching": {"local_places": []}},
            "matching.commuter_locations.0.minimum_remote_percentage": {
                "matching": {
                    "commuter_locations": [
                        {"search_location": "Kassel", "aliases": ["kassel"], "minimum_remote_percentage": 120}
                    ]
                }
            },
            "search.terms": {"search": {"terms": [" "]}},
        }
        for field, change in cases.items():
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, f"Einstellung {re.escape(field)}"):
                example_settings(**change)

    def test_own_search_terms_and_companies_replace_the_defaults(self):
        from job_finder.matching.config import commuter_search_terms, search_terms, stepstone_search_terms

        with with_settings(search={"terms": ["Python"], "stepstone_terms": ["Data Engineer"]}):
            self.assertEqual(search_terms(), ["Python"])
            self.assertEqual(stepstone_search_terms(), ["Data Engineer"])
            self.assertIn("Junior IT", commuter_search_terms())
        self.assertGreater(len(search_terms()), 1)

    def test_the_settings_choose_company_pages_and_refuse_unknown_names(self):
        import run_finder

        chosen = example_settings(sources={"companies": ["jumo", "edag"]})
        skipped = run_finder.unselected_companies(chosen)

        self.assertNotIn("jumo", skipped)
        self.assertIn("bytewerk", skipped)
        self.assertEqual(run_finder.unselected_companies(example_settings()), set())
        with self.assertRaisesRegex(SystemExit, "unbekannt"):
            run_finder.unselected_companies(example_settings(sources={"companies": ["unbekannt"]}))
