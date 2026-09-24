"""Tests for locally archived application documents."""

import base64
import contextlib
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from job_finder.persistence.application_documents import (
    document_path,
    public_documents,
    resolve_document_key,
    store_documents,
)

LEGACY_FOLDER = hashlib.sha256(b"job:1").hexdigest()[:20]


class ApplicationDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_document_keeps_its_name_in_a_readable_application_folder(self):
        content = b"%PDF-1.7 test"

        documents = store_documents(
            "portal:job/123",
            [
                {
                    "kind": "cover_letter",
                    "name": "../Anschreiben.pdf",
                    "content": base64.b64encode(content).decode("ascii"),
                }
            ],
            self.directory,
            company="Example GmbH",
            title="Junior Python Developer (m/w/d)",
        )

        path = document_path("portal:job/123", documents[0], self.directory)
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(documents[0]["name"], "Anschreiben.pdf")
        self.assertEqual(path.name, "Anschreiben.pdf")
        self.assertTrue(
            path.parent.name.startswith("Example GmbH - Junior Python Developer (m_w_d) [")
        )

    def test_equal_titles_for_different_jobs_use_distinct_folders(self):
        payload = [
            {
                "kind": "resume",
                "name": "Lebenslauf.pdf",
                "content": base64.b64encode(b"first").decode("ascii"),
            }
        ]
        first = store_documents(
            "source:1", payload, self.directory, company="Example", title="Developer"
        )
        payload[0]["content"] = base64.b64encode(b"second").decode("ascii")
        second = store_documents(
            "source:2", payload, self.directory, company="Example", title="Developer"
        )

        first_path = document_path("source:1", first[0], self.directory)
        second_path = document_path("source:2", second[0], self.directory)
        self.assertNotEqual(first_path.parent, second_path.parent)
        self.assertEqual(first_path.read_bytes(), b"first")
        self.assertEqual(second_path.read_bytes(), b"second")

    def test_public_metadata_does_not_expose_storage_name(self):
        documents = store_documents(
            "job:1",
            [
                {
                    "kind": "resume",
                    "name": "Lebenslauf.docx",
                    "content": base64.b64encode(b"docx").decode("ascii"),
                }
            ],
            self.directory,
        )

        public = public_documents({"application_documents": documents})

        self.assertEqual(public[0]["kind"], "resume")
        self.assertEqual(public[0]["name"], "Lebenslauf.docx")
        self.assertNotIn("stored_name", public[0])

    def test_unsupported_file_type_is_rejected_without_writing_files(self):
        with self.assertRaisesRegex(ValueError, "Erlaubt sind"):
            store_documents(
                "job:1",
                [
                    {
                        "kind": "resume",
                        "name": "Lebenslauf.exe",
                        "content": base64.b64encode(b"unsafe").decode("ascii"),
                    }
                ],
                self.directory,
            )

        self.assertEqual(list(self.directory.rglob("*")), [])

    def test_named_and_legacy_folders_resolve_to_the_same_key_and_path(self):
        named = {"stored_name": "cv.pdf", "folder_name": "Example GmbH - Dev [abc]"}
        legacy = {"stored_name": "cv.pdf", "folder_name": ""}
        roots = [self.directory, str(self.directory)]
        with contextlib.suppress(ValueError):  # temporary directory on another drive
            roots.append(Path(os.path.relpath(self.directory)))
        for metadata, folder in ((named, "Example GmbH - Dev [abc]"), (legacy, LEGACY_FOLDER)):
            (self.directory / folder).mkdir()
            (self.directory / folder / "cv.pdf").write_bytes(b"%PDF")
            with self.subTest(folder=folder):
                self.assertEqual(resolve_document_key("job:1", metadata), f"{folder}/cv.pdf")
                for root in roots:
                    self.assertEqual(
                        document_path("job:1", metadata, root), Path(root) / folder / "cv.pdf"
                    )

        missing = {"stored_name": "missing.pdf"}
        self.assertEqual(resolve_document_key("job:1", missing), f"{LEGACY_FOLDER}/missing.pdf")
        with self.assertRaisesRegex(FileNotFoundError, "nicht gefunden"):
            document_path("job:1", missing, self.directory)

    def test_unsafe_document_metadata_is_rejected_by_path_and_key(self):
        cases = [
            (None, "nicht gefunden"),
            ("cv.pdf", "nicht gefunden"),
            ({}, "Ungültiger Dokumentpfad"),
            ({"stored_name": ""}, "Ungültiger Dokumentpfad"),
            ({"stored_name": "../cv.pdf"}, "Ungültiger Dokumentpfad"),
            ({"stored_name": "a/cv.pdf"}, "Ungültiger Dokumentpfad"),
        ]
        for folder in ("..", ".", "a/b", "a\b", "bad:name", " padded"):
            cases.append(
                ({"stored_name": "cv.pdf", "folder_name": folder}, "Ungültiger Dokumentordner")
            )
        for metadata, message in cases:
            for name, resolve in (
                ("path", lambda value: document_path("job:1", value, self.directory)),
                ("key", lambda value: resolve_document_key("job:1", value)),
            ):
                with (
                    self.subTest(metadata=metadata, via=name),
                    self.assertRaisesRegex(ValueError, message),
                ):
                    resolve(metadata)

    def test_backslash_in_stored_name_follows_the_platform_path_rules(self):
        metadata = {"stored_name": "a\cv.pdf"}
        if os.sep == "\\":
            with self.assertRaisesRegex(ValueError, "Ungültiger Dokumentpfad"):
                resolve_document_key("job:1", metadata)
        else:
            self.assertEqual(resolve_document_key("job:1", metadata), f"{LEGACY_FOLDER}/a\cv.pdf")
