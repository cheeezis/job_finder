"""Tests for rule-based Discord notifications."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from job_finder.workflow.notifications import (
    NotificationError,
    decode_notification_state,
    deliver_notifications,
    discord_embed,
    load_notification_state,
    process_notifications,
    queue_notifications,
    run_summary_payload,
    send_warning,
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
    def test_updated_pending_cards_are_sized_again_before_the_next_part_is_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            queue_notifications(
                {"included": [make_job(f"job:{i}") for i in range(20)], "excluded": []}, state_path=path
            )

            class UpdatingClient(FakeClient):
                def send(self, payload):
                    super().send(payload)
                    if len(self.payloads) == 1:
                        changed = [make_job(f"job:{i}", is_new=False) for i in range(10, 20)]
                        for job in changed:
                            job["company"] = "Long example employer " * 50
                        queue_notifications({"included": changed, "excluded": []}, state_path=path)

            client = UpdatingClient()
            stats = deliver_notifications(state_path=path, webhook_url="https://discord.test/webhook", client=client)
        self.assertEqual(stats["sent"], 20)
        self.assertGreater(len(client.payloads), 2)
        for payload in client.payloads:
            self.assertLessEqual(discord_limited_characters(payload), 6000)
            self.assertLessEqual(len(payload["embeds"]), 10)

    def test_a_merged_pending_id_retains_its_event_and_retry_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            process_notifications(
                {"included": [make_job("old:1")], "excluded": []},
                send=True,
                webhook_url="https://discord.test/webhook",
                client=FakeClient(NotificationError("offline")),
                state_path=path,
            )
            queue_notifications(
                {"included": [make_job("canonical:1", is_new=False)], "excluded": []},
                aliases={"old:1": "intermediate:1", "intermediate:1": "canonical:1"},
                state_path=path,
            )
            state = load_notification_state(path)
            entry = state["pending"]["canonical:1"]
            self.assertEqual(set(state["pending"]), {"canonical:1"})
            self.assertEqual(entry["event_key"], "job-found:old:1")
            self.assertEqual(entry["attempts"], 1)
            self.assertEqual(entry["payload"]["id"], "canonical:1")
            deliver_notifications(state_path=path, webhook_url="https://discord.test/webhook", client=FakeClient())
            self.assertEqual(load_notification_state(path)["sent"]["canonical:1"]["event_key"], "job-found:old:1")

    def test_merging_an_already_sent_find_discards_its_duplicate_pending_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            process_notifications(
                {"included": [make_job("old:1")], "excluded": []},
                send=True,
                webhook_url="https://discord.test/webhook",
                client=FakeClient(),
                state_path=path,
            )
            queue_notifications({"included": [make_job("canonical:1")], "excluded": []}, state_path=path)
            stats = queue_notifications(
                {"included": [make_job("canonical:1", is_new=False)], "excluded": []},
                state_path=path,
                aliases={"old:1": "canonical:1"},
            )
            self.assertEqual(stats["queued"], 0)
            self.assertFalse(load_notification_state(path)["pending"])
            self.assertEqual(set(load_notification_state(path)["sent"]), {"old:1", "canonical:1"})

    def test_committed_order_can_be_delivered_without_collecting_the_source_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            queue_notifications({"included": [make_job()], "excluded": []}, state_path=path)
            # A split schedule or failed source supplies no matching job on retry.
            queue_notifications({"included": [], "excluded": []}, state_path=path)
            client = FakeClient()
            stats = deliver_notifications(state_path=path, webhook_url="https://discord.test/webhook", client=client)
            state = load_notification_state(path)
        self.assertEqual(stats["sent"], 1)
        self.assertEqual(client.payloads[0]["embeds"][0]["url"], "https://example.test/job:1")
        self.assertIn("job:1", state["sent"])
        self.assertFalse(state["pending"])

    def test_retry_preserves_attempts_and_never_creates_a_second_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            results = {"included": [make_job()], "excluded": []}
            failed = process_notifications(
                results,
                send=True,
                state_path=path,
                webhook_url="https://discord.test/webhook",
                client=FakeClient(NotificationError("nicht erreichbar")),
            )
            job = make_job(is_new=False)
            job["company"] = "Updated Example"
            queued = queue_notifications({"included": [job], "excluded": []}, state_path=path)
            state = load_notification_state(path)
            client = FakeClient()
            sent = deliver_notifications(state_path=path, webhook_url="https://discord.test/webhook", client=client)
        self.assertEqual(failed["failed"], 1)
        self.assertEqual(queued["queued"], 0)
        self.assertEqual(state["pending"]["job:1"]["attempts"], 1)
        self.assertIn("Updated Example", client.payloads[0]["embeds"][0]["description"])
        self.assertEqual(sent["sent"], 1)

    def test_source_return_recovers_an_old_order_without_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"version": 3, "sent": {}, "pending": {"job:1": {"job_id": "job:1", "attempts": 2}}})
            )
            self.assertEqual(deliver_notifications(state_path=path)["ready"], 0)
            self.assertEqual(load_notification_state(path)["pending"]["job:1"]["attempts"], 2)
            queue_notifications({"included": [make_job(is_new=False)], "excluded": []}, state_path=path)
            self.assertIn("payload", load_notification_state(path)["pending"]["job:1"])

    def test_excluded_or_reviewed_orders_are_cancelled_before_retry(self):
        for updates in (
            {"included": [], "excluded": [make_job()]},
            {"included": [make_job(status="applied")], "excluded": []},
        ):
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.json"
                queue_notifications({"included": [make_job()], "excluded": []}, state_path=path)
                queue_notifications(updates, state_path=path)
                client = FakeClient()
                self.assertEqual(
                    deliver_notifications(state_path=path, webhook_url="https://discord.test/webhook", client=client)[
                        "sent"
                    ],
                    0,
                )
                self.assertFalse(client.payloads)

    def test_only_new_prefiltered_jobs_are_queued(self):
        with tempfile.TemporaryDirectory() as directory:
            stats = process_notifications(
                {
                    "included": [make_job("new"), make_job("changed", is_new=False), make_job("known", is_new=False)],
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
                {"included": [make_job(status="ignored")], "excluded": []}, state_path=Path(directory) / "state.json"
            )
        self.assertEqual(stats["queued"], 0)

    def test_hidden_special_cases_are_never_eligible(self):
        """A job hidden behind an extra review filter must never be notified."""
        junior_hybrid = make_job("junior-hybrid")
        junior_hybrid["location_precheck"] = "Junior-Hybrid außerhalb des Suchgebiets; Präsenzumfang prüfen"
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
        self.assertEqual(fields["Review"], "[Stelle öffnen](https://review.example.test/review?job=job:42)")

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
                "detail_failures": [{"label": "Arbeitnow", "failed": 2}, {"label": "StudySmarter", "failed": 12}],
            }
        )
        embed = payload["embeds"][0]

        self.assertIn(
            "\u26a0\ufe0f Details fehlen: Arbeitnow 2 Kandidat(en), StudySmarter 12 Kandidat(en)", embed["description"]
        )
        self.assertIn("1 erfolgreich", embed["description"])
        self.assertEqual(embed["color"], 0xD99A2B)

    def test_warning_is_one_orange_embed_without_mentions(self):
        client = FakeClient()

        error = send_warning(
            "Job Finder · Steckbriefe", "Agent abgebrochen: RuntimeError", webhook_url=None, client=client
        )

        self.assertIsNone(error)
        (payload,) = client.payloads
        embed = payload["embeds"][0]
        self.assertEqual(embed["title"], "Job Finder · Steckbriefe")
        self.assertEqual(embed["description"], "Agent abgebrochen: RuntimeError")
        self.assertEqual(embed["color"], 0xD99A2B)
        self.assertEqual(payload["allowed_mentions"], {"parse": []})

    def test_a_warning_that_cannot_be_sent_says_why(self):
        failing = FakeClient(error=NotificationError("Discord ist nicht erreichbar"))

        self.assertEqual(send_warning("t", "x", webhook_url=None), "DISCORD_WEBHOOK_URL ist nicht gesetzt")
        self.assertEqual(send_warning("t", "x", webhook_url=None, client=failing), "Discord ist nicht erreichbar")


class StateCompatibilityTests(unittest.TestCase):
    def test_notification_state_keeps_delivery_state_across_reloading(self):
        document = {
            "version": 3,
            "sent": {"sent:1": {"job_id": "sent:1", "sent_at": "unchanged"}},
            "pending": {
                "queued:1": {"job_id": "queued:1", "attempts": 2},
                "sent:1": {"job_id": "sent:1", "attempts": 1},
            },
        }
        before = copy.deepcopy(document)
        state = decode_notification_state(document)
        self.assertEqual(state["sent"], {"sent:1": document["sent"]["sent:1"]})
        self.assertEqual(state["pending"], {"queued:1": {"job_id": "queued:1", "attempts": 2}})
        self.assertEqual(decode_notification_state({"version": 3, **state}), state)
        self.assertEqual(document, before)

    def test_other_versions_are_rejected(self):
        for version in (1, 2, 4):
            with self.subTest(version=version), self.assertRaises(ValueError):
                decode_notification_state({"version": version})
