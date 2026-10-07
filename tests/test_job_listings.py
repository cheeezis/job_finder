"""Listings, links and sighting columns derived from the remembered jobs (F17, stage 1)."""

import os
import unittest
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from job_finder.persistence import job_listings
from job_finder.persistence.database import transaction
from job_finder.persistence.postgres_store import read_memory, read_review_memory, write_memory


def entry(**fields):
    return {
        "title": "Junior Python Developer",
        "workflow_status": "new",
        "first_seen_at": "2026-10-01T08:00:00.123456+00:00",
        "last_seen_at": "2026-10-07T06:00:00+00:00",
        **fields,
    }


class JobListingsTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE job_state CASCADE")

    def write(self, memory, before=None):
        with transaction() as connection:
            write_memory(connection, "default", before or {}, memory)

    def rows(self, sql):
        with transaction() as connection:
            return connection.execute(sql).fetchall()

    def test_listings_follow_the_lists_as_written_even_when_their_lengths_differ(self):
        # As in older data: more URLs than names, more names than URLs, no names at all.
        self.write(
            {
                "a": entry(source_urls=["https://x.test/1", "https://x.test/2"], source_names=["stepstone"]),
                "b": entry(source_urls=["https://x.test/3"], source_names=["arbeitnow", "jobicy"]),
                "c": entry(source_urls=["https://x.test/4"], linked_job_ids=["old:1"], locations=["Fulda", "Remote"]),
            }
        )

        self.assertEqual(
            self.rows("SELECT job_id, position, url, source_name FROM job_listings ORDER BY job_id, position"),
            [
                ("a", 0, "https://x.test/1", "stepstone"),
                ("a", 1, "https://x.test/2", None),
                ("b", 0, "https://x.test/3", "arbeitnow"),
                ("b", 1, None, "jobicy"),
                ("c", 0, "https://x.test/4", None),
            ],
        )
        self.assertEqual(self.rows("SELECT job_id, linked_job_id FROM job_links"), [("c", "old:1")])
        first_seen, locations = self.rows("SELECT first_seen_at, locations FROM job_state WHERE job_id = 'c'")[0]
        self.assertEqual(first_seen, datetime(2026, 10, 1, 8, 0, 0, 123456, tzinfo=UTC))
        self.assertEqual(locations, ["Fulda", "Remote"])
        with transaction() as connection:
            self.assertEqual(job_listings.drift(connection), {"listings": 0, "links": 0, "columns": 0})

    def test_a_changed_or_removed_job_updates_its_rows_and_the_memory_reads_as_before(self):
        before = {
            "a": entry(source_urls=["https://x.test/1"], source_names=["stepstone"]),
            "b": entry(source_urls=["https://x.test/2"]),
        }
        self.write(before)
        after = {"a": entry(source_urls=["https://x.test/1", "https://x.test/5"], source_names=["stepstone", "jobicy"])}
        self.write(after, before)

        self.assertEqual(
            self.rows("SELECT job_id, position, url FROM job_listings ORDER BY job_id, position"),
            [("a", 0, "https://x.test/1"), ("a", 1, "https://x.test/5")],
        )
        with transaction() as connection:
            self.assertEqual(read_memory(connection, "default"), after)
            found = read_review_memory(connection, "default", [], ["https://x.test/5"], [], [])
        self.assertEqual(list(found), ["a"])

    def test_drift_from_an_older_writer_is_found_and_repaired(self):
        self.write({"a": entry(source_urls=["https://x.test/1"], source_names=["stepstone"])})
        # An earlier image changes only the JSONB fields.
        with transaction() as connection:
            connection.execute(
                "UPDATE job_state SET extra = extra || %s WHERE job_id = 'a'",
                (Jsonb({"source_urls": ["https://x.test/9"], "last_seen_at": "2026-10-08T06:00:00+00:00"}),),
            )
            self.assertEqual(job_listings.drift(connection), {"listings": 2, "links": 0, "columns": 1})
            job_listings.resync(connection)
            self.assertEqual(job_listings.drift(connection), {"listings": 0, "links": 0, "columns": 0})
            self.assertEqual(connection.execute("SELECT url FROM job_listings").fetchall(), [("https://x.test/9",)])
