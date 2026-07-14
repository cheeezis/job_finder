"""Tests for the shared job domain model."""

import unittest

from job_agent.models import (
    FilterStatus,
    Job,
    JobSource,
    WorkflowStatus,
    WorkMode,
)


def make_job(**overrides):
    values = {
        "id": "job-1",
        "title": "Junior Python Developer",
        "company": "Example GmbH",
        "locations": ["Fulda"],
        "sources": [
            JobSource(
                source="stepstone",
                source_id="123",
                url="https://example.test/job/123",
            )
        ],
        "description_raw": "<p>Python</p>",
        "description_clean": "Python",
    }
    values.update(overrides)
    return Job(**values)


class JobModelTests(unittest.TestCase):
    def test_defaults_describe_an_unreviewed_job(self):
        job = make_job()

        self.assertEqual(job.workflow_status, WorkflowStatus.NEW)
        self.assertEqual(job.work_mode, WorkMode.UNKNOWN)
        self.assertIsNone(job.filter_status)
        self.assertIsNone(job.match_score)
        self.assertEqual(job.score_reasons, [])

    def test_scoring_and_work_mode_use_separate_status_fields(self):
        job = make_job(
            workflow_status=WorkflowStatus.INTERESTING,
            filter_status=FilterStatus.INCLUDED,
            match_score=82,
            work_mode=WorkMode.REMOTE,
            remote_percentage=100,
        )

        self.assertEqual(job.workflow_status, WorkflowStatus.INTERESTING)
        self.assertEqual(job.filter_status, FilterStatus.INCLUDED)
        self.assertEqual(job.match_score, 82)

    def test_rejects_invalid_percentages(self):
        with self.assertRaises(ValueError):
            make_job(remote_percentage=120)

        with self.assertRaises(ValueError):
            make_job(match_score=-1)

    def test_rejects_invalid_salary_range(self):
        with self.assertRaises(ValueError):
            make_job(salary_min_eur=50_000, salary_max_eur=45_000)


if __name__ == "__main__":
    unittest.main()
