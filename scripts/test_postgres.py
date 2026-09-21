"""Run the suite against an explicitly separate, disposable PostgreSQL database."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg.conninfo import conninfo_to_dict

from job_finder.persistence.database import database_url, initialize, transaction


def main():
    """Refuse production targets and isolate persistence tests from real data."""
    production = database_url()
    target = os.environ.get("JOBFINDER_TEST_DATABASE_URL")
    if not target:
        raise SystemExit(
            "JOBFINDER_TEST_DATABASE_URL fehlt; keine Tests gegen Produktivdaten."
        )
    info = conninfo_to_dict(target)
    if not info.get("dbname", "").endswith("_test") or info.get(
        "dbname"
    ) == conninfo_to_dict(production).get("dbname"):
        raise SystemExit("Tests benötigen eine separate Datenbank mit Suffix _test.")
    os.environ["JOBFINDER_DATABASE_URL"] = target
    # The disposable test database has no app/admin split; the same superuser
    # connection is fine for schema setup (initialize()) and TRUNCATE below.
    os.environ["JOBFINDER_ADMIN_DATABASE_URL"] = target
    os.environ["JOBFINDER_TEST_MODE"] = "1"
    initialize()
    with transaction() as connection:
        connection.execute("TRUNCATE job_state,datasets,migration_runs CASCADE")
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
