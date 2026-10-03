"""Tests for lifecycle metadata in the job memory."""

import copy
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import date
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
    def test_old_board_names_are_cleared_even_when_the_listing_is_absent(self):
        for company in ("Arbeitsagentur", " join ", "REMOTELY"):
            for status in ("new", "interesting", "applied", "rejected"):
                with self.subTest(company=company, status=status):
                    entry = remembered(status, "Fulda")
                    entry.update(
                        company=company,
                        source_names=["studysmarter"],
                        review_note="Keep my note",
                        application_documents=[{"id": "document:1", "name": "CV.pdf"}],
                    )
                    before = copy.deepcopy(entry)
                    memory = {"studysmarter:old": entry}

                    update_memory([], memory)

                    self.assertEqual(memory, {"studysmarter:old": {**before, "company": ""}})

    def test_an_unnamed_returning_listing_does_not_restore_its_old_board_name(self):
        job = self.unknown_employer_listing()
        entry = remembered("interesting", "Berlin")
        entry.update(title=job.title, company="JOIN", source_names=["studysmarter"], source_urls=[job.primary_url])
        memory = {job.id: entry}
        history = copy.deepcopy(entry["workflow_history"])

        update_memory([job], memory)

        self.assertEqual(job.company, "")
        self.assertEqual(entry["company"], "")
        self.assertEqual(job.workflow_status, WorkflowStatus.INTERESTING)
        self.assertEqual(entry["workflow_history"], history)

    def test_board_cleanup_preserves_real_employers_and_names_from_other_sources(self):
        for job_id, company, names in (
            ("studysmarter:real", "Example GmbH", ["studysmarter"]),
            ("arbeitnow:real", "JOIN", ["arbeitnow"]),
            ("arbeitsagentur:real", "Arbeitsagentur", ["arbeitsagentur"]),
            ("remotely:real", "Remotely", ["remotely"]),
        ):
            with self.subTest(job_id=job_id):
                entry = remembered("applied", "Fulda")
                entry.update(company=company, source_names=names)
                before = copy.deepcopy(entry)
                memory = {job_id: entry}

                update_memory([], memory)

                self.assertEqual(memory, {job_id: before})

    def test_board_cleanup_recovers_the_source_from_legacy_ids(self):
        memory = {"studysmarter:legacy": {"company": "JOIN", "workflow_status": "ignored"}}

        update_memory([], memory)

        self.assertEqual(memory["studysmarter:legacy"], {"company": "", "workflow_status": "ignored"})

    def test_a_returning_listing_can_supply_the_actual_employer_after_cleanup(self):
        job = self.unknown_employer_listing()
        job.company = "Example GmbH"
        entry = remembered("interesting", "Berlin")
        entry.update(title=job.title, company="JOIN", source_names=["studysmarter"], source_urls=[job.primary_url])
        memory = {job.id: entry}

        update_memory([job], memory)

        self.assertEqual(job.company, "Example GmbH")
        self.assertEqual(entry["company"], "Example GmbH")

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
        memory[first_job.id]["workflow_history"] = [{"status": "applied", "occurred_on": "2026-08-01"}]

        known_job = make_job()
        stats = update_memory([known_job], memory)

        self.assertEqual(stats, {"new": 0, "known": 1, "inactive": 0, "reactivated": 0})
        self.assertFalse(known_job.is_new)
        self.assertEqual(known_job.first_seen_at, first_job.first_seen_at)
        self.assertEqual(known_job.workflow_status, WorkflowStatus.INTERESTING)
        self.assertEqual(memory[first_job.id]["workflow_history"], [{"status": "applied", "occurred_on": "2026-08-01"}])

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
        memory["test:123"].update({"active": False, "missed_runs": 3, "workflow_status": "interesting"})

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
        self.assertEqual(memory[old_id]["workflow_history"], [{"status": "applied", "occurred_on": "2026-08-01"}])
        self.assertEqual(memory[old_id]["source_urls"], ["https://stepstone.test/jobs/456", job.primary_url])

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
        self.assertEqual(memory[old_id]["source_urls"], ["https://stepstone.test/jobs/old", job.primary_url])

    def test_existing_new_repost_within_thirty_days_is_folded_into_earlier_application(self):
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
                "first_seen_at": "2026-07-20T08:00:00+00:00",
                "last_seen_at": "2026-07-20T08:00:00+00:00",
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

    def test_a_repost_under_a_group_name_joins_the_application(self):
        # The same portal posted the ad again with a new number and a shorter company name.
        job = Job(
            id="studysmarter:new",
            title="Junior SAP Data Analyst Inhouse (m/w/d)",
            company="EDAG Group",
            locations=["Fulda"],
            sources=[JobSource(source="studysmarter", source_id="new", url="https://studysmarter.test/new")],
            description_raw="SAP",
            description_clean="SAP",
        )
        memory = {
            "studysmarter:old": {
                "title": job.title,
                "company": "EDAG ENGINEERING GROUP",
                "locations": ["Fulda"],
                "first_seen_at": "2026-09-28T08:00:00+00:00",
                "last_seen_at": "2026-10-02T08:00:00+00:00",
                "workflow_status": "applied",
                "workflow_history": [{"status": "applied", "occurred_on": "2026-09-29"}],
                "source_urls": ["https://studysmarter.test/old"],
                "source_names": ["studysmarter"],
                "missed_runs": 0,
                "active": True,
            }
        }

        stats = update_memory([job], memory)

        self.assertEqual(stats["new"], 0)
        self.assertEqual(job.id, "studysmarter:old")
        self.assertEqual(job.workflow_status, WorkflowStatus.APPLIED)

    def unknown_employer_listing(self):
        """A StudySmarter listing that named the board instead of the employer."""
        return Job(
            id="studysmarter:45022765",
            title="Junior Full-Stack Developer Next.js",
            company="",
            locations=["Berlin"],
            sources=[JobSource(source="studysmarter", source_id="45022765", url="https://studysmarter.test/45022765")],
            description_raw="Next.js",
            description_clean="Next.js",
            remote_percentage=100,
        )

    def application(self, status="interview", place="Berlin"):
        return {
            "arbeitnow:next": {
                "title": "Junior Full-Stack Developer Next.js",
                "company": "Linear Service GmbH",
                "locations": [place],
                "fully_remote": True,
                "first_seen_at": "2026-09-28T08:00:00+00:00",
                "last_seen_at": "2026-10-02T08:00:00+00:00",
                "workflow_status": status,
                "workflow_history": [{"status": status, "occurred_on": "2026-09-30"}] if status != "new" else [],
                "source_urls": ["https://arbeitnow.test/next"],
                "source_names": ["arbeitnow"],
                "missed_runs": 0,
                "active": True,
            }
        }

    def test_a_listing_without_employer_joins_the_application_with_its_title_and_place(self):
        job = self.unknown_employer_listing()
        memory = self.application()

        stats = update_memory([job], memory)

        self.assertEqual(stats["new"], 0)
        self.assertEqual(job.id, "arbeitnow:next")
        self.assertEqual(job.workflow_status, WorkflowStatus.INTERVIEW)
        # The employer the other listing named stays.
        self.assertEqual(memory["arbeitnow:next"]["company"], "Linear Service GmbH")
        self.assertEqual(job.company, "Linear Service GmbH")

    def test_a_listing_without_employer_stays_apart_from_other_places_and_undecided_jobs(self):
        # Being remote is not enough without an employer: the city must be one the application knows.
        for memory in (self.application(place="Hamburg"), self.application(status="new")):
            with self.subTest(memory=memory):
                job = self.unknown_employer_listing()

                stats = update_memory([job], memory)

                self.assertEqual(stats["new"], 1)
                self.assertEqual(job.id, "studysmarter:45022765")

    def test_completed_applications_merge_at_thirty_days_but_not_thirty_one(self):
        for company in ("", "Example GmbH"):
            for status in ("rejected", "no_response", "offer", "withdrawn", "closed"):
                for published, merged in ((date(2026, 8, 31), True), (date(2026, 9, 1), False)):
                    with self.subTest(company=company, status=status, published=published):
                        job = self.unknown_employer_listing()
                        job.company = company
                        job.published_at = published
                        memory = self.application(status=status)
                        previous = memory["arbeitnow:next"]
                        previous["company"] = "Example GmbH"
                        previous["first_seen_at"] = "2026-08-01T08:00:00+00:00"
                        original_history = deepcopy(previous["workflow_history"])

                        update_memory([job], memory)

                        self.assertEqual(job.id, "arbeitnow:next" if merged else "studysmarter:45022765")
                        self.assertEqual(job.workflow_status.value, status if merged else "new")
                        self.assertEqual(previous["workflow_history"], original_history)
                        if not merged:
                            self.assertEqual(previous["workflow_status"], status)
                            self.assertEqual(len(memory), 2)

    def test_open_applications_still_take_distant_listings(self):
        for company in ("", "Example GmbH"):
            for status in ("applied", "response", "interview"):
                with self.subTest(company=company, status=status):
                    job = self.unknown_employer_listing()
                    job.company = company
                    job.published_at = date(2026, 10, 1)
                    memory = self.application(status=status)
                    memory["arbeitnow:next"]["company"] = "Example GmbH"
                    memory["arbeitnow:next"]["first_seen_at"] = "2026-08-01T08:00:00+00:00"

                    update_memory([job], memory)

                    self.assertEqual(job.id, "arbeitnow:next")
                    self.assertEqual(job.workflow_status.value, status)

    def test_same_id_or_url_keeps_an_old_completed_application(self):
        for matching in ("id", "url"):
            with self.subTest(matching=matching):
                job = self.unknown_employer_listing()
                job.published_at = date(2026, 10, 1)
                memory = self.application(status="rejected")
                memory["arbeitnow:next"]["first_seen_at"] = "2026-08-01T08:00:00+00:00"
                if matching == "id":
                    job.id = "arbeitnow:next"
                else:
                    job.sources[0].url = memory["arbeitnow:next"]["source_urls"][0]

                update_memory([job], memory)

                self.assertEqual(job.id, "arbeitnow:next")
                self.assertEqual(job.workflow_status, WorkflowStatus.REJECTED)

    def test_completed_application_uses_publication_before_discovery_or_recent_crawl(self):
        job = self.unknown_employer_listing()
        job.published_at = date(2026, 10, 1)
        memory = self.application(status="rejected")
        memory["arbeitnow:next"]["published_at"] = "2026-08-01"

        update_memory([job], memory)

        self.assertEqual(job.id, "studysmarter:45022765")
        self.assertEqual(job.workflow_status, WorkflowStatus.NEW)

    def test_existing_new_listing_uses_its_first_discovery_when_publication_is_missing(self):
        job = self.unknown_employer_listing()
        memory = self.application(status="rejected")
        memory["arbeitnow:next"]["first_seen_at"] = "2026-08-01T08:00:00+00:00"
        memory[job.id] = {
            "title": job.title,
            "company": job.company,
            "locations": list(job.locations),
            "workflow_status": "new",
            "first_seen_at": "2026-09-01T08:00:00+00:00",
            "source_urls": [job.primary_url],
            "source_names": job.source_names,
        }

        update_memory([job], memory)

        self.assertEqual(job.id, "studysmarter:45022765")
        self.assertEqual(job.workflow_status, WorkflowStatus.NEW)
        self.assertIn("arbeitnow:next", memory)

    def test_completed_application_without_a_usable_date_stays_separate(self):
        for first_seen in (None, "invalid-date"):
            with self.subTest(first_seen=first_seen):
                job = self.unknown_employer_listing()
                job.published_at = date(2026, 10, 1)
                memory = self.application(status="rejected")
                memory["arbeitnow:next"]["first_seen_at"] = first_seen

                update_memory([job], memory)

                self.assertEqual(job.id, "studysmarter:45022765")
                self.assertEqual(job.workflow_status, WorkflowStatus.NEW)

    def test_original_publication_is_persisted_without_being_reset_by_later_crawls(self):
        job = make_job()
        job.published_at = date(2026, 8, 1)
        memory = {}
        update_memory([job], memory)
        self.assertEqual(memory[job.id]["published_at"], "2026-08-01")

        job.published_at = date(2026, 10, 1)
        update_memory([job], memory)

        self.assertEqual(memory[job.id]["published_at"], "2026-08-01")

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

    def test_each_run_counts_a_job_of_both_runs_as_missed_by_its_own_sources(self):
        # One job with listings from the Azure run (Arbeitnow) and the local run (Remotely).
        memory = {"arbeitnow:1": {**remembered("new", "Fulda"), "source_names": ["arbeitnow", "remotely"]}}
        local_run = {"stepstone", "remotely"}

        update_memory([], memory, successful_sources={"stepstone"}, run_sources=local_run)
        self.assertEqual(memory["arbeitnow:1"]["missed_runs"], 0)

        for _ in range(3):
            update_memory([], memory, successful_sources=local_run, run_sources=local_run)
        self.assertFalse(memory["arbeitnow:1"]["active"])

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
