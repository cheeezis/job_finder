"""Tests for top-level source isolation in the productive runner."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from job_finder.models import Job, JobSource
from job_finder.sources.common import (
    record_candidate_failure,
    record_partial_failure,
    record_total_segments,
)
from run_finder import (
    SOURCES,
    IncompleteSourceSnapshotError,
    build_run_summary,
    collect_jobs,
    enrich_candidate_jobs,
    excluded_source_names,
    format_duration,
    parse_args,
    parse_source_names,
    print_source_summary,
    require_usable_source_snapshot,
    run_pipeline,
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

        with redirect_stdout(io.StringIO()):
            reports = enrich_candidate_jobs(jobs, {"detailed:1"}, [plain_source, detailed_source])

        self.assertEqual(reports, [{"name": "detailed", "enriched": 2, "failed": 0}])
        self.assertEqual(calls, [(jobs, {"detailed:1"})])

    def test_failed_candidate_details_are_reported_and_logged_per_source(self):
        def failing(jobs, candidate_ids):
            record_candidate_failure(3)
            return 1

        sources = [
            SimpleNamespace(SOURCE_NAME="studysmarter", enrich_candidate_jobs=failing),
            SimpleNamespace(SOURCE_NAME="get_in_it", enrich_candidate_jobs=lambda jobs, ids: 2),
        ]
        output = io.StringIO()

        with redirect_stdout(output):
            reports = enrich_candidate_jobs([], set(), sources, run_id="run-1")

        self.assertEqual(
            reports,
            [
                {"name": "studysmarter", "enriched": 1, "failed": 3},
                {"name": "get_in_it", "enriched": 2, "failed": 0},
            ],
        )
        events = [
            json.loads(line) for line in output.getvalue().splitlines() if line.startswith("{")
        ]
        self.assertEqual(
            [
                (event["event"], event["source"], event["level"], event["failed"])
                for event in events
            ],
            [
                ("enrichment_completed", "studysmarter", "warning", 3),
                ("enrichment_completed", "get_in_it", "info", 0),
            ],
        )
        self.assertEqual({event["run_id"] for event in events}, {"run-1"})

    def test_pipeline_persists_jobs_after_arbeitnow_enrichment(self):
        job = make_job("arbeitnow:1")
        job.remote_percentage = 100

        def enrich(jobs, candidate_ids):
            self.assertIn(job.id, candidate_ids)
            jobs[0].description_clean = "Originalbeschreibung"
            return 1

        with tempfile.TemporaryDirectory() as directory:
            jobs_file = Path(directory) / "jobs.json"
            source = SimpleNamespace(SOURCE_NAME="arbeitnow", enrich_candidate_jobs=enrich)
            with (
                patch("run_finder.JOBS_FILE", jobs_file),
                patch("run_finder.MEMORY_FILE", Path(directory) / "state.sqlite3"),
                patch("run_finder.create_backup"),
                patch("run_finder.SOURCES", [source]),
                patch(
                    "run_finder.collect_jobs",
                    return_value=([job], [{"name": "arbeitnow", "status": "success", "jobs": 1}]),
                ),
                patch("run_finder.write_recommendations") as recommendations,
                patch(
                    "run_finder.process_notifications",
                    return_value={"ready": 0, "sent": 0, "failed": 0, "configuration_error": None},
                ),
                redirect_stdout(io.StringIO()),
            ):
                run_pipeline()
            persisted = json.loads(jobs_file.read_text(encoding="utf-8"))[0]
        result = recommendations.call_args.args[0]["included"][0]
        self.assertEqual(persisted["description_clean"], "Originalbeschreibung")
        self.assertEqual(result["description_clean"], "Originalbeschreibung")
        self.assertEqual(result["id"], persisted["id"])
        self.assertTrue(result["is_new"])
        self.assertTrue(result["first_seen_at"])
        self.assertGreater(result["match_percent"], 0)

    def test_pipeline_enriches_only_selected_sources(self):
        job = make_job("kept:1")
        called = []
        sources = [
            SimpleNamespace(
                SOURCE_NAME=name,
                enrich_candidate_jobs=lambda jobs, ids, name=name: called.append(name) or 0,
            )
            for name in ("kept", "excluded")
        ]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("run_finder.JOBS_FILE", Path(directory) / "jobs.json"),
            patch("run_finder.MEMORY_FILE", Path(directory) / "state.sqlite3"),
            patch("run_finder.create_backup"),
            patch("run_finder.SOURCES", sources),
            patch(
                "run_finder.collect_jobs",
                return_value=([job], [{"name": "kept", "status": "success", "jobs": 1}]),
            ),
            patch("run_finder.write_recommendations"),
            patch(
                "run_finder.process_notifications",
                return_value={"ready": 0, "sent": 0, "failed": 0, "configuration_error": None},
            ),
            redirect_stdout(io.StringIO()),
        ):
            run_pipeline(exclude_sources={"excluded"})
        self.assertEqual(called, ["kept"])

    def test_invalid_enriched_data_does_not_commit_memory(self):
        job = make_job("source:1")
        with (
            patch("run_finder.create_backup"),
            patch(
                "run_finder.collect_jobs",
                return_value=([job], [{"name": "source", "status": "success", "jobs": 1}]),
            ),
            patch("run_finder.enrich_candidate_jobs"),
            patch("run_finder.evaluate_jobs", side_effect=ValueError("invalid details")),
            patch("run_finder.edit_memory") as memory,
            patch("run_finder.publish_results") as output,
            self.assertRaises(ValueError),
            redirect_stdout(io.StringIO()),
        ):
            run_pipeline()
        memory.assert_not_called()
        output.assert_not_called()

    def test_container_runs_skip_the_discarded_zip_backup(self):
        for env, expected_calls in (({}, 1), ({"JOBFINDER_SKIP_RUN_BACKUP": "1"}, 0)):
            with (
                self.subTest(env=env),
                patch.dict(os.environ),
                patch("run_finder.create_backup") as backup,
                patch("run_finder.collect_jobs", side_effect=RuntimeError("stop after backup")),
                redirect_stdout(io.StringIO()),
            ):
                os.environ.pop("JOBFINDER_SKIP_RUN_BACKUP", None)
                os.environ.update(env)
                with self.assertRaises(RuntimeError):
                    run_pipeline()
                self.assertEqual(backup.call_count, expected_calls)

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
            patch("run_finder.publish_results") as write_jobs,
            patch("run_finder.write_recommendations") as write_recommendations,
            patch("run_finder.process_notifications") as notifications,
            self.assertRaises(IncompleteSourceSnapshotError),
        ):
            run_pipeline()

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

        require_usable_source_snapshot(reports)

    def test_source_summary_distinguishes_all_coverage_states(self):
        reports = [
            {"name": "complete", "status": "success", "jobs": 2},
            {"name": "partial", "status": "partial", "jobs": 1, "failed_segments": 3},
            {"name": "empty", "status": "empty", "jobs": 0},
            {"name": "failed", "status": "failed", "jobs": 0},
        ]
        output = io.StringIO()

        with redirect_stdout(output):
            print_source_summary(reports, 3)

        self.assertIn(
            "1 vollständig · 1 teilweise · 1 ohne Treffer · 1 fehlgeschlagen", output.getvalue()
        )

    def test_failed_source_does_not_stop_following_sources(self):
        failing = SimpleNamespace(
            SOURCE_NAME="broken", fetch_jobs=lambda: (_ for _ in ()).throw(RuntimeError("kaputt"))
        )
        working = SimpleNamespace(SOURCE_NAME="working", fetch_jobs=lambda: [make_job("working:1")])
        jobs, reports = collect_jobs([failing, working])
        self.assertEqual([job.id for job in jobs], ["working:1"])
        self.assertEqual(reports[0]["error"], "RuntimeError")
        self.assertEqual(reports[1]["status"], "success")

    def test_collect_jobs_logs_one_structured_event_per_source(self):
        failing = SimpleNamespace(
            SOURCE_NAME="broken", fetch_jobs=lambda: (_ for _ in ()).throw(RuntimeError("kaputt"))
        )
        working = SimpleNamespace(SOURCE_NAME="working", fetch_jobs=lambda: [make_job("working:1")])
        output = io.StringIO()
        with redirect_stdout(output):
            collect_jobs([failing, working], run_id="test-run-1")

        events = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if line.strip().startswith("{")
        ]
        self.assertEqual([e["event"] for e in events], ["source_completed"] * 2)
        self.assertEqual(events[0]["run_id"], "test-run-1")
        self.assertEqual(events[0]["source"], "broken")
        self.assertEqual(events[0]["status"], "failed")
        self.assertEqual(events[0]["level"], "error")
        self.assertEqual(events[1]["source"], "working")
        self.assertEqual(events[1]["status"], "success")
        self.assertEqual(events[1]["jobs_found"], 1)
        self.assertIn("duration_seconds", events[1])

    def test_empty_source_is_reported_as_a_complete_empty_snapshot(self):
        jobs, reports = collect_jobs([SimpleNamespace(SOURCE_NAME="empty", fetch_jobs=list)])
        self.assertEqual(jobs, [])
        self.assertEqual(reports, [{"name": "empty", "status": "empty", "jobs": 0}])

    def test_adapter_can_report_partial_search_coverage(self):
        def fetch_jobs():
            record_total_segments(10)
            record_partial_failure(2)
            return [make_job("partial:1")]

        jobs, reports = collect_jobs(
            [SimpleNamespace(SOURCE_NAME="partial", fetch_jobs=fetch_jobs)]
        )

        self.assertEqual([job.id for job in jobs], ["partial:1"])
        self.assertEqual(reports[0]["status"], "partial")
        self.assertEqual(reports[0]["failed_segments"], 2)
        self.assertEqual(reports[0]["total_segments"], 10)

    def test_run_summary_tracks_source_counts_and_review_new(self):
        job = make_job("working:1")
        job.is_new = True
        summary = build_run_summary(
            duration_seconds=125.4,
            jobs=[job],
            results={"included": [{"is_new": True}, {"is_new": False}], "excluded": [{}]},
            memory_stats={"new": 1, "known": 0},
            source_reports=[
                {"name": "working", "status": "success", "jobs": 1},
                {"name": "broken", "status": "failed", "jobs": 0},
            ],
            notification_stats={"sent": 1, "failed": 0},
            enrichment_reports=[
                {"name": "studysmarter", "enriched": 3, "failed": 12},
                {"name": "get_in_it", "enriched": 5, "failed": 0},
            ],
        )
        self.assertEqual(summary["duration"], "2 Min. 05 Sek.")
        self.assertEqual(summary["detail_failures"], [{"label": "StudySmarter", "failed": 12}])
        self.assertEqual(summary["review_new"], 1)
        self.assertEqual(summary["notifications"]["sent"], 1)
        self.assertEqual(summary["sources"][0]["new"], 1)
        self.assertEqual(format_duration(5), "5 Sek.")

    def test_source_error_label_uses_http_status_without_printing_urls(self):
        self.assertEqual(source_error_label(SimpleNamespace(code=429)), "HTTP 429")

    def test_parse_source_names_splits_and_ignores_blanks(self):
        self.assertEqual(parse_source_names("stepstone, remotely,, "), {"stepstone", "remotely"})
        self.assertEqual(parse_source_names(""), set())

    def test_only_sources_skip_every_other_source(self):
        all_names = {source.SOURCE_NAME for source in SOURCES}
        cases = {
            "exclude": (
                SimpleNamespace(exclude_sources="stepstone", only_sources=None),
                {"stepstone"},
            ),
            "only": (
                SimpleNamespace(exclude_sources="", only_sources="stepstone, remotely"),
                all_names - {"stepstone", "remotely"},
            ),
        }
        for name, (args, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(excluded_source_names(args), expected)

    def test_selections_without_any_source_are_refused(self):
        every_source = ",".join(source.SOURCE_NAME for source in SOURCES)
        for exclude, only in (("", ""), ("", " , "), (every_source, None)):
            args = SimpleNamespace(exclude_sources=exclude, only_sources=only)
            with (
                self.subTest(exclude=exclude, only=only),
                self.assertRaisesRegex(SystemExit, "Keine Quelle"),
            ):
                excluded_source_names(args)

    def test_empty_source_list_runs_no_source(self):
        source = SimpleNamespace(SOURCE_NAME="unused", fetch_jobs=self.fail)
        with patch("run_finder.SOURCES", [source]), redirect_stdout(io.StringIO()):
            self.assertEqual(collect_jobs([]), ([], []))
            self.assertEqual(enrich_candidate_jobs([], set(), sources=[]), [])

    def test_only_sources_rejects_unknown_names(self):
        args = SimpleNamespace(exclude_sources="", only_sources="stepstone,stepstnoe")
        with self.assertRaisesRegex(SystemExit, "stepstnoe"):
            excluded_source_names(args)

    def test_only_and_exclude_sources_cannot_be_combined(self):
        argv = ["run_finder.py", "--only-sources", "stepstone", "--exclude-sources", "css"]
        with patch("sys.argv", argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args()

    def test_excluded_sources_are_skipped_before_collection(self):
        kept = SimpleNamespace(SOURCE_NAME="arbeitnow")
        excluded = SimpleNamespace(SOURCE_NAME="stepstone")
        seen_sources = []

        def fake_collect_jobs(sources, run_id=None):
            seen_sources.extend(sources)
            return [], [{"name": "arbeitnow", "status": "empty", "jobs": 0}]

        with (
            patch("run_finder.SOURCES", [kept, excluded]),
            patch("run_finder.create_backup"),
            patch("run_finder.collect_jobs", side_effect=fake_collect_jobs),
            patch("run_finder.write_recommendations"),
            patch(
                "run_finder.process_notifications",
                return_value={"ready": 0, "sent": 0, "failed": 0, "configuration_error": None},
            ),
            redirect_stdout(io.StringIO()),
        ):
            run_pipeline(exclude_sources={"stepstone"})

        self.assertEqual(seen_sources, [kept])
