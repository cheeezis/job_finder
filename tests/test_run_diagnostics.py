"""Run diagnostics must distinguish counters and preserve failures."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from job_finder.operations import timed_step
from run_finder import print_review_diagnostics


class RunDiagnosticsTests(unittest.TestCase):
    def test_step_reports_duration_on_success_and_preserves_failure(self):
        for fails in (False, True):
            output = io.StringIO()
            with self.subTest(fails=fails), redirect_stdout(output), patch(
                "job_finder.operations.time.monotonic", side_effect=[10, 75],
            ):
                if fails:
                    with self.assertRaisesRegex(ValueError, "synthetic"):
                        with timed_step("Quelle Test"):
                            raise ValueError("synthetic")
                else:
                    with timed_step("Quelle Test"):
                        pass
            text = output.getvalue()
            self.assertIn("01:05", text)
            self.assertIn("abgebrochen/fehlgeschlagen" if fails else "fertig", text)
            self.assertNotIn("synthetic", text)

    def test_review_counts_separate_discovery_from_pending_workflow(self):
        output = io.StringIO()
        with redirect_stdout(output):
            print_review_diagnostics({
                "included": [
                    {"is_new": True, "workflow_status": "new"},
                    {"is_new": False, "workflow_status": "new"},
                    {"is_new": False, "workflow_status": "interesting"},
                ],
                "excluded": [{"is_new": True}, {"is_new": False}],
            }, {"new": 2, "known": 3})
        text = output.getvalue()
        self.assertIn("Erstfunde: 2", text)
        self.assertIn("1 davon passend", text)
        self.assertIn("1 davon ausgeschlossen", text)
        self.assertIn("2 unbearbeitet einschließlich Sonderfilter", text)
        self.assertIn("Review Neu: 2 im Standardfilter", text)


    def test_standard_review_count_keeps_unprocessed_known_jobs(self):
        output = io.StringIO()
        rows = [
            {"is_new": True, "workflow_status": "new"},
            {"is_new": True, "workflow_status": "new", "location_precheck": "Junior-Hybrid: Test"},
            {"is_new": True, "workflow_status": "new", "locations": ["Worldwide"]},
            {"is_new": False, "workflow_status": "new"},
        ]
        with redirect_stdout(output):
            print_review_diagnostics({"included": rows, "excluded": []}, {"new": 3, "known": 1})
        self.assertIn("Review Neu: 2 im Standardfilter", output.getvalue())
