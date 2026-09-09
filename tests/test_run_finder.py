"""Tests for top-level source isolation in the productive runner."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from job_finder.models import Job, JobSource
from run_finder import (
    IncompleteSourceSnapshotError,
    build_run_summary,
    canonical_url,
    collect_jobs,
    enrich_candidate_jobs,
    format_duration,
    print_source_summary,
    run_pipeline,
    source_snapshot_is_usable,
    source_error_label,
)


def make_job(job_id):
    return Job(
        id=job_id,
        title="Junior Developer",
        company="Example GmbH",
        locations=["Fulda"],
        sources=[JobSource(source=job_id.split(":")[0], url=f"https://example.test/{job_id}")],
        description_raw="Python",
        description_clean="Python",
    )


class RunFinderTests(unittest.TestCase):
    def test_every_source_can_offer_optional_candidate_enrichment(self):
        calls = []
        plain_source = SimpleNamespace(SOURCE_NAME="plain")
        detailed_source = SimpleNamespace(
            SOURCE_NAME="detailed",
            enrich_candidate_jobs=lambda jobs, ids: calls.append((jobs, ids)) or 2,
        )
        jobs = [make_job("detailed:1")]

        enriched = enrich_candidate_jobs(
            jobs,
            {"detailed:1"},
            [plain_source, detailed_source],
        )

        self.assertEqual(enriched, 2)
        self.assertEqual(calls, [(jobs, {"detailed:1"})])

    def test_pipeline_persists_jobs_after_arbeitnow_enrichment(self):
        job = make_job("arbeitnow:1")
        job.remote_percentage = 100

        def enrich(jobs, candidate_ids):
            self.assertIn(job.id, candidate_ids)
            jobs[0].description_clean = "Originalbeschreibung"
            return 1

        with tempfile.TemporaryDirectory() as directory:
            jobs_file = Path(directory) / "jobs.json"
            source = SimpleNamespace(enrich_candidate_jobs=enrich)
            with (
                patch("run_finder.JOBS_FILE", jobs_file),
                patch("run_finder.MEMORY_FILE", Path(directory) / "state.sqlite3"),
                patch("run_finder.create_backup"),
                patch("run_finder.SOURCES", [source]),
                patch("run_finder.collect_jobs", return_value=(
                    [job], [{"name": "arbeitnow", "status": "success", "jobs": 1}]
                )),
                patch("run_finder.write_recommendations") as recommendations,
                patch("run_finder.process_notifications", return_value={
                    "ready": 0, "configuration_error": None,
                }),
                redirect_stdout(io.StringIO()),
            ):
                run_pipeline(SimpleNamespace(notify=False))
            persisted = json.loads(jobs_file.read_text(encoding="utf-8"))[0]
        result = recommendations.call_args.args[0]["included"][0]
        self.assertEqual(persisted["description_clean"], "Originalbeschreibung")
        self.assertEqual(result["description_clean"], "Originalbeschreibung")
        self.assertEqual(result["id"], persisted["id"])
        self.assertTrue(result["is_new"])
        self.assertTrue(result["first_seen_at"])
        self.assertGreater(result["match_percent"], 0)

    def test_invalid_enriched_data_does_not_commit_memory(self):
        job = make_job("source:1")
        with (
            patch("run_finder.create_backup"),
            patch("run_finder.collect_jobs", return_value=(
                [job], [{"name": "source", "status": "success", "jobs": 1}]
            )),
            patch("run_finder.enrich_candidate_jobs"),
            patch("run_finder.evaluate_jobs", side_effect=ValueError("invalid details")),
            patch("run_finder.edit_memory") as memory,
            patch("run_finder.write_json_atomic") as output,
            self.assertRaises(ValueError),
            redirect_stdout(io.StringIO()),
        ):
            run_pipeline(SimpleNamespace(notify=False))
        memory.assert_not_called()
        output.assert_not_called()

    def test_incomplete_snapshot_does_not_replace_persistent_output(self):
        reports = [
            {"name": "working", "status": "success", "jobs": 1},
            {"name": "broken-1", "status": "failed", "jobs": 0},
            {"name": "broken-2", "status": "failed", "jobs": 0},
            {"name": "partial", "status": "partial", "jobs": 0},
        ]
        with (
            patch("run_finder.create_backup"),
            patch("run_finder.collect_jobs", return_value=([make_job("working:1")], reports)),
            patch("run_finder.edit_memory") as edit_memory,
            patch("run_finder.write_json_atomic") as write_jobs,
            patch("run_finder.write_recommendations") as write_recommendations,
            patch("run_finder.process_notifications") as notifications,
            self.assertRaises(IncompleteSourceSnapshotError),
        ):
            run_pipeline(SimpleNamespace(notify=False))

        edit_memory.assert_not_called()
        write_jobs.assert_not_called()
        write_recommendations.assert_not_called()
        notifications.assert_not_called()

    def test_half_reachable_sources_are_still_usable(self):
        reports = [
            {"status": "success", "jobs": 2},
            {"status": "empty", "jobs": 0},
            {"status": "failed", "jobs": 0},
            {"status": "partial", "jobs": 0},
        ]

        self.assertTrue(source_snapshot_is_usable(reports))

    def test_source_summary_distinguishes_all_coverage_states(self):
        reports = [
            {"name": "complete", "status": "success", "jobs": 2},
            {
                "name": "partial",
                "status": "partial",
                "jobs": 1,
                "failed_segments": 3,
            },
            {"name": "empty", "status": "empty", "jobs": 0},
            {"name": "failed", "status": "failed", "jobs": 0},
        ]
        output = io.StringIO()

        with redirect_stdout(output):
            print_source_summary(reports, 3)

        self.assertIn(
            "1 vollständig · 1 teilweise · 1 ohne Treffer · 1 fehlgeschlagen",
            output.getvalue(),
        )

    def test_failed_source_does_not_stop_following_sources(self):
        failing = SimpleNamespace(SOURCE_NAME="broken", fetch_jobs=lambda: (_ for _ in ()).throw(RuntimeError("kaputt")))
        working = SimpleNamespace(SOURCE_NAME="working", fetch_jobs=lambda: [make_job("working:1")])
        jobs, reports = collect_jobs([failing, working])
        self.assertEqual([job.id for job in jobs], ["working:1"])
        self.assertEqual(reports[0]["error"], "RuntimeError")
        self.assertEqual(reports[1]["status"], "success")

    def test_empty_source_is_reported_as_a_complete_empty_snapshot(self):
        jobs, reports = collect_jobs([SimpleNamespace(SOURCE_NAME="empty", fetch_jobs=lambda: [])])
        self.assertEqual(jobs, [])
        self.assertEqual(reports, [{"name": "empty", "status": "empty", "jobs": 0}])

    def test_adapter_can_report_partial_search_coverage(self):
        source = SimpleNamespace(
            SOURCE_NAME="partial",
            fetch_jobs_with_report=lambda: {
                "jobs": [make_job("partial:1")],
                "status": "partial",
                "details": {"failed_segments": 2, "total_segments": 10},
            },
        )

        jobs, reports = collect_jobs([source])

        self.assertEqual([job.id for job in jobs], ["partial:1"])
        self.assertEqual(reports[0]["status"], "partial")
        self.assertEqual(reports[0]["failed_segments"], 2)

    def test_run_summary_tracks_source_counts_and_review_updates(self):
        job = make_job("working:1")
        job.is_new = True
        summary = build_run_summary(
            duration_seconds=125.4,
            jobs=[job],
            results={"included": [{"is_new": True}, {"content_changed": False}], "excluded": [{}]},
            memory_stats={"new": 1, "known": 0},
            source_reports=[
                {"name": "working", "status": "success", "jobs": 1},
                {"name": "broken", "status": "failed", "jobs": 0},
            ],
            notification_stats={"sent": 1, "failed": 0},
        )
        self.assertEqual(summary["duration"], "2 Min. 05 Sek.")
        self.assertEqual(summary["review_updates"], 1)
        self.assertEqual(summary["notifications"]["sent"], 1)
        self.assertEqual(summary["sources"][0]["new"], 1)
        self.assertEqual(format_duration(5), "5 Sek.")

    def test_canonical_url_keeps_jumo_job_offer_id(self):
        first = canonical_url("https://jobs.jumo.de/engage/jobexchange/showJobOfferDetail.do?jobOfferId=first&j=jobexchange")
        second = canonical_url("https://jobs.jumo.de/engage/jobexchange/showJobOfferDetail.do?jobOfferId=second&j=jobexchange")
        self.assertNotEqual(first, second)

    def test_source_error_label_uses_http_status_without_printing_urls(self):
        self.assertEqual(source_error_label(SimpleNamespace(code=429)), "HTTP 429")


if __name__ == "__main__":
    unittest.main()
