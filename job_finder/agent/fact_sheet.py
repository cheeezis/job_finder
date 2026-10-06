"""The fact sheet the agent writes: fixed lines with a traffic light, verdict and short reason.

One Pydantic model describes it twice over: as the structure the model must
answer in (structured output), so none of the seven fixed lines can be
missing, and as the check of that answer. The review turns the light words
into symbols: gruen 🟢, gelb 🟡, orange 🟠, rot 🔴, unbekannt ⚪, hinweis ⚠️.
"""

import re
from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, field_validator

FIXED_LINES = (
    ("status", "Status"),
    ("berufseinstieg", "Berufseinstieg"),
    ("fachlicher_fit", "Fachlicher Fit"),
    ("luecken", "Lücken"),
    ("homeoffice_standort", "Homeoffice / Standort"),
    ("reiseanteil", "Reiseanteil"),
    ("gehalt", "Gehalt"),
)
# A warning (hinweis) belongs in an extra line; a fixed line always judges.
FixedLight = Literal["gruen", "gelb", "orange", "rot", "unbekannt"]
Light = Literal["gruen", "gelb", "orange", "rot", "unbekannt", "hinweis"]
Verdict = Literal["bewerben", "erst_klaeren", "eher_streichen", "streichen"]
FIXED_LIGHTS, LIGHTS, VERDICTS = get_args(FixedLight), get_args(Light), get_args(Verdict)
# Clutter the review would show as text: citations the web search appends,
# as in "([example.com](https://example.com/x))", other Markdown links and a
# leading light word, as in "gruen – offen".
CITATION = re.compile(r"\s*\(\[[^\]]*\]\([^)]*\)\)")
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
LEADING_LIGHT = re.compile(rf"^\s*(?:{'|'.join(LIGHTS)})\s*[–-]\s*", re.IGNORECASE)
MAX_EXTRA_LINES = 2


def tidy(text):
    """Keep the words, drop link markup and a leading light word; the sources list the links."""
    text = MARKDOWN_LINK.sub(r"\1", CITATION.sub("", text))
    return LEADING_LIGHT.sub("", text).strip()


def tidy_required(text):
    """Return the tidied text; nothing left means the line says nothing."""
    if not (text := tidy(text)):
        raise ValueError("Text fehlt")
    return text


Text = Annotated[str, AfterValidator(tidy_required)]


class Closed(BaseModel):
    """Strict structured output wants every property required and no others allowed."""

    model_config = ConfigDict(extra="forbid")


class Line(Closed):
    ampel: FixedLight
    text: Text


class ExtraLine(Closed):
    thema: Text
    ampel: Light
    text: Text


class Conclusion(Closed):
    stufe: Verdict
    text: Text


class FactSheet(Closed):
    status: Line
    berufseinstieg: Line
    fachlicher_fit: Line
    luecken: Line
    homeoffice_standort: Line
    reiseanteil: Line
    gehalt: Line
    zusatz: list[ExtraLine]
    fazit: Conclusion
    kurzgrund: Text
    quellen: list[str]

    @field_validator("zusatz")
    @classmethod
    def at_most_two(cls, lines):
        return lines[:MAX_EXTRA_LINES]


# The keywords in the order the hand-written schema had, so the request stays byte for byte the same.
KEYWORD_ORDER = ("type", "properties", "required", "additionalProperties", "items", "enum")


def strict_schema(model):
    """Return the model's JSON schema with references inlined and titles dropped.

    This is exactly the structure the agent sent before the model existed,
    so the switch to Pydantic does not change what the model is asked for.
    """
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(node):
        if "$ref" in node:
            return inline(definitions[node["$ref"].rsplit("/", 1)[1]])
        result = {}
        for keyword in sorted(node.keys() - {"title"}, key=KEYWORD_ORDER.index):
            value = node[keyword]
            if keyword == "properties":
                value = {name: inline(child) for name, child in value.items()}
            elif keyword == "items":
                value = inline(value)
            result[keyword] = value
        return result

    return inline(schema)


SCHEMA = strict_schema(FactSheet)
# The Responses API's text.format for this structure.
RESPONSE_FORMAT = {"type": "json_schema", "name": "steckbrief", "strict": True, "schema": SCHEMA}
LABELS = {**dict(FIXED_LINES), "zusatz": "Zusatz", "fazit": "Fazit", "kurzgrund": "Kurzgrund"}


def parse_fact_sheet(text):
    """Check the model's JSON against the structure and return it; raise ValueError otherwise.

    Structured output should already guarantee the shape; this is the second
    line, because a broken fact sheet must never reach the review.
    """
    try:
        return FactSheet.model_validate_json(text or "").model_dump()
    except ValidationError as error:
        raise ValueError(reason(error.errors()[0])) from None


def reason(error):
    """Name the first problem in the words the review shows for an unusable sheet."""
    kind, location = error["type"], error["loc"]
    if kind == "json_invalid":
        return "Steckbrief ist kein gültiges JSON"
    if len(location) <= 1 and kind in {"missing", "extra_forbidden", "model_type", "dict_type"}:
        return "Steckbrief hat nicht die erwarteten Felder"
    label = LABELS.get(str(location[0]), str(location[0]))
    if location[-1] == "ampel":
        return f"{label}: keine gültige Ampel"
    if location[-1] == "stufe":
        return "Fazit hat keine gültige Stufe"
    if location[-1] == "thema":
        return "Zusatzzeile ohne Thema"
    if location[-1] == "text" or location == ("kurzgrund",):
        return f"{label}: Text fehlt"
    return f"{label}: unerwartete Form"
