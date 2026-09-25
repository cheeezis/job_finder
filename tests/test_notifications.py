"""Tests for rule-based Discord notifications."""

import json
import tempfile
import unittest
from pathlib import Path

from job_finder.workflow.notifications import (
    NotificationError,
    discord_embed,
    process_notifications,
    run_summary_payload,
)


def make_job(job_id="job:1", *, is_new=True, status="new"):
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
    }


class FakeClient:
    def __init__(self, error=None):
        self.payloads = []
        self.error = error

    def send(self, payload):
        self.payloads.append(payload)
        if self.error:
            raise self.error


def discord_limited_characters(payload):
    """Count the embed text Discord limits to 6000 characters per message."""
    return sum(
        len(embed.get("title", ""))
        + len(embed.get("description", ""))
        + sum(len(field["name"]) + len(field["value"]) for field in embed["fields"])
        + len(embed.get("footer", {}).get("text", ""))
        for embed in payload["embeds"]
    )


class NotificationTests(unittest.TestCase):
    def test_only_new_prefiltered_jobs_are_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            stats = process_notifications(
                {
                    "included": [
                        make_job("new"),
                        make_job("changed", is_new=False),
                        make_job("known", is_new=False),
                    ],
                    "excluded": [],
                },
                state_path=Path(directory) / "state.json",
            )
        self.assertEqual(stats["queued"], 1)
        self.assertEqual(stats["ready"], 1)
        self.assertEqual(stats["current_new"], 1)
        self.assertEqual(stats["eligible_new"], 1)

    def test_content_changes_never_resend_an_already_notified_job(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "sent": {"job:1": {"job_id": "job:1", "sent_at": "2026-09-01"}},
                        "pending": {"job:1": {"job_id": "job:1"}},
                    }
                ),
                encoding="utf-8",
            )
            job = make_job()
            job["description_clean"] = "A completely rewritten job description"
            stats = process_notifications({"included": [job], "excluded": []}, state_path=path)
            self.assertEqual(stats["queued"], 0)
            self.assertEqual(stats["ready"], 0)
            state = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("job:1", state["sent"])

    def test_reviewed_job_is_not_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            stats = process_notifications(
                {"included": [make_job(status="ignored")], "excluded": []},
                state_path=Path(directory) / "state.json",
            )
        self.assertEqual(stats["queued"], 0)

    def test_hidden_special_cases_are_never_eligible(self):
        """A job hidden behind an extra review filter must never be notified."""
        junior_hybrid = make_job("junior-hybrid")
        junior_hybrid["location_precheck"] = (
            "Junior-Hybrid außerhalb des Suchgebiets; Präsenzumfang prüfen"
        )
        international = make_job("international")
        international["locations"] = ["Europe"]
        with tempfile.TemporaryDirectory() as directory:
            stats = process_notifications(
                {"included": [junior_hybrid, international, make_job("visible")], "excluded": []},
                state_path=Path(directory) / "state.json",
            )

        # All three are "new", but only the review-visible one is notifiable.
        self.assertEqual(stats["current_new"], 3)
        self.assertEqual(stats["eligible_new"], 1)
        self.assertEqual(stats["queued"], 1)

    def test_successful_delivery_is_sent_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            client = FakeClient()
            first = process_notifications(
                {"included": [make_job()], "excluded": []},
                send=True,
                webhook_url="https://discord.test/webhook",
                client=client,
                state_path=path,
            )
            second = process_notifications(
                {"included": [make_job()], "excluded": []},
                send=True,
                webhook_url="https://discord.test/webhook",
                client=client,
                state_path=path,
            )
        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(len(client.payloads), 1)

    def test_failed_delivery_remains_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            stats = process_notifications(
                {"included": [make_job()], "excluded": []},
                send=True,
                webhook_url="https://discord.test/webhook",
                client=FakeClient(NotificationError("nicht erreichbar")),
                state_path=path,
            )
            state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(len(state["pending"]), 1)

    def test_embed_contains_only_compact_rule_facts(self):
        embed = discord_embed(make_job())
        fields = {field["name"]: field["value"] for field in embed["fields"]}
        self.assertEqual(embed["title"], "Junior Python Developer")
        self.assertIn("Example GmbH", embed["description"])
        self.assertEqual(fields["Kurzcheck"], "Neu · Softwareentwicklung · Vorfilter 82/100")
        self.assertEqual(fields["Einstieg"], "klare Einstiegsstelle")
        self.assertEqual(fields["Standortprüfung"], "100% remote Deutschland")
        self.assertNotIn("Pro", fields)

    def test_delayed_first_notification_is_still_a_new_job(self):
        embed = discord_embed(make_job(is_new=False, status="review"))
        fields = {field["name"]: field["value"] for field in embed["fields"]}

        self.assertIn("Neu", fields["Kurzcheck"])

    def test_embed_has_no_review_link_without_a_configured_host(self):
        embed = discord_embed(make_job())
        fields = {field["name"]: field["value"] for field in embed["fields"]}
        self.assertNotIn("Review", fields)

    def test_embed_links_to_the_matching_review_job_when_host_is_configured(self):
        embed = discord_embed(make_job("job:42"), review_host="review.example.test")
        fields = {field["name"]: field["value"] for field in embed["fields"]}
        self.assertEqual(
            fields["Review"], "[Stelle öffnen](https://review.example.test/review?job=job:42)"
        )

    def test_sent_messages_stay_within_discord_limit_with_review_links(self):
        """Batching must count the review link and footer that are actually sent."""
        jobs = []
        for index in range(10):
            job = make_job(f"job:{index}")
            job["location_precheck"] = "Standort und Präsenzumfang prüfen. " * 12
            jobs.append(job)
        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory:
            stats = process_notifications(
                {"included": jobs, "excluded": []},
                send=True,
                webhook_url="https://discord.test/webhook",
                review_host="jobfinder-review.example.azurecontainerapps.io",
                client=client,
                state_path=Path(directory) / "state.json",
            )

        self.assertEqual(stats["sent"], 10)
        self.assertEqual(sum(len(payload["embeds"]) for payload in client.payloads), 10)
        for payload in client.payloads:
            self.assertLessEqual(discord_limited_characters(payload), 6000)

    def test_run_summary_contains_no_ai_statistics(self):
        payload = run_summary_payload(
            {
                "duration": "10 Sek.",
                "jobs_total": 100,
                "jobs_new": 3,
                "jobs_known": 97,
                "included": 20,
                "excluded": 80,
                "review_new": 2,
                "notifications": {"eligible_new": 2, "sent": 2, "failed": 0},
                "sources": [{"label": "StepStone", "status": "success", "jobs": 10, "new": 1}],
            }
        )
        embed = payload["embeds"][0]
        description = embed["description"]
        self.assertIn("20 im Vorfilter", description)
        self.assertIn("2 zur Benachrichtigung · 2 gesendet", description)
        self.assertNotIn("im Lauf neu/geändert", description)
        self.assertNotIn("fields", embed)
        self.assertNotIn("KI", json.dumps(payload, ensure_ascii=False))
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertNotIn("Details fehlen", description)
        self.assertEqual(embed["color"], 0x2E8B57)

    def test_run_summary_warns_about_candidates_without_details(self):
        payload = run_summary_payload(
            {
                "duration": "10 Sek.",
                "jobs_total": 100,
                "jobs_new": 3,
                "jobs_known": 97,
                "included": 20,
                "excluded": 80,
                "review_new": 2,
                "notifications": {"eligible_new": 2, "sent": 2, "failed": 0},
                "sources": [{"label": "StudySmarter", "status": "success", "jobs": 10}],
                "detail_failures": [
                    {"label": "Arbeitnow", "failed": 2},
                    {"label": "StudySmarter", "failed": 12},
                ],
            }
        )
        embed = payload["embeds"][0]

        self.assertIn(
            "\u26a0\ufe0f Details fehlen: Arbeitnow 2 Kandidat(en), StudySmarter 12 Kandidat(en)",
            embed["description"],
        )
        self.assertIn("1 erfolgreich", embed["description"])
        self.assertEqual(embed["color"], 0xD99A2B)


if __name__ == "__main__":
    unittest.main()
