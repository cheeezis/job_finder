"""The runs table: each finder run with where it ran, how it ended and its key figures."""

import os
import unittest

from job_finder.persistence.database import transaction
from job_finder.persistence.runs import finish_run, latest_runs, runner, start_run


class RunsTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE runs")

    def test_the_runner_comes_from_the_environment_and_defaults_to_local(self):
        self.assertEqual(runner({"JOBFINDER_RUNNER": "cloud"}), "cloud")
        self.assertEqual(runner({"JOBFINDER_RUNNER": "hybrid"}), "hybrid")
        self.assertEqual(runner({"JOBFINDER_RUNNER": "irgendwo"}), "local")
        self.assertEqual(runner({}), "local")

    def test_the_review_sees_the_newest_run_of_each_runner(self):
        start_run("cloud-1", ["arbeitnow"], {"JOBFINDER_RUNNER": "cloud"})
        finish_run("cloud-1", "finished", jobs_total=12, jobs_new=3, review_new=2, sources_partial=0, sources_failed=1)
        start_run("hybrid-1", ["stepstone", "remotely"], {"JOBFINDER_RUNNER": "hybrid"})
        finish_run("hybrid-1", "failed")
        start_run("cloud-2", ["arbeitnow"], {"JOBFINDER_RUNNER": "cloud"})

        runs = {run["runner"]: run for run in latest_runs()}

        self.assertEqual(set(runs), {"cloud", "hybrid"})
        self.assertEqual((runs["cloud"]["run_id"], runs["cloud"]["outcome"]), ("cloud-2", "running"))
        self.assertEqual((runs["hybrid"]["outcome"], runs["hybrid"]["jobs_new"]), ("failed", None))
        self.assertIsNone(runs["cloud"]["finished_at"])
        with transaction() as connection:
            row = connection.execute(
                "SELECT jobs_new, review_new, sources FROM runs WHERE run_id = 'cloud-1'"
            ).fetchone()
        self.assertEqual(row, (3, 2, ["arbeitnow"]))
