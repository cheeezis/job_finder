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

from psycopg.conninfo import conninfo_to_dict

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
            patch.object(self.script, "DiscordWebhookClient", return_value=SimpleNamespace(send=self.sent.append)),
            patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.test/hook"}),
        ):
            self.enterContext(patcher)

    def run_main(self, container_exit=0, **steps):
        """Run main() with Docker, az and the container replaced; return the container call."""
        process = MagicMock(returncode=container_exit)
        process.__enter__.return_value = process
        process.stdout = iter(["[1/4] Quellen\n", "Lauf beendet\n"])
        defaults = {
            "start_docker": MagicMock(return_value=False),
            "stop_docker": MagicMock(),
            "allow_current_ip": MagicMock(return_value=False),
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
            self.script.main([])
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

    def firewall(self, current, public="9.9.9.9"):
        """Run allow_current_ip against a rule that holds current; return the az calls."""
        log = self.script.HybridLog()
        self.addCleanup(log.close)
        az = MagicMock(side_effect=lambda *args, log: current if "show" in args else "")
        with (
            patch.object(self.script, "fetch_text", return_value=f"{public}\n"),
            patch.object(self.script, "database_server", return_value="psql-test"),
            patch.object(self.script, "az", az),
            redirect_stdout(io.StringIO()),
        ):
            changed = self.script.allow_current_ip(log)
        return changed, [call.args for call in az.call_args_list]

    def test_the_firewall_rule_follows_a_new_public_ip(self):
        changed, calls = self.firewall(current="198.51.100.1")

        self.assertTrue(changed)
        show, update = calls
        self.assertIn("show", show)
        self.assertIn("update", update)
        for flag, value in (
            ("--server-name", "psql-test"),
            ("--name", "local-review"),
            ("--start-ip-address", "9.9.9.9"),
            ("--end-ip-address", "9.9.9.9"),
        ):
            self.assertEqual(update[update.index(flag) + 1], value)

    def test_an_unchanged_ip_leaves_the_rule_alone(self):
        changed, calls = self.firewall(current="9.9.9.9")

        self.assertFalse(changed)
        self.assertEqual(len(calls), 1)

    def test_an_unusable_ip_answer_stops_before_any_change(self):
        for answer in ("<html>", "10.0.0.5"):
            with self.subTest(answer), self.assertRaisesRegex(self.script.RunFailed, "öffentliche IP"):
                self.firewall(current="198.51.100.1", public=answer)

    def test_allow_ip_only_updates_the_rule(self):
        allow, start = MagicMock(return_value=True), MagicMock()
        with patch.multiple(self.script, allow_current_ip=allow, start_docker=start), redirect_stdout(io.StringIO()):
            self.script.main(["--allow-ip"])

        allow.assert_called_once()
        start.assert_not_called()

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

    def test_the_container_gets_the_optional_values_and_personal_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".env.postgres-azure").write_text(
                "JOBFINDER_DATABASE_URL=postgresql://app@db.test/jobfinder?sslrootcert=C:/ca.pem\n", encoding="utf-8"
            )
            docker_local = "AZURE_CLIENT_ID=c\nAZURE_TENANT_ID=t\nAZURE_CLIENT_SECRET=s\n"
            (project / ".env.docker-local").write_text(docker_local, encoding="utf-8")
            with patch.object(self.script, "PROJECT_DIR", project):
                bare = self.script.container_environment()
                (project / ".env.docker-local").write_text(
                    docker_local + "JOBFINDER_OPENAI_ENDPOINT=https://model.test/\nJOBFINDER_REVIEW_HOST=review.test\n",
                    encoding="utf-8",
                )
                (project / "profile.local.yaml").write_text("profil: ja\n", encoding="utf-8")
                full = self.script.container_environment()

        self.assertNotIn("JOBFINDER_OPENAI_ENDPOINT", bare)
        self.assertNotIn("JOBFINDER_PROFILE", bare)
        self.assertEqual(full["JOBFINDER_OPENAI_ENDPOINT"], "https://model.test/")
        self.assertEqual(full["JOBFINDER_REVIEW_HOST"], "review.test")
        self.assertEqual(full["JOBFINDER_PROFILE"], "profil: ja\n")
        self.assertIn("sslrootcert=/etc/ssl/certs/ca-certificates.crt", full["JOBFINDER_DATABASE_URL"])

    def test_separate_hybrid_credentials_require_explicit_activation_and_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".env.postgres-azure").write_text(
                "JOBFINDER_DATABASE_URL=postgresql://legacy@db.test/jobfinder\n", encoding="utf-8"
            )
            (project / ".env.docker-local").write_text(
                "AZURE_CLIENT_ID=c\nAZURE_TENANT_ID=t\nAZURE_CLIENT_SECRET=s\n", encoding="utf-8"
            )
            path = project / ".env.runtime-azure"
            credentials = "JOBFINDER_HYBRID_DATABASE_URL=postgresql://hybrid@db.test/jobfinder?sslrootcert=C:/ca.pem\n"
            with patch.object(self.script, "PROJECT_DIR", project):
                path.write_text(
                    credentials + "JOBFINDER_RUNTIME_CREDENTIALS_READY=1\nJOBFINDER_RUNTIME_ACCESS=legacy\n",
                    encoding="utf-8",
                )
                self.assertIn("legacy@", self.script.container_environment()["JOBFINDER_DATABASE_URL"])
                path.write_text(
                    credentials + "JOBFINDER_RUNTIME_CREDENTIALS_READY=0\nJOBFINDER_RUNTIME_ACCESS=split\n",
                    encoding="utf-8",
                )
                with self.assertRaises(self.script.RunFailed):
                    self.script.container_environment()
                path.write_text(
                    credentials + "JOBFINDER_RUNTIME_CREDENTIALS_READY=1\nJOBFINDER_RUNTIME_ACCESS=split\n",
                    encoding="utf-8",
                )
                activated = self.script.container_environment()
                self.assertIn("hybrid@", activated["JOBFINDER_DATABASE_URL"])
                self.assertIn("sslrootcert=/etc/ssl/certs/ca-certificates.crt", activated["JOBFINDER_DATABASE_URL"])

    def test_entra_hybrid_requires_acceptance_and_matching_password_free_target(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".env.postgres-azure").write_text(
                "JOBFINDER_DATABASE_URL=postgresql://legacy:old@db.test/jobfinder\n", encoding="utf-8"
            )
            (project / ".env.docker-local").write_text(
                "AZURE_CLIENT_ID=hybrid-client\nAZURE_TENANT_ID=tenant\nAZURE_CLIENT_SECRET=synthetic-secret\n",
                encoding="utf-8",
            )
            values = {
                "JOBFINDER_RUNTIME_ACCESS": "split",
                "JOBFINDER_RUNTIME_CREDENTIALS_READY": "1",
                "JOBFINDER_HYBRID_DATABASE_URL": "postgresql://jobfinder_hybrid:old@db.test:5432/jobfinder?sslmode=verify-full",
                "JOBFINDER_HYBRID_DATABASE_AUTH": "entra",
                "JOBFINDER_HYBRID_ENTRA_VERIFIED": "1",
                "JOBFINDER_HYBRID_ENTRA_DATABASE_URL": "postgresql://jobfinder_hybrid_entra@db.test/jobfinder?sslmode=verify-full&sslrootcert=C:/roots.pem",
            }
            path = project / ".env.runtime-azure"

            def write(config):
                path.write_text("".join(f"{key}={value}\n" for key, value in config.items()), encoding="utf-8")

            url = values["JOBFINDER_HYBRID_ENTRA_DATABASE_URL"]
            cases = [
                {"JOBFINDER_HYBRID_ENTRA_VERIFIED": "0"},
                {"JOBFINDER_RUNTIME_ACCESS": "legacy"},
                {"JOBFINDER_RUNTIME_CREDENTIALS_READY": "0"},
                {"JOBFINDER_HYBRID_DATABASE_AUTH": "typo"},
                *[
                    {"JOBFINDER_HYBRID_ENTRA_DATABASE_URL": wrong}
                    for wrong in (
                        "",
                        url.replace("hybrid_entra", "review_entra"),
                        url.replace("entra@", "entra:old@"),
                        url.replace("verify-full", "require"),
                        url.replace("db.test", "other.test"),
                        url.replace("/jobfinder", "/other"),
                        url.replace("db.test", "db.test:5444"),
                        url + "&passfile=/other-login",
                    )
                ],
            ]
            with patch.object(self.script, "PROJECT_DIR", project), patch.dict(os.environ, {}, clear=True):
                for changed in cases:
                    with self.subTest(changed=changed):
                        write({**values, **changed})
                        with self.assertRaises(self.script.RunFailed):
                            self.script.container_environment()
                write(values)
                active = self.script.container_environment()
                self.assertEqual(active["JOBFINDER_DATABASE_AUTH"], "service_principal")
                self.assertEqual(active["AZURE_CLIENT_ID"], "hybrid-client")
                actual = conninfo_to_dict(active["JOBFINDER_DATABASE_URL"])
                self.assertEqual(actual["user"], "jobfinder_hybrid_entra")
                self.assertEqual(actual["sslrootcert"], "/etc/ssl/certs/ca-certificates.crt")
                self.assertNotIn("password", actual)
                self.assertEqual(actual["sslmode"], "verify-full")
                # Preparing a new target never changes the default or rollback path.
                write({key: value for key, value in values.items() if key != "JOBFINDER_HYBRID_DATABASE_AUTH"})
                previous = self.script.container_environment()
                self.assertNotIn("JOBFINDER_DATABASE_AUTH", previous)
                self.assertEqual(conninfo_to_dict(previous["JOBFINDER_DATABASE_URL"])["user"], "jobfinder_hybrid")
                write({**values, "JOBFINDER_HYBRID_DATABASE_AUTH": "password"})
                self.assertEqual(self.script.container_environment(), previous)

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
