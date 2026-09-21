"""Parity checks for supported persisted formats."""

import copy
import unittest

from job_finder.persistence.state_compat import (
    decode_legacy_memory,
    decode_notification_state,
    restore_initial_discovery_date,
)


class StateCompatibilityTests(unittest.TestCase):
    def test_notification_versions_keep_delivery_state_across_reloading(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                document = {
                    "version": version,
                    "sent": {"old-hash": {"job_id": "sent:1", "sent_at": "unchanged"}},
                    "pending": {
                        "queued-hash": {"job_id": "queued:1", "attempts": 2},
                        "sent-hash": {"job_id": "sent:1", "attempts": 1},
                    },
                }
                before = copy.deepcopy(document)
                state = decode_notification_state(document)
                self.assertEqual(
                    state["sent"], {"sent:1": document["sent"]["old-hash"]}
                )
                self.assertEqual(
                    state["pending"],
                    {}
                    if version == 1
                    else {"queued:1": {"job_id": "queued:1", "attempts": 2}},
                )
                self.assertEqual(
                    decode_notification_state({"version": 3, **state}), state
                )
                self.assertEqual(document, before)

    def test_unknown_versions_remain_rejected(self):
        with self.assertRaises(ValueError):
            decode_legacy_memory({"version": 1})
        with self.assertRaises(ValueError):
            decode_notification_state({"version": 4})

    def test_legacy_entry_keeps_decisions_and_normalizes_only_once(self):
        entry = {
            "workflow_status": "applied",
            "workflow_history": [
                {"status": "new", "occurred_on": None},
                {"status": "applied", "occurred_on": "2026-09-02"},
            ],
            "first_seen_at": "2026-09-01T12:00:00+00:00",
            "review_note": "keep",
        }
        document = {"version": 2, "jobs": {"job:1": entry}}
        self.assertIs(decode_legacy_memory(document)["job:1"], entry)
        restore_initial_discovery_date(entry)
        first = copy.deepcopy(entry)
        restore_initial_discovery_date(entry)
        self.assertEqual(entry, first)
        self.assertEqual(entry["workflow_status"], "applied")
        self.assertEqual(entry["review_note"], "keep")
        self.assertEqual(entry["workflow_history"][1]["occurred_on"], "2026-09-02")
