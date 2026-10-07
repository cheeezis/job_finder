"""Tests for the backlogs logged after a run."""

import os
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from psycopg.types.json import Jsonb

from job_finder.persistence.database import transaction
from job_finder.persistence.fact_sheets import save_aborted, save_fact_sheet
from job_finder.persistence.health import age_hours, backlog


class BacklogTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE datasets, agent_fact_sheets CASCADE")

    def test_pending_messages_and_aborted_sheets_are_counted(self):
        now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
        with transaction() as connection:
            connection.execute("INSERT INTO datasets (name, metadata) VALUES ('test/notifications.json', '{}')")
            for key, state, hours in (("a", "pending", 30), ("b", "pending", 2), ("c", "sent", 90)):
                connection.execute(
                    "INSERT INTO notifications (dataset, notification_key, delivery_state, present, extra) "
                    "VALUES ('test/notifications.json', %s, %s, ARRAY[]::text[], %s)",
                    (key, state, Jsonb({"created_at": (now - timedelta(hours=hours)).isoformat()})),
                )
        save_aborted("job:1", "gpt-5-mini", "unvollständig", Decimal("0.01"), retryable=True)
        save_aborted("job:2", "gpt-5-mini", "Grenze", Decimal("0.01"))
        save_fact_sheet("job:3", "gpt-5-mini", {"fazit": {"stufe": "bewerben"}}, Decimal("0.03"))

        self.assertEqual(
            backlog(now),
            {"outbox_pending": 2, "outbox_oldest_hours": 30.0, "fact_sheets_aborted": 2, "fact_sheets_retryable": 1},
        )

    def test_ages_without_a_zone_count_as_utc_and_none_as_zero(self):
        now = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

        self.assertEqual(age_hours("2026-10-07T09:00:00", now), 3.0)
        self.assertEqual(age_hours(None, now), 0)
