"""Tests for local application history and derived statistics."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from job_finder.applications import load_application_overview
from job_finder.memory import load_memory, save_memory
from job_finder.review import (
    delete_workflow_history,
    update_workflow_history,
    update_workflow_status,
)


class ApplicationTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.memory_path = Path(self.temporary_directory.name) / "seen_jobs.json"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def save_jobs(self, jobs):
        save_memory(jobs, self.memory_path)

    def test_every_manual_status_change_is_kept_with_its_date(self):
        self.save_jobs(
            {
                "job:1": {
                    "title": "IT Consultant",
                    "company": "Example GmbH",
                    "workflow_status": "interesting",
                }
            }
        )

        update_workflow_status("job:1", "applied", self.memory_path, "2026-08-01")
        update_workflow_status("job:1", "response", self.memory_path, "2026-08-04")
        update_workflow_status("job:1", "interview", self.memory_path, "2026-08-10")
        update_workflow_status("job:1", "offer", self.memory_path, "2026-08-20")

        entry = load_memory(self.memory_path)["job:1"]
        self.assertEqual(entry["workflow_status"], "offer")
        self.assertEqual(
            entry["workflow_history"],
            [
                {"status": "interesting", "occurred_on": None},
                {"status": "applied", "occurred_on": "2026-08-01"},
                {"status": "response", "occurred_on": "2026-08-04"},
                {"status": "interview", "occurred_on": "2026-08-10"},
                {"status": "offer", "occurred_on": "2026-08-20"},
            ],
        )

    def test_reselecting_same_status_without_date_does_not_duplicate_it(self):
        self.save_jobs(
            {
                "job:1": {
                    "title": "IT Consultant",
                    "company": "Example GmbH",
                    "workflow_status": "interesting",
                }
            }
        )

        update_workflow_status("job:1", "interesting", self.memory_path)

        self.assertEqual(
            load_memory(self.memory_path)["job:1"].get("workflow_history", []),
            [],
        )

    def test_interview_can_store_one_upcoming_appointment(self):
        self.save_jobs(
            {
                "job:1": {
                    "title": "IT Consultant",
                    "company": "Example GmbH",
                    "workflow_status": "applied",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"}
                    ],
                }
            }
        )

        update_workflow_status(
            "job:1",
            "interview",
            memory_path=self.memory_path,
            occurred_on="2026-08-17",
            scheduled_for="2099-08-21T14:30",
        )
        overview = load_application_overview(self.memory_path)

        self.assertEqual(
            load_memory(self.memory_path)["job:1"]["workflow_history"][-1],
            {
                "status": "interview",
                "occurred_on": "2026-08-17",
                "scheduled_for": "2099-08-21T14:30",
            },
        )
        self.assertEqual(
            overview["applications"][0]["next_interview_at"],
            "2099-08-21T14:30",
        )

    def test_interview_appointment_can_be_edited_and_snapshot_is_checked(self):
        jobs = {
            "job:1": {
                "workflow_status": "interview",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-08-01"},
                    {
                        "status": "interview",
                        "occurred_on": "2026-08-17",
                        "scheduled_for": "2099-08-21T14:30",
                    },
                ],
            }
        }
        self.save_jobs(jobs)

        result = update_workflow_history(
            "job:1",
            1,
            "interview",
            "2026-08-17",
            "interview",
            "2026-08-17",
            memory_path=self.memory_path,
            scheduled_for="2099-08-22T09:15",
            previous_scheduled_for="2099-08-21T14:30",
        )

        self.assertEqual(result["scheduled_for"], "2099-08-22T09:15")
        with self.assertRaisesRegex(ValueError, "zwischenzeitlich geändert"):
            update_workflow_history(
                "job:1",
                1,
                "interview",
                "2026-08-17",
                "interview",
                "2026-08-17",
                memory_path=self.memory_path,
                scheduled_for="2099-08-23T10:00",
                previous_scheduled_for="2099-08-21T14:30",
            )

    def test_appointment_is_rejected_for_non_interview_status(self):
        jobs = {
            "job:1": {
                "workflow_status": "applied",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-08-01"}
                ],
            }
        }
        self.save_jobs(jobs)

        with self.assertRaisesRegex(ValueError, "nur beim Status Gespräch"):
            update_workflow_status(
                "job:1",
                "response",
                memory_path=self.memory_path,
                occurred_on="2026-08-17",
                scheduled_for="2026-08-21T14:30",
            )

        self.assertEqual(load_memory(self.memory_path), jobs)

    def test_invalid_date_does_not_change_current_status(self):
        self.save_jobs(
            {
                "job:1": {
                    "title": "IT Consultant",
                    "company": "Example GmbH",
                    "workflow_status": "interesting",
                }
            }
        )

        with self.assertRaisesRegex(ValueError, "Ungueltiges Datum"):
            update_workflow_status(
                "job:1",
                "applied",
                self.memory_path,
                "11.08.2026",
            )

        self.assertEqual(
            load_memory(self.memory_path)["job:1"]["workflow_status"],
            "interesting",
        )

    def test_legacy_application_remains_visible_without_invented_date(self):
        self.save_jobs(
            {
                "job:legacy": {
                    "title": "Business Analyst",
                    "company": "Example GmbH",
                    "workflow_status": "applied",
                    "active": False,
                    "source_urls": ["https://example.test/job"],
                    "source_names": ["stepstone"],
                }
            }
        )

        overview = load_application_overview(self.memory_path)

        self.assertEqual(overview["statistics"]["total"], 1)
        self.assertEqual(overview["statistics"]["open"], 1)
        application = overview["applications"][0]
        self.assertFalse(application["active"])
        self.assertIsNone(application["applied_on"])
        self.assertEqual(application["workflow_history"], [])
        self.assertEqual(
            application["source_links"],
            [{"source": "stepstone", "url": "https://example.test/job"}],
        )

    def test_application_overview_exposes_only_public_document_metadata(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "applied",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"}
                    ],
                    "application_documents": [
                        {
                            "id": "document-1",
                            "kind": "cover_letter",
                            "name": "Anschreiben.pdf",
                            "stored_name": "private-name.pdf",
                        }
                    ],
                }
            }
        )

        documents = load_application_overview(
            self.memory_path,
            as_of=date(2026, 8, 2),
        )["applications"][0]["documents"]

        self.assertEqual(
            documents,
            [
                {
                    "id": "document-1",
                    "kind": "cover_letter",
                    "name": "Anschreiben.pdf",
                }
            ],
        )

    def test_statistics_use_complete_history_and_response_dates(self):
        self.save_jobs(
            {
                "job:1": {
                    "title": "Consultant",
                    "company": "A GmbH",
                    "workflow_status": "rejected",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"},
                        {"status": "response", "occurred_on": "2026-08-03"},
                        {"status": "interview", "occurred_on": "2026-08-05"},
                        {"status": "rejected", "occurred_on": "2026-08-10"},
                    ],
                },
                "job:2": {
                    "title": "Developer",
                    "company": "B GmbH",
                    "workflow_status": "offer",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-02"},
                        {"status": "offer", "occurred_on": "2026-08-07"},
                    ],
                },
                "job:3": {
                    "title": "Analyst",
                    "company": "C GmbH",
                    "workflow_status": "applied",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-03"},
                    ],
                },
            }
        )

        statistics = load_application_overview(
            self.memory_path,
            as_of=date(2026, 8, 10),
        )["statistics"]

        self.assertEqual(
            statistics,
            {
                "total": 3,
                "open": 1,
                "completed": 2,
                "responses": 2,
                "interviews": 1,
                "rejections": 1,
                "no_responses": 0,
                "offers": 1,
                "response_rate_percent": 100,
                "average_response_days": 3.5,
                "response_time_samples": 2,
            },
        )

    def test_application_overview_exposes_salary_expectation(self):
        self.save_jobs(
            {
                "job:salary": {
                    "workflow_status": "applied",
                    "salary_expectation_eur": 58_000,
                }
            }
        )

        application = load_application_overview(self.memory_path)["applications"][0]

        self.assertEqual(
            application["salary_expectation_eur"],
            58_000,
        )

    def test_application_overview_normalizes_legacy_salary_text(self):
        self.save_jobs(
            {
                "job:salary": {
                    "workflow_status": "applied",
                    "salary_expectation": "58.000 € brutto/Jahr",
                }
            }
        )

        application = load_application_overview(self.memory_path)["applications"][0]

        self.assertEqual(application["salary_expectation_eur"], 58_000)

    def test_response_rate_ignores_open_applications(self):
        self.save_jobs(
            {
                "job:completed": {
                    "workflow_status": "no_response",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"},
                        {"status": "no_response", "occurred_on": "2026-08-20"},
                    ],
                },
                "job:open-with-response": {
                    "workflow_status": "interview",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-02"},
                        {"status": "response", "occurred_on": "2026-08-03"},
                        {"status": "interview", "occurred_on": "2026-08-04"},
                    ],
                },
            }
        )

        statistics = load_application_overview(self.memory_path)["statistics"]

        self.assertEqual(statistics["completed"], 1)
        self.assertEqual(statistics["responses"], 1)
        self.assertEqual(statistics["response_rate_percent"], 0)

    def test_completed_application_leaves_the_default_list(self):
        self.save_jobs(
            {
                "job:1": {
                    "title": "Consultant",
                    "company": "Example GmbH",
                    "workflow_status": "ignored",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"},
                        {"status": "ignored", "occurred_on": "2026-08-02"},
                    ],
                }
            }
        )

        overview = load_application_overview(self.memory_path)

        self.assertEqual(overview["applications"], [])
        self.assertEqual(overview["statistics"]["completed"], 1)
        self.assertEqual(
            overview["completed_applications"][0]["workflow_status"],
            "ignored",
        )

    def test_legacy_application_survives_future_status_changes(self):
        self.save_jobs(
            {
                "job:legacy": {
                    "title": "Consultant",
                    "company": "Example GmbH",
                    "workflow_status": "applied",
                }
            }
        )

        update_workflow_status(
            "job:legacy",
            "response",
            self.memory_path,
            "2026-08-11",
        )
        update_workflow_status(
            "job:legacy",
            "ignored",
            self.memory_path,
            "2026-08-12",
        )
        overview = load_application_overview(self.memory_path)

        self.assertEqual(overview["applications"], [])
        self.assertEqual(
            overview["completed_applications"][0]["workflow_history"][0],
            {"status": "applied", "occurred_on": None, "event_index": 0},
        )

    def test_unknown_legacy_date_does_not_hide_later_application_date(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "response",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": None},
                        {"status": "applied", "occurred_on": "2026-08-01"},
                        {"status": "response", "occurred_on": "2026-08-03"},
                    ],
                }
            }
        )

        application = load_application_overview(self.memory_path)["applications"][0]

        self.assertEqual(application["applied_on"], "2026-08-01")
        self.assertEqual(application["days_to_response"], 2)

    def test_invalid_history_shape_is_ignored(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "applied",
                    "workflow_history": None,
                }
            }
        )

        overview = load_application_overview(self.memory_path)

        self.assertEqual(len(overview["applications"]), 1)
        self.assertEqual(overview["applications"][0]["workflow_history"], [])

    def test_response_before_application_is_not_used_for_duration(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "response",
                    "workflow_history": [
                        {"status": "response", "occurred_on": "2026-08-01"},
                        {"status": "applied", "occurred_on": "2026-08-05"},
                    ],
                }
            }
        )

        overview = load_application_overview(self.memory_path)
        application = overview["applications"][0]

        self.assertIsNone(application["response_on"])
        self.assertIsNone(application["days_to_response"])
        self.assertEqual(overview["statistics"]["response_time_samples"], 0)

    def test_no_response_is_a_manual_terminal_status(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "applied",
                    "active": False,
                }
            }
        )

        before = load_application_overview(self.memory_path)
        update_workflow_status(
            "job:1",
            "no_response",
            self.memory_path,
            "2026-08-20",
        )
        after = load_application_overview(self.memory_path)

        self.assertEqual(before["statistics"]["open"], 1)
        self.assertEqual(before["statistics"]["completed"], 0)
        self.assertEqual(before["statistics"]["no_responses"], 0)
        self.assertEqual(after["statistics"]["open"], 0)
        self.assertEqual(after["statistics"]["completed"], 1)
        self.assertEqual(after["statistics"]["responses"], 0)
        self.assertEqual(after["statistics"]["no_responses"], 1)
        self.assertEqual(after["applications"], [])
        self.assertEqual(
            after["completed_applications"][0]["workflow_status"],
            "no_response",
        )
        self.assertFalse(
            after["completed_applications"][0]["automatic_no_response"]
        )

    def test_no_response_is_derived_after_fourteen_days_without_event(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "applied",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"}
                    ],
                }
            }
        )

        before = load_application_overview(
            self.memory_path,
            as_of=date(2026, 8, 14),
        )
        after = load_application_overview(
            self.memory_path,
            as_of=date(2026, 8, 15),
        )

        self.assertEqual(before["statistics"]["open"], 1)
        self.assertEqual(before["statistics"]["no_responses"], 0)
        self.assertEqual(after["statistics"]["open"], 0)
        self.assertEqual(after["statistics"]["no_responses"], 1)
        application = after["completed_applications"][0]
        self.assertEqual(application["workflow_status"], "no_response")
        self.assertTrue(application["automatic_no_response"])
        self.assertEqual(
            application["workflow_history"],
            [
                {
                    "status": "applied",
                    "occurred_on": "2026-08-01",
                    "event_index": 0,
                }
            ],
        )
        self.assertEqual(
            load_memory(self.memory_path)["job:1"]["workflow_status"],
            "applied",
        )
        self.assertNotIn("no_response", after["application_statuses"])

    def test_response_reopens_automatically_derived_no_response(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "applied",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"}
                    ],
                }
            }
        )

        before = load_application_overview(
            self.memory_path,
            as_of=date(2026, 8, 20),
        )
        update_workflow_status(
            "job:1",
            "response",
            self.memory_path,
            "2026-08-21",
        )
        after = load_application_overview(
            self.memory_path,
            as_of=date(2026, 8, 21),
        )

        self.assertEqual(before["statistics"]["no_responses"], 1)
        self.assertEqual(after["statistics"]["no_responses"], 0)
        self.assertEqual(after["applications"][0]["workflow_status"], "response")

    def test_late_response_reopens_no_response_outcome(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "no_response",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"},
                        {"status": "no_response", "occurred_on": "2026-08-20"},
                    ],
                }
            }
        )

        update_workflow_status(
            "job:1",
            "response",
            self.memory_path,
            "2026-08-25",
        )
        overview = load_application_overview(self.memory_path)

        self.assertEqual(overview["statistics"]["responses"], 1)
        self.assertEqual(overview["statistics"]["no_responses"], 0)
        self.assertEqual(
            overview["applications"][0]["workflow_status"],
            "response",
        )

    def test_history_event_can_be_edited_and_reopens_application(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "no_response",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"},
                        {"status": "no_response", "occurred_on": "2026-08-20"},
                    ],
                }
            }
        )

        result = update_workflow_history(
            "job:1",
            1,
            "no_response",
            "2026-08-20",
            "response",
            "2026-08-25",
            self.memory_path,
        )
        overview = load_application_overview(self.memory_path)

        self.assertEqual(result["workflow_status"], "response")
        self.assertEqual(overview["statistics"]["open"], 1)
        self.assertEqual(overview["statistics"]["completed"], 0)
        self.assertEqual(overview["applications"][0]["response_on"], "2026-08-25")
        self.assertEqual(
            load_memory(self.memory_path)["job:1"]["workflow_history"][1],
            {"status": "response", "occurred_on": "2026-08-25"},
        )

    def test_deleting_final_event_restores_previous_current_status(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "no_response",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"},
                        {"status": "no_response", "occurred_on": "2026-08-20"},
                    ],
                }
            }
        )

        result = delete_workflow_history(
            "job:1",
            1,
            "no_response",
            "2026-08-20",
            self.memory_path,
        )
        overview = load_application_overview(
            self.memory_path,
            as_of=date(2026, 8, 10),
        )

        self.assertEqual(result["workflow_status"], "applied")
        self.assertEqual(overview["statistics"]["open"], 1)
        self.assertEqual(overview["statistics"]["completed"], 0)
        self.assertEqual(
            load_memory(self.memory_path)["job:1"]["workflow_history"],
            [{"status": "applied", "occurred_on": "2026-08-01"}],
        )

    def test_invalid_history_edit_does_not_change_memory(self):
        jobs = {
            "job:1": {
                "workflow_status": "applied",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-08-01"}
                ],
            }
        }
        self.save_jobs(jobs)

        with self.assertRaisesRegex(ValueError, "nicht gefunden"):
            update_workflow_history(
                "job:1",
                4,
                "applied",
                "2026-08-01",
                "response",
                "2026-08-02",
                self.memory_path,
            )

        self.assertEqual(load_memory(self.memory_path), jobs)

    def test_history_date_can_be_changed_to_unknown(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "applied",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"}
                    ],
                }
            }
        )

        update_workflow_history(
            "job:1",
            0,
            "applied",
            "2026-08-01",
            "applied",
            None,
            self.memory_path,
        )

        event = load_memory(self.memory_path)["job:1"]["workflow_history"][0]
        self.assertEqual(event, {"status": "applied", "occurred_on": None})

    def test_stale_history_snapshot_is_rejected_without_mutation(self):
        jobs = {
            "job:1": {
                "workflow_status": "applied",
                "workflow_history": [
                    {"status": "applied", "occurred_on": "2026-08-01"}
                ],
            }
        }
        self.save_jobs(jobs)

        with self.assertRaisesRegex(ValueError, "zwischenzeitlich geändert"):
            delete_workflow_history(
                "job:1",
                0,
                "applied",
                "2026-08-02",
                self.memory_path,
            )

        self.assertEqual(load_memory(self.memory_path), jobs)

    def test_closed_after_no_response_keeps_no_response_outcome(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "closed",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"},
                        {"status": "no_response", "occurred_on": "2026-08-20"},
                        {"status": "closed", "occurred_on": "2026-08-20"},
                    ],
                }
            }
        )

        overview = load_application_overview(self.memory_path)

        self.assertEqual(overview["statistics"]["completed"], 1)
        self.assertEqual(overview["statistics"]["no_responses"], 1)
        self.assertEqual(overview["applications"], [])

    def test_editing_older_event_keeps_latest_status_and_updates_duration(self):
        self.save_jobs(
            {
                "job:1": {
                    "workflow_status": "interview",
                    "workflow_history": [
                        {"status": "applied", "occurred_on": "2026-08-01"},
                        {"status": "response", "occurred_on": "2026-08-04"},
                        {"status": "interview", "occurred_on": "2026-08-10"},
                    ],
                }
            }
        )

        result = update_workflow_history(
            "job:1",
            0,
            "applied",
            "2026-08-01",
            "applied",
            "2026-08-02",
            self.memory_path,
        )
        application = load_application_overview(self.memory_path)["applications"][0]

        self.assertEqual(result["workflow_status"], "interview")
        self.assertEqual(application["applied_on"], "2026-08-02")
        self.assertEqual(application["days_to_response"], 2)


if __name__ == "__main__":
    unittest.main()
