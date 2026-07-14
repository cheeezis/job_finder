"""Integration test for the model-based offline pipeline."""

import json
import tempfile
import unittest
from pathlib import Path

from job_agent.main import load_jobs, score_jobs
from job_agent.memory import update_memory
from job_agent.models import Job, JobSource, WorkMode
from job_agent.reporting import write_review_files


class PipelineTests(unittest.TestCase):
    def test_job_round_trip_memory_scoring_and_reporting(self):
        job = Job(
            id="test:123",
            title="Junior Python Developer",
            company="Example GmbH",
            locations=["Fulda"],
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
            write_review_files(
                results,
                scored_path=directory_path / "scored.json",
                review_path=directory_path / "review.md",
            )
            review = (directory_path / "review.md").read_text(encoding="utf-8")

        self.assertEqual(len(results["included"]), 1)
        self.assertIn("Junior Python Developer", review)
        self.assertIn("https://example.test/jobs/123", review)


if __name__ == "__main__":
    unittest.main()
