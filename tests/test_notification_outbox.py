"""Exercise the durable notification boundary against isolated PostgreSQL."""

import os
import threading
import unittest
from unittest.mock import patch

from job_finder.persistence.database import in_transaction, transaction
from job_finder.persistence.postgres_store import write_dataset
from job_finder.workflow.memory import save_memory
from job_finder.workflow.notifications import (
    deliver_notifications,
    load_notification_state,
    queue_notifications,
    save_notification_state,
)
from tests.test_notifications import FakeClient, make_job


class NotificationOutboxTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE job_state,datasets CASCADE")

    def queue(self, *jobs):
        return queue_notifications({"included": list(jobs), "excluded": []})

    def deliver(self, client):
        return deliver_notifications(webhook_url="https://discord.test/webhook", client=client)

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

    def test_old_pending_order_recovers_from_publication_and_sent_entries_stay_sent(self):
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
