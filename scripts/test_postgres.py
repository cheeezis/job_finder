"""Run the suite against an explicitly separate, disposable PostgreSQL database.

Extra arguments go to pytest, for example --cov for the coverage report in CI.
"""

import os
import sys

import pytest
from psycopg.conninfo import conninfo_to_dict

from job_finder.persistence.database import database_url, initialize, transaction


def main():
    """Refuse production targets and isolate persistence tests from real data."""
    production = database_url()
    target = os.environ.get("JOBFINDER_TEST_DATABASE_URL")
    if not target:
        raise SystemExit("JOBFINDER_TEST_DATABASE_URL fehlt; keine Tests gegen Produktivdaten.")
    name = conninfo_to_dict(target).get("dbname", "")
    if not name.endswith("_test") or name == conninfo_to_dict(production).get("dbname"):
        raise SystemExit("Tests benötigen eine separate Datenbank mit Suffix _test.")
    os.environ["JOBFINDER_DATABASE_URL"] = target
    # The disposable test database has no app/admin split; the same superuser
    # connection is fine for schema setup (initialize()) and TRUNCATE below.
    os.environ["JOBFINDER_ADMIN_DATABASE_URL"] = target
    os.environ["JOBFINDER_TEST_MODE"] = "1"
    initialize()
    with transaction() as connection:
        connection.execute("TRUNCATE job_state,datasets CASCADE")
    # pytest runs the unittest test classes unchanged.
    raise SystemExit(pytest.main(["-q", *sys.argv[1:]]))


if __name__ == "__main__":
    main()
