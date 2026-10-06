"""Tests for the agent's part of a finder run: selection, limits and the phase itself."""

import io
import os
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from itertools import count
from unittest.mock import Mock, patch

from job_finder.agent import run
from job_finder.agent.basis import job_basis, run_basis
from job_finder.agent.cost_guard import AgentStopped
from job_finder.agent.settings import agent_settings
from job_finder.paths import JOBS_FILE, RECOMMENDATIONS_JSON
from job_finder.persistence.database import transaction
from job_finder.persistence.fact_sheets import fact_sheets, save_aborted, save_fact_sheet
from job_finder.persistence.postgres_store import read_jobs, write_dataset, write_memory
from job_finder.persistence.storage import dataset_name
from job_finder.workflow.memory import load_memory
from job_finder.workflow.review_actions import RERUN_FIELD

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

    def test_an_agent_failure_never_breaks_the_finder_run_but_warns_in_discord(self):
        warning = Mock(return_value=None)
        result, text = phase(
            ENABLED,
            {**ENDPOINT, "DISCORD_WEBHOOK_URL": "https://discord.example/hook"},
            configured_profile=Mock(return_value=("version: 5", "profile.local.yaml")),
            model_client=Mock(),
            run_agent=Mock(side_effect=RuntimeError("kaputt")),
            send_warning=warning,
        )

        self.assertIsNone(result)
        self.assertIn("Agent abgebrochen: RuntimeError", text)
        warning.assert_called_once_with(
            "Job Finder · Steckbriefe", "Agent abgebrochen: RuntimeError", webhook_url="https://discord.example/hook"
        )

    def test_a_finished_run_reports_its_numbers(self):
        stats = {
            "fertig": 12,
            "abgebrochen": 1,
            "offen": 4,
            "stopp": "Tagesgrenze erreicht: 1,00 € von 1,00 €",
            "heute_eur": Decimal("1.0012"),
            "monat_eur": Decimal("3.5"),
        }

        warning = Mock(return_value="DISCORD_WEBHOOK_URL ist nicht gesetzt")
        result, text = phase(
            ENABLED,
            ENDPOINT,
            configured_profile=Mock(return_value=("version: 5", "JOBFINDER_PROFILE")),
            model_client=Mock(),
            run_agent=Mock(return_value=stats),
            send_warning=warning,
        )

        self.assertEqual(result, stats)
        self.assertIn("12 fertig · 1 abgebrochen · 4 offen · 0 veraltet · heute 1,00 € von 1,00 €", text)
        self.assertIn("Stopp: Tagesgrenze erreicht", text)
        self.assertEqual(warning.call_args.args[1], "Agent gestoppt: Tagesgrenze erreicht: 1,00 € von 1,00 € · 4 offen")
        self.assertIn("Discord-Warnung: DISCORD_WEBHOOK_URL ist nicht gesetzt", text)

    def test_a_run_that_finishes_its_jobs_sends_no_warning(self):
        warning = Mock(return_value=None)
        stats = {
            "fertig": 3,
            "abgebrochen": 0,
            "offen": 0,
            "stopp": "",
            "heute_eur": Decimal("0.12"),
            "monat_eur": Decimal("1.5"),
        }

        phase(
            ENABLED,
            ENDPOINT,
            configured_profile=Mock(return_value=("version: 5", "JOBFINDER_PROFILE")),
            model_client=Mock(),
            run_agent=Mock(return_value=stats),
            send_warning=warning,
        )

        warning.assert_not_called()

    def test_traces_are_sent_before_the_container_ends_even_after_a_failure(self):
        tracing = Mock()
        _result, text = phase(
            ENABLED,
            ENDPOINT,
            configured_profile=Mock(return_value=("version: 5", "JOBFINDER_PROFILE")),
            model_client=Mock(),
            run_agent=Mock(side_effect=RuntimeError("kaputt")),
            send_warning=Mock(return_value=None),
            configure_tracing=Mock(return_value=tracing),
        )

        self.assertIn("Traces: Application Insights", text)
        tracing.shutdown.assert_called_once_with()

    def test_broken_tracing_costs_only_the_traces(self):
        run_agent = Mock(return_value={"fertig": 0, "abgebrochen": 0, "offen": 0, "stopp": "", "heute_eur": 0})
        _result, text = phase(
            ENABLED,
            ENDPOINT,
            configured_profile=Mock(return_value=("version: 5", "JOBFINDER_PROFILE")),
            model_client=Mock(),
            run_agent=run_agent,
            configure_tracing=Mock(side_effect=ValueError("kaputte Verbindungszeichenfolge")),
        )

        self.assertIn("Traces aus: ValueError", text)
        run_agent.assert_called_once()

    def test_the_agent_gets_the_places_the_profile_refers_to(self):
        values = {
            **ENABLED,
            "search": {"local_location": "Fulda", "local_radius_km": 25},
            "matching": {
                "local_places": ["fulda", "hünfeld"],
                "commuter_locations": [
                    {
                        "search_location": "Frankfurt am Main",
                        "aliases": ["frankfurt"],
                        "excluded_aliases": [],
                        "minimum_remote_percentage": 60,
                    }
                ],
            },
        }
        stats = {"fertig": 0, "abgebrochen": 0, "offen": 0, "stopp": ""}
        stats.update(heute_eur=Decimal("0"), monat_eur=Decimal("0"))
        run_agent = Mock(return_value=stats)

        phase(
            values,
            ENDPOINT,
            configured_profile=Mock(return_value=("version: 6", "JOBFINDER_PROFILE")),
            model_client=Mock(),
            run_agent=run_agent,
        )

        profile_text = run_agent.call_args.args[1]
        self.assertTrue(profile_text.startswith("version: 6\n\n# Orte aus seinen Sucheinstellungen"))
        self.assertIn("- Nahbereich, vor Ort gut erreichbar (um Fulda, etwa 25 km): Fulda, Hünfeld", profile_text)
        self.assertIn("Frankfurt am Main (mindestens 60 % Homeoffice)", profile_text)
        self.assertEqual(run.profile_with_places("version: 6", {}), "version: 6")


class RunAgentTests(unittest.TestCase):
    def run_agent(self, outcomes, clock=None):
        waiting = [{"id": f"memory:{number}", "recommendation_id": f"job:{number}"} for number in range(1, 5)]
        ads = {f"job:{number}": {"id": f"job:{number}"} for number in (1, 3, 4)}
        self.write = Mock(side_effect=outcomes)
        with patch.multiple(
            run,
            load_review_jobs=Mock(return_value=[]),
            fact_sheets=Mock(return_value={}),
            waiting_jobs=Mock(return_value=waiting),
            read_jobs=Mock(return_value=ads),
            write_fact_sheet=self.write,
            spent_today_and_this_month=Mock(return_value=(Decimal("0.2"), 1)),
        ):
            return run.run_agent(agent_settings(ENABLED), "version: 5", object(), clock=clock or count().__next__)

    def test_every_job_with_details_gets_its_turn_under_the_review_id(self):
        stats = self.run_agent(["fertig", "abgebrochen", "fertig"])

        self.assertEqual((stats["fertig"], stats["abgebrochen"], stats["offen"], stats["stopp"]), (2, 1, 0, ""))
        self.assertEqual(stats["heute_eur"], Decimal("0.2"))
        self.assertEqual(
            [call.args[0]["id"] for call in self.write.call_args_list], ["memory:1", "memory:3", "memory:4"]
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
                    recommendation("job:junior-hybrid", 85, 0, location_precheck="Junior-Hybrid: Pendelweg"),
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

    def recommend(self, *jobs):
        """Publish recommendations and remember them with the given status and extra fields."""
        write_dataset(
            dataset_name(RECOMMENDATIONS_JSON),
            {
                "recommendations": [
                    {
                        "id": job_id,
                        "title": job_id,
                        "company": "Beispiel GmbH",
                        "match_percent": score,
                        "experience_rank": 0,
                        "url": f"https://example.com/{job_id}",
                    }
                    for job_id, score, _status, _extra in jobs
                ]
            },
        )
        memory = {
            job_id: {
                "title": job_id,
                "workflow_status": status,
                "source_urls": [f"https://example.com/{job_id}"],
                **extra,
            }
            for job_id, _score, status, extra in jobs
        }
        with transaction() as connection:
            write_memory(connection, "default", {}, memory)

    def test_an_aborted_sheet_gets_one_more_attempt_and_a_requested_job_comes_first(self):
        self.recommend(
            ("job:retry", 60, "new", {}),
            ("job:failed", 70, "new", {}),
            ("job:fresh", 50, "new", {}),
            # Decided and below the gate, but the user asked for a new sheet in the review.
            ("job:asked", 10, "interesting", {RERUN_FIELD: "2026-10-06T18:00:00+00:00"}),
        )
        save_aborted("job:retry", "gpt-5-mini", "unvollständig", Decimal("0.02"), retryable=True)
        save_aborted("job:failed", "gpt-5-mini", "unvollständig", Decimal("0.02"), attempt=2)
        save_fact_sheet("job:asked", "gpt-5-mini", {"fazit": {"stufe": "bewerben"}}, Decimal("0.03"))

        waiting = run.waiting_jobs()

        self.assertEqual(
            [(job["id"], job["agent_attempt"]) for job in waiting],
            [("job:asked", 1), ("job:retry", 2), ("job:fresh", 1)],
        )

    def test_a_run_stamps_new_sheets_marks_changed_ones_and_clears_the_request(self):
        ads = {
            "job:old": {"id": "job:old", "title": "Python", "company": "A", "description_clean": "Neuer Text"},
            "job:asked": {"id": "job:asked", "title": "Java", "company": "B", "description_clean": "Text"},
        }
        write_dataset(dataset_name(JOBS_FILE), list(ads.values()))
        self.recommend(
            ("job:old", 60, "interesting", {}),
            ("job:asked", 50, "interesting", {RERUN_FIELD: "2026-10-06T18:00:00+00:00"}),
        )
        settings = agent_settings(ENABLED)
        basis = run_basis("name: Alex", {}, settings)
        written_on = job_basis(basis, {**ads["job:old"], "description_clean": "Alter Text"})
        save_fact_sheet("job:old", "gpt-5-mini", {"fazit": {"stufe": "bewerben"}}, Decimal("0.03"), versions=written_on)
        write = Mock(return_value="fertig")

        with patch.multiple(
            run, write_fact_sheet=write, spent_today_and_this_month=Mock(return_value=(Decimal("0.03"), 1))
        ):
            stats = run.run_agent(settings, "name: Alex", object(), clock=count().__next__, basis=basis)

        self.assertEqual((stats["fertig"], stats["veraltet"]), (1, 1))
        self.assertEqual(fact_sheets()["job:old"]["outdated"], ["ad"])
        self.assertEqual(write.call_args.args[0]["id"], "job:asked")
        self.assertEqual(write.call_args.kwargs, {"attempt": 1, "versions": job_basis(basis, ads["job:asked"])})
        self.assertNotIn(RERUN_FIELD, load_memory()["job:asked"])

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
            jobs, {"job:1": {"id": "job:1", "title": "Python", "company": "A", "description_clean": "Text"}}
        )


if __name__ == "__main__":
    unittest.main()
