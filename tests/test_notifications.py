"""Tests for durable Discord job notification summaries."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from job_agent.notifications import (
    NotificationError,
    discord_payload,
    load_notification_state,
    notification_chunks,
    process_notifications,
    webhook_url_with_confirmation,
)


def make_job(
    job_id="job:1",
    *,
    recommendation="strong_match",
    status="new",
    is_new=True,
    content_changed=False,
    llm_status="analyzed",
):
    return {
        "id": job_id,
        "title": "Junior Python Developer",
        "company": "Example GmbH",
        "locations": ["Fulda"],
        "sources": [{"url": f"https://example.test/{job_id}"}],
        "description_clean": "Python und APIs",
        "work_mode": "hybrid",
        "remote_percentage": None,
        "employment_type": "FULL_TIME",
        "salary_min_eur": None,
        "salary_max_eur": None,
        "workflow_status": status,
        "is_new": is_new,
        "content_changed": content_changed,
        "llm_status": llm_status,
        "llm_score": 90,
        "llm_result": {
            "recommendation": recommendation,
            "summary": "Sehr passende Einstiegsstelle.",
            "seniority": "junior_entry",
            "matching_evidence": ["Python-Projekte passen zur Stelle."],
            "gaps": ["Docker ist nicht belegt."],
            "risks": [],
        },
    }


class NotificationTests(unittest.TestCase):
    def test_preview_queues_all_unsent_active_matches_and_borderline_jobs(self):
        results = {
            "included": [
                make_job("job:positive"),
                make_job("job:borderline", recommendation="borderline"),
                make_job("job:ignored", status="ignored"),
                make_job(
                    "job:known",
                    is_new=False,
                    llm_status="cached",
                ),
            ],
            "excluded": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.json"
            stats = process_notifications(results, state_path=path)
            state = load_notification_state(path)

        self.assertEqual(stats["queued"], 3)
        self.assertEqual(stats["ready"], 3)
        self.assertEqual(len(state["pending"]), 3)
        self.assertEqual(state["sent"], {})

    def test_successful_delivery_is_not_sent_twice(self):
        results = {"included": [make_job()], "excluded": []}
        client = Mock()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.json"
            first = process_notifications(
                results,
                send=True,
                webhook_url="https://discord.test/webhook",
                state_path=path,
                client=client,
            )
            second = process_notifications(
                results,
                send=True,
                webhook_url="https://discord.test/webhook",
                state_path=path,
                client=client,
            )
            state = load_notification_state(path)

        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(client.send.call_count, 1)
        self.assertEqual(len(state["sent"]), 1)
        self.assertEqual(state["pending"], {})

    def test_failed_delivery_remains_pending_and_is_retried(self):
        first_results = {"included": [make_job()], "excluded": []}
        failed_client = Mock()
        failed_client.send.side_effect = NotificationError("nicht erreichbar")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.json"
            failed = process_notifications(
                first_results,
                send=True,
                webhook_url="https://discord.test/webhook",
                state_path=path,
                client=failed_client,
            )
            retry_results = {
                "included": [
                    make_job(is_new=False, llm_status="cached"),
                ],
                "excluded": [],
            }
            successful_client = Mock()
            retried = process_notifications(
                retry_results,
                send=True,
                webhook_url="https://discord.test/webhook",
                state_path=path,
                client=successful_client,
            )
            state = load_notification_state(path)

        self.assertEqual(failed["failed"], 1)
        self.assertEqual(retried["sent"], 1)
        successful_client.send.assert_called_once()
        self.assertEqual(len(state["sent"]), 1)
        self.assertEqual(state["pending"], {})

    def test_changed_known_job_creates_a_new_notification_version(self):
        original = make_job()
        client = Mock()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.json"
            process_notifications(
                {"included": [original], "excluded": []},
                send=True,
                webhook_url="https://discord.test/webhook",
                state_path=path,
                client=client,
            )
            changed = make_job(
                is_new=False,
                content_changed=True,
                llm_status="analyzed",
            )
            changed["description_clean"] = "Python, APIs und neue Cloud-Aufgaben"
            stats = process_notifications(
                {"included": [changed], "excluded": []},
                send=True,
                webhook_url="https://discord.test/webhook",
                state_path=path,
                client=client,
            )

        self.assertEqual(stats["sent"], 1)
        self.assertEqual(client.send.call_count, 2)

    def test_missing_webhook_keeps_ready_jobs_without_sending(self):
        results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.json"
            stats = process_notifications(
                results,
                send=True,
                state_path=path,
            )
            state = load_notification_state(path)

        self.assertIn("DISCORD_WEBHOOK_URL", stats["configuration_error"])
        self.assertEqual(len(state["pending"]), 1)

    def test_payload_disables_mentions_and_chunks_within_discord_limits(self):
        jobs = [make_job(f"job:{index}") for index in range(12)]
        candidates = [(str(index), job) for index, job in enumerate(jobs)]

        chunks = notification_chunks(candidates)
        payload = discord_payload([job for _key, job in chunks[0]])

        self.assertGreaterEqual(len(chunks), 2)
        self.assertLessEqual(len(chunks[0]), 10)
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertIn("Junior Python Developer", payload["embeds"][0]["title"])
        fields = {
            field["name"]: field["value"]
            for field in payload["embeds"][0]["fields"]
        }
        self.assertEqual(fields["Level"], "Einsteiger / Junior")
        self.assertIn("Python-Projekte", fields["Pro"])
        self.assertIn("Docker", fields["Contra"])
        self.assertEqual(
            payload["embeds"][0]["url"],
            "https://example.test/job:0",
        )

    def test_webhook_requests_delivery_confirmation(self):
        url = webhook_url_with_confirmation(
            "https://discord.test/webhook?thread_id=123"
        )

        self.assertIn("thread_id=123", url)
        self.assertIn("wait=true", url)


if __name__ == "__main__":
    unittest.main()
