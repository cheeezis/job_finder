"""Tests for rule-based Discord notifications."""

import json
import tempfile
import unittest
from pathlib import Path

from job_finder.notifications import NotificationError, discord_embed, process_notifications, run_summary_payload


def make_job(job_id="job:1", *, is_new=True, content_changed=False, status="new"):
    return {
        "id": job_id,
        "title": "Junior Python Developer",
        "company": "Example GmbH",
        "locations": ["Fulda"],
        "sources": [{"source": "stepstone", "url": f"https://example.test/{job_id}"}],
        "description_clean": "Python entwickeln",
        "work_mode": "remote",
        "remote_percentage": 100,
        "match_percent": 82,
        "role_group": "software_development",
        "experience_level": "klare Einstiegsstelle",
        "location_precheck": "100% remote Deutschland",
        "workflow_status": status,
        "is_new": is_new,
        "content_changed": content_changed,
    }


class FakeClient:
    def __init__(self, error=None):
        self.payloads = []
        self.error = error

    def send(self, payload):
        self.payloads.append(payload)
        if self.error:
            raise self.error


class NotificationTests(unittest.TestCase):
    def test_legacy_pending_ai_backlog_is_discarded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"version": 1, "sent": {}, "pending": {"legacy": {}}}),
                encoding="utf-8",
            )
            stats = process_notifications(
                {"included": [make_job(is_new=False)], "excluded": []},
                state_path=path,
            )
            state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stats["ready"], 0)
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["pending"], {})

    def test_only_new_or_changed_prefiltered_jobs_are_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            stats = process_notifications(
                {
                    "included": [
                        make_job("new"),
                        make_job("changed", is_new=False, content_changed=True),
                        make_job("known", is_new=False),
                    ],
                    "excluded": [],
                },
                state_path=Path(directory) / "state.json",
            )
        self.assertEqual(stats["queued"], 2)
        self.assertEqual(stats["ready"], 2)

    def test_reviewed_job_is_not_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            stats = process_notifications(
                {"included": [make_job(status="ignored")], "excluded": []},
                state_path=Path(directory) / "state.json",
            )
        self.assertEqual(stats["queued"], 0)

    def test_successful_delivery_is_sent_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            client = FakeClient()
            first = process_notifications(
                {"included": [make_job()], "excluded": []}, send=True,
                webhook_url="https://discord.test/webhook", client=client, state_path=path,
            )
            second = process_notifications(
                {"included": [make_job()], "excluded": []}, send=True,
                webhook_url="https://discord.test/webhook", client=client, state_path=path,
            )
        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(len(client.payloads), 1)

    def test_failed_delivery_remains_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            stats = process_notifications(
                {"included": [make_job()], "excluded": [],}, send=True,
                webhook_url="https://discord.test/webhook",
                client=FakeClient(NotificationError("nicht erreichbar")), state_path=path,
            )
            state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(len(state["pending"]), 1)

    def test_embed_contains_only_compact_rule_facts(self):
        embed = discord_embed(make_job())
        fields = {field["name"]: field["value"] for field in embed["fields"]}
        self.assertEqual(embed["title"], "Junior Python Developer")
        self.assertEqual(fields["Vorfilter"], "82/100")
        self.assertEqual(fields["Einstieg"], "klare Einstiegsstelle")
        self.assertEqual(fields["Standortprüfung"], "100% remote Deutschland")
        self.assertNotIn("description", embed)
        self.assertNotIn("Pro", fields)

    def test_run_summary_contains_no_ai_statistics(self):
        payload = run_summary_payload(
            {
                "duration": "10 Sek.", "jobs_total": 100, "jobs_new": 3,
                "jobs_known": 97, "included": 20, "excluded": 80,
                "review_updates": 2,
                "sources": [{"label": "StepStone", "status": "success", "jobs": 10, "new": 1}],
            }
        )
        self.assertIn("20 zur Prüfung", payload["content"])
        self.assertIn("Review: 2 neu oder aktualisiert", payload["content"])
        self.assertNotIn("KI", payload["content"])
        self.assertEqual(payload["allowed_mentions"], {"parse": []})


if __name__ == "__main__":
    unittest.main()
