"""Tests for the demo data script: its database guard, the demo file and the jobs it builds, without a database."""

import importlib.util
import json
import unittest
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from job_finder.agent.fact_sheet import parse_fact_sheet
from job_finder.models import WorkflowStatus

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demo_data.py"


def load_demo_data():
    spec = importlib.util.spec_from_file_location("demo_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEMO = load_demo_data()


class DemoDataTests(unittest.TestCase):
    def test_only_a_local_server_gets_the_demo_database(self):
        local = "postgresql://admin:secret@127.0.0.1:55432/jobfinder"

        admin, demo = DEMO.demo_database_urls({"JOBFINDER_ADMIN_DATABASE_URL": local}, env_file=Path("fehlt"))

        self.assertEqual(admin, local)
        self.assertIn("dbname=jobfinder_demo", demo)
        azure = "postgresql://admin:secret@psql-jobfinder.postgres.database.azure.com/jobfinder"
        for environ in ({"JOBFINDER_ADMIN_DATABASE_URL": azure}, {}):
            with self.subTest(environ=environ), self.assertRaises(SystemExit):
                DEMO.demo_database_urls(environ, env_file=Path("fehlt"))

    def test_the_demo_file_fits_the_cases_and_the_fact_sheet_schema(self):
        demo = yaml.safe_load(DEMO.DEMO_FILE.read_text(encoding="utf-8"))
        cases = yaml.safe_load(DEMO.CASES_FILE.read_text(encoding="utf-8"))
        ids = {case_id for case_id, _ad in DEMO.demo_ads(demo, cases)}

        self.assertLessEqual(set(demo["history"]) | set(demo["fact_sheets"]), ids)
        for events in demo["history"].values():
            for status, days_ago in events:
                self.assertEqual(WorkflowStatus(status).value, status)
                self.assertGreaterEqual(days_ago, 0)
        for case_id, sheet in demo["fact_sheets"].items():
            with self.subTest(case=case_id):
                parse_fact_sheet(json.dumps(sheet))

    def test_jobs_keep_their_distance_to_today(self):
        ad = {
            "title": "Junior Cloud Engineer (m/w/d)",
            "company": "Nordlicht Systems GmbH",
            "work_mode": "vor Ort",
            "published_at": "2026-09-24",
            "sources": [{"source": "beispielboerse", "url": "https://jobs.example.org/1"}],
            "description_clean": "Text.",
        }

        job = DEMO.demo_job("fall", ad, date(2027, 3, 1), date(2026, 10, 1), datetime(2027, 3, 1, tzinfo=UTC))

        self.assertEqual((job.id, job.work_mode.value, job.published_at), ("demo:fall", "onsite", date(2027, 2, 22)))
        self.assertEqual(job.sources[0].source_id, "fall")


if __name__ == "__main__":
    unittest.main()
