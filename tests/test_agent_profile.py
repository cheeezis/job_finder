"""Tests for loading the agent's personal profile."""

import tempfile
import unittest
from pathlib import Path

from job_finder.agent.profile import PROFILE_ENV, configured_profile

PROFILE = "# Faktenbasis\nversion: 5\nskills:\n  - Python  # sicher\n"


class AgentProfileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.local = Path(directory.name) / "profile.local.yaml"

    def test_container_profile_wins_and_keeps_its_comments(self):
        self.local.write_text("version: 4\n", encoding="utf-8")

        text, source = configured_profile({PROFILE_ENV: PROFILE}, self.local)

        self.assertEqual((text, source), (PROFILE, PROFILE_ENV))

    def test_local_file_is_the_fallback(self):
        self.local.write_text(PROFILE, encoding="utf-8")

        self.assertEqual(configured_profile({}, self.local), (PROFILE, "profile.local.yaml"))

    def test_missing_or_unusable_profiles_are_refused_with_a_reason(self):
        with self.assertRaisesRegex(ValueError, "Profil fehlt"):
            configured_profile({}, self.local)
        for text, message in (("version: [", "kein gültiges YAML"), ("- a\n- b\n", "YAML-Objekt")):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                configured_profile({PROFILE_ENV: text}, self.local)


if __name__ == "__main__":
    unittest.main()
