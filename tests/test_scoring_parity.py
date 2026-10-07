"""Frozen anonymized scoring outputs captured before module extraction."""

import json
import unittest
from datetime import date
from pathlib import Path

from settings_helpers import with_settings

from job_finder.matching import scoring
from job_finder.models import Job


class ScoringParityTests(unittest.TestCase):
    def test_complete_results_match_frozen_baseline(self):
        fixture = json.loads(
            Path(__file__).with_name("fixtures").joinpath("scoring_parity.json").read_text(encoding="utf-8")
        )
        # The frozen fixture names the settings as the module constants of that time.
        frozen = fixture["settings"]
        settings = with_settings(
            search={"local_radius_km": frozen["LOCAL_SEARCH_RADIUS_KM"]},
            matching={
                "local_places": frozen["LOCAL_PLACES"],
                "commuter_locations": frozen["COMMUTER_LOCATIONS"],
                "salary_target_eur": frozen["SALARY_TARGET"],
                "salary_minimum_eur": frozen["SALARY_MINIMUM"],
                "profile_domain_keywords": frozen["PROFILE_DOMAIN_KEYWORDS"],
            },
        )
        with settings:
            for index, case in enumerate(fixture["cases"]):
                with self.subTest(case=index):
                    job = Job.from_dict({**fixture["job_defaults"], **case["job"]})
                    before = job.to_dict()
                    self.assertEqual(
                        scoring.score_job(job, today=date.fromisoformat(fixture["today"])),
                        fixture["results"][case["result"]],
                    )
                    self.assertEqual(job.to_dict(), before)
