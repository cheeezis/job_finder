"""Validated local copies of documents sent with applications."""

import base64
import binascii
import hashlib
import re
import uuid
from pathlib import Path

from job_finder import document_store
from job_finder.paths import APPLICATION_DOCUMENTS_DIR

ALLOWED_KINDS = {"cover_letter", "resume"}
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".odt"}
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024
MAX_FOLDER_NAME_LENGTH = 140
INVALID_WINDOWS_NAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def store_documents(
    job_id,
    documents,
    root=APPLICATION_DOCUMENTS_DIR,
    *,
    company="",
    title="",
):
    """Validate and persist at most one document of each supported kind."""
    prepared = _prepare_documents(documents)
    if not prepared:
        return []
    folder_name = application_folder_name(company, title, job_id)
    written = []
    try:
        for metadata, content in prepared:
            metadata["folder_name"] = folder_name
            key = resolve_document_key(job_id, metadata)
            document_store.write(key, content, root)
            written.append(key)
    except Exception:
        for key in written:
            document_store.delete(key, root)
        raise
    return [metadata for metadata, _ in prepared]


def _prepare_documents(documents):
    """Validate and decode the entire upload before creating any files."""
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
        original_name = safe_original_name(document.get("name"))
        suffix = Path(original_name).suffix.casefold()
        if suffix not in ALLOWED_EXTENSIONS:
            raise ValueError("Erlaubt sind PDF-, DOC-, DOCX- und ODT-Dateien")
        content = decode_content(document.get("content"))
        identifier = uuid.uuid4().hex
        prepared.append(
            (
                {
                    "id": identifier,
                    "kind": kind,
                    "name": original_name,
                    "stored_name": original_name,
                },
                content,
            )
        )

    stored_names = [metadata["stored_name"].casefold() for metadata, _ in prepared]
    if len(stored_names) != len(set(stored_names)):
        raise ValueError("Bewerbungsunterlagen müssen unterschiedliche Namen haben")

    return prepared


def document_path(job_id, metadata, root=APPLICATION_DOCUMENTS_DIR):
    """Resolve one stored document without accepting a path from the browser."""
    if not isinstance(metadata, dict):
        raise ValueError("Bewerbungsunterlage wurde nicht gefunden")
    stored_name = str(metadata.get("stored_name") or "")
    if Path(stored_name).name != stored_name or not stored_name:
        raise ValueError("Ungültiger Dokumentpfad")
    folder_name = metadata.get("folder_name")
    path = document_directory(job_id, root, folder_name) / stored_name
    if not path.is_file():
        raise FileNotFoundError("Bewerbungsunterlage wurde nicht gefunden")
    return path


def resolve_document_key(job_id, metadata):
    """Compute one document's storage key without touching either backend.

    Shares document_directory()'s folder validation so the key matches
    exactly what document_path() would resolve on the local backend.
    """
    if not isinstance(metadata, dict):
        raise ValueError("Bewerbungsunterlage wurde nicht gefunden")
    stored_name = str(metadata.get("stored_name") or "")
    if Path(stored_name).name != stored_name or not stored_name:
        raise ValueError("Ungültiger Dokumentpfad")
    directory = document_directory(job_id, "", metadata.get("folder_name"))
    return (directory / stored_name).as_posix()


def public_documents(entry):
    """Return document metadata without exposing local storage names or paths."""
    documents = entry.get("application_documents", [])
    if not isinstance(documents, list):
        return []
    return [
        {
            "id": document.get("id"),
            "kind": document.get("kind"),
            "name": document.get("name"),
        }
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


def document_directory(job_id, root=APPLICATION_DOCUMENTS_DIR, folder_name=None):
    """Resolve readable current folders and legacy opaque folders safely."""
    if folder_name:
        name = str(folder_name)
        if Path(name).name != name or safe_windows_name(name) != name:
            raise ValueError("Ungültiger Dokumentordner")
        return Path(root) / name
    identifier = hashlib.sha256(str(job_id).encode("utf-8")).hexdigest()[:20]
    return Path(root) / identifier


def application_folder_name(company, title, job_id):
    """Create a readable folder whose stable suffix prevents cross-job collisions."""
    label = " - ".join(
        value
        for value in [str(company or "").strip(), str(title or "").strip()]
        if value
    )
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
    if not content:
        raise ValueError("Die Datei ist leer")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Eine Datei darf höchstens 15 MB groß sein")
    return content
