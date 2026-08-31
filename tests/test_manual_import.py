"""Tests for targeted manual processing without a complete finder run."""

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from job_finder.manual_import import import_manual_url
from job_finder.memory import load_memory
from job_finder.models import Job, JobSource, WorkMode


class ManualImportTests(unittest.TestCase):
    def test_import_runs_only_target_and_keeps_prefilter_warning(self):
        job = Job(
            id="manual:python",
            title="Junior Python Entwickler (m/w/d)",
            company="Example GmbH",
            locations=["München"],
            sources=[JobSource("manual", "https://example.com/jobs/python")],
            description_raw="Python Junior Entwicklung " * 20,
            description_clean="Python Junior Entwicklung " * 20,
            work_mode=WorkMode.ONSITE,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {
                "cache_path": root / "manual.json",
                "jobs_path": root / "jobs.json",
                "memory_path": root / "seen.json",
                "recommendations_path": root / "recommendations.json",
            }
            with patch("job_finder.manual_import.manual.add_url", return_value=job) as add_url:
                result = import_manual_url("https://example.com/jobs/python", **paths)
            recommendations = json.loads(
                paths["recommendations_path"].read_text(encoding="utf-8")
            )["recommendations"]
            saved_jobs = json.loads(paths["jobs_path"].read_text(encoding="utf-8"))
            memory = load_memory(paths["memory_path"])

        add_url.assert_called_once()
        self.assertEqual(result["prefilter_warning"], "Ort/Remote passt nicht")
        self.assertIsInstance(result["match_percent"], int)
        self.assertEqual(recommendations[0]["prefilter_warning"], "Ort/Remote passt nicht")
        self.assertNotIn("llm_score", recommendations[0])
        self.assertEqual(saved_jobs[0]["id"], "manual:python")
        self.assertIn("manual:python", memory)

    def test_manual_source_remains_reviewable_in_later_pipeline_runs(self):
        from job_finder.main import score_jobs

        job = Job(
            id="manual:remote-conflict",
            title="Junior Python Entwickler",
            company="Example GmbH",
            locations=["München"],
            sources=[JobSource("manual", "https://example.com/job")],
            description_raw="Python Entwicklung " * 20,
            description_clean="Python Entwicklung " * 20,
            work_mode=WorkMode.ONSITE,
        )
        results = score_jobs([job])
        self.assertEqual(len(results["included"]), 1)
        self.assertEqual(results["included"][0]["prefilter_warning"], "Ort/Remote passt nicht")

    def test_old_manual_job_remains_reviewable_with_warning(self):
        from job_finder.main import score_jobs

        job = Job(
            id="manual:old",
            title="Junior Python Entwickler",
            company="Example GmbH",
            locations=["Fulda"],
            sources=[JobSource("manual", "https://example.com/old")],
            description_raw="Python Entwicklung " * 20,
            description_clean="Python Entwicklung " * 20,
            published_at=date.today() - timedelta(days=61),
        )

        results = score_jobs([job])

        self.assertEqual(len(results["included"]), 1)
        self.assertIn("älter als 60 Tage", results["included"][0]["prefilter_warning"])


if __name__ == "__main__":
    unittest.main()
