"""Tests for the trace helpers: what a span may carry and when tracing is on."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from job_finder import telemetry


class TelemetryTests(unittest.TestCase):
    def test_only_numbers_flags_and_short_words_are_kept(self):
        kept = telemetry.safe_attributes(
            {"count": 3, "cost": 0.004, "stopped": False, "outcome": "fertig", "missing": None, "text": "x" * 41}
        )

        self.assertEqual(kept, {"count": 3, "cost": 0.004, "stopped": False, "outcome": "fertig"})

    def test_without_a_connection_string_nothing_is_exported(self):
        self.assertIsNone(telemetry.configure_tracing({}))

    def test_broken_tracing_costs_only_the_traces(self):
        output = io.StringIO()
        broken = ValueError("kaputte Verbindungszeichenfolge")
        with redirect_stdout(output), patch.object(telemetry, "configure_tracing", side_effect=broken):
            self.assertIsNone(telemetry.start_tracing({}))

        self.assertIn("Traces aus: ValueError", output.getvalue())
        self.assertNotIn("kaputte", output.getvalue())

    def test_a_step_outside_a_trace_records_nothing(self):
        with telemetry.step("db_connect") as current:
            self.assertIsNone(current)


if __name__ == "__main__":
    unittest.main()
