"""Frozen anonymized scoring outputs captured before module extraction."""

import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from job_finder.matching import scoring
from job_finder.models import Job


class ScoringParityTests(unittest.TestCase):
    def test_complete_results_match_frozen_baseline(self):
        fixture = json.loads(
            Path(__file__)
            .with_name("fixtures")
            .joinpath("scoring_parity.json")
            .read_text(encoding="utf-8")
        )
        with patch.multiple(scoring, **fixture["settings"]):
            for index, case in enumerate(fixture["cases"]):
                with self.subTest(case=index):
                    job = Job.from_dict({**fixture["job_defaults"], **case["job"]})
                    before = job.to_dict()
                    self.assertEqual(
                        scoring.score_job(job, today=date.fromisoformat(fixture["today"])),
                        case["expected"],
                    )
                    self.assertEqual(job.to_dict(), before)
