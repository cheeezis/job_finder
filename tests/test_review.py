"""Tests for the local recommendation review workflow."""

from datetime import date
import json
import tempfile
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from job_finder.memory import load_memory, save_memory
from job_finder.review import (
    APPLICATIONS_PAGE,
    LANDING_PAGE,
    REVIEW_PAGE,
    ReviewRequestHandler,
    load_review_jobs,
    start_application,
    update_review_note,
    update_review_decision,
    update_workflow_status,
)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.memory_path = self.directory / "seen_jobs.json"
        self.recommendations_path = self.directory / "recommendations.json"
        save_memory(
            {
                "job:1": {
                    "title": "Python Developer",
                    "company": "Example GmbH",
                    "workflow_status": "interesting",
                }
            },
            self.memory_path,
        )
        self.recommendations_path.write_text(
            json.dumps(
                {
                    "recommendations": [
                        {
                            "id": "job:1",
                            "title": "Python Developer",
                            "company": "Example GmbH",
                            "llm_score": 90,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_review_jobs_include_persisted_workflow_status(self):
        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(jobs[0]["workflow_status"], "interesting")
        self.assertFalse(jobs[0]["application_tracked"])

    def test_stale_recommendation_id_resolves_through_known_url(self):
        memory = load_memory(self.memory_path)
        memory["job:1"]["workflow_status"] = "applied"
        memory["job:1"]["source_urls"] = ["https://portal.test/job"]
        memory["job:1"]["source_names"] = ["stepstone"]
        save_memory(memory, self.memory_path)
        self.recommendations_path.write_text(
            json.dumps(
                {
                    "recommendations": [
                        {
                            "id": "portal:99",
                            "url": "https://portal.test/job",
                            "title": "Python Developer",
                            "company": "Example GmbH",
                            "llm_score": 90,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertEqual(jobs[0]["id"], "job:1")
        self.assertEqual(jobs[0]["workflow_status"], "applied")
        self.assertTrue(jobs[0]["application_tracked"])
        self.assertEqual(
            jobs[0]["source_links"],
            [{"source": "stepstone", "url": "https://portal.test/job"}],
        )

    def test_review_jobs_recognize_historical_application(self):
        memory = load_memory(self.memory_path)
        memory["job:1"].update(
            {
                "workflow_status": "ignored",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-08-01"},
                    {"status": "ignored", "occurred_on": "2026-08-02"},
                ],
            }
        )
        save_memory(memory, self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)

        self.assertTrue(jobs[0]["application_tracked"])

    def test_status_change_is_persisted(self):
        update_workflow_status("job:1", "applied", self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["workflow_status"], "applied")

    def test_start_application_records_one_dated_event(self):
        result = start_application("job:1", self.memory_path)
        repeated_result = start_application("job:1", self.memory_path)

        entry = load_memory(self.memory_path)["job:1"]
        applied_events = [
            event
            for event in entry["workflow_history"]
            if event["status"] == "applied"
        ]
        self.assertEqual(result["workflow_status"], "applied")
        self.assertTrue(result["application_tracked"])
        self.assertEqual(repeated_result, result)
        self.assertEqual(
            applied_events,
            [{"status": "applied", "occurred_on": date.today().isoformat()}],
        )

    def test_start_application_does_not_overwrite_later_progress(self):
        memory = load_memory(self.memory_path)
        memory["job:1"].update(
            {
                "workflow_status": "interview",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-08-01"},
                    {"status": "interview", "occurred_on": "2026-08-10"},
                ],
            }
        )
        save_memory(memory, self.memory_path)

        result = start_application("job:1", self.memory_path)

        entry = load_memory(self.memory_path)["job:1"]
        self.assertEqual(result["workflow_status"], "interview")
        self.assertEqual(entry["workflow_status"], "interview")
        self.assertEqual(len(entry["workflow_history"]), 2)

    def test_stale_review_decision_does_not_overwrite_application(self):
        memory = load_memory(self.memory_path)
        memory["job:1"].update(
            {
                "workflow_status": "interview",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-08-01"},
                    {"status": "interview", "occurred_on": "2026-08-10"},
                ],
            }
        )
        save_memory(memory, self.memory_path)

        result = update_review_decision(
            "job:1",
            "ignored",
            self.memory_path,
        )

        entry = load_memory(self.memory_path)["job:1"]
        self.assertEqual(result["workflow_status"], "interview")
        self.assertTrue(result["application_tracked"])
        self.assertEqual(entry["workflow_status"], "interview")
        self.assertEqual(len(entry["workflow_history"]), 2)

    def test_start_application_rejects_unknown_job(self):
        with self.assertRaisesRegex(KeyError, "Unbekannte Job-ID"):
            start_application("job:unknown", self.memory_path)

    def test_note_is_persisted_with_workflow_status(self):
        review = update_review_note(
            "job:1",
            "Juniorrolle in Fulda und fachlich passend.",
            self.memory_path,
        )

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["workflow_status"], "interesting")
        self.assertEqual(
            jobs[0]["review_note"],
            "Juniorrolle in Fulda und fachlich passend.",
        )

    def test_invalid_note_is_rejected(self):
        with self.assertRaises(ValueError):
            update_review_note("job:1", 42, memory_path=self.memory_path)

    def test_invalid_status_is_rejected_without_changing_memory(self):
        with self.assertRaises(ValueError):
            update_workflow_status("job:1", "maybe", self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["workflow_status"], "interesting")

    def test_unknown_job_is_rejected(self):
        with self.assertRaisesRegex(KeyError, "Unbekannte Job-ID"):
            update_workflow_status("job:unknown", "ignored", self.memory_path)

    def test_pages_have_distinct_routes(self):
        handler = type(
            "TemporaryPageHandler",
            (ReviewRequestHandler,),
            {
                "recommendations_path": self.recommendations_path,
                "memory_path": self.memory_path,
                "landing_page_path": LANDING_PAGE,
                "page_path": REVIEW_PAGE,
                "applications_page_path": APPLICATIONS_PAGE,
            },
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base_url}/") as response:
                landing_page = response.read().decode("utf-8")
            with urlopen(f"{base_url}/review") as response:
                review_page = response.read().decode("utf-8")
            with urlopen(f"{base_url}/applications") as response:
                applications_page = response.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertIn('href="/review"', landing_page)
        self.assertIn('href="/applications"', landing_page)
        self.assertIn("Stellen prüfen", landing_page)
        self.assertIn("Bewerbungen verwalten", landing_page)
        self.assertIn("Als beworben markieren", review_page)
        self.assertIn("Bewerbung verwalten", review_page)
        self.assertIn("function safeUrl(value)", review_page)
        self.assertIn("renderSourceLinks(job);", review_page)
        self.assertIn("Anzeigen öffnen (${links.length})", review_page)
        self.assertNotIn("progress-select", review_page)
        self.assertIn('href="/">← Zur Startseite</a>', review_page)
        self.assertIn("Bewerbungsübersicht", applications_page)
        self.assertIn('href="/">← Zur Startseite</a>', applications_page)
        self.assertLess(
            applications_page.index('["offers",'),
            applications_page.index('["rejections",'),
        )
        self.assertLess(
            applications_page.index('["rejections",'),
            applications_page.index('["no_responses",'),
        )

    def test_application_start_api_adds_job_to_overview(self):
        handler = type(
            "TemporaryApplicationStartHandler",
            (ReviewRequestHandler,),
            {
                "recommendations_path": self.recommendations_path,
                "memory_path": self.memory_path,
            },
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            request = Request(
                f"{base_url}/api/applications",
                data=json.dumps({"job_id": "job:1"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                result = json.load(response)
            with urlopen(f"{base_url}/api/applications") as response:
                overview = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(result["workflow_status"], "applied")
        self.assertTrue(result["application_tracked"])
        self.assertEqual(overview["statistics"]["total"], 1)
        self.assertEqual(overview["applications"][0]["id"], "job:1")

    def test_local_api_loads_jobs_and_persists_status(self):
        handler = type(
            "TemporaryReviewHandler",
            (ReviewRequestHandler,),
            {
                "recommendations_path": self.recommendations_path,
                "memory_path": self.memory_path,
                "page_path": REVIEW_PAGE,
            },
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base_url}/api/recommendations") as response:
                document = json.load(response)
            request = Request(
                f"{base_url}/api/review-status",
                data=json.dumps(
                    {"job_id": "job:1", "workflow_status": "ignored"}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                result = json.load(response)
            note_request = Request(
                f"{base_url}/api/note",
                data=json.dumps(
                    {
                        "job_id": "job:1",
                        "review_note": "Fachlich interessant, aber noch unsicher.",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(note_request) as response:
                note_result = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(
            document["recommendations"][0]["workflow_status"],
            "interesting",
        )
        self.assertEqual(result["workflow_status"], "ignored")
        self.assertNotIn("personal_ratings", document)
        self.assertEqual(
            note_result["review_note"],
            "Fachlich interessant, aber noch unsicher.",
        )
        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["workflow_status"], "ignored")
        self.assertEqual(jobs[0]["review_note"], "Fachlich interessant, aber noch unsicher.")

    def test_application_page_records_dated_event_and_returns_statistics(self):
        handler = type(
            "TemporaryApplicationHandler",
            (ReviewRequestHandler,),
            {
                "recommendations_path": self.recommendations_path,
                "memory_path": self.memory_path,
                "page_path": REVIEW_PAGE,
                "applications_page_path": APPLICATIONS_PAGE,
            },
        )
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base_url}/applications") as response:
                page = response.read().decode("utf-8")
            request = Request(
                f"{base_url}/api/status",
                data=json.dumps(
                    {
                        "job_id": "job:1",
                        "workflow_status": "applied",
                        "occurred_on": "2026-08-11",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request):
                pass
            no_response_request = Request(
                f"{base_url}/api/status",
                data=json.dumps(
                    {
                        "job_id": "job:1",
                        "workflow_status": "no_response",
                        "occurred_on": "2026-08-20",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(no_response_request):
                pass
            with urlopen(f"{base_url}/api/applications") as response:
                overview = json.load(response)
            no_response_event = next(
                event
                for event in overview["completed_applications"][0][
                    "workflow_history"
                ]
                if event["status"] == "no_response"
            )
            edit_request = Request(
                f"{base_url}/api/history",
                data=json.dumps(
                    {
                        "job_id": "job:1",
                        "event_index": no_response_event["event_index"],
                        "previous_status": "no_response",
                        "previous_occurred_on": "2026-08-20",
                        "workflow_status": "response",
                        "occurred_on": "2026-08-21",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(edit_request) as response:
                edit_result = json.load(response)
            delete_request = Request(
                f"{base_url}/api/history/delete",
                data=json.dumps(
                    {
                        "job_id": "job:1",
                        "event_index": no_response_event["event_index"],
                        "previous_status": "response",
                        "previous_occurred_on": "2026-08-21",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(delete_request) as response:
                delete_result = json.load(response)
            with urlopen(f"{base_url}/api/applications") as response:
                final_overview = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertIn("Bewerbungsübersicht", page)
        self.assertIn("Abgeschlossene Bewerbungen bearbeiten", page)
        self.assertEqual(overview["statistics"]["total"], 1)
        self.assertEqual(overview["applications"], [])
        self.assertEqual(
            overview["completed_applications"][0]["applied_on"],
            "2026-08-11",
        )
        self.assertEqual(edit_result["workflow_status"], "response")
        self.assertEqual(delete_result["workflow_status"], "applied")
        self.assertEqual(final_overview["statistics"]["open"], 1)


if __name__ == "__main__":
    unittest.main()
