"""Tests for the personal profile supplied to language models."""

import json
import tempfile
import unittest
from pathlib import Path

from job_agent.llm.profile_loader import (
    ProfileValidationError,
    load_llm_profile,
)


class LlmProfileTests(unittest.TestCase):
    def test_project_profile_loads_as_json_compatible_facts(self):
        profile = load_llm_profile()

        self.assertEqual(profile.version, 2)
        self.assertEqual(
            profile.data["profile"]["professional_experience_years"],
            0,
        )
        self.assertEqual(
            profile.data["current_learning"][0]["status"],
            "in_progress",
        )
        self.assertTrue(profile.data["truth_rules"])
        self.assertEqual(
            profile.data["career_preferences"]["evaluation_priorities"]["order"][0],
            "entry_suitability",
        )
        json.dumps(profile.data)

    def test_missing_required_section_is_rejected(self):
        text = Path("profile.yaml").read_text(encoding="utf-8")
        text = text.replace("truth_rules:\n", "removed_truth_rules:\n", 1)

        with TemporaryProfile(text) as path:
            with self.assertRaisesRegex(ProfileValidationError, "truth_rules"):
                load_llm_profile(path)

    def test_invalid_version_is_rejected(self):
        text = Path("profile.yaml").read_text(encoding="utf-8")
        text = text.replace("version: 2", "version: unknown", 1)

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
