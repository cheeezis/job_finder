"""Exercise the durable notification boundary against isolated PostgreSQL."""

import io
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import psycopg

from job_finder.paths import JOBS_FILE, RECOMMENDATIONS_JSON
from job_finder.persistence.database import database_url, in_transaction, transaction
from job_finder.persistence.postgres_backup import create_postgres_backup, restore_backup
from job_finder.persistence.postgres_store import write_dataset
from job_finder.persistence.storage import read_json
from job_finder.workflow.memory import load_memory, save_memory
from job_finder.workflow.notifications import (
    NotificationError,
    deliver_notifications,
    load_notification_state,
    queue_notifications,
    save_notification_state,
)
from job_finder.workflow.reporting import write_recommendations
from run_finder import run_pipeline
from tests.test_notifications import FakeClient, make_job
from tests.test_run_finder import make_job as collected_job


class NotificationOutboxTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        self.clear_database()

    def clear_database(self):
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE job_state,datasets,agent_usage,agent_fact_sheets CASCADE")

    def queue(self, *jobs):
        memory = load_memory()
        for job in jobs:
            memory.setdefault(job["id"], {"workflow_status": job.get("workflow_status", "new")})
        save_memory(memory)
        return queue_notifications({"included": list(jobs), "excluded": []})

    def deliver(self, client):
        return deliver_notifications(webhook_url="https://discord.test/webhook", client=client)

    @contextmanager
    def pipeline(self, jobs, *, reports=None):
        reports = reports or [{"name": "test", "status": "success", "jobs": len(jobs)}]
        with (
            patch.dict(
                os.environ, {"JOBFINDER_SKIP_RUN_BACKUP": "1", "DISCORD_WEBHOOK_URL": "https://discord.test/webhook"}
            ),
            patch("run_finder.SOURCES", []),
            patch("run_finder.collect_jobs", side_effect=lambda *args, **kwargs: (deepcopy(jobs), reports)),
            patch("run_finder.enrich_candidate_jobs", return_value=[]),
            patch("run_finder.send_run_summary", return_value=None),
            patch("run_finder.agent_phase"),
            redirect_stdout(io.StringIO()),
        ):
            yield

    def assert_no_publication(self):
        self.assertFalse(load_memory())
        self.assertFalse(read_json(JOBS_FILE, []))
        self.assertFalse(read_json(RECOMMENDATIONS_JSON, {}))
        self.assertFalse(load_notification_state()["pending"])

    def test_publication_and_outbox_failures_roll_back_discovery_and_both_review_views(self):
        job = collected_job("test:1")
        job.remote_percentage = 100
        for target, original in (
            ("write_recommendations", write_recommendations),
            ("queue_notifications", queue_notifications),
        ):
            with self.subTest(stage=target):

                def fail_after_write(*args, **kwargs):
                    original(*args, **kwargs)
                    raise RuntimeError("injected before commit")

                with (
                    self.pipeline([job]),
                    patch("run_finder." + target, side_effect=fail_after_write),
                    patch("run_finder.deliver_notifications") as delivery,
                    self.assertRaisesRegex(RuntimeError, "before commit"),
                ):
                    run_pipeline()
                delivery.assert_not_called()
                self.assert_no_publication()

    def test_uncommitted_publication_is_invisible_to_a_second_database_connection(self):
        job = collected_job("test:1")
        job.remote_percentage = 100

        def inspect_before_commit(results):
            self.assertTrue(in_transaction())
            write_recommendations(results)
            with psycopg.connect(database_url()) as reader:
                for table in ("job_state", "jobs", "recommendations", "notifications"):
                    self.assertEqual(reader.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

        client = FakeClient()
        with (
            self.pipeline([job]),
            patch("run_finder.write_recommendations", side_effect=inspect_before_commit),
            patch("job_finder.workflow.notifications.DiscordWebhookClient", return_value=client),
        ):
            run_pipeline()
        self.assertIn("test:1", load_memory())
        self.assertEqual(read_json(JOBS_FILE)[0]["id"], "test:1")
        self.assertEqual(read_json(RECOMMENDATIONS_JSON)["recommendations"][0]["id"], "test:1")
        self.assertEqual(len(client.payloads), 1)

    def test_crash_after_commit_is_retried_by_a_split_run_with_no_second_business_order(self):
        job = collected_job("test:1")
        job.remote_percentage = 100
        with (
            self.pipeline([job]),
            patch("run_finder.deliver_notifications", side_effect=RuntimeError("crash after commit")),
            self.assertRaisesRegex(RuntimeError, "after commit"),
        ):
            run_pipeline()
        self.assertIn("test:1", load_memory())
        self.assertEqual(set(load_notification_state()["pending"]), {"test:1"})
        self.assertIn("payload", load_notification_state()["pending"]["test:1"])

        # The other schedule does not fetch this source; its card and order survive.
        client = FakeClient()
        with (
            self.pipeline([], reports=[{"name": "other", "status": "empty", "jobs": 0}]),
            patch("job_finder.workflow.notifications.DiscordWebhookClient", return_value=client),
        ):
            run_pipeline(exclude_sources={"test"})
        self.assertEqual(len(client.payloads), 1)
        self.assertEqual(set(load_notification_state()["sent"]), {"test:1"})
        self.assertEqual(read_json(RECOMMENDATIONS_JSON)["recommendations"][0]["id"], "test:1")
        with self.pipeline([job]), patch("job_finder.workflow.notifications.DiscordWebhookClient", return_value=client):
            run_pipeline()
        self.assertEqual(len(client.payloads), 1)
        self.assertFalse(load_notification_state()["pending"])

    def test_closure_checks_run_before_writes_and_roll_back_with_a_failed_publication(self):
        previous = {
            "test:saved": {
                "workflow_status": "interesting",
                "source_names": ["test"],
                "source_urls": ["https://example.test/old"],
            }
        }
        save_memory(previous)

        def check(url):
            self.assertFalse(in_transaction())
            self.assertEqual(load_memory(), previous)
            return True

        with (
            self.pipeline([]),
            patch("job_finder.workflow.availability.listing_is_closed", side_effect=check),
            patch("run_finder.queue_notifications", side_effect=RuntimeError("abort")),
            self.assertRaisesRegex(RuntimeError, "abort"),
        ):
            run_pipeline()
        self.assertEqual(load_memory(), previous)

    def test_workflow_decision_during_closure_request_takes_precedence_in_final_commit(self):
        previous = {
            "test:saved": {
                "workflow_status": "interesting",
                "source_names": ["test"],
                "source_urls": ["https://example.test/old"],
            }
        }
        save_memory(previous)

        def change_during_check(url):
            save_memory({"test:saved": {**previous["test:saved"], "workflow_status": "inquiry"}})
            return True

        with (
            self.pipeline([]),
            patch("job_finder.workflow.availability.listing_is_closed", side_effect=change_during_check),
        ):
            run_pipeline()
        entry = load_memory()["test:saved"]
        self.assertEqual(entry["workflow_status"], "inquiry")
        self.assertNotIn("availability_checks", entry)

    def test_rolled_back_order_is_not_deliverable_and_can_be_queued_on_retry(self):
        with self.assertRaisesRegex(RuntimeError, "abort"), transaction():
            self.queue(make_job())
            raise RuntimeError("abort")
        self.assertFalse(load_notification_state()["pending"])
        self.assertEqual(self.queue(make_job())["queued"], 1)
        self.assertEqual(self.deliver(FakeClient())["sent"], 1)

    def test_delivery_refuses_an_uncommitted_transaction(self):
        client = FakeClient()
        with transaction():
            self.queue(make_job())
            with self.assertRaisesRegex(RuntimeError, "Commit"):
                self.deliver(client)
        self.assertFalse(client.payloads)
        self.assertEqual(self.deliver(client)["sent"], 1)

    def test_application_backup_restores_a_retryable_order_and_its_original_event(self):
        self.queue(make_job())
        self.assertEqual(self.deliver(FakeClient(NotificationError("offline")))["failed"], 1)
        before = load_notification_state()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = create_postgres_backup(root / "backups", root / "documents")
            self.clear_database()
            result = restore_backup(archive, root / "restored-documents")
            self.assertTrue(result["verified"])
        self.assertEqual(load_notification_state(), before)
        self.assertEqual(before["pending"]["job:1"]["attempts"], 1)
        self.assertEqual(before["pending"]["job:1"]["event_key"], "job-found:job:1")
        client = FakeClient()
        self.assertEqual(self.deliver(client)["sent"], 1)
        self.assertEqual(len(client.payloads), 1)

    def test_old_pending_order_recovers_from_publication_and_sent_entries_stay_sent(self):
        save_memory({"job:1": {"workflow_status": "new"}})
        save_notification_state(
            {
                "sent": {"job:sent": {"job_id": "job:sent", "sent_at": "unchanged"}},
                "pending": {"job:1": {"job_id": "job:1", "attempts": 2}},
            }
        )
        write_dataset(
            "output/recommendations.json",
            {"recommendations": [{**make_job(is_new=False), "source_links": make_job()["sources"]}]},
        )
        client = FakeClient()
        self.assertEqual(self.deliver(client)["sent"], 1)
        self.assertEqual(len(client.payloads), 1)
        self.assertEqual(load_notification_state()["sent"]["job:sent"]["sent_at"], "unchanged")

    def test_current_review_decision_cancels_committed_order_without_recollection(self):
        self.queue(make_job())
        save_memory({"job:1": {"workflow_status": "applied"}})
        client = FakeClient()
        self.assertEqual(self.deliver(client)["sent"], 0)
        self.assertFalse(client.payloads)
        self.assertFalse(load_notification_state()["pending"])

    def test_a_merged_memory_id_cannot_send_an_obsolete_new_card(self):
        self.queue(make_job())
        save_memory({"canonical:1": {"workflow_status": "interview"}})
        client = FakeClient()
        self.assertEqual(self.deliver(client)["sent"], 0)
        self.assertFalse(client.payloads)
        self.assertFalse(load_notification_state()["pending"])

    def test_finder_merge_moves_the_pending_event_in_the_publication_transaction(self):
        job = collected_job("test:old")
        job.remote_percentage = 100
        base = {
            "title": job.title,
            "company": job.company,
            "locations": job.locations,
            "workflow_status": "new",
            "source_names": ["test"],
            "active": True,
        }
        save_memory(
            {
                "test:old": {**base, "first_seen_at": "2026-10-02T00:00:00+00:00", "source_urls": [job.primary_url]},
                "test:canonical": {
                    **base,
                    "first_seen_at": "2026-10-01T00:00:00+00:00",
                    "source_urls": ["https://example.test/canonical"],
                },
            }
        )
        self.queue(make_job("test:old"))
        with (
            self.pipeline([job]),
            patch("run_finder.deliver_notifications", side_effect=RuntimeError("after commit")),
            self.assertRaisesRegex(RuntimeError, "after commit"),
        ):
            run_pipeline()
        self.assertEqual(set(load_memory()), {"test:canonical"})
        pending = load_notification_state()["pending"]
        self.assertEqual(set(pending), {"test:canonical"})
        self.assertEqual(pending["test:canonical"]["event_key"], "job-found:test:old")
        self.assertEqual(pending["test:canonical"]["payload"]["id"], "test:canonical")
        self.assertEqual(self.deliver(FakeClient())["sent"], 1)

    def test_live_listing_under_a_second_source_id_is_not_checked_as_missing(self):
        job = collected_job("test:new-id")
        job.remote_percentage = 100
        save_memory(
            {
                "test:canonical": {
                    "title": job.title,
                    "company": job.company,
                    "locations": job.locations,
                    "workflow_status": "interesting",
                    "first_seen_at": "2026-10-01T00:00:00+00:00",
                    "source_names": ["test"],
                    "source_urls": ["https://example.test/canonical"],
                }
            }
        )
        with self.pipeline([job]), patch("job_finder.workflow.availability.listing_is_closed") as check:
            run_pipeline()
        check.assert_not_called()
        self.assertEqual(load_memory()["test:canonical"]["workflow_status"], "interesting")

    def test_a_crash_before_acknowledgement_keeps_the_order_for_retry(self):
        self.queue(make_job())
        client = FakeClient()
        original = save_notification_state

        def fail_ack(state, path):
            if state["sent"]:
                raise RuntimeError("crash after Discord accepted")
            original(state, path)

        with (
            patch("job_finder.workflow.notifications.save_notification_state", side_effect=fail_ack),
            self.assertRaisesRegex(RuntimeError, "crash after Discord"),
        ):
            self.deliver(client)
        self.assertEqual(len(client.payloads), 1)
        self.assertIn("job:1", load_notification_state()["pending"])
        self.assertEqual(self.deliver(client)["sent"], 1)
        self.assertEqual(len(client.payloads), 2)
        self.assertFalse(load_notification_state()["pending"])

    def test_completed_chunks_survive_a_crash_before_the_next_chunk(self):
        self.queue(*(make_job(f"job:{i}") for i in range(11)))

        class CrashOnSecondChunk(FakeClient):
            def send(self, payload):
                if self.payloads:
                    raise RuntimeError("crash before second chunk")
                super().send(payload)

        client = CrashOnSecondChunk()
        with self.assertRaisesRegex(RuntimeError, "second chunk"):
            self.deliver(client)
        self.assertEqual(len(load_notification_state()["sent"]), 10)
        self.assertEqual(len(load_notification_state()["pending"]), 1)
        retry = FakeClient()
        self.assertEqual(self.deliver(retry)["sent"], 1)
        self.assertEqual(len(retry.payloads[0]["embeds"]), 1)

    def test_delivery_holds_no_write_transaction_and_ack_preserves_new_orders(self):
        self.queue(make_job())
        sending = threading.Event()
        release = threading.Event()
        errors = []

        class WaitingClient(FakeClient):
            def send(client, payload):
                self.assertFalse(in_transaction())
                sending.set()
                if not release.wait(10):
                    raise RuntimeError("test sender timed out")
                super().send(payload)

        def send():
            try:
                self.deliver(WaitingClient())
            except BaseException as error:
                errors.append(error)

        sender = threading.Thread(target=send)
        sender.start()
        try:
            self.assertTrue(sending.wait(10))
            self.assertEqual(self.queue(make_job("job:2"))["queued"], 1)
            with self.assertRaisesRegex(RuntimeError, "bereits aktiv"):
                self.deliver(FakeClient())
        finally:
            release.set()
            sender.join(10)
        self.assertFalse(sender.is_alive())
        self.assertFalse(errors)
        state = load_notification_state()
        self.assertEqual(set(state["sent"]), {"job:1"})
        self.assertEqual(set(state["pending"]), {"job:2"})
