"""Promises the database keeps, checked in a real PostgreSQL: the app role's rights and one worker at a time."""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg import errors
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from job_finder.persistence.database import database_url, worker_lock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
# Its own role, so the test never resets the password of a real jobfinder_app on the same server.
TEST_ROLE = "jobfinder_app_boundary_test"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_app_role_module():
    with patch.dict(sys.modules, {"azure_postgres": load_script("azure_postgres")}):
        return load_script("create_app_role")


class AppRoleRightsTests(unittest.TestCase):
    """The role worker and review use reads and writes rows, but never changes the schema."""

    @classmethod
    def setUpClass(cls):
        script = create_app_role_module()
        with psycopg.connect(database_url(), autocommit=True) as admin, patch.object(script, "APP_ROLE", TEST_ROLE):
            password = script._ensure_role(admin)
            admin_role = admin.execute("SELECT current_user").fetchall()[0][0]
            script._apply_grants(admin, conninfo_to_dict(database_url())["dbname"], admin_role)
        cls.app_url = make_conninfo(database_url(), user=TEST_ROLE, password=password)

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(database_url(), autocommit=True) as admin:
            admin.execute(f'DROP OWNED BY "{TEST_ROLE}"')
            admin.execute(f'DROP ROLE "{TEST_ROLE}"')

    def run_as_app(self, statement):
        with psycopg.connect(self.app_url) as connection:
            connection.execute(statement)
            connection.rollback()

    def test_the_app_role_reads_and_writes_rows(self):
        # WHERE false changes nothing, but PostgreSQL still checks the privilege.
        for statement in (
            "SELECT count(*) FROM job_state",
            "INSERT INTO job_state SELECT * FROM job_state WHERE false",
            "UPDATE job_state SET scope = scope WHERE false",
            "DELETE FROM job_state WHERE false",
        ):
            with self.subTest(statement=statement):
                self.run_as_app(statement)

    def test_the_app_role_cannot_change_the_schema(self):
        for statement in (
            "CREATE TABLE boundary_probe (id int)",
            "ALTER TABLE job_state ADD COLUMN boundary_probe int",
            "DROP TABLE job_state",
            "TRUNCATE job_state",
            "SELECT * FROM schema_version",
            "INSERT INTO schema_version VALUES (999)",
        ):
            with self.subTest(statement=statement), self.assertRaises(errors.InsufficientPrivilege):
                self.run_as_app(statement)


class WorkerLockTests(unittest.TestCase):
    def test_a_second_run_is_refused_while_the_first_holds_the_lock(self):
        with worker_lock(), self.assertRaisesRegex(RuntimeError, "bereits aktiv"), worker_lock():
            pass

    def test_the_lock_is_free_again_after_a_run(self):
        with worker_lock():
            pass
        with worker_lock():
            pass


if __name__ == "__main__":
    unittest.main()
