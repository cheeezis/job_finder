"""Tests for readable console progress output."""

import io
import unittest
from contextlib import redirect_stdout

from job_finder.console import (
    print_phase,
    print_progress,
    progress_bar,
    progress_line,
)
from job_finder.operations import TeeStream


class FakeTerminal(io.StringIO):
    def isatty(self):
        return True


class ConsoleProgressTests(unittest.TestCase):
    def test_progress_bar_is_bounded(self):
        self.assertEqual(progress_bar(0, 4, width=8), "[--------]")
        self.assertEqual(progress_bar(2, 4, width=8), "[####----]")
        self.assertEqual(progress_bar(5, 4, width=8), "[########]")

    def test_progress_lines_remain_readable_in_logs(self):
        output = io.StringIO()
        with redirect_stdout(output):
            print_phase(3, 4, "Vorfilter")
            print_progress("Remotely", 1, 1, "42 Stellen")

        text = output.getvalue()
        self.assertIn("3/4 Vorfilter", text)
        self.assertIn("Remotely 100%|", text)
        self.assertIn("1/1", text)
        self.assertIn("42 Stellen", text)
        self.assertNotIn("\r", text)

    def test_tqdm_style_line_contains_timing_eta_and_rate(self):
        line = progress_line(
            "Remotely Details",
            128,
            2598,
            elapsed_seconds=2,
        )

        self.assertIn("  5%|", line)
        self.assertIn("128/2598", line)
        self.assertIn("[00:02<00:39, 64.00 it/s]", line)

    def test_terminal_progress_redraws_one_line_and_logs_only_completion(self):
        terminal = FakeTerminal()
        log = io.StringIO()
        stream = TeeStream(terminal, log)

        stream.write_progress("[----] Remotely 0/1")
        stream.write_progress("[##--] Remotely 1/2")
        stream.write_progress("[####] Remotely 1/1", complete=True)

        self.assertIn("\r[----] Remotely 0/1", terminal.getvalue())
        self.assertTrue(terminal.getvalue().endswith("\n"))
        self.assertEqual(log.getvalue(), "[####] Remotely 1/1\n")

    def test_regular_output_clears_an_active_progress_line(self):
        terminal = FakeTerminal()
        log = io.StringIO()
        stream = TeeStream(terminal, log)
        stream.write_progress("[##--] StepStone")

        stream.write("WARNUNG\n")

        self.assertRegex(terminal.getvalue(), r"\r +\rWARNUNG\n")
        self.assertEqual(log.getvalue(), "WARNUNG\n")


if __name__ == "__main__":
    unittest.main()
