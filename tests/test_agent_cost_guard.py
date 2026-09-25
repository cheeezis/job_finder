"""Tests for the cost guard, driven by a fake model instead of real tokens."""

import os
import unittest
from decimal import Decimal
from unittest.mock import call, patch

from job_finder.agent import cost_guard
from job_finder.agent.cost_guard import AgentStopped, CostGuard, JobLimitReached
from job_finder.agent.pricing import Usage, call_cost
from job_finder.agent.settings import agent_settings
from job_finder.persistence.agent_usage import spent_today_and_this_month
from job_finder.persistence.database import transaction

ENABLED = agent_settings({"agent": {"enabled": True}})
# What the fake model reports for one round of a typical agent: about 0.8 cent.
ROUND = Usage(input_tokens=20_000, cached_input_tokens=0, output_tokens=2_000)


def run_fake_agent(guard, job_ids):
    """Call the fake model until the guard says stop; return what happened per job."""
    outcome = []
    for job_id in job_ids:
        try:
            guard.start_job(job_id)
            while True:
                guard.before_model_call()
                guard.after_model_call(ROUND)
        except JobLimitReached:
            outcome.append((job_id, "Stelle abgebrochen"))
        except AgentStopped:
            outcome.append((job_id, "Agent gestoppt"))
            break
    return outcome


class CostGuardTests(unittest.TestCase):
    def setUp(self):
        spent = patch.object(cost_guard, "spent_today_and_this_month", return_value=(0, 0))
        record = patch.object(cost_guard, "record_model_call")
        self.spent = spent.start()
        self.record = record.start()
        self.addCleanup(spent.stop)
        self.addCleanup(record.stop)

    def test_no_call_without_switch_price_or_readable_ledger(self):
        for guard, message in (
            (CostGuard(agent_settings({}), "gpt-5-mini"), "Agent aus: Abschnitt agent fehlt"),
            (CostGuard(ENABLED, "gpt-unbekannt"), "Kein Preis"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(AgentStopped, message):
                guard.start_job("job:1")
        self.spent.assert_not_called()

        self.spent.side_effect = OSError("database down")
        with self.assertRaisesRegex(AgentStopped, "Kostenbuch nicht lesbar"):
            CostGuard(ENABLED, "gpt-5-mini").start_job("job:1")

    def test_daily_and_monthly_money_stop_the_run(self):
        for spent, message in (
            ((Decimal("1.00"), Decimal("3")), "Tagesgrenze erreicht: 1,00 € von 1,00 €"),
            ((Decimal("0.20"), Decimal("20")), "Monatsgrenze erreicht: 20,00 € von 20,00 €"),
        ):
            self.spent.return_value = spent
            with self.subTest(message=message), self.assertRaisesRegex(AgentStopped, message):
                CostGuard(ENABLED, "gpt-5-mini").start_job("job:1")

    def test_call_and_tool_limits_end_only_the_current_job(self):
        guard = CostGuard(ENABLED, "gpt-5-mini")
        guard.start_job("job:1")
        for _ in range(8):
            guard.before_model_call()
        with self.assertRaisesRegex(JobLimitReached, "8 Modellaufrufe"):
            guard.before_model_call()
        for _ in range(6):
            guard.before_tool_call()
        with self.assertRaisesRegex(JobLimitReached, "6 Werkzeugaufrufe"):
            guard.before_tool_call()

        guard.start_job("job:2")
        guard.before_model_call()
        guard.before_tool_call()

    def test_calls_are_booked_until_the_job_money_is_used_up(self):
        settings = agent_settings({"agent": {"enabled": True, "job_max_cost_eur": 0.05}})
        guard = CostGuard(settings, "gpt-5-mini")
        guard.start_job("job:1")
        for _ in range(7):
            guard.before_model_call()
            guard.after_model_call(ROUND)

        # 7 rounds of about 0.77 cent pass the 5 cent limit before the 8 calls are used.
        with self.assertRaisesRegex(JobLimitReached, "von 0,05 € verbraucht"):
            guard.before_model_call()
        cost = call_cost("gpt-5-mini", ROUND)
        self.assertEqual(self.record.call_args_list, [call("job:1", "gpt-5-mini", ROUND, cost)] * 7)

    def test_missing_token_counts_book_the_job_maximum(self):
        guard = CostGuard(ENABLED, "gpt-5-mini")
        guard.start_job("job:1")
        guard.before_model_call()

        with self.assertRaisesRegex(JobLimitReached, "Token-Angaben"):
            guard.after_model_call(None)
        self.record.assert_called_once_with("job:1", "gpt-5-mini", Usage(0, 0, 0), Decimal("0.08"))

    def test_search_budget_closes_the_search_but_not_the_job(self):
        # Enough money per job that only the search budget matters here.
        settings = agent_settings({"agent": {"enabled": True, "job_max_cost_eur": 0.25}})
        guard = CostGuard(settings, "gpt-5-mini")
        guard.start_job("job:1")
        searching = Usage(
            input_tokens=8540, cached_input_tokens=0, output_tokens=210, web_searches=2
        )

        self.assertTrue(guard.search_allowed())
        guard.before_model_call()
        guard.after_model_call(searching)
        self.assertTrue(guard.search_allowed())  # 2 of 3 searches used
        guard.before_model_call()
        guard.after_model_call(searching)

        # 4 searches: the last response overshot the budget of 3; no more search,
        # but the job goes on, and the searches were booked with their price.
        self.assertFalse(guard.search_allowed())
        guard.before_model_call()
        self.assertEqual(self.record.call_args.args[3], call_cost("gpt-5-mini", searching))
        guard.start_job("job:2")
        self.assertTrue(guard.search_allowed())

    def test_a_ledger_that_cannot_be_written_stops_the_run(self):
        self.record.side_effect = OSError("database down")
        guard = CostGuard(ENABLED, "gpt-5-mini")
        guard.start_job("job:1")
        guard.before_model_call()

        with self.assertRaisesRegex(AgentStopped, "Kostenbuch nicht beschreibbar"):
            guard.after_model_call(ROUND)


class CostGuardLedgerTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE agent_usage")

    def test_fake_agent_run_stops_at_the_daily_limit(self):
        settings = agent_settings(
            {"agent": {"enabled": True, "job_max_cost_eur": 0.01, "daily_max_cost_eur": 0.02}}
        )

        outcome = run_fake_agent(CostGuard(settings, "gpt-5-mini"), ["job:1", "job:2", "job:3"])

        # job:1 stops after two rounds (1.5 cent > 1 cent); job:2 gets one more
        # round, then the day (2.3 cent > 2 cent) ends the run; job:3 waits.
        self.assertEqual(outcome, [("job:1", "Stelle abgebrochen"), ("job:2", "Agent gestoppt")])
        self.assertEqual(spent_today_and_this_month()[0], 3 * call_cost("gpt-5-mini", ROUND))


if __name__ == "__main__":
    unittest.main()
