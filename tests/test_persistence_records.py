"""Offline checks for sparse listing rows and unchanged persistence inputs."""

import unittest
from copy import deepcopy
from unittest.mock import MagicMock

from job_finder.persistence import job_listings
from job_finder.persistence.postgres_store import write_memory


class ListingRowsTests(unittest.TestCase):
    def test_sparse_and_asymmetric_lists_keep_original_positions(self):
        cases = (
            ({}, []),
            ({"source_urls": [], "source_names": []}, []),
            (
                {"source_urls": ["a", None, "c"], "source_names": ["first"]},
                [("scope", "job", 0, "a", "first"), ("scope", "job", 2, "c", None)],
            ),
            (
                {"source_urls": [None, "b"], "source_names": ["first", None, "third"]},
                [
                    ("scope", "job", 0, None, "first"),
                    ("scope", "job", 1, "b", None),
                    ("scope", "job", 2, None, "third"),
                ],
            ),
        )
        for entry, expected in cases:
            with self.subTest(entry=entry):
                original = deepcopy(entry)
                self.assertEqual(job_listings.rows("scope", "job", entry), (expected, []))
                self.assertEqual(entry, original)


class MemoryInputTests(unittest.TestCase):
    def entry(self):
        return {
            "workflow_history": [{"status": "applied", "occurred_on": None, "notes": {"lines": ["keep"]}}],
            "application_documents": [{"id": "cv", "name": "CV.pdf", "metadata": {"tags": ["original"]}}],
            "source_urls": ["a", None, "c"],
            "source_names": ["first"],
            "additional": {"values": [1, None]},
        }

    def records(self, connection, table):
        cursor = connection.cursor.return_value.__enter__.return_value
        for statement, records in (call.args for call in cursor.executemany.call_args_list):
            if statement.as_string().startswith(f'INSERT INTO "{table}" '):
                return records
        raise AssertionError(f"No records written for {table}")

    def test_writing_history_documents_and_extras_keeps_input_unchanged(self):
        memory = {"job": self.entry()}
        original = deepcopy(memory)
        connection = MagicMock()

        write_memory(connection, "scope", {}, memory)

        self.assertEqual(memory, original)
        core = self.records(connection, "job_state")[0][-1].obj
        self.assertEqual(core["workflow_history"], [])
        self.assertEqual(core["application_documents"], [])
        self.assertEqual(core["additional"], original["job"]["additional"])
        history = self.records(connection, "workflow_history")[0]
        self.assertIn("occurred_on", history[-2])
        self.assertEqual(history[-1].obj["notes"], {"lines": ["keep"]})
        document = self.records(connection, "application_documents")[0]
        self.assertNotIn("kind", document[-2])
        self.assertEqual(document[-1].obj["metadata"], {"tags": ["original"]})

    def test_invalid_child_leaves_nested_input_unchanged(self):
        for field in ("workflow_history", "application_documents"):
            memory = {"job": self.entry()}
            memory["job"][field].append(None)
            original = deepcopy(memory)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                write_memory(MagicMock(), "scope", {}, memory)
            self.assertEqual(memory, original)

    def test_write_failure_leaves_nested_input_unchanged(self):
        memory = {"job": self.entry()}
        original = deepcopy(memory)
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.executemany.side_effect = RuntimeError("write failed")

        with self.assertRaisesRegex(RuntimeError, "write failed"):
            write_memory(connection, "scope", {}, memory)

        self.assertEqual(memory, original)
