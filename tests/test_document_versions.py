"""Immutable document references and upload rollback with only fake Blob storage."""

import base64
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from azure.storage.blob import ContainerClient
from document_store_helpers import FakeVersionedContainer, OfflineUploadTransport

from job_finder.persistence import document_store
from job_finder.persistence.application_documents import (
    public_documents,
    read_document,
    resolve_document_key,
    store_documents,
)


def upload(kind, name, content):
    return {"kind": kind, "name": name, "content": base64.b64encode(content).decode("ascii")}


class DocumentVersionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        backend = patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="local")
        backend.start()
        self.addCleanup(backend.stop)

    def test_local_create_only_preserves_existing_file(self):
        key = "synthetic/cv.pdf"
        self.assertIsNone(document_store.write(key, b"existing", self.root, overwrite=False))
        with self.assertRaises(FileExistsError):
            document_store.write(key, b"replacement", self.root, overwrite=False)
        self.assertEqual(document_store.read(key, self.root), b"existing")

    def test_a_local_key_never_leads_outside_the_documents_folder(self):
        outside = self.root.parent / f"{self.root.name}-outside.pdf"
        outside.write_bytes(b"not a document")
        self.addCleanup(outside.unlink, missing_ok=True)
        keys = (f"../{outside.name}", str(outside), "folder/../../x.pdf", "..")
        for key in keys:
            with self.subTest(key=key):
                for action in (
                    lambda: document_store.write(key, b"x", self.root),
                    lambda: document_store.read(key, self.root),
                    lambda: document_store.exists(key, self.root),
                    lambda: document_store.delete(key, self.root),
                ):
                    with self.assertRaisesRegex(ValueError, "Dokumentschlüssel"):
                        action()
        self.assertEqual(outside.read_bytes(), b"not a document")

    def test_repeated_uploads_for_same_job_keep_distinct_keys_and_original_names(self):
        first = store_documents(
            "synthetic:job", [upload("resume", "CV.pdf", b"earlier")], self.root, company="Example", title="Developer"
        )[0]
        second = store_documents(
            "synthetic:job", [upload("resume", "CV.pdf", b"later")], self.root, company="Example", title="Developer"
        )[0]
        first_key = resolve_document_key("synthetic:job", first)
        second_key = resolve_document_key("synthetic:job", second)
        self.assertNotEqual(first_key, second_key)
        for metadata in (first, second):
            self.assertEqual(metadata["name"], "CV.pdf")
            self.assertEqual(metadata["stored_name"], "CV.pdf")
            self.assertTrue(metadata["folder_name"].startswith("Example - Developer ["))
        self.assertEqual(read_document("synthetic:job", first, self.root), b"earlier")
        self.assertEqual(read_document("synthetic:job", second, self.root), b"later")

    def test_pinned_blob_read_uses_earlier_bytes_after_overwrite(self):
        container = FakeVersionedContainer()
        with (
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=container),
        ):
            metadata = store_documents("synthetic:job", [upload("resume", "CV.pdf", b"earlier")], self.root)[0]
            key = resolve_document_key("synthetic:job", metadata)
            original_version = metadata["blob_version_id"]
            self.assertEqual(metadata["sha256"], hashlib.sha256(b"earlier").hexdigest())
            self.assertFalse(container.uploads[0][2])
            later_version = document_store.write(key, b"later", self.root)
            self.assertNotEqual(original_version, later_version)
            self.assertEqual(document_store.read(key, self.root), b"later")
            self.assertEqual(read_document("synthetic:job", metadata, self.root), b"earlier")
            self.assertEqual(container.downloads[-1], (key, original_version))

    def test_blob_write_uses_blob_client_receipt_instead_of_container_upload_return_value(self):
        container = FakeVersionedContainer()
        uploaded_client = container.upload_blob("synthetic/earlier.pdf", b"earlier")
        self.assertTrue(callable(uploaded_client.upload_blob))
        self.assertFalse(isinstance(uploaded_client, dict))
        with (
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=container),
            patch.object(container, "upload_blob", side_effect=AssertionError("Container uploads return a BlobClient")),
        ):
            version = document_store.write("synthetic/new.pdf", b"new", self.root, overwrite=False)
        self.assertEqual(container.versions["synthetic/new.pdf", version], b"new")

    def test_real_sdk_create_only_upload_extracts_version_and_sends_if_none_match_without_network(self):
        transport = OfflineUploadTransport()
        with (
            ContainerClient(
                "https://offline.invalid", "synthetic-documents", credential=None, transport=transport, retry_total=0
            ) as container,
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=container),
        ):
            version = document_store.write("synthetic/CV.pdf", b"synthetic document", self.root, overwrite=False)
        self.assertEqual(version, "2026-10-05T10:00:00.0000000Z")
        self.assertEqual(len(transport.requests), 1)
        request = transport.requests[0]
        self.assertEqual(request.method, "PUT")
        self.assertEqual(request.headers["If-None-Match"], "*")
        self.assertEqual(request.body, b"synthetic document")

    def test_missing_pinned_blob_version_does_not_fall_back_to_matching_current_bytes(self):
        container = FakeVersionedContainer()
        metadata = {"stored_name": "CV.pdf", "blob_version_id": "absent", "sha256": hashlib.sha256(b"same").hexdigest()}
        key = resolve_document_key("synthetic:job", metadata)
        container.upload_blob(key, b"same")
        with (
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=container),
            self.assertRaises(FileNotFoundError),
        ):
            read_document("synthetic:job", metadata, self.root)
        self.assertEqual(container.downloads, [(key, "absent")])

    def test_pinned_reference_without_hash_still_uses_selected_version(self):
        container = FakeVersionedContainer()
        metadata = {"stored_name": "CV.pdf"}
        key = resolve_document_key("synthetic:job", metadata)
        metadata["blob_version_id"] = container.get_blob_client(key).upload_blob(b"earlier")["version_id"]
        container.upload_blob(key, b"later", overwrite=True)
        with (
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=container),
        ):
            self.assertEqual(read_document("synthetic:job", metadata, self.root), b"earlier")

    def test_changed_document_bytes_are_blocked_by_stored_hash(self):
        metadata = store_documents("synthetic:job", [upload("resume", "CV.pdf", b"expected")], self.root)[0]
        key = resolve_document_key("synthetic:job", metadata)
        document_store.write(key, b"changed", self.root)
        with self.assertRaises(ValueError):
            read_document("synthetic:job", metadata, self.root)

    def test_legacy_metadata_is_readable_with_local_and_blob_backend(self):
        metadata = {"id": "legacy", "kind": "resume", "name": "CV.pdf", "stored_name": "CV.pdf"}
        key = resolve_document_key("synthetic:job", metadata)
        document_store.write(key, b"legacy", self.root)
        self.assertEqual(read_document("synthetic:job", metadata, self.root), b"legacy")
        container = FakeVersionedContainer()
        container.upload_blob(key, b"legacy")
        with (
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=container),
        ):
            self.assertEqual(read_document("synthetic:job", metadata, self.root), b"legacy")
        self.assertEqual(container.downloads, [(key, None)])

    def test_public_document_metadata_exposes_neither_blob_version_nor_hash(self):
        metadata = {
            "id": "synthetic",
            "kind": "resume",
            "name": "CV.pdf",
            "stored_name": "CV.pdf",
            "folder_name": "private",
            "blob_version_id": "private-version",
            "sha256": hashlib.sha256(b"private").hexdigest(),
        }
        self.assertEqual(
            public_documents({"application_documents": [metadata]}),
            [{"id": "synthetic", "kind": "resume", "name": "CV.pdf"}],
        )

    def test_second_upload_failure_cleans_only_this_group_and_preserves_earlier_application(self):
        container = FakeVersionedContainer()
        with (
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=container),
        ):
            earlier = store_documents("synthetic:job", [upload("resume", "CV.pdf", b"earlier")], self.root)[0]
            earlier_key = resolve_document_key("synthetic:job", earlier)
            original_upload = container._store_upload
            calls = 0

            def fail_second_upload(key, content, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("Synthetic second upload failure")
                return original_upload(key, content, **kwargs)

            with patch.object(container, "_store_upload", side_effect=fail_second_upload), self.assertRaises(OSError):
                store_documents(
                    "synthetic:job",
                    [upload("resume", "CV.pdf", b"later"), upload("cover_letter", "Letter.pdf", b"letter")],
                    self.root,
                )
            self.assertEqual(set(container.current), {earlier_key})
            self.assertEqual(read_document("synthetic:job", earlier, self.root), b"earlier")

    def test_missing_upload_version_is_refused_and_successful_create_only_write_is_cleaned(self):
        container = FakeVersionedContainer()
        upload_blob = container._store_upload

        def without_version(*args, **kwargs):
            result = upload_blob(*args, **kwargs)
            result.pop("version_id")
            return result

        with (
            patch.dict(os.environ, JOBFINDER_DOCUMENTS_BACKEND="blob"),
            patch.object(document_store, "_blob_container", return_value=container),
            patch.object(container, "_store_upload", side_effect=without_version),
            self.assertRaisesRegex(RuntimeError, "Blob-Versionierung"),
        ):
            store_documents("synthetic:job", [upload("resume", "CV.pdf", b"document")], self.root)
        self.assertEqual(container.current, {})
        self.assertEqual(len(container.deletions), 1)
