"""Tests for the basis stamped onto every fact sheet and the parts the review names as changed."""

import unittest

from job_finder.agent.basis import PARTS, changed_parts, job_basis, run_basis
from job_finder.agent.settings import agent_settings

SETTINGS = agent_settings({"agent": {"enabled": True}})
PLACES = {"matching": {"local_places": ["telgte"]}, "search": {"local_location": "Münster", "local_radius_km": 25}}
AD = {
    "title": "Junior Python Developer",
    "company": "Beispiel GmbH",
    "locations": ["Münster"],
    "sources": [{"source": "stepstone", "url": "https://example.com/jobs/1"}],
    "description_clean": "Wir suchen Verstärkung.",
}


class BasisTests(unittest.TestCase):
    def test_the_profile_counts_by_content_not_by_its_text(self):
        # The local file and the Key Vault copy may differ in comments, quotes and line endings.
        local = "# Mein Profil\nname: Alex\nziele:\n  - Python\n"
        vault = "name: 'Alex'\r\nziele: [Python]\r\n"

        self.assertEqual(run_basis(local, PLACES, SETTINGS), run_basis(vault, PLACES, SETTINGS))
        self.assertNotEqual(
            run_basis(local, PLACES, SETTINGS)["profile"],
            run_basis("name: Alex\nziele: [Java]", PLACES, SETTINGS)["profile"],
        )

    def test_the_places_of_the_search_settings_belong_to_the_profile(self):
        moved = {**PLACES, "matching": {"local_places": ["greven"]}}

        self.assertNotEqual(run_basis("name: Alex", PLACES, SETTINGS), run_basis("name: Alex", moved, SETTINGS))

    def test_the_model_part_names_model_and_reasoning_effort(self):
        effort = agent_settings({"agent": {"enabled": True, "reasoning_effort": "low"}})

        self.assertEqual(run_basis("name: Alex", {}, effort)["model"], "gpt-5-mini low")

    def test_the_ad_counts_what_the_agent_is_shown(self):
        run = run_basis("name: Alex", PLACES, SETTINGS)
        stamp = job_basis(run, AD)

        self.assertEqual(job_basis(run, {**AD, "fetched_at": "2026-10-07T08:00:00"}), stamp)
        self.assertEqual(changed_parts(stamp, job_basis(run, {**AD, "description_clean": "Neuer Text."})), ["ad"])
        self.assertEqual(changed_parts(stamp, job_basis(run, {**AD, "locations": ["Greven"]})), ["ad"])

    def test_changed_parts_come_in_a_fixed_order(self):
        stamp = job_basis(run_basis("name: Alex", PLACES, SETTINGS), AD)
        current = {**stamp, "graph": "2", "profile": "anders", "ad": "anders"}

        self.assertEqual(changed_parts(stamp, stamp), [])
        self.assertEqual(changed_parts(stamp, current), ["profile", "ad", "graph"])
        self.assertEqual(set(stamp), set(PARTS))
