"""Tests for the shared job domain model."""

import json
import unittest
from datetime import date, datetime, timezone

from job_finder.models import (
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

    def test_workflow_and_work_mode_use_separate_status_fields(self):
        job = make_job(
            workflow_status=WorkflowStatus.INTERESTING,
            work_mode=WorkMode.REMOTE,
            remote_percentage=100,
        )

        self.assertEqual(job.workflow_status, WorkflowStatus.INTERESTING)
        self.assertEqual(job.work_mode, WorkMode.REMOTE)

    def test_rejects_invalid_percentages(self):
        with self.assertRaises(ValueError):
            make_job(remote_percentage=120)

    def test_rejects_invalid_salary_range(self):
        with self.assertRaises(ValueError):
            make_job(salary_min_eur=91_000, salary_max_eur=73_000)

    def test_json_round_trip_preserves_complete_job(self):
        job = make_job(
            work_mode=WorkMode.HYBRID,
            remote_percentage=80,
            employment_type="full_time",
            salary_min_eur=73_000,
            salary_max_eur=91_000,
            published_at=date(2026, 7, 10),
            first_seen_at=datetime(2026, 7, 14, 9, 30, tzinfo=timezone.utc),
            last_seen_at=datetime(2026, 7, 14, 10, 30, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 7, 14, 10, 31, tzinfo=timezone.utc),
            workflow_status=WorkflowStatus.REVIEW,
        )

        serialized = json.loads(json.dumps(job.to_dict()))
        restored = Job.from_dict(serialized)

        self.assertEqual(restored, job)


if __name__ == "__main__":
    unittest.main()
