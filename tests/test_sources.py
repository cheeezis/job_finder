"""Tests for source-specific search configuration."""

import unittest

from job_agent.sources.stepstone import build_search_url


class StepStoneSearchTests(unittest.TestCase):
    def test_local_search_uses_postcode_and_radius(self):
        url = build_search_url("Python Developer", "12345", page=2)

        self.assertEqual(
            url,
            "https://www.stepstone.de/jobs/Python-Developer/"
            "in-12345?page=2&radius=30",
        )

    def test_remote_search_does_not_add_local_radius(self):
        url = build_search_url("Python Developer", "Remote")

        self.assertEqual(
            url,
            "https://www.stepstone.de/jobs/Python-Developer/in-Remote?page=1",
        )


if __name__ == "__main__":
    unittest.main()
