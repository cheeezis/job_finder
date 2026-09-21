"""Database integration tests using only the separately configured test database."""

import base64
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from job_finder.application_documents import store_documents
from job_finder.database import transaction
from job_finder.memory import edit_job, edit_memory, load_memory, save_memory
from job_finder.migration import migrate
from job_finder.models import Job, JobSource
from job_finder.postgres_backup import create_postgres_backup, restore_backup
from job_finder.postgres_store import prune_cache, read_dataset, write_dataset
from job_finder.review_actions import update_review_decision
from job_finder.review_data import load_review_jobs
from job_finder.scoring import LOCAL_PLACES


class PostgresTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        self.clear_database()

    def clear_database(self):
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE job_state,datasets,migration_runs CASCADE")

    def test_typed_state_history_and_unknown_fields_round_trip(self):
        value = {
            "job:1": {
                "title": "Example",
                "workflow_status": "interview",
                "review_note": None,
                "personal_rating": "good",
                "active": True,
                "workflow_history": [
                    {
                        "status": "interview",
                        "occurred_on": "2026-09-18",
                        "scheduled_for": "2026-09-22T14:30",
                        "reason": "test",
                    }
                ],
                "application_documents": [],
                "additional": {"preserve": [1, 2]},
            }
        }
        save_memory(value)
        self.assertEqual(load_memory(), value)
        with transaction() as connection:
            row = connection.execute(
                "SELECT workflow_status,personal_rating,extra FROM job_state"
            ).fetchone()
            self.assertEqual(row[:2], ("interview", "good"))
            self.assertNotIn("workflow_status", row[2])

    def test_snapshot_duplicates_and_all_dataset_shapes_round_trip(self):
        records = [{"id": "same", "title": "a"}, {"id": "same", "title": "b"}]
        samples = {
            "internal/jobs.json": records,
            "output/recommendations.json": {
                "recommendations": [dict(records[0], match_percent=82)]
            },
            "internal/notifications.json": {
                "version": 3,
                "sent": {"x": {"job_id": "x", "sent_at": "2026-09-18"}},
                "pending": {"y": {"attempts": 2, "job": {"title": "pending"}}},
            },
            "internal/test_cache.json": {
                "version": 1,
                "jobs": {"url": {"title": "cache"}},
            },
            "internal/feed_cache.json": {"version": 1, "jobs": [{"id": "feed"}]},
            "internal/checks.json": {
                "version": 1,
                "checks": {"url": {"closed": False}},
            },
        }
        for name, value in samples.items():
            write_dataset(name, value)
            self.assertEqual(read_dataset(name), value)

    def test_related_writes_roll_back_together(self):
        with self.assertRaisesRegex(RuntimeError, "abort"):
            with transaction():
                save_memory({"job:1": {"workflow_status": "new"}})
                write_dataset("internal/jobs.json", [{"id": "job:1"}])
                raise RuntimeError("abort")
        self.assertEqual(load_memory(), {})
        self.assertIsNone(read_dataset("internal/jobs.json"))

    def test_individual_edits_preserve_concurrent_changes(self):
        save_memory(
            {"job:1": {"workflow_status": "new"}, "job:2": {"workflow_status": "new"}}
        )
        finished = threading.Event()
        errors = []

        def other_job():
            try:
                with edit_job("job:2") as entry:
                    entry["review_note"] = "parallel"
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()

        with edit_job("job:1") as entry:
            thread = threading.Thread(target=other_job)
            thread.start()
            self.assertTrue(
                finished.wait(5), "Different jobs must not block each other"
            )
            entry["workflow_status"] = "applied"
        thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(load_memory()["job:2"]["review_note"], "parallel")

    def test_bulk_update_and_review_edit_do_not_lose_changes(self):
        save_memory({"job:1": {"workflow_status": "new"}})
        started, finished = threading.Event(), threading.Event()
        errors = []

        def reviewer():
            started.set()
            try:
                with edit_job("job:1") as entry:
                    entry["workflow_status"] = "applied"
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()

        with edit_memory() as memory:
            thread = threading.Thread(target=reviewer)
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(finished.wait(0.1))
            memory["job:1"]["title"] = "updated by worker"
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(
            load_memory()["job:1"],
            {"title": "updated by worker", "workflow_status": "applied"},
        )

    def test_manual_sources_survive_an_outdated_cache_save(self):
        name = "internal/manual_jobs_cache.json"
        value = {"version": 1, "jobs": {"https://example.test/1": {"id": "manual:1"}}}
        write_dataset(name, value)
        write_dataset(name, {"version": 1, "jobs": {}})
        self.assertEqual(read_dataset(name), value)

    def test_cache_cleanup_keeps_manual_input_and_fresh_cache(self):
        write_dataset(
            "internal/test_cache.json",
            {"version": 1, "jobs": {"old": {"id": "old"}, "fresh": {"id": "fresh"}}},
        )
        write_dataset(
            "internal/manual_jobs_cache.json",
            {"version": 1, "jobs": {"manual": {"id": "manual"}}},
        )
        with transaction() as connection:
            connection.execute(
                "UPDATE source_cache SET stored_at=now()-interval '40 days' WHERE cache_key='old'"
            )
        self.assertEqual(prune_cache(30), 1)
        self.assertEqual(
            list(read_dataset("internal/test_cache.json")["jobs"]), ["fresh"]
        )
        self.assertIn("manual", read_dataset("internal/manual_jobs_cache.json")["jobs"])

    def test_two_worker_runs_and_review_share_persistent_state(self):
        from run_finder import run_pipeline

        job = Job(
            id="test:1",
            title="Junior Python Developer",
            company="Example GmbH",
            locations=[LOCAL_PLACES[0]],
            sources=[JobSource(source="test", url="https://example.test/1")],
            description_raw="Python, keine Berufserfahrung erforderlich.",
            description_clean="Python, keine Berufserfahrung erforderlich.",
        )
        with (
            patch("run_finder.create_backup"),
            patch("run_finder.enrich_candidate_jobs"),
            patch("run_finder.ignore_closed_listings", return_value=[]),
            patch(
                "run_finder.collect_jobs",
                side_effect=lambda sources=None: (
                    [deepcopy(job)],
                    [{"name": "test", "status": "success", "jobs": 1}],
                ),
            ),
            redirect_stdout(io.StringIO()),
        ):
            run_pipeline()
            update_review_decision("test:1", "interesting")
            first_seen = load_memory()["test:1"]["first_seen_at"]
            run_pipeline()
        restored = load_memory()["test:1"]
        self.assertEqual(restored["workflow_status"], "interesting")
        self.assertEqual(restored["first_seen_at"], first_seen)
        self.assertEqual(load_review_jobs()[0]["workflow_status"], "interesting")
        self.assertEqual(len(read_dataset("internal/jobs.json")), 1)

    def test_worker_publication_preserves_manual_import_not_in_snapshot(self):
        from job_finder.paths import JOBS_FILE, RECOMMENDATIONS_JSON
        from job_finder.storage import publish_results, write_json_atomic

        manual_job = {
            "id": "manual:1",
            "sources": [{"source": "manual", "url": "https://example.test/manual"}],
        }
        manual_result = {"id": "manual:1", "source_links": manual_job["sources"]}
        write_dataset("internal/jobs.json", [manual_job])
        write_dataset(
            "output/recommendations.json", {"recommendations": [manual_result]}
        )
        publish_results(
            [],
            {},
            jobs_path=JOBS_FILE,
            writer=lambda _results: write_json_atomic(
                RECOMMENDATIONS_JSON, {"recommendations": []}
            ),
        )
        self.assertEqual(read_dataset("internal/jobs.json"), [manual_job])
        self.assertEqual(
            read_dataset("output/recommendations.json")["recommendations"],
            [manual_result],
        )

    def test_backup_restore_includes_document_bytes_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"%PDF-1.4 test-document"
            metadata = store_documents(
                "job:1",
                [
                    {
                        "kind": "resume",
                        "name": "resume.pdf",
                        "content": base64.b64encode(content).decode(),
                    }
                ],
                root / "documents",
            )
            memory = {
                "job:1": {
                    "workflow_status": "applied",
                    "application_documents": metadata,
                }
            }
            save_memory(memory)
            write_dataset(
                "internal/notifications.json",
                {"version": 3, "sent": {"job:1": {"job_id": "job:1"}}, "pending": {}},
            )
            backup = create_postgres_backup(root / "backups", root / "documents")
            with self.assertRaisesRegex(ValueError, "leere Datenbank"):
                restore_backup(backup, root / "restored")
            self.clear_database()
            result = restore_backup(backup, root / "restored")
            self.assertTrue(result["verified"])
            self.assertEqual(load_memory(), memory)
            self.assertEqual(
                next((root / "restored").rglob("*.pdf")).read_bytes(), content
            )
            self.assertIn("job:1", read_dataset("internal/notifications.json")["sent"])

    def test_legacy_migration_is_verified_repeatable_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "internal").mkdir()
            database = root / "internal" / "job_finder.sqlite3"
            expected = {"job:1": {"workflow_status": "applied", "review_note": "keep"}}
            connection = sqlite3.connect(database)
            connection.executescript(
                "CREATE TABLE metadata(key TEXT,value TEXT); INSERT INTO metadata VALUES ('schema_version','1'); CREATE TABLE job_state(job_id TEXT,payload_json TEXT);"
            )
            connection.execute(
                "INSERT INTO job_state VALUES (?,?)",
                ("job:1", json.dumps(expected["job:1"])),
            )
            connection.commit()
            connection.close()
            (root / "internal" / "jobs.json").write_text(
                '[{"id":"job:1"},{"id":"job:1"}]', encoding="utf-8"
            )
            before = database.read_bytes()
            result = migrate(root)
            self.assertTrue(result["verified"])
            self.assertEqual(load_memory(), expected)
            self.assertTrue(migrate(root)["already_migrated"])
            self.assertEqual(database.read_bytes(), before)
