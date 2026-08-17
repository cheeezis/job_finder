"""Shared helpers for repairing structured LLM responses."""

import json


def chat_with_telemetry(
    client,
    *,
    stage,
    validation_repair,
    request_log=None,
    **request,
):
    """Call the model and retain usage even when later validation fails."""
    entry = {
        "stage": stage,
        "validation_repair": bool(validation_repair),
        "success": False,
        "metadata": {},
    }
    if request_log is not None:
        request_log.append(entry)

    try:
        result, metadata = client.chat(**request)
    except Exception as error:
        request_metadata = getattr(error, "request_metadata", None)
        if isinstance(request_metadata, dict):
            entry["metadata"] = dict(request_metadata)
        raise

    entry["success"] = True
    entry["metadata"] = dict(metadata or {})
    return result, metadata


def build_validation_retry_messages(messages, invalid_output, error):
    """Ask the model once more using the concrete contract violation."""
    return [
        *messages,
        {
            "role": "assistant",
            "content": json.dumps(invalid_output, ensure_ascii=False),
        },
        {
            "role": "user",
            "content": (
                "Die Antwort verletzt den Ausgabevertrag: "
                f"{error}. Korrigiere nur diesen Fehler und gib erneut das "
                "vollstaendige JSON-Objekt aus."
            ),
        },
    ]
