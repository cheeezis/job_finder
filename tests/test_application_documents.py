"""Tests for locally archived application documents."""

import base64
import tempfile
import unittest
from pathlib import Path

from job_finder.application_documents import (
    document_path,
    public_documents,
    store_documents,
)


class ApplicationDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_document_is_stored_below_opaque_job_directory(self):
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
        )

        path = document_path("portal:job/123", documents[0], self.directory)
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(documents[0]["name"], "Anschreiben.pdf")
        self.assertNotIn("portal", str(path.relative_to(self.directory)))

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


if __name__ == "__main__":
    unittest.main()
