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
    REVIEW_PAGE,
    ReviewRequestHandler,
    load_review_jobs,
    update_personal_review,
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

    def test_personal_rating_and_note_are_persisted_separately(self):
        review = update_personal_review(
            "job:1",
            "very_interesting",
            "Juniorrolle in Fulda und fachlich passend.",
            self.memory_path,
        )

        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(review["personal_rating"], "very_interesting")
        self.assertEqual(jobs[0]["workflow_status"], "interesting")
        self.assertEqual(jobs[0]["personal_rating"], "very_interesting")
        self.assertEqual(
            jobs[0]["review_note"],
            "Juniorrolle in Fulda und fachlich passend.",
        )

    def test_invalid_personal_rating_is_rejected(self):
        with self.assertRaises(ValueError):
            update_personal_review("job:1", "great", memory_path=self.memory_path)

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
            review_request = Request(
                f"{base_url}/api/review",
                data=json.dumps(
                    {
                        "job_id": "job:1",
                        "personal_rating": "maybe",
                        "review_note": "Fachlich interessant, aber noch unsicher.",
                    }
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(review_request) as response:
                review_result = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

        self.assertEqual(
            document["recommendations"][0]["workflow_status"],
            "interesting",
        )
        self.assertEqual(result["workflow_status"], "ignored")
        self.assertIn("very_interesting", document["personal_ratings"])
        self.assertEqual(review_result["personal_rating"], "maybe")
        jobs = load_review_jobs(self.recommendations_path, self.memory_path)
        self.assertEqual(jobs[0]["workflow_status"], "ignored")
        self.assertEqual(jobs[0]["review_note"], "Fachlich interessant, aber noch unsicher.")


if __name__ == "__main__":
    unittest.main()
