"""Document bytes for the live app: local filesystem or Azure Blob Storage.

Selected by JOBFINDER_DOCUMENTS_BACKEND ("local", the default, or "blob").
Local needs no Azure dependency; the Azure SDK is only imported for "blob",
so local development and tests never require Azure credentials.
"""

import os
from pathlib import Path

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
        service = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=DefaultAzureCredential(),
        )
        _container_client = service.get_container_client(container)
    return _container_client


def write(key, content, root=APPLICATION_DOCUMENTS_DIR):
    """Store bytes under key; atomic on the local backend, overwrite on blob."""
    if _backend() == "blob":
        _blob_container().upload_blob(key, content, overwrite=True)
        return
    path = Path(root) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def read(key, root=APPLICATION_DOCUMENTS_DIR):
    """Return the bytes stored under key; FileNotFoundError if absent, either backend."""
    if _backend() == "blob":
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return _blob_container().download_blob(key).readall()
        except ResourceNotFoundError as error:
            raise FileNotFoundError(key) from error
    return (Path(root) / key).read_bytes()


def exists(key, root=APPLICATION_DOCUMENTS_DIR):
    """Check presence without reading the content."""
    if _backend() == "blob":
        return _blob_container().get_blob_client(key).exists()
    return (Path(root) / key).is_file()


def delete(key, root=APPLICATION_DOCUMENTS_DIR):
    """Best-effort delete; a missing key is not an error on either backend."""
    if _backend() == "blob":
        client = _blob_container().get_blob_client(key)
        if client.exists():
            client.delete_blob()
        return
    (Path(root) / key).unlink(missing_ok=True)
