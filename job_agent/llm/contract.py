"""Versioned scoring rubric and structured output contract for LLM reviews."""

from job_agent.user_settings import USER_SETTINGS


PREFERRED_LOCATION_LABEL = USER_SETTINGS["matching"]["preferred_location_label"]

RUBRIC_VERSION = 6
SCHEMA_VERSION = 5

RATING_VALUES = ("excellent", "good", "partial", "weak", "conflict")

RUBRIC = {
    "entry_fit": {
        "max_points": 50,
        "guidance": (
            "Eine realistische Einstiegsstelle ist die wichtigste Voraussetzung."
        ),
        "anchors": {
            "excellent": "klare Junior- oder Berufseinsteigerrolle",
            "good": "Einstieg ohne ausdrueckliches Junior-Label realistisch",
            "partial": "Einstieg wegen Anforderungen unsicher",
            "weak": "deutliche Erfahrungsluecke fuer diese konkrete Stelle",
            "conflict": "klar keine realistische Einstiegsstelle",
        },
    },
    "working_conditions_fit": {
        "max_points": 30,
        "guidance": (
            f"{PREFERRED_LOCATION_LABEL}, lokaler Umkreis oder vollstaendiges Homeoffice sind nach der "
            "Einstiegstauglichkeit am wichtigsten."
        ),
        "anchors": {
            "excellent": "bevorzugter Standort oder vollstaendig remote",
            "good": "akzeptable lokale oder hybride Bedingungen",
            "partial": "Arbeitsbedingungen sind nicht vollstaendig klar",
            "weak": "nur mit deutlichem Kompromiss akzeptabel",
            "conflict": "Standort oder Arbeitsbedingungen ausgeschlossen",
        },
    },
    "direction_fit": {
        "max_points": 15,
        "guidance": (
            "Die Aufgaben sollen grob zu Software, Daten, KI oder technischem "
            "Consulting passen; ein perfekter Schwerpunkt ist nicht erforderlich."
        ),
        "anchors": {
            "excellent": "inhaltlich besonders interessante Richtung",
            "good": "ungefaehr passende technische oder analytische Richtung",
            "partial": "neutral, aber grundsaetzlich akzeptabel",
            "weak": "wenig ansprechender Schwerpunkt",
            "conflict": "klar unpassende Taetigkeit",
        },
    },
    "technology_head_start": {
        "max_points": 5,
        "guidance": (
            "Vorhandene Technologien sind nur ein kleiner Startvorteil. Grundlagen "
            "sind keine volle praktische Erfahrung."
        ),
        "anchors": {
            "excellent": "geforderte Kerntechnologien praktisch belegt",
            "good": "mehrere hilfreiche Kenntnisse belegt",
            "partial": "ein Teil ist belegt oder nur Grundlagen vorhanden",
            "weak": "wenig direkt nutzbare Vorerfahrung",
            "conflict": "zwingender Kern-Stack klar nicht erfuellt",
        },
    },
}

SCORE_BANDS = (
    (80, "strong_match"),
    (60, "match"),
    (40, "borderline"),
    (0, "not_recommended"),
)


def recommendation_for_score(score):
    """Return the fixed recommendation label for a 0-100 score."""
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        raise ValueError("score must be an integer between 0 and 100")
    return next(label for minimum, label in SCORE_BANDS if score >= minimum)


def recommendation_for_analysis(score, hard_conflicts=None):
    """Return the score band unless a verified hard conflict blocks the job."""
    if hard_conflicts:
        return "not_recommended"
    return recommendation_for_score(score)


def score_property(maximum):
    """Return one bounded integer property used by the JSON schema."""
    return {
        "type": "integer",
        "minimum": 0,
        "maximum": maximum,
    }


ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "overall_score",
        "recommendation",
        "confidence",
        "summary",
        "dimension_scores",
        "dimension_ratings",
        "key_tasks",
        "matching_evidence",
        "gaps",
        "risks",
        "uncertainties",
        "hard_conflicts",
    ],
    "properties": {
        "overall_score": score_property(100),
        "recommendation": {
            "type": "string",
            "enum": [label for _, label in SCORE_BANDS],
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "summary": {
            "type": "string",
            "maxLength": 600,
        },
        "dimension_scores": {
            "type": "object",
            "additionalProperties": False,
            "required": list(RUBRIC),
            "properties": {
                name: score_property(values["max_points"])
                for name, values in RUBRIC.items()
            },
        },
        "dimension_ratings": {
            "type": "object",
            "additionalProperties": False,
            "required": list(RUBRIC),
            "properties": {
                name: {"type": "string", "enum": list(RATING_VALUES)}
                for name in RUBRIC
            },
        },
        "key_tasks": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "maxLength": 300},
        },
        "matching_evidence": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "maxLength": 300},
        },
        "gaps": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "maxLength": 300},
        },
        "risks": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "maxLength": 300},
        },
        "uncertainties": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "maxLength": 300},
        },
        "hard_conflicts": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "maxLength": 300},
        },
    },
}
