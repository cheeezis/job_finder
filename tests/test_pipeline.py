"""Integration test for the rule-based offline pipeline."""

import json
import tempfile
import unittest
from pathlib import Path

from job_finder.matching.scoring import LOCAL_PLACES
from job_finder.models import Job, JobSource, WorkMode
from job_finder.workflow.main import combine_listings, load_jobs, score_jobs
from job_finder.workflow.memory import update_memory
from job_finder.workflow.reporting import write_recommendations


class PipelineTests(unittest.TestCase):
    def test_job_round_trip_memory_scoring_and_reporting(self):
        job = Job(
            id="test:123",
            title="Junior Python Developer",
            company="Example GmbH",
            locations=[LOCAL_PLACES[0]],
            sources=[
                JobSource(source="test", source_id="123", url="https://example.test/jobs/123")
            ],
            description_raw="<p>Python, keine Berufserfahrung erforderlich.</p>",
            description_clean="Python, keine Berufserfahrung erforderlich.",
            work_mode=WorkMode.HYBRID,
        )
        update_memory([job], {})

        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            jobs_path = directory_path / "jobs.json"
            jobs_path.write_text(json.dumps([job.to_dict()]), encoding="utf-8")

            restored_jobs = load_jobs(jobs_path)
            results = score_jobs(restored_jobs)
            write_recommendations(results, json_path=directory_path / "recommendations.json")
            review = json.loads(
                (directory_path / "recommendations.json").read_text(encoding="utf-8")
            )

        self.assertEqual(len(results["included"]), 1)
        self.assertEqual(review["recommendations"][0]["title"], "Junior Python Developer")
        self.assertEqual(review["recommendations"][0]["url"], "https://example.test/jobs/123")
        self.assertIn("match_percent", review["recommendations"][0])

    def test_listings_of_one_job_become_one_card_led_by_the_best_rated(self):
        def listing(source, place, **changes):
            return Job(
                id="arbeitnow:1",
                title="Junior Python Developer",
                company="Example GmbH",
                locations=[place],
                sources=[JobSource(source=source, url=f"https://{source}.test/1")],
                description_raw=source,
                description_clean=source,
                **changes,
            )

        berlin = listing("stepstone", "Berlin", is_new=True)
        fulda = listing("arbeitnow", "Fulda")
        excluded = {"filter_status": "excluded", "match_percent": 0, "experience_rank": 99}
        included = {"filter_status": "included", "match_percent": 70, "experience_rank": 0}

        cards = combine_listings([(berlin, excluded), (fulda, included)])

        self.assertEqual(len(cards), 1)
        job, result = cards[0]
        self.assertIs(result, included)
        self.assertEqual(job.description_clean, "arbeitnow")
        self.assertEqual(job.locations, ["Fulda", "Berlin"])
        self.assertEqual([source.source for source in job.sources], ["arbeitnow", "stepstone"])
        self.assertTrue(job.is_new)
