"""Versioned scoring rubric and structured output contract for LLM reviews."""

RUBRIC_VERSION = 5
SCHEMA_VERSION = 3

RATING_POINTS = {
    "excellent": 20,
    "good": 15,
    "partial": 10,
    "weak": 5,
    "conflict": 0,
}

RUBRIC = {
    "role_fit": {
        "max_points": 20,
        "guidance": (
            "Bewerte die tatsaechliche Rolle: primaere Zielrollen am hoechsten, "
            "danach sekundaere, explorative und niedrig priorisierte Rollen."
        ),
        "anchors": {
            "excellent": "klare primaere Zielrolle",
            "good": "passende sekundaere Zielrolle",
            "partial": "explorative oder niedrig priorisierte Rolle",
            "weak": "nur geringe fachliche Naehe",
            "conflict": "unpassende Rolle oder klares Ausschlussprofil",
        },
    },
    "technology_fit": {
        "max_points": 20,
        "guidance": (
            "Vergleiche geforderte Technologien mit belegten Kenntnissen. "
            "Python, RAG und agentische KI sind besonders relevant; Grundlagen "
            "duerfen nicht als praktische oder fortgeschrittene Kenntnisse "
            "gelten. Mehrere geforderte Technologien brauchen mehrere Belege."
        ),
        "anchors": {
            "excellent": "belegte Kerntechnologien decken die Stelle weitgehend ab",
            "good": "mehrere zentrale Technologien sind belegt; nur Nebenluecken",
            "partial": "relevante Ueberschneidung mit deutlichem Lernbedarf",
            "weak": "die meisten zentralen Technologien fehlen",
            "conflict": "der zwingende Kern-Stack ist nicht belegt",
        },
    },
    "experience_fit": {
        "max_points": 20,
        "guidance": (
            "Bevorzuge echte Einstiegsrollen ohne Berufserfahrung. Praktikum, "
            "Studium und Projekte sind Belege, aber keine regulaere "
            "Berufserfahrung. Berufserfahrung darf nur anerkannt werden, wenn "
            "sie im Profil ausdruecklich belegt ist."
        ),
        "anchors": {
            "excellent": "explizite Einstiegsrolle oder keine Erfahrung erforderlich",
            "good": "erste praktische Erfahrung reicht; Projekte oder Praktikum passen",
            "partial": "ein bis drei Jahre sind nur bevorzugt oder unklar formuliert",
            "weak": "ein bis drei Jahre oder fundierte Berufserfahrung sind zwingend",
            "conflict": "Seniorniveau oder mehr als drei Jahre erforderlich",
        },
    },
    "location_fit": {
        "max_points": 20,
        "guidance": (
            "Nutze den bereits deterministisch ermittelten location_precheck. "
            "Remote innerhalb Deutschlands ist bevorzugt, danach lokal mit "
            "Homeoffice und lokal vor Ort. Berechne keine Entfernungen selbst."
        ),
        "anchors": {
            "excellent": "vollstaendig remote in Deutschland",
            "good": "lokal vor Ort oder mit passender Hybridregelung",
            "partial": "zulaessiger Zusatzstandort mit erfuellter Remote-Regel",
            "weak": "Standort oder Remote-Anteil ist unklar",
            "conflict": "gepruefter Standortkonflikt",
        },
    },
    "task_fit": {
        "max_points": 20,
        "guidance": (
            "Bewerte konkrete Aufgaben statt nur den Titel. Entwicklung mit "
            "Python, RAG, Agenten, Daten und Backend ist besonders interessant. "
            "Bewerte hier die inhaltliche Richtung; Technologieluecken werden "
            "bereits in technology_fit bewertet."
        ),
        "anchors": {
            "excellent": "Schwerpunkt auf Python, RAG, Agenten oder angewandter KI",
            "good": "passende Software-, Backend- oder Datenaufgaben",
            "partial": "interessante Teilaufgaben, aber anderer Schwerpunkt",
            "weak": "Aufgaben passen nur gering zur gewuenschten Entwicklung",
            "conflict": "Aufgaben widersprechen der angestrebten Entwicklung",
        },
    },
}

SCORE_BANDS = (
    (90, "strong_match"),
    (75, "match"),
    (60, "borderline"),
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
        "dimension_ratings": {
            "type": "object",
            "additionalProperties": False,
            "required": list(RUBRIC),
            "properties": {
                name: {"type": "string", "enum": list(RATING_POINTS)}
                for name in RUBRIC
            },
        },
        "key_tasks": {
            "type": "array",
            "maxItems": 3,
            "items": {"type": "string", "maxLength": 300},
        },
        "key_requirements": {
            "type": "array",
            "maxItems": 5,
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
                        "maxItems": 2,
                        "items": {"type": "string", "maxLength": 300},
                    },
                },
            },
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
