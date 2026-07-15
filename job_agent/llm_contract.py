"""Versioned scoring rubric and structured output contract for LLM reviews."""

RUBRIC_VERSION = 1
SCHEMA_VERSION = 1

RUBRIC = {
    "role_fit": {
        "max_points": 20,
        "guidance": (
            "Bewerte die tatsaechliche Rolle: primaere Zielrollen am hoechsten, "
            "danach sekundaere, explorative und niedrig priorisierte Rollen."
        ),
        "anchors": [
            "17-20: klare primaere Zielrolle",
            "12-16: passende sekundaere Zielrolle",
            "6-11: explorative oder niedrig priorisierte Rolle",
            "0-5: fachlich kaum passende Rolle",
        ],
    },
    "technology_fit": {
        "max_points": 20,
        "guidance": (
            "Vergleiche geforderte Technologien mit belegten Kenntnissen. "
            "Python, RAG und agentische KI sind besonders relevant; Grundlagen "
            "duerfen nicht als fortgeschrittene Kenntnisse gelten."
        ),
        "anchors": [
            "17-20: starke Ueberschneidung mit belegten Kernkenntnissen",
            "12-16: mehrere passende und realistisch vertiefbare Technologien",
            "6-11: teilweise Ueberschneidung mit deutlichem Lernbedarf",
            "0-5: zentrale Technologien fehlen weitgehend",
        ],
    },
    "experience_fit": {
        "max_points": 20,
        "guidance": (
            "Bevorzuge echte Einstiegsrollen ohne Berufserfahrung. Praktikum, "
            "Studium und Projekte sind Belege, aber keine regulaere "
            "Berufserfahrung."
        ),
        "anchors": [
            "18-20: explizite Einstiegsrolle oder keine Erfahrung erforderlich",
            "13-17: erste praktische Erfahrung reicht aus",
            "6-12: ein bis drei Jahre oder fundierte Vorerfahrung gewuenscht",
            "0-5: klares Seniorniveau oder mehr als drei Jahre erforderlich",
        ],
    },
    "location_fit": {
        "max_points": 20,
        "guidance": (
            "Remote innerhalb Deutschlands ist bevorzugt. Lokale Stellen im "
            "definierten Radius sind geeignet; der zusaetzliche Standort ist "
            "nur mit dem hinterlegten hohen Remote-Anteil geeignet."
        ),
        "anchors": [
            "18-20: vollstaendig remote innerhalb Deutschlands",
            "15-17: lokal mit passendem Homeoffice-Anteil",
            "12-14: lokal vor Ort",
            "8-11: zusaetzlicher Standort mit ausreichendem Remote-Anteil",
            "0-7: unklarer oder widerspruechlicher Standort",
        ],
    },
    "task_fit": {
        "max_points": 20,
        "guidance": (
            "Bewerte konkrete Aufgaben statt nur den Titel. Entwicklung mit "
            "Python, RAG, Agenten, Daten und Backend ist besonders interessant."
        ),
        "anchors": [
            "17-20: Schwerpunkt auf Python, RAG, Agenten oder angewandter KI",
            "12-16: passende Software-, Backend- oder Datenaufgaben",
            "6-11: interessante Teilaufgaben, aber anderer Schwerpunkt",
            "0-5: Aufgaben passen kaum zur gewuenschten Entwicklung",
        ],
    },
}

SCORE_BANDS = (
    (85, "strong_match"),
    (70, "match"),
    (55, "borderline"),
    (0, "not_recommended"),
)


def recommendation_for_score(score):
    """Return the fixed recommendation label for a 0-100 score."""
    if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 100:
        raise ValueError("score must be an integer between 0 and 100")
    return next(label for minimum, label in SCORE_BANDS if score >= minimum)


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
        "key_tasks",
        "key_requirements",
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
        "key_tasks": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string", "maxLength": 300},
        },
        "key_requirements": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["requirement", "priority", "match", "evidence"],
                "properties": {
                    "requirement": {"type": "string", "maxLength": 300},
                    "priority": {
                        "type": "string",
                        "enum": ["required", "preferred", "unclear"],
                    },
                    "match": {
                        "type": "string",
                        "enum": ["strong", "partial", "missing", "unknown"],
                    },
                    "evidence": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {"type": "string", "maxLength": 300},
                    },
                },
            },
        },
        "matching_evidence": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "maxLength": 300},
        },
        "gaps": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "maxLength": 300},
        },
        "risks": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string", "maxLength": 300},
        },
        "uncertainties": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string", "maxLength": 300},
        },
        "hard_conflicts": {
            "type": "array",
            "maxItems": 5,
            "items": {"type": "string", "maxLength": 300},
        },
    },
}
