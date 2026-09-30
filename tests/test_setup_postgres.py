"""The local database setup writes everything a fresh `job_finder.db init` needs."""

import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_postgres.py"


def load_setup_postgres():
    spec = importlib.util.spec_from_file_location("setup_postgres", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_env(path):
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines())


class SetupPostgresTests(unittest.TestCase):
    def test_a_fresh_setup_can_initialize_the_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / ".env.postgres"
            with redirect_stdout(io.StringIO()):
                load_setup_postgres().main(target)

            values = read_env(target)

        self.assertEqual(values["JOBFINDER_ADMIN_DATABASE_URL"], values["JOBFINDER_DATABASE_URL"])
        self.assertIn(values["POSTGRES_PASSWORD"], values["JOBFINDER_DATABASE_URL"])
        self.assertTrue(values["JOBFINDER_TEST_DATABASE_URL"].endswith("/jobfinder_test"))

    def test_an_existing_file_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / ".env.postgres"
            target.write_text("POSTGRES_PASSWORD=keep\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()) as output:
                load_setup_postgres().main(target)

            self.assertEqual(target.read_text(encoding="utf-8"), "POSTGRES_PASSWORD=keep\n")
        self.assertIn("bleibt unverändert", output.getvalue())
