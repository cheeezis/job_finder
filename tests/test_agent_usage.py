"""Tests for model prices and the ledger of the agent's model calls."""

import os
import unittest
from datetime import UTC, datetime
from decimal import Decimal

from job_finder.agent.pricing import Usage, call_cost
from job_finder.persistence.agent_usage import record_model_call, spent_today_and_this_month
from job_finder.persistence.database import transaction


class PricingTests(unittest.TestCase):
    def test_cached_input_and_reasoning_are_priced_like_the_bill(self):
        usage = Usage(
            input_tokens=1_000_000,
            cached_input_tokens=500_000,
            output_tokens=100_000,
            reasoning_tokens=40_000,
        )

        # 0.5M fresh input x 0.2147 + 0.5M cached x 0.0215 + 0.1M output x 1.7173;
        # the reasoning tokens are part of the output, not billed twice.
        self.assertEqual(call_cost("gpt-5-mini", usage), Decimal("0.28983"))

    def test_web_searches_are_billed_per_search_on_top_of_the_tokens(self):
        # The real probe of 25.09.2026: one search step, two billed searches.
        usage = Usage(
            input_tokens=8540,
            cached_input_tokens=0,
            output_tokens=210,
            reasoning_tokens=64,
            web_searches=2,
        )

        # (8540 x 0.2147 + 210 x 1.7173) / 1M for the tokens + 2 x 0.0120213.
        self.assertEqual(call_cost("gpt-5-mini", usage), Decimal("0.026236771"))

    def test_unknown_models_and_odd_counts_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Kein Preis"):
            call_cost("gpt-unbekannt", Usage(10, 0, 10))
        for usage in (
            Usage(10, 11, 10),
            Usage(10, 0, 10, 11),
            Usage(-1, 0, 10),
            Usage(10, 0, None),
            Usage(10, 0, 10, 0, -1),
        ):
            with self.subTest(usage=usage), self.assertRaisesRegex(ValueError, "Token"):
                call_cost("gpt-5-mini", usage)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE agent_usage")

    def insert_call(self, called_at, cost_eur):
        with transaction() as connection:
            connection.execute(
                """
                INSERT INTO agent_usage (
                    called_at, job_id, model, input_tokens, cached_input_tokens,
                    output_tokens, reasoning_tokens, cost_eur
                ) VALUES (%s, 'job:old', 'gpt-5-mini', 1, 0, 1, 0, %s)
                """,
                (called_at, cost_eur),
            )

    def test_booked_calls_count_for_today_and_this_month(self):
        self.assertEqual(spent_today_and_this_month(), (0, 0))

        record_model_call("job:1", "gpt-5-mini", Usage(1000, 0, 100), Decimal("0.0004"))
        record_model_call("job:2", "gpt-5-mini", Usage(2000, 500, 200, 50, 2), Decimal("0.0006"))

        self.assertEqual(spent_today_and_this_month(), (Decimal("0.0010"), Decimal("0.0010")))
        with transaction() as connection:
            searches = connection.execute("SELECT sum(web_searches) FROM agent_usage").fetchone()
        self.assertEqual(searches, (2,))

    def test_day_and_month_start_at_midnight_german_time(self):
        # 15.09. 12:00 in Berlin (summer time, UTC+2).
        at = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
        self.insert_call(datetime(2026, 9, 14, 22, 30, tzinfo=UTC), Decimal("0.10"))  # 15.09. 00:30
        self.insert_call(datetime(2026, 9, 14, 21, 30, tzinfo=UTC), Decimal("0.20"))  # 14.09. 23:30
        self.insert_call(datetime(2026, 8, 31, 22, 30, tzinfo=UTC), Decimal("0.80"))  # 01.09. 00:30
        self.insert_call(datetime(2026, 8, 31, 21, 30, tzinfo=UTC), Decimal("0.40"))  # 31.08. 23:30

        self.assertEqual(spent_today_and_this_month(at), (Decimal("0.10"), Decimal("1.10")))


if __name__ == "__main__":
    unittest.main()
