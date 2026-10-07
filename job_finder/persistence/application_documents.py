"""Validated local copies of documents sent with applications."""

import base64
import binascii
import hashlib
import re
import uuid
from pathlib import Path

from job_finder.paths import APPLICATION_DOCUMENTS_DIR
from job_finder.persistence import document_store

ALLOWED_KINDS = {"cover_letter", "resume"}
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".odt"}
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
MAX_FOLDER_NAME_LENGTH = 140
INVALID_WINDOWS_NAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def store_documents(job_id, documents, root=APPLICATION_DOCUMENTS_DIR, *, company="", title=""):
    """Validate and persist at most one document of each supported kind."""
    return write_documents(job_id, prepare_documents(documents), root, company=company, title=title)


def write_documents(job_id, prepared, root=APPLICATION_DOCUMENTS_DIR, *, company="", title=""):
    """Persist validated documents under new keys and return their metadata; remove them again on failure."""
    if not prepared:
        return []
    # A new upload group never reuses an earlier application's keys, so a
    # failed DB commit cannot delete or overwrite its existing documents.
    folder_name = application_folder_name(company, title, f"{job_id}:upload:{uuid.uuid4().hex}")
    written = []
    try:
        for metadata, content in prepared:
            metadata["folder_name"] = folder_name
            key = resolve_document_key(job_id, metadata)
            version = document_store.write(key, content, root, overwrite=False)
            written.append(key)
            metadata["sha256"] = hashlib.sha256(content).hexdigest()
            if version:
                metadata["blob_version_id"] = version
    except Exception:
        for key in written:
            document_store.delete(key, root)
        raise
    return [metadata for metadata, _ in prepared]


def prepare_documents(documents):
    """Validate and decode the entire upload before creating any files.

    Each document is {kind, name, content}: content is the file's bytes from
    an upload, or base64 text as older callers send it.
    """
    if documents is None:
        return []
    if not isinstance(documents, list):
        raise ValueError("Bewerbungsunterlagen müssen eine Liste sein")

    prepared = []
    kinds = set()
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("Ungültige Bewerbungsunterlage")
        kind = str(document.get("kind") or "")
        if kind not in ALLOWED_KINDS or kind in kinds:
            raise ValueError("Anschreiben und Lebenslauf dürfen je einmal vorkommen")
        kinds.add(kind)
        name = safe_original_name(document.get("name"))
        if Path(name).suffix.casefold() not in ALLOWED_EXTENSIONS:
            raise ValueError("Erlaubt sind PDF-, DOC-, DOCX- und ODT-Dateien")
        raw = document.get("content")
        content = checked_content(raw) if isinstance(raw, bytes) else decode_content(raw)
        metadata = {"id": uuid.uuid4().hex, "kind": kind, "name": name, "stored_name": name}
        prepared.append((metadata, content))

    stored_names = [metadata["stored_name"].casefold() for metadata, _ in prepared]
    if len(stored_names) != len(set(stored_names)):
        raise ValueError("Bewerbungsunterlagen müssen unterschiedliche Namen haben")

    return prepared


def resolve_document_key(job_id, metadata):
    """Compute one document's storage key without touching either backend."""
    if not isinstance(metadata, dict):
        raise ValueError("Bewerbungsunterlage wurde nicht gefunden")
    stored_name = str(metadata.get("stored_name") or "")
    if Path(stored_name).name != stored_name or not stored_name:
        raise ValueError("Ungültiger Dokumentpfad")
    directory = document_directory(job_id, metadata.get("folder_name"))
    return (directory / stored_name).as_posix()


def read_document(job_id, metadata, root=APPLICATION_DOCUMENTS_DIR):
    """Read the referenced version and check its contents; legacy metadata remains readable."""
    key = resolve_document_key(job_id, metadata)
    version = metadata.get("blob_version_id")
    if version is not None and (not isinstance(version, str) or not version):
        raise ValueError("Ungültige Dokumentversion")
    content = document_store.read(key, root, version_id=version)
    expected = metadata.get("sha256")
    if expected is not None and expected != hashlib.sha256(content).hexdigest():
        raise ValueError("Dokument-Prüfsumme stimmt nicht überein")
    return content


def live_document_manifest(memory, root=APPLICATION_DOCUMENTS_DIR):
    """Verify each document reference; ZIP v1 cannot represent conflicting bytes at one key."""
    manifest = {}
    for job_id, entry in memory.items():
        for metadata in entry.get("application_documents", []):
            key = resolve_document_key(job_id, metadata)
            digest = hashlib.sha256(read_document(job_id, metadata, root)).hexdigest()
            if key in manifest and manifest[key] != digest:
                raise ValueError("Backup benötigt unterschiedliche Dokumentversionen am selben Speicherpfad.")
            manifest[key] = digest
    return manifest


def public_documents(entry):
    """Return document metadata without exposing local storage names or paths."""
    documents = entry.get("application_documents", [])
    if not isinstance(documents, list):
        return []
    return [
        {"id": document.get("id"), "kind": document.get("kind"), "name": document.get("name")}
        for document in documents
        if isinstance(document, dict)
        and document.get("id")
        and document.get("kind") in ALLOWED_KINDS
        and document.get("name")
    ]


def find_document(entry, document_id):
    """Find stored metadata belonging to one application entry."""
    for document in entry.get("application_documents", []):
        if isinstance(document, dict) and document.get("id") == document_id:
            return document
    raise KeyError("Bewerbungsunterlage wurde nicht gefunden")


def remove_documents(job_id, documents, root=APPLICATION_DOCUMENTS_DIR):
    """Clean up stored documents if saving their matching memory entry fails."""
    for document in documents:
        stored_name = str(document.get("stored_name") or "")
        if stored_name and Path(stored_name).name == stored_name:
            document_store.delete(resolve_document_key(job_id, document), root)


def orphaned_documents(memory, root=APPLICATION_DOCUMENTS_DIR, *, older_than, now):
    """Return stored keys no remembered job refers to and older than the given age.

    An upload is written before its database commit; a crash in between
    leaves such a key. The age keeps an upload that is still being saved.
    """
    referenced = {
        resolve_document_key(job_id, metadata)
        for job_id, entry in memory.items()
        for metadata in entry.get("application_documents", [])
    }
    return sorted(
        key for key, modified in document_store.list_keys(root) if key not in referenced and modified < now - older_than
    )


def document_directory(job_id, folder_name=None):
    """Resolve readable current folders and legacy opaque folders safely."""
    if folder_name:
        name = str(folder_name)
        if Path(name).name != name or safe_windows_name(name) != name:
            raise ValueError("Ungültiger Dokumentordner")
        return Path(name)
    return Path(hashlib.sha256(str(job_id).encode("utf-8")).hexdigest()[:20])


def application_folder_name(company, title, job_id):
    """Create a readable folder whose stable suffix prevents cross-job collisions."""
    label = " - ".join(value for value in [str(company or "").strip(), str(title or "").strip()] if value)
    identifier = hashlib.sha256(str(job_id).encode("utf-8")).hexdigest()[:12]
    suffix = f" [{identifier}]"
    readable = safe_windows_name(label or "Bewerbung")
    readable = readable[: MAX_FOLDER_NAME_LENGTH - len(suffix)].rstrip(". ")
    return f"{readable}{suffix}"


def safe_windows_name(value):
    """Replace characters that Windows does not allow in file or folder names."""
    name = INVALID_WINDOWS_NAME_CHARACTERS.sub("_", str(value or ""))
    return " ".join(name.split()).strip(". ")


def safe_original_name(value):
    """Keep a display filename but never a user-supplied directory path."""
    name = Path(str(value or "").replace("\\", "/")).name.strip()
    name = safe_windows_name(name)
    if not name or len(name) > 240:
        raise ValueError("Ungültiger Dateiname")
    return name


def decode_content(value):
    """Decode a bounded base64 document payload."""
    if not isinstance(value, str) or not value:
        raise ValueError("Dateiinhalt fehlt")
    try:
        content = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Dateiinhalt ist ungültig") from error
    return checked_content(content)


def checked_content(content):
    """Refuse an empty or oversized document."""
    if not content:
        raise ValueError("Die Datei ist leer")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Eine Datei darf höchstens 15 MB groß sein")
    return content
