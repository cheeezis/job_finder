"""Shared helpers for repairing structured LLM responses."""

import json


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
