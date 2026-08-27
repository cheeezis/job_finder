"""Validated local copies of documents sent with applications."""

import base64
import binascii
import hashlib
import uuid
from pathlib import Path

from job_finder.paths import APPLICATION_DOCUMENTS_DIR


ALLOWED_KINDS = {"cover_letter", "resume"}
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".odt"}
MAX_DOCUMENT_BYTES = 15 * 1024 * 1024


def store_documents(job_id, documents, root=APPLICATION_DOCUMENTS_DIR):
    """Validate and persist at most one document of each supported kind."""
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
                    "stored_name": f"{identifier}{suffix}",
                },
                content,
            )
        )

    directory = document_directory(job_id, root)
    written = []
    try:
        for metadata, content in prepared:
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / metadata["stored_name"]
            temporary = destination.with_suffix(f"{destination.suffix}.tmp")
            temporary.write_bytes(content)
            temporary.replace(destination)
            written.append(destination)
    except OSError:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return [metadata for metadata, _ in prepared]


def document_path(job_id, metadata, root=APPLICATION_DOCUMENTS_DIR):
    """Resolve one stored document without accepting a path from the browser."""
    if not isinstance(metadata, dict):
        raise ValueError("Bewerbungsunterlage wurde nicht gefunden")
    stored_name = str(metadata.get("stored_name") or "")
    if Path(stored_name).name != stored_name or not stored_name:
        raise ValueError("Ungültiger Dokumentpfad")
    path = document_directory(job_id, root) / stored_name
    if not path.is_file():
        raise FileNotFoundError("Bewerbungsunterlage wurde nicht gefunden")
    return path


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
    """Clean up files if saving their matching memory entry fails."""
    directory = document_directory(job_id, root)
    for document in documents:
        stored_name = str(document.get("stored_name") or "")
        if stored_name and Path(stored_name).name == stored_name:
            (directory / stored_name).unlink(missing_ok=True)


def document_directory(job_id, root=APPLICATION_DOCUMENTS_DIR):
    """Use a stable opaque directory instead of the source-controlled job ID."""
    identifier = hashlib.sha256(str(job_id).encode("utf-8")).hexdigest()[:20]
    return Path(root) / identifier


def safe_original_name(value):
    """Keep a display filename but never a user-supplied directory path."""
    name = Path(str(value or "").replace("\\", "/")).name.strip()
    if not name or len(name) > 240 or "\x00" in name:
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
