"""Integration test for the model-based offline pipeline."""

import json
import tempfile
import unittest
from pathlib import Path

from job_finder.main import load_jobs, score_jobs
from job_finder.memory import update_memory
from job_finder.models import Job, JobSource, WorkMode
from job_finder.profile import LOCAL_PLACES
from job_finder.reporting import write_recommendations


class PipelineTests(unittest.TestCase):
    def test_job_round_trip_memory_scoring_and_reporting(self):
        job = Job(
            id="test:123",
            title="Junior Python Developer",
            company="Example GmbH",
            locations=[LOCAL_PLACES[0]],
            sources=[
                JobSource(
                    source="test",
                    source_id="123",
                    url="https://example.test/jobs/123",
                )
            ],
            description_raw="<p>Python, keine Berufserfahrung erforderlich.</p>",
            description_clean="Python, keine Berufserfahrung erforderlich.",
            work_mode=WorkMode.HYBRID,
        )
        update_memory([job], {})

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            jobs_path = directory_path / "jobs.json"
            jobs_path.write_text(
                json.dumps([job.to_dict()]),
                encoding="utf-8",
            )

            restored_jobs = load_jobs(jobs_path)
            results = score_jobs(restored_jobs)
            results["included"][0]["llm_score"] = 90
            results["included"][0]["llm_result"] = {
                "recommendation": "strong_match",
                "confidence": "high",
                "summary": "Passende Einstiegsstelle.",
                "tasks": [],
                "requirements": [],
                "matching_evidence": [],
                "gaps": [],
                "risks": [],
            }
            write_recommendations(
                results,
                json_path=directory_path / "recommendations.json",
                markdown_path=directory_path / "recommendations.md",
            )
            review = (directory_path / "recommendations.md").read_text(
                encoding="utf-8"
            )

        self.assertEqual(len(results["included"]), 1)
        self.assertIn("Junior Python Developer", review)
        self.assertIn("https://example.test/jobs/123", review)


if __name__ == "__main__":
    unittest.main()
