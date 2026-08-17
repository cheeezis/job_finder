"""Tests for the personal profile supplied to language models."""

import json
import tempfile
import unittest
from pathlib import Path

from job_finder.llm.profile_loader import (
    EXAMPLE_PROFILE_PATH,
    ProfileValidationError,
    load_llm_profile,
)


class LlmProfileTests(unittest.TestCase):
    def test_example_profile_loads_as_json_compatible_facts(self):
        profile = load_llm_profile(EXAMPLE_PROFILE_PATH)

        self.assertEqual(profile.version, 3)
        self.assertIsNone(
            profile.data["profile"]["professional_experience_years"]
        )
        self.assertEqual(profile.data["profile"]["location"], {})
        self.assertEqual(profile.data["education"], [])
        self.assertEqual(profile.data["experience"], [])
        self.assertEqual(profile.data["projects"], [])
        self.assertEqual(profile.data["skills"], {})
        self.assertEqual(profile.data["languages"], [])
        self.assertEqual(profile.data["certifications"], [])
        self.assertEqual(profile.data["current_learning"], [])
        self.assertTrue(profile.data["truth_rules"])
        self.assertEqual(
            profile.data["career_preferences"]["evaluation_priorities"]["order"][0],
            "entry_suitability",
        )
        json.dumps(profile.data)

    def test_missing_required_section_is_rejected(self):
        text = EXAMPLE_PROFILE_PATH.read_text(encoding="utf-8")
        text = text.replace("truth_rules:\n", "removed_truth_rules:\n", 1)

        with TemporaryProfile(text) as path:
            with self.assertRaisesRegex(ProfileValidationError, "truth_rules"):
                load_llm_profile(path)

    def test_invalid_version_is_rejected(self):
        text = EXAMPLE_PROFILE_PATH.read_text(encoding="utf-8")
        text = text.replace("version: 3", "version: unknown", 1)

        with TemporaryProfile(text) as path:
            with self.assertRaisesRegex(ProfileValidationError, "version"):
                load_llm_profile(path)

    def test_invalid_yaml_is_rejected(self):
        with TemporaryProfile("version: [\n") as path:
            with self.assertRaisesRegex(ProfileValidationError, "ungueltiges YAML"):
                load_llm_profile(path)


class TemporaryProfile:
    """Context manager for one temporary profile file."""

    def __init__(self, text):
        self.text = text
        self.directory = None

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "profile.yaml"
        path.write_text(self.text, encoding="utf-8")
        return path

    def __exit__(self, *_):
        self.directory.cleanup()
