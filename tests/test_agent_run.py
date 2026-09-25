"""Tests for the agent's part of a finder run: selection, limits and the phase itself."""

import io
import os
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from itertools import count
from unittest.mock import Mock, patch

from job_finder.agent import run
from job_finder.agent.cost_guard import AgentStopped
from job_finder.agent.settings import agent_settings
from job_finder.paths import JOBS_FILE, RECOMMENDATIONS_JSON
from job_finder.persistence.database import transaction
from job_finder.persistence.fact_sheets import save_aborted
from job_finder.persistence.postgres_store import read_jobs, write_dataset, write_memory
from job_finder.persistence.storage import dataset_name

ENABLED = {"agent": {"enabled": True}}
ENDPOINT = {run.ENDPOINT_ENV: "https://oai-example.openai.azure.com/"}


def phase(values, environ, **patches):
    """Run agent_phase with its collaborators replaced; return (result, printed text)."""
    output = io.StringIO()
    with redirect_stdout(output), patch.multiple(run, **patches):
        result = run.agent_phase(values=values, environ=environ)
    return result, output.getvalue()


class AgentPhaseTests(unittest.TestCase):
    def test_the_log_says_why_the_agent_does_not_run(self):
        missing = ValueError("Profil fehlt: weder JOBFINDER_PROFILE noch profile.local.yaml")
        cases = (
            ({}, ENDPOINT, "Agent aus: Abschnitt agent fehlt"),
            (ENABLED, {}, "Agent übersprungen: JOBFINDER_OPENAI_ENDPOINT fehlt"),
            (ENABLED, ENDPOINT, "Agent aus: Profil fehlt"),
        )
        for values, environ, message in cases:
            with self.subTest(message=message):
                result, text = phase(values, environ, configured_profile=Mock(side_effect=missing))

                self.assertIsNone(result)
                self.assertIn(message, text)

    def test_an_agent_failure_never_breaks_the_finder_run(self):
        result, text = phase(
            ENABLED,
            ENDPOINT,
            configured_profile=Mock(return_value=("version: 5", "profile.local.yaml")),
            model_client=Mock(),
            run_agent=Mock(side_effect=RuntimeError("kaputt")),
        )

        self.assertIsNone(result)
        self.assertIn("Agent abgebrochen: RuntimeError", text)

    def test_a_finished_run_reports_its_numbers(self):
        stats = {
            "fertig": 12,
            "abgebrochen": 1,
            "offen": 4,
            "stopp": "Tagesgrenze erreicht: 1.00 € von 1.00 €",
            "heute_eur": Decimal("1.0012"),
            "monat_eur": Decimal("3.5"),
        }

        result, text = phase(
            ENABLED,
            ENDPOINT,
            configured_profile=Mock(return_value=("version: 5", "JOBFINDER_PROFILE")),
            model_client=Mock(),
            run_agent=Mock(return_value=stats),
        )

        self.assertEqual(result, stats)
        self.assertIn("12 fertig · 1 abgebrochen · 4 offen · heute 1,00 € von 1,00 €", text)
        self.assertIn("Stopp: Tagesgrenze erreicht", text)


class RunAgentTests(unittest.TestCase):
    def run_agent(self, outcomes, clock=None):
        waiting = [
            {"id": f"memory:{number}", "recommendation_id": f"job:{number}"}
            for number in range(1, 5)
        ]
        ads = {f"job:{number}": {"id": f"job:{number}"} for number in (1, 3, 4)}
        self.write = Mock(side_effect=outcomes)
        with patch.multiple(
            run,
            waiting_jobs=Mock(return_value=waiting),
            read_jobs=Mock(return_value=ads),
            write_fact_sheet=self.write,
            spent_today_and_this_month=Mock(return_value=(Decimal("0.2"), 1)),
        ):
            return run.run_agent(
                agent_settings(ENABLED), "version: 5", object(), clock=clock or count().__next__
            )

    def test_every_job_with_details_gets_its_turn_under_the_review_id(self):
        stats = self.run_agent(["fertig", "abgebrochen", "fertig"])

        self.assertEqual(
            (stats["fertig"], stats["abgebrochen"], stats["offen"], stats["stopp"]), (2, 1, 0, "")
        )
        self.assertEqual(stats["heute_eur"], Decimal("0.2"))
        self.assertEqual(
            [call.args[0]["id"] for call in self.write.call_args_list],
            ["memory:1", "memory:3", "memory:4"],
        )

    def test_a_run_stop_leaves_the_rest_waiting(self):
        stats = self.run_agent(["fertig", AgentStopped("Tagesgrenze erreicht")])

        self.assertEqual((stats["fertig"], stats["offen"]), (1, 2))
        self.assertEqual(stats["stopp"], "Tagesgrenze erreicht")

    def test_the_time_budget_ends_the_run_between_jobs(self):
        ticks = iter([0, 10, run.RUN_SECONDS + 1])

        stats = self.run_agent(["fertig"], clock=lambda: next(ticks))

        self.assertEqual((stats["fertig"], stats["offen"]), (1, 2))
        self.assertEqual(stats["stopp"], "Zeitbudget des Laufs erreicht")


class AgentSelectionTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE job_state, datasets, agent_fact_sheets CASCADE")

    def test_waiting_jobs_are_the_reviews_undecided_jobs_behind_the_gate(self):
        def recommendation(job_id, score, rank, **extra):
            return {
                "id": job_id,
                "title": job_id,
                "company": "Beispiel GmbH",
                "match_percent": score,
                "experience_rank": rank,
                "url": f"https://example.com/{job_id}",
                **extra,
            }

        write_dataset(
            dataset_name(RECOMMENDATIONS_JSON),
            {
                "recommendations": [
                    recommendation("job:entry", 40, 0),
                    recommendation("job:high", 70, 2),
                    recommendation("job:low", 45, 2),
                    recommendation("job:decided", 80, 0),
                    recommendation("job:review", 60, 0),
                    recommendation("job:international", 90, 0, international=True),
                    recommendation(
                        "job:junior-hybrid", 85, 0, location_precheck="Junior-Hybrid: Pendelweg"
                    ),
                    recommendation("job:done", 75, 0),
                    recommendation("job:unknown-state", 55, 0),
                    # Same listing as an older, already decided memory entry.
                    recommendation("job:moved", 95, 0, url="https://example.com/old"),
                ]
            },
        )
        states = {
            "job:entry": "new",
            "job:high": "new",
            "job:low": "new",
            "job:decided": "ignored",
            "job:review": "review",
            "job:international": "new",
            "job:junior-hybrid": "new",
            "job:done": "new",
        }
        memory = {
            job_id: {
                "title": job_id,
                "workflow_status": status,
                "source_urls": [f"https://example.com/{job_id}"],
                "first_seen_at": "2026-09-20T08:00:00",
            }
            for job_id, status in states.items()
        }
        memory["job:old"] = {
            "title": "job:old",
            "workflow_status": "ignored",
            "source_urls": ["https://example.com/old"],
            "first_seen_at": "2026-08-01T08:00:00",
        }
        with transaction() as connection:
            write_memory(connection, "default", {}, memory)
        save_aborted("job:done", "gpt-5-mini", "abgebrochen", Decimal("0.08"))

        waiting = run.waiting_jobs()

        self.assertEqual(
            [(job["id"], job["recommendation_id"]) for job in waiting],
            [
                ("job:high", "job:high"),
                ("job:review", "job:review"),
                ("job:unknown-state", "job:unknown-state"),
                ("job:entry", "job:entry"),
            ],
        )

    def test_only_the_requested_job_details_are_read(self):
        write_dataset(
            dataset_name(JOBS_FILE),
            [
                {"id": "job:1", "title": "Python", "company": "A", "description_clean": "Text"},
                {"id": "job:2", "title": "Java", "company": "B", "description_clean": "Mehr"},
            ],
        )

        jobs = read_jobs(dataset_name(JOBS_FILE), ["job:1", "job:unbekannt"])

        self.assertEqual(
            jobs,
            {
                "job:1": {
                    "id": "job:1",
                    "title": "Python",
                    "company": "A",
                    "description_clean": "Text",
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
