"""Parity checks for supported persisted formats."""

import copy
import unittest

from job_finder.persistence.state_compat import decode_notification_state


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
