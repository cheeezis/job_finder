"""Explicitly attach another listing to an existing application."""

from copy import deepcopy

from job_finder.paths import MEMORY_FILE
from job_finder.persistence.database import lock, transaction
from job_finder.workflow.applications import is_application
from job_finder.workflow.memory import edit_memory, unique_values


def link_listing_to_application(job_id, application_id, memory_path=MEMORY_FILE):
    """Keep the application's identity and history, and remember the listing's alias.

    Preserve the source review entry as association metadata before removing
    its separate card. Serialize with publication and bulk matching, so a
    worker cannot restore the duplicate. Refuse to combine two applications.
    """
    if not isinstance(job_id, str) or not isinstance(application_id, str) or job_id == application_id:
        raise ValueError("Bitte eine andere bestehende Bewerbung auswählen")
    with transaction() as connection:
        lock(connection, "finder-publication")
        with edit_memory(memory_path) as memory:
            target = memory.get(application_id)
            if target is None or not is_application(target):
                raise ValueError("Die ausgewählte Bewerbung existiert nicht mehr")
            if job_id in target.get("linked_job_ids", []):
                return {"job_id": application_id}
            source = memory.get(job_id)
            if source is None:
                raise ValueError("Die Anzeige existiert nicht mehr oder wurde bereits zugeordnet")
            if is_application(source) or source.get("application_documents"):
                raise ValueError("Zwei bestehende Bewerbungen können hier nicht zusammengeführt werden")
            target.setdefault("linked_review_entries", {})[job_id] = deepcopy(source)
            target["linked_job_ids"] = unique_values(target.get("linked_job_ids", []), [job_id])
            for field in ("source_urls", "source_names", "locations"):
                target[field] = unique_values(target.get(field, []), source.get(field, []))
            target["active"] = bool(target.get("active", True) or source.get("active", True))
            del memory[job_id]
    return {"job_id": application_id}
