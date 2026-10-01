"""Tests for the trace helpers: what a span may carry and when tracing is on."""

import unittest

from job_finder import telemetry


class TelemetryTests(unittest.TestCase):
    def test_only_numbers_flags_and_short_words_are_kept(self):
        kept = telemetry.safe_attributes(
            {"count": 3, "cost": 0.004, "stopped": False, "outcome": "fertig", "missing": None, "text": "x" * 41}
        )

        self.assertEqual(kept, {"count": 3, "cost": 0.004, "stopped": False, "outcome": "fertig"})

    def test_without_a_connection_string_nothing_is_exported(self):
        self.assertIsNone(telemetry.configure_tracing({}))


if __name__ == "__main__":
    unittest.main()
