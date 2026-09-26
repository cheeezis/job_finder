"""Tests for lifecycle metadata in the job memory."""

import tempfile
import threading
import unittest
from pathlib import Path

from job_finder.models import Job, JobSource, WorkflowStatus
from job_finder.persistence.database import transaction
from job_finder.workflow.memory import edit_memory, load_memory, save_memory, update_memory


def make_job():
    return Job(
        id="test:123",
        title="Junior Python Developer",
        company="Example GmbH",
        locations=["Fulda"],
        sources=[JobSource(source="test", source_id="123", url="https://example.test/jobs/123")],
        description_raw="Python",
        description_clean="Python",
    )


def listing(job_id, place, *, remote=None):
    """A listing of one job on the portal the ID names."""
    source = job_id.split(":")[0]
    return Job(
        id=job_id,
        title="Junior Python Developer (m/w/d)",
        company="Example GmbH",
        locations=[place],
        sources=[JobSource(source=source, url=f"https://{source}.test/{job_id}")],
        description_raw="Python",
        description_clean="Python",
        remote_percentage=remote,
    )


def remembered(status, place, *, active=True, remote=False):
    """A remembered StepStone listing of the same job; decided unless status is new."""
    entry = {
        "title": "Junior Python Developer",
        "company": "Example GmbH",
        "locations": [place],
        "first_seen_at": "2026-09-01T08:00:00+00:00",
        "last_seen_at": "2026-09-20T08:00:00+00:00",
        "workflow_status": status,
        "source_urls": ["https://stepstone.test/stepstone:1"],
        "source_names": ["stepstone"],
        "missed_runs": 0 if active else 3,
        "active": active,
    }
    if status != "new":
        entry["workflow_history"] = [{"status": status, "occurred_on": "2026-09-20"}]
    if remote:
        entry["fully_remote"] = True
    return entry


class MemoryTests(unittest.TestCase):
    def test_new_job_receives_first_and_last_seen_timestamps(self):
        memory = {}
        job = make_job()

        stats = update_memory([job], memory)

        self.assertEqual(stats, {"new": 1, "known": 0, "inactive": 0, "reactivated": 0})
        self.assertTrue(job.is_new)
        self.assertIsNotNone(job.first_seen_at)
        self.assertEqual(job.first_seen_at, job.last_seen_at)
        self.assertEqual(memory[job.id]["workflow_status"], "new")

    def test_known_job_keeps_first_seen_and_manual_status(self):
        memory = {}
        first_job = make_job()
        update_memory([first_job], memory)
        memory[first_job.id]["workflow_status"] = "interesting"
        memory[first_job.id]["workflow_history"] = [
            {"status": "applied", "occurred_on": "2026-08-01"}
        ]

        known_job = make_job()
        stats = update_memory([known_job], memory)

        self.assertEqual(stats, {"new": 0, "known": 1, "inactive": 0, "reactivated": 0})
        self.assertFalse(known_job.is_new)
        self.assertEqual(known_job.first_seen_at, first_job.first_seen_at)
        self.assertEqual(known_job.workflow_status, WorkflowStatus.INTERESTING)
        self.assertEqual(
            memory[first_job.id]["workflow_history"],
            [{"status": "applied", "occurred_on": "2026-08-01"}],
        )

    def test_changed_known_job_keeps_its_decision_and_is_not_new(self):
        memory = {}
        update_memory([make_job()], memory)
        changed = make_job()
        memory[changed.id]["workflow_status"] = "interesting"
        changed.title = "An updated title"

        update_memory([changed], memory)

        self.assertFalse(changed.is_new)
        self.assertEqual(changed.workflow_status, WorkflowStatus.INTERESTING)

    def test_job_becomes_inactive_after_three_successful_missed_runs(self):
        memory = {}
        update_memory([make_job()], memory)

        first = update_memory([], memory, successful_sources={"test"})
        second = update_memory([], memory, successful_sources={"test"})
        third = update_memory([], memory, successful_sources={"test"})

        self.assertEqual(first["inactive"], 0)
        self.assertEqual(second["inactive"], 0)
        self.assertEqual(third["inactive"], 1)
        self.assertFalse(memory["test:123"]["active"])
        self.assertEqual(memory["test:123"]["missed_runs"], 3)

    def test_failed_source_does_not_count_as_a_missed_run(self):
        memory = {}
        update_memory([make_job()], memory)

        update_memory([], memory, successful_sources={"other"})

        self.assertEqual(memory["test:123"]["missed_runs"], 0)
        self.assertTrue(memory["test:123"]["active"])

    def test_returning_job_is_reactivated_without_losing_status(self):
        memory = {}
        update_memory([make_job()], memory)
        memory["test:123"].update(
            {"active": False, "missed_runs": 3, "workflow_status": "interesting"}
        )

        job = make_job()
        stats = update_memory([job], memory, successful_sources={"test"})

        self.assertEqual(stats["reactivated"], 1)
        self.assertTrue(memory["test:123"]["active"])
        self.assertEqual(memory["test:123"]["missed_runs"], 0)
        self.assertEqual(job.workflow_status, WorkflowStatus.INTERESTING)

    def test_known_source_url_reuses_reviewed_canonical_job(self):
        job = make_job()
        old_id = "stepstone:456"
        memory = {
            old_id: {
                "title": job.title,
                "company": job.company,
                "first_seen_at": "2026-07-01T08:00:00+00:00",
                "last_seen_at": "2026-08-01T08:00:00+00:00",
                "workflow_status": "applied",
                "workflow_history": [{"status": "applied", "occurred_on": "2026-08-01"}],
                "source_urls": ["https://stepstone.test/jobs/456", job.primary_url],
                "source_names": ["stepstone", "test"],
                "missed_runs": 2,
                "active": True,
            },
            job.id: {
                "title": job.title,
                "company": job.company,
                "first_seen_at": "2026-08-10T08:00:00+00:00",
                "last_seen_at": "2026-08-10T08:00:00+00:00",
                "workflow_status": "new",
                "source_urls": [job.primary_url],
                "source_names": ["test"],
                "missed_runs": 0,
                "active": True,
            },
        }

        stats = update_memory([job], memory, successful_sources={"test"})

        self.assertEqual(stats, {"new": 0, "known": 1, "inactive": 0, "reactivated": 0})
        self.assertEqual(job.id, old_id)
        self.assertEqual(job.workflow_status, WorkflowStatus.APPLIED)
        self.assertNotIn("test:123", memory)
        self.assertEqual(
            memory[old_id]["workflow_history"], [{"status": "applied", "occurred_on": "2026-08-01"}]
        )
        self.assertEqual(
            memory[old_id]["source_urls"], ["https://stepstone.test/jobs/456", job.primary_url]
        )

    def test_application_wins_over_conflicting_review_entry(self):
        job = make_job()
        memory = {
            "stepstone:456": {
                "first_seen_at": "2026-07-01T08:00:00+00:00",
                "last_seen_at": "2026-08-01T08:00:00+00:00",
                "workflow_status": "applied",
                "source_urls": [job.primary_url],
                "source_names": ["stepstone"],
                "missed_runs": 0,
                "active": True,
            },
            job.id: {
                "first_seen_at": "2026-08-10T08:00:00+00:00",
                "last_seen_at": "2026-08-10T08:00:00+00:00",
                "workflow_status": "ignored",
                "source_urls": [job.primary_url],
                "source_names": ["test"],
                "missed_runs": 0,
                "active": True,
            },
        }

        update_memory([job], memory)

        self.assertEqual(job.id, "stepstone:456")
        self.assertEqual(job.workflow_status, WorkflowStatus.APPLIED)
        self.assertIn("stepstone:456", memory)
        self.assertIn("test:123", memory)

    def test_older_review_decision_wins_over_later_duplicate_decision(self):
        job = make_job()
        older_id = "remotely:older"
        job.sources.append(JobSource(source="remotely", url="https://remotely.test/jobs/older"))
        memory = {
            older_id: {
                "first_seen_at": "2026-09-04T08:00:00+00:00",
                "last_seen_at": "2026-09-08T08:00:00+00:00",
                "workflow_status": "inquiry",
                "source_urls": ["https://remotely.test/jobs/older"],
                "source_names": ["remotely"],
                "missed_runs": 0,
                "active": True,
            },
            job.id: {
                "first_seen_at": "2026-09-08T10:00:00+00:00",
                "last_seen_at": "2026-09-08T10:00:00+00:00",
                "workflow_status": "ignored",
                "source_urls": [job.primary_url],
                "source_names": ["test"],
                "missed_runs": 0,
                "active": True,
            },
        }

        update_memory([job], memory)

        self.assertEqual(job.id, older_id)
        self.assertEqual(job.workflow_status, WorkflowStatus.INQUIRY)

    def test_republished_job_with_new_url_reuses_ignored_decision(self):
        job = make_job()
        old_id = "stepstone:old"
        memory = {
            old_id: {
                "title": "Junior Python Developer (m/w/d)",
                "company": "Example GmbH & Co. KG",
                "locations": list(job.locations),
                "first_seen_at": "2026-07-01T08:00:00+00:00",
                "last_seen_at": "2026-07-10T08:00:00+00:00",
                "workflow_status": "ignored",
                "source_urls": ["https://stepstone.test/jobs/old"],
                "source_names": ["stepstone"],
                "missed_runs": 3,
                "active": False,
            }
        }

        stats = update_memory([job], memory, successful_sources={"test"})

        self.assertEqual(stats["known"], 1)
        self.assertEqual(stats["new"], 0)
        self.assertEqual(job.id, old_id)
        self.assertEqual(job.workflow_status, WorkflowStatus.IGNORED)
        self.assertEqual(
            memory[old_id]["source_urls"], ["https://stepstone.test/jobs/old", job.primary_url]
        )

    def test_existing_new_repost_is_folded_into_earlier_application(self):
        job = make_job()
        memory = {
            "stepstone:applied": {
                "title": job.title,
                "company": job.company,
                "locations": list(job.locations),
                "first_seen_at": "2026-07-01T08:00:00+00:00",
                "last_seen_at": "2026-07-10T08:00:00+00:00",
                "workflow_status": "rejected",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-07-03"},
                    {"status": "rejected", "occurred_on": "2026-07-10"},
                ],
                "source_urls": ["https://stepstone.test/jobs/applied"],
                "source_names": ["stepstone"],
                "missed_runs": 3,
                "active": False,
            },
            job.id: {
                "title": job.title,
                "company": job.company,
                "first_seen_at": "2026-08-20T08:00:00+00:00",
                "last_seen_at": "2026-08-20T08:00:00+00:00",
                "workflow_status": "new",
                "source_urls": [job.primary_url],
                "source_names": ["test"],
                "missed_runs": 0,
                "active": True,
            },
        }

        update_memory([job], memory)

        self.assertEqual(job.id, "stepstone:applied")
        self.assertEqual(job.workflow_status, WorkflowStatus.REJECTED)
        self.assertNotIn("test:123", memory)
        self.assertEqual(len(memory["stepstone:applied"]["workflow_history"]), 2)

    def test_existing_manual_decision_is_not_replaced_by_repost_matching(self):
        job = make_job()
        memory = {
            "stepstone:ignored": {
                "title": job.title,
                "company": job.company,
                "workflow_status": "ignored",
                "source_urls": ["https://stepstone.test/jobs/ignored"],
                "source_names": ["stepstone"],
            },
            job.id: {
                "title": job.title,
                "company": job.company,
                "first_seen_at": "2026-08-20T08:00:00+00:00",
                "last_seen_at": "2026-08-20T08:00:00+00:00",
                "workflow_status": "interesting",
                "source_urls": [job.primary_url],
                "source_names": ["test"],
                "missed_runs": 0,
                "active": True,
            },
        }

        update_memory([job], memory)

        self.assertEqual(job.id, "test:123")
        self.assertEqual(job.workflow_status, WorkflowStatus.INTERESTING)
        self.assertIn("stepstone:ignored", memory)

    def test_same_title_at_another_company_remains_new(self):
        job = make_job()
        memory = {
            "stepstone:ignored": {
                "title": job.title,
                "company": "Another GmbH",
                "workflow_status": "ignored",
                "source_urls": ["https://stepstone.test/jobs/ignored"],
                "source_names": ["stepstone"],
            }
        }

        stats = update_memory([job], memory)

        self.assertEqual(stats["new"], 1)
        self.assertEqual(job.id, "test:123")
        self.assertEqual(job.workflow_status, WorkflowStatus.NEW)

    def test_listings_from_any_portal_and_city_share_one_new_entry(self):
        memory = {}
        fulda, berlin = listing("arbeitnow:1", "Fulda"), listing("stepstone:1", "Berlin")

        stats = update_memory([fulda, berlin], memory)

        self.assertEqual((fulda.id, berlin.id), ("arbeitnow:1", "arbeitnow:1"))
        self.assertEqual(list(memory), ["arbeitnow:1"])
        self.assertEqual(memory["arbeitnow:1"]["locations"], ["Fulda", "Berlin"])
        self.assertEqual(memory["arbeitnow:1"]["source_names"], ["arbeitnow", "stepstone"])
        self.assertEqual((stats["new"], stats["known"]), (1, 1))
        self.assertTrue(fulda.is_new)

    def test_a_copy_from_the_other_run_keeps_the_decision(self):
        # As IT Studio Rech on 25.09.2026: interesting on one portal, then found again on Remotely.
        memory = {"stepstone:1": remembered("interesting", "Würzburg")}
        copy = listing("remotely:1", "Würzburg")

        stats = update_memory([copy], memory)

        self.assertEqual(copy.id, "stepstone:1")
        self.assertEqual(copy.workflow_status, WorkflowStatus.INTERESTING)
        self.assertFalse(copy.is_new)
        self.assertEqual(stats["new"], 0)
        self.assertIn("https://remotely.test/remotely:1", memory["stepstone:1"]["source_urls"])

    def test_a_declined_job_in_a_new_city_comes_back_as_its_own_card(self):
        memory = {"stepstone:1": remembered("ignored", "Stuttgart")}
        fulda = listing("arbeitnow:1", "Fulda")

        update_memory([fulda], memory)

        self.assertEqual(fulda.id, "arbeitnow:1")
        self.assertEqual(fulda.workflow_status, WorkflowStatus.NEW)
        self.assertEqual(memory["stepstone:1"]["locations"], ["Stuttgart"])

    def test_fully_remote_listings_share_a_decision_whatever_place_they_name(self):
        # As WattFox: "Germany" on one portal, the company's town on the other.
        memory = {"stepstone:1": remembered("interesting", "Germany", remote=True)}
        copy = listing("remotely:1", "Freiburg im Breisgau", remote=100)

        update_memory([copy], memory)

        self.assertEqual(copy.id, "stepstone:1")

    def test_a_gone_job_posted_again_is_new_unless_it_was_declined(self):
        for status, expected_id in (("interesting", "arbeitnow:1"), ("ignored", "stepstone:1")):
            with self.subTest(status=status):
                memory = {"stepstone:1": remembered(status, "Fulda", active=False)}
                repost = listing("arbeitnow:1", "Fulda")

                update_memory([repost], memory)

                self.assertEqual(repost.id, expected_id)

    def test_a_decision_never_takes_over_the_cities_of_an_undecided_card(self):
        # The undecided card also names Fulda; its declined twin was only in Stuttgart.
        memory = {
            "stepstone:1": remembered("ignored", "Stuttgart"),
            "arbeitnow:1": {
                **remembered("new", "Fulda"),
                "locations": ["Fulda", "Stuttgart"],
                "source_urls": ["https://arbeitnow.test/arbeitnow:1"],
                "source_names": ["arbeitnow"],
            },
        }
        stuttgart = listing("arbeitnow:1", "Stuttgart")

        update_memory([stuttgart], memory)

        self.assertEqual(stuttgart.id, "arbeitnow:1")
        self.assertEqual(stuttgart.workflow_status, WorkflowStatus.NEW)
        self.assertIn("stepstone:1", memory)

    def test_memory_database_has_an_explicit_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            save_memory({"test:123": {"workflow_status": "new"}}, path)
            with transaction() as connection:
                version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
            self.assertEqual(version, 2)
            self.assertIn("test:123", load_memory(path))

    def test_postgres_state_round_trip_and_transactional_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            save_memory({"test:1": {"workflow_status": "new"}}, path)
            with edit_memory(path) as memory:
                memory["test:1"]["workflow_status"] = "interesting"

            restored = load_memory(path)

        self.assertEqual(restored["test:1"]["workflow_status"], "interesting")

    def test_failed_postgres_edit_rolls_back_all_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            original = {"job:1": {"workflow_status": "interesting"}}
            save_memory(original, path)
            with self.assertRaises(RuntimeError), edit_memory(path) as memory:
                memory["job:1"]["workflow_status"] = "applied"
                memory["job:2"] = {"workflow_status": "ignored"}
                raise RuntimeError("abort")
            self.assertEqual(load_memory(path), original)

    def test_concurrent_postgres_edits_preserve_both_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite3"
            save_memory({"job:1": {"workflow_status": "new"}}, path)
            started = threading.Event()
            finished = threading.Event()
            errors = []

            def second_editor():
                started.set()
                try:
                    with edit_memory(path) as memory:
                        memory["job:2"] = {"workflow_status": "ignored"}
                except Exception as error:
                    errors.append(error)
                finally:
                    finished.set()

            with edit_memory(path) as memory:
                memory["job:1"]["workflow_status"] = "applied"
                worker = threading.Thread(target=second_editor)
                worker.start()
                self.assertTrue(started.wait(2))
                self.assertFalse(finished.wait(0.05))
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            restored = load_memory(path)
            self.assertEqual(restored["job:1"]["workflow_status"], "applied")
            self.assertEqual(restored["job:2"]["workflow_status"], "ignored")
