"""Ensure the local recovery drill cannot silently use a production target."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.restore_drill import isolated_environment, run_drill


class RestoreDrillSafetyTests(unittest.TestCase):
    def test_remote_and_redirected_targets_are_refused_before_connecting(self):
        for url in (
            "postgresql://test:fake@example.postgres.database.azure.com/jobfinder",
            "host=localhost hostaddr=192.0.2.1 user=test dbname=jobfinder",
            "host=localhost service=production user=test dbname=jobfinder",
            "host=localhost,example.test user=test dbname=jobfinder",
        ):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                env_file = root / "local.env"
                env_file.write_text(f"JOBFINDER_ADMIN_DATABASE_URL='{url}'\n", encoding="utf-8")
                with (
                    patch("scripts.restore_drill.psycopg.connect") as connect,
                    self.assertRaisesRegex(ValueError, "lokalen"),
                ):
                    run_drill(env_file, root / "report.json")
                connect.assert_not_called()
                self.assertFalse((root / "report.json").exists())

    def test_cloud_settings_are_isolated_and_original_environment_returns_on_failure(self):
        inherited = {
            "JOBFINDER_DATABASE_URL": "production-placeholder",
            "JOBFINDER_DOCUMENTS_BACKEND": "blob",
            "JOBFINDER_DATABASE_AUTH": "managed_identity",
            "JOBFINDER_OPENAI_ENDPOINT": "https://example.test",
            "DISCORD_WEBHOOK_URL": "https://example.test/webhook",
            "PGHOSTADDR": "192.0.2.1",
            "PGSERVICE": "production",
        }
        with patch.dict(os.environ, inherited):
            before = dict(os.environ)
            with self.assertRaises(RuntimeError), isolated_environment():
                self.assertEqual(os.environ["JOBFINDER_DOCUMENTS_BACKEND"], "local")
                self.assertEqual(os.environ["JOBFINDER_DATABASE_AUTH"], "password")
                self.assertEqual(os.environ["DISCORD_WEBHOOK_URL"], "")
                self.assertEqual(os.environ["JOBFINDER_OPENAI_ENDPOINT"], "")
                self.assertNotIn("JOBFINDER_DATABASE_URL", os.environ)
                self.assertNotIn("PGHOSTADDR", os.environ)
                self.assertNotIn("PGSERVICE", os.environ)
                raise RuntimeError("synthetic failure")
            self.assertEqual(dict(os.environ), before)
