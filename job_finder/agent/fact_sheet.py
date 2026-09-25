"""The fact sheet the agent writes: fixed lines with a traffic light, verdict and short reason.

The model must answer in this structure (structured output), so none of the
seven fixed lines can be missing. The review turns the light words into
symbols: gruen 🟢, gelb 🟡, orange 🟠, rot 🔴, unbekannt ⚪, hinweis ⚠️.
"""

import json
import re

FIXED_LINES = (
    ("status", "Status"),
    ("berufseinstieg", "Berufseinstieg"),
    ("fachlicher_fit", "Fachlicher Fit"),
    ("luecken", "Lücken"),
    ("homeoffice_standort", "Homeoffice / Standort"),
    ("reiseanteil", "Reiseanteil"),
    ("gehalt", "Gehalt"),
)
LIGHTS = ("gruen", "gelb", "orange", "rot", "unbekannt", "hinweis")
VERDICTS = ("bewerben", "erst_klaeren", "eher_streichen", "streichen")
# Clutter the review would show as text: citations the web search appends,
# as in "([example.com](https://example.com/x))", other Markdown links and a
# leading light word, as in "gruen – offen".
CITATION = re.compile(r"\s*\(\[[^\]]*\]\([^)]*\)\)")
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
LEADING_LIGHT = re.compile(rf"^\s*(?:{'|'.join(LIGHTS)})\s*[–-]\s*", re.IGNORECASE)
MAX_EXTRA_LINES = 2


def closed_object(properties):
    """Strict structured output wants every property required and no others allowed."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


LINE = closed_object(
    {"ampel": {"type": "string", "enum": list(LIGHTS)}, "text": {"type": "string"}}
)
EXTRA_LINE = closed_object({"thema": {"type": "string"}, **LINE["properties"]})
SCHEMA = closed_object(
    {
        **{key: LINE for key, _label in FIXED_LINES},
        "zusatz": {"type": "array", "items": EXTRA_LINE},
        "fazit": closed_object(
            {"stufe": {"type": "string", "enum": list(VERDICTS)}, "text": {"type": "string"}}
        ),
        "kurzgrund": {"type": "string"},
        "quellen": {"type": "array", "items": {"type": "string"}},
    }
)
# The Responses API's text.format for this structure.
RESPONSE_FORMAT = {"type": "json_schema", "name": "steckbrief", "strict": True, "schema": SCHEMA}


def parse_fact_sheet(text):
    """Check the model's JSON against the structure and return it; raise ValueError otherwise.

    Structured output should already guarantee the shape; this is the second
    line, because a broken fact sheet must never reach the review.
    """
    try:
        sheet = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Steckbrief ist kein gültiges JSON") from error
    if not isinstance(sheet, dict) or set(sheet) != set(SCHEMA["properties"]):
        raise ValueError("Steckbrief hat nicht die erwarteten Felder")
    for key, label in FIXED_LINES:
        check_line(sheet[key], label)
    if not isinstance(sheet["zusatz"], list):
        raise ValueError("Zusatzzeilen fehlen")
    for line in sheet["zusatz"]:
        check_line(line, "Zusatz")
        if not isinstance(line.get("thema"), str) or not line["thema"].strip():
            raise ValueError("Zusatzzeile ohne Thema")
    sheet["zusatz"] = sheet["zusatz"][:MAX_EXTRA_LINES]
    verdict = sheet["fazit"]
    if not isinstance(verdict, dict) or verdict.get("stufe") not in VERDICTS:
        raise ValueError("Fazit hat keine gültige Stufe")
    if not non_empty_text(verdict.get("text")) or not non_empty_text(sheet["kurzgrund"]):
        raise ValueError("Fazit oder Kurzgrund ist leer")
    verdict["text"], sheet["kurzgrund"] = tidy(verdict["text"]), tidy(sheet["kurzgrund"])
    if not isinstance(sheet["quellen"], list) or not all(
        isinstance(source, str) for source in sheet["quellen"]
    ):
        raise ValueError("Quellen müssen eine Liste von Texten sein")
    return sheet


def check_line(line, label):
    if not isinstance(line, dict) or line.get("ampel") not in LIGHTS:
        raise ValueError(f"{label}: keine gültige Ampel")
    if not non_empty_text(line.get("text")):
        raise ValueError(f"{label}: Text fehlt")
    line["text"] = tidy(line["text"])
    if not line["text"]:
        raise ValueError(f"{label}: Text fehlt")


def tidy(text):
    """Keep the words, drop link markup and a leading light word; the sources list the links."""
    text = MARKDOWN_LINK.sub(r"\1", CITATION.sub("", text))
    return LEADING_LIGHT.sub("", text).strip()


def non_empty_text(value):
    return isinstance(value, str) and bool(value.strip())
