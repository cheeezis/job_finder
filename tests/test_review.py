"""Tests for the local recommendation review workflow."""

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from job_agent.memory import save_memory
from job_agent.review import (
    APPLICATIONS_PAGE,
    REVIEW_PAGE,
    ReviewRequestHandler,
    load_review_jobs,
    update_review_note,
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

    def test_status_change_is_persisted(self):
        update_workflow_status("job:1", "applied", self.memory_path)

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["workflow_status"], "applied")

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
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base_url}/api/recommendations") as response:
                document = json.load(response)
            request = Request(
                f"{base_url}/api/status",
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
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
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
