"""An in-memory versioned Blob container; no credentials or network access."""

from types import SimpleNamespace

from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.core.utils import CaseInsensitiveDict


class OfflineUploadResponse(HttpResponse):
    """A synthetic successful Put Blob response consumed by the real SDK."""

    def __init__(self, request):
        super().__init__(request, None)
        self.status_code = 201
        self.reason = "Created"
        self.headers = CaseInsensitiveDict(
            {
                "x-ms-version-id": "2026-10-05T10:00:00.0000000Z",
                "ETag": '"offline-etag"',
                "Last-Modified": "Mon, 05 Oct 2026 10:00:00 GMT",
                "Content-Length": "0",
                "x-ms-request-id": "offline-request",
            }
        )

    def body(self):
        return b""


class OfflineUploadTransport(HttpTransport):
    """Capture SDK requests without a socket, credential, or HTTP library."""

    def __init__(self):
        self.requests = []

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        self.requests.append(request)
        if request.method != "PUT":
            raise AssertionError("Only a synthetic Put Blob request is expected")
        return OfflineUploadResponse(request)

    def sleep(self, duration):
        raise AssertionError("The offline upload must never retry")


class FakeVersionedContainer:
    """Preserve old upload bytes while keeping current blobs separately visible."""

    def __init__(self, *, version_prefix="test-version"):
        self.version_prefix = version_prefix
        self.versions = {}
        self.current = {}
        self.downloads = []
        self.uploads = []
        self.deletions = []
        self._revision = 0

    def upload_blob(self, key, content, *, overwrite=False, **kwargs):
        client = self.get_blob_client(key)
        client.upload_blob(content, overwrite=overwrite, **kwargs)
        return client

    def _store_upload(self, key, content, *, overwrite=False, **kwargs):
        if not overwrite and key in self.current:
            raise ResourceExistsError("Synthetic blob already exists")
        self._revision += 1
        version_id = f"{self.version_prefix}-{self._revision}"
        self.versions[key, version_id] = bytes(content)
        self.current[key] = version_id
        self.uploads.append((key, version_id, overwrite))
        return {"version_id": version_id, "etag": f'"{version_id}"'}

    def download_blob(self, key, *, version_id=None, **kwargs):
        self.downloads.append((key, version_id))
        selected = version_id if version_id is not None else self.current.get(key)
        if (key, selected) not in self.versions:
            raise ResourceNotFoundError("Synthetic blob version is absent")
        content = self.versions[key, selected]
        return SimpleNamespace(readall=lambda: content)

    def get_blob_client(self, key, **kwargs):
        version_id = kwargs.get("version_id")

        def upload_blob(content, **options):
            return self._store_upload(key, content, **options)

        def exists():
            if version_id is not None:
                return (key, version_id) in self.versions
            return key in self.current

        def delete_blob(**conditions):
            selected = version_id if version_id is not None else self.current.get(key)
            if (key, selected) not in self.versions:
                raise ResourceNotFoundError("Synthetic blob version is absent")
            if conditions.get("etag") not in (None, f'"{selected}"'):
                raise ResourceModifiedError("Synthetic blob was changed by another writer")
            self.deletions.append((key, version_id))
            if version_id is not None:
                del self.versions[key, selected]
            if self.current.get(key) == selected:
                del self.current[key]

        return SimpleNamespace(upload_blob=upload_blob, exists=exists, delete_blob=delete_blob, version_id=version_id)

    def list_blobs(self, **kwargs):
        return iter(SimpleNamespace(name=key) for key in sorted(self.current))
