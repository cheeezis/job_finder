"""Tests for durable Discord job notification summaries."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from job_agent.notifications import (
    NotificationError,
    discord_embed,
    discord_payload,
    load_notification_state,
    notification_chunks,
    process_notifications,
    run_summary_payload,
    send_run_summary,
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

    def test_promoted_recommendation_is_sent_once_as_an_update(self):
        original = make_job(recommendation="borderline")
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
            promoted = make_job(
                recommendation="strong_match",
                is_new=False,
                llm_status="cached",
            )
            stats = process_notifications(
                {"included": [promoted], "excluded": []},
                send=True,
                webhook_url="https://discord.test/webhook",
                state_path=path,
                client=client,
            )
            repeated = process_notifications(
                {"included": [promoted], "excluded": []},
                send=True,
                webhook_url="https://discord.test/webhook",
                state_path=path,
                client=client,
            )

        self.assertEqual(stats["sent"], 1)
        self.assertEqual(repeated["sent"], 0)
        self.assertEqual(client.send.call_count, 2)

    def test_legacy_notification_is_updated_when_a_score_correction_promotes_it(self):
        original = make_job(recommendation="borderline")
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
            state = load_notification_state(path)
            next(iter(state["sent"].values())).pop("recommendation")
            path.write_text(
                json.dumps({"version": 1, **state}),
                encoding="utf-8",
            )
            corrected = make_job(
                recommendation="strong_match",
                is_new=False,
                llm_status="cached",
            )
            corrected["llm_score_changed"] = True
            stats = process_notifications(
                {"included": [corrected], "excluded": []},
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

    def test_excluded_job_is_removed_from_pending_notifications(self):
        job = make_job()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notifications.json"
            process_notifications({"included": [job], "excluded": []}, state_path=path)
            stats = process_notifications(
                {"included": [], "excluded": [job]},
                state_path=path,
            )
            state = load_notification_state(path)

        self.assertEqual(stats["ready"], 0)
        self.assertEqual(state["pending"], {})

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

    def test_explicit_portal_career_level_overrides_inferred_level(self):
        job = make_job()
        job["career_levels"] = ["Berufseinstieg/Trainee"]
        fields = {
            field["name"]: field["value"] for field in discord_embed(job)["fields"]
        }

        self.assertEqual(fields["Level"], "Berufseinstieg/Trainee")

    def test_embed_prefers_application_url_from_any_source(self):
        job = make_job()
        job["sources"] = [
            {"url": "https://portal.test/job"},
            {
                "url": "https://arbeitnow.test/job",
                "application_url": "https://company.test/job",
            },
        ]

        self.assertEqual(discord_embed(job)["url"], "https://company.test/job")

    def test_webhook_requests_delivery_confirmation(self):
        url = webhook_url_with_confirmation(
            "https://discord.test/webhook?thread_id=123"
        )

        self.assertIn("thread_id=123", url)
        self.assertIn("wait=true", url)

    def test_run_summary_includes_source_breakdown_and_disables_mentions(self):
        payload = run_summary_payload(
            {
                "duration": "2 Min. 05 Sek.",
                "jobs_total": 20,
                "jobs_new": 3,
                "jobs_known": 17,
                "included": 5,
                "excluded": 15,
                "analyzed": 2,
                "cached": 3,
                "analysis_failed": 0,
                "recommended": 2,
                "not_recommended": 3,
                "sources": [
                    {
                        "label": "Arbeitsagentur",
                        "status": "success",
                        "jobs": 12,
                        "new": 2,
                    },
                    {
                        "label": "StepStone",
                        "status": "failed",
                        "jobs": 0,
                        "new": 0,
                    },
                ],
            }
        )

        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        self.assertIn("20 gesamt (3 neu, 17 bekannt)", payload["content"])
        self.assertIn("Arbeitsagentur: 12 Stellen, 2 neu", payload["content"])
        self.assertIn("StepStone: Fehler", payload["content"])

    def test_run_summary_reports_missing_webhook_without_sending(self):
        self.assertIn(
            "DISCORD_WEBHOOK_URL",
            send_run_summary({"sources": []}, webhook_url=None),
        )


if __name__ == "__main__":
    unittest.main()
