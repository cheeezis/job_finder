"""Source reports, console lines and log events of collect_jobs."""

import io
import os
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from job_finder.models import Job, JobSource
from job_finder.persistence.database import transaction
from job_finder.sources import german_tech_jobs, get_in_it, stepstone, studysmarter
from job_finder.sources.common import record_partial_failure
from run_finder import collect_jobs

GET_IN_IT_RECORD = {
    "id": 311970,
    "title": "(Junior) DevOps Software Engineer (m/w/d)",
    "url": "/jobsuche/p311970",
    "homeOffice": True,
    "careers": [{"name": "System Engineering / Admin"}],
    "locations": [{"name": "München"}],
    "company": {"title": "Reply Deutschland SE"},
}
STUDYSMARTER_RECORD = {
    "id": 12345678,
    "title": "Junior Python Developer (m/w/d)",
    "link": "https://talents.studysmarter.de/companies/example/junior-python-developer-12345678/",
    "company_name": "Example GmbH",
    "is_remote_positions": "completely",
}
FEED_JOB = """<job id="feed-1"><id><![CDATA[feed-1]]></id>
    <title><![CDATA[Junior Python Developer (m/w/d)]]></title>
    <link><![CDATA[https://germantechjobs.de/jobs/example]]></link>
    <country><![CDATA[Germany]]></country><location><![CDATA[Full Remote]]></location>
    <company-name><![CDATA[Example GmbH]]></company-name>
    <description><![CDATA[<p>Python und APIs.</p>]]></description></job>"""
FEED_WITHOUT_TITLE = """<job id="feed-2"><id><![CDATA[feed-2]]></id>
    <link><![CDATA[https://germantechjobs.de/jobs/other]]></link>
    <company-name><![CDATA[Example GmbH]]></company-name></job>"""


def feed(*jobs):
    return f'<?xml version="1.0" encoding="UTF-8"?><jobs>{"".join(jobs)}</jobs>'


def plain_job(source, number):
    return Job(
        id=f"{source}:{number}",
        title=f"Job {number}",
        company="Example GmbH",
        locations=["Fulda"],
        sources=[JobSource(source, f"https://example.test/{source}/{number}")],
        description_raw="Text",
        description_clean="Text",
    )


class StepStoneClient:
    """Answer every StepStone request with one prepared response or error."""

    answer = ""

    def get(self, url):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class SourceReportTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py: report sources write caches")
        self.clear_datasets()
        self.addCleanup(self.clear_datasets)

    def clear_datasets(self):
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE datasets CASCADE")

    def collect(self, source):
        """Run collect_jobs for one source; return report, events and console text."""
        events = []
        output = io.StringIO()
        with (
            patch(
                "run_finder.log_event",
                side_effect=lambda event, **fields: events.append({"event": event, **fields}),
            ),
            redirect_stdout(output),
        ):
            _jobs, reports = collect_jobs([source])
        for event in events:
            del event["duration_seconds"]
        return reports, events, output.getvalue()

    def assert_report(self, source, name, status, jobs, details, console):
        reports, events, output = self.collect(source)
        level = "info" if status in {"success", "empty"} else "warning"
        self.assertEqual(reports, [{"name": name, "status": status, "jobs": jobs, **details}])
        self.assertEqual(
            events,
            [
                {
                    "event": "source_completed",
                    "run_id": None,
                    "level": level,
                    "source": name,
                    "status": status,
                    "jobs_found": jobs,
                    **details,
                }
            ],
        )
        for line in console:
            self.assertIn(line, output)

    def test_get_in_it_reports_searched_and_failed_segments(self):
        searches = [
            {"priority_id": 1, "location": "Fulda"},
            {"priority_id": 2, "location": "remote"},
        ]
        cases = [
            ([[GET_IN_IT_RECORD], [GET_IN_IT_RECORD]], "success", 0, ["1 Stellen"]),
            (
                [[GET_IN_IT_RECORD], RuntimeError("offline")],
                "partial",
                1,
                [
                    "WARNUNG get-in-IT: 1 Suche(n) fehlgeschlagen",
                    "1 Stellen · Teilergebnis (1 Segment(e) fehlgeschlagen)",
                ],
            ),
        ]
        for answers, status, failed, console in cases:
            with (
                self.subTest(status),
                patch.object(get_in_it, "build_api_searches", return_value=iter(searches)),
                patch.object(get_in_it, "search_api", side_effect=answers),
            ):
                self.assert_report(
                    get_in_it,
                    "get_in_it",
                    status,
                    1,
                    {"failed_segments": failed, "total_segments": 2},
                    console,
                )

    def test_studysmarter_reports_searched_and_failed_segments(self):
        page = {"data": [STUDYSMARTER_RECORD], "total_pages": 1}
        cases = [
            ([{"keyword": "a"}], [page], "success", 0, ["1 Stellen"]),
            (
                [{"keyword": "a"}, {"keyword": "b"}],
                [page, RuntimeError("offline")],
                "partial",
                1,
                ["WARNUNG StudySmarter: 1 Suche(n) fehlgeschlagen"],
            ),
        ]
        for searches, answers, status, failed, console in cases:
            with (
                self.subTest(status),
                patch.object(studysmarter, "build_searches", return_value=searches),
                patch.object(studysmarter, "fetch_json", side_effect=answers),
            ):
                self.assert_report(
                    studysmarter,
                    "studysmarter",
                    status,
                    1,
                    {"failed_segments": failed, "total_segments": len(searches)},
                    console,
                )

    def test_german_tech_jobs_reports_invalid_feed_entries_as_segments(self):
        cases = [
            (feed(FEED_JOB), "success", 0, 1),
            (feed(FEED_JOB, FEED_WITHOUT_TITLE), "partial", 1, 2),
        ]
        for xml, status, failed, total in cases:
            with (
                self.subTest(status),
                patch.object(german_tech_jobs, "fetch_text", return_value=xml),
            ):
                self.assert_report(
                    german_tech_jobs,
                    "german_tech_jobs",
                    status,
                    1,
                    {"failed_segments": failed, "total_segments": total},
                    ["1 Stellen"],
                )

    def test_stepstone_reports_empty_searches_blocks_and_search_errors(self):
        cases = [
            ("<html></html>", "empty", 0, []),
            (
                stepstone.StepStoneBlockedError(429, "https://www.stepstone.de/jobs"),
                "partial",
                1,
                ["WARNUNG StepStone: HTTP 429; nutze letzten Cache-Stand"],
            ),
            (
                RuntimeError("offline"),
                "partial",
                1,
                ["WARNUNG StepStone: 1 Suchseite(n) nicht erreichbar"],
            ),
        ]
        for answer, status, failed, console in cases:
            with (
                self.subTest(status=status, answer=type(answer).__name__),
                patch.multiple(
                    stepstone,
                    STEPSTONE_SEARCH_TERMS=["python"],
                    STEPSTONE_SEARCH_LOCATIONS=["Fulda"],
                ),
                patch.object(
                    stepstone,
                    "StepStoneHttpClient",
                    type("Client", (StepStoneClient,), {"answer": answer}),
                ),
            ):
                self.assert_report(
                    stepstone,
                    "stepstone",
                    status,
                    0,
                    {"failed_segments": failed, "total_segments": 1},
                    console,
                )

    def test_plain_sources_report_handled_failures_errors_and_empty_results(self):
        def partly_failed():
            record_partial_failure(2)
            return [plain_job("arbeitnow", 1)]

        self.assert_report(
            SimpleNamespace(SOURCE_NAME="arbeitnow", fetch_jobs=partly_failed),
            "arbeitnow",
            "partial",
            1,
            {"failed_segments": 2},
            ["1 Stellen · Teilergebnis (2 Segment(e) fehlgeschlagen)"],
        )
        self.assert_report(
            SimpleNamespace(SOURCE_NAME="jobicy", fetch_jobs=list),
            "jobicy",
            "empty",
            0,
            {},
            ["0 Stellen"],
        )

        def broken():
            raise RuntimeError("kaputt")

        reports, events, output = self.collect(
            SimpleNamespace(SOURCE_NAME="css", fetch_jobs=broken)
        )
        self.assertEqual(
            reports, [{"name": "css", "status": "failed", "jobs": 0, "error": "RuntimeError"}]
        )
        self.assertEqual(
            events,
            [
                {
                    "event": "source_completed",
                    "run_id": None,
                    "level": "error",
                    "source": "css",
                    "status": "failed",
                    "jobs_found": 0,
                    "error": "RuntimeError",
                }
            ],
        )
        self.assertIn("fehlgeschlagen (RuntimeError)", output)
