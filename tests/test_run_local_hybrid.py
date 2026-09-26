"""The unattended local StepStone/Remotely run starts Docker, keeps a log and reports failures."""

import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_local_hybrid.py"


def load_script():
    spec = importlib.util.spec_from_file_location("run_local_hybrid", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalHybridRunTests(unittest.TestCase):
    def setUp(self):
        self.script = load_script()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.logs = Path(directory.name)
        self.sent = []
        for patcher in (
            patch.object(self.script, "LOG_DIR", self.logs),
            patch.object(
                self.script,
                "DiscordWebhookClient",
                return_value=SimpleNamespace(send=self.sent.append),
            ),
            patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.test/hook"}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, container_exit=0, **steps):
        """Run main() with Docker, az and the container replaced; return the container call."""
        process = MagicMock(returncode=container_exit)
        process.__enter__.return_value = process
        process.stdout = iter(["[1/4] Quellen\n", "Lauf beendet\n"])
        defaults = {
            "start_docker": MagicMock(return_value=False),
            "stop_docker": MagicMock(),
            "deployed_image": MagicMock(return_value="acr.test/jobfinder:sha-1"),
            "pull": MagicMock(),
            "container_environment": MagicMock(
                return_value={"JOBFINDER_DATABASE_URL": "postgresql://app:s3cret@db.test/jobfinder"}
            ),
        }
        defaults.update(steps)
        with (
            patch.multiple(self.script, **defaults),
            patch.object(self.script.subprocess, "Popen", return_value=process) as popen,
            redirect_stdout(io.StringIO()),
        ):
            self.script.main()
        return popen

    def log_text(self):
        (log,) = self.logs.glob("hybrid-*.log")
        return log.name, log.read_text(encoding="utf-8")

    def test_docker_is_started_only_when_its_engine_does_not_answer(self):
        log = self.script.HybridLog()
        self.addCleanup(log.close)
        with (
            patch.object(self.script, "docker_answers", return_value=True),
            patch.object(self.script.subprocess, "run") as run,
            redirect_stdout(io.StringIO()),
        ):
            self.assertFalse(self.script.start_docker(log))
        run.assert_not_called()

        with (
            patch.object(self.script, "docker_answers", side_effect=[False, True]),
            patch.object(self.script.subprocess, "run") as run,
            redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(self.script.start_docker(log))
        self.assertEqual(run.call_args.args[0][:3], ["docker", "desktop", "start"])

    def test_a_successful_run_is_logged_without_the_secrets_it_passes_on(self):
        popen = self.run_main()

        name, text = self.log_text()
        self.assertIn("[1/4] Quellen", text)
        self.assertIn("Lokaler Lauf beendet.", text)
        self.assertNotIn("s3cret", text)
        self.assertIn("s3cret", " ".join(popen.call_args.args[0]))
        self.assertEqual(self.sent, [])

    def test_a_failed_step_reaches_discord_and_the_log_and_docker_stops_again(self):
        failure = self.script.RunFailed("az containerapp job fehlgeschlagen, az-Anmeldung prüfen")
        stop = MagicMock()

        with self.assertRaises(SystemExit) as caught:
            self.run_main(
                start_docker=MagicMock(return_value=True),
                stop_docker=stop,
                deployed_image=MagicMock(side_effect=failure),
            )

        self.assertEqual(caught.exception.code, 1)
        stop.assert_called_once()
        name, text = self.log_text()
        self.assertIn("Lokaler Lauf fehlgeschlagen: az containerapp job fehlgeschlagen", text)
        (message,) = self.sent
        self.assertIn("az containerapp job fehlgeschlagen, az-Anmeldung prüfen", message["content"])
        self.assertIn(name, message["content"])

    def test_a_failed_run_inside_the_container_is_reported(self):
        with self.assertRaises(SystemExit):
            self.run_main(container_exit=1)

        (message,) = self.sent
        self.assertIn("der Lauf im Container endete mit Code 1", message["content"])

    def test_logs_older_than_two_weeks_are_removed(self):
        now = datetime(2026, 9, 26, 10, 0)
        ages = {"hybrid-20260911-100000.log": 15, "hybrid-20260920-100000.log": 6}
        ages["run-20260801-100000.log"] = 56  # the finder's own logs are not this script's
        for name, days in ages.items():
            path = self.logs / name
            path.write_text("x", encoding="utf-8")
            moment = (now - timedelta(days=days)).timestamp()
            os.utime(path, (moment, moment))

        self.script.HybridLog(now=now).close()

        self.assertEqual(
            sorted(path.name for path in self.logs.iterdir()),
            ["hybrid-20260920-100000.log", "hybrid-20260926-100000.log", "run-20260801-100000.log"],
        )


if __name__ == "__main__":
    unittest.main()
