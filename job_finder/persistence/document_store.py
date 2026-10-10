"""Document bytes for the live app: local filesystem or Azure Blob Storage.

Selected by JOBFINDER_DOCUMENTS_BACKEND ("local", the default, or "blob").
Local needs no Azure dependency; the Azure SDK is only imported for "blob",
so local development and tests never require Azure credentials.
"""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from job_finder.errors import DocumentAccessError
from job_finder.paths import APPLICATION_DOCUMENTS_DIR

_container_client = None


def _backend():
    return os.environ.get("JOBFINDER_DOCUMENTS_BACKEND", "local")


def _blob_container():
    """Reuse one authenticated client.

    DefaultAzureCredential resolves to the Container App's managed identity
    in Azure, or the developer's az login locally.
    """
    global _container_client
    if _container_client is None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        account = os.environ["JOBFINDER_STORAGE_ACCOUNT"]
        container = os.environ["JOBFINDER_STORAGE_CONTAINER"]
        # A user-assigned identity isn't auto-detected like a system-assigned
        # one; without its client ID, ManagedIdentityCredential can't tell
        # which identity to use and DefaultAzureCredential falls through to
        # every other (failing) credential type.
        client_id = os.environ.get("JOBFINDER_MANAGED_IDENTITY_CLIENT_ID")
        service = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=DefaultAzureCredential(managed_identity_client_id=client_id),
        )
        _container_client = service.get_container_client(container)
    return _container_client


def local_path(key, root=APPLICATION_DOCUMENTS_DIR):
    """Return the file of key inside root; refuse a key that would lead outside it.

    The keys are built from cleaned names already. This second check sits
    where the file is touched, so no later caller can bypass it.
    """
    base = os.path.realpath(root)
    path = os.path.realpath(os.path.join(base, key))
    if not path.startswith(base + os.sep):
        raise DocumentAccessError("Ungültiger Dokumentschlüssel")
    return Path(path)


def write(key, content, root=APPLICATION_DOCUMENTS_DIR, *, overwrite=True):
    """Publish complete bytes and return their Blob version; create-only is optional."""
    if _backend() == "blob":
        client = _blob_container().get_blob_client(key)
        receipt = client.upload_blob(content, overwrite=overwrite)
        version = receipt.get("version_id")
        if not isinstance(version, str) or not version:
            # A successful create-only upload belongs to us, but only remove
            # those exact bytes if the response contains their ETag.
            if not overwrite and receipt.get("etag"):
                from azure.core import MatchConditions

                client.delete_blob(etag=receipt["etag"], match_condition=MatchConditions.IfNotModified)
            raise RuntimeError("Blob-Versionierung fehlt; Dokument wird nicht als Bewerbung gespeichert.")
        return version
    path = local_path(key, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix="jobfinder-", suffix=".tmp", delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(content)
        except BaseException:
            output.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        if overwrite:
            temporary.replace(path)
        else:
            # Publishing a hardlink is atomic and fails if the destination
            # already exists. Never replace a concurrent writer's file.
            path.hardlink_to(temporary)
    finally:
        temporary.unlink(missing_ok=True)
    return None


def read(key, root=APPLICATION_DOCUMENTS_DIR, *, version_id=None):
    """Return the bytes stored under key; FileNotFoundError if absent, either backend."""
    if _backend() == "blob":
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return _blob_container().download_blob(key, version_id=version_id).readall()
        except ResourceNotFoundError as error:
            raise FileNotFoundError(key) from error
    return local_path(key, root).read_bytes()


def exists(key, root=APPLICATION_DOCUMENTS_DIR):
    """Check presence without reading the content."""
    if _backend() == "blob":
        return _blob_container().get_blob_client(key).exists()
    return local_path(key, root).is_file()


def delete(key, root=APPLICATION_DOCUMENTS_DIR, *, prune_empty=False):
    """Delete bytes; optionally prune empty local restore folders below the root."""
    if _backend() == "blob":
        client = _blob_container().get_blob_client(key)
        if client.exists():
            client.delete_blob()
        return
    path = local_path(key, root)
    path.unlink(missing_ok=True)
    if prune_empty:
        directory = path.parent
        boundary = Path(os.path.realpath(root))
        while directory != boundary and boundary in directory.parents:
            try:
                directory.rmdir()
            except OSError:
                # Keep a nonempty or inaccessible directory; never remove another writer's files.
                break
            directory = directory.parent


def list_keys(root=APPLICATION_DOCUMENTS_DIR):
    """Yield (key, last modified in UTC) of every stored document, either backend."""
    if _backend() == "blob":
        for blob in _blob_container().list_blobs():
            yield blob.name, blob.last_modified
        return
    base = Path(os.path.realpath(root))
    if not base.exists():
        return
    for path in base.rglob("*"):
        if path.is_file():
            yield path.relative_to(base).as_posix(), datetime.fromtimestamp(path.stat().st_mtime, UTC)


def is_empty(root=APPLICATION_DOCUMENTS_DIR):
    """Restore requires an empty target so nothing gets silently overwritten."""
    if _backend() == "blob":
        return next(iter(_blob_container().list_blobs()), None) is None
    path = Path(root)
    return not path.exists() or not any(path.iterdir())
