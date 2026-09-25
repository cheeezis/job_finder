"""Tools the agent may call. They only read: ads and web pages are untrusted text."""

import json

from job_finder.matching.deduplication import companies_match, normalize_company, normalize_title
from job_finder.matching.matching_text import contains_keyword
from job_finder.persistence.decisions import decided_jobs

MAX_DECISIONS = 10
MAX_NOTE_CHARS = 300
# The same wording as the review page (app.js).
DECISION_LABELS = {
    "interesting": "Interessant",
    "inquiry": "Rückfrage offen",
    "ignored": "Nicht interessant",
    "applied": "Beworben",
    "response": "Antwort erhalten",
    "interview": "Interview",
    "rejected": "Absage",
    "no_response": "Keine Rückmeldung",
    "offer": "Angebot",
    "closed": "Abgeschlossen",
}

PAST_DECISIONS_TOOL = {
    "type": "function",
    "name": "past_decisions",
    "description": (
        "Frühere Entscheidungen des Nutzers zu Stellen derselben Firma oder mit ähnlichen "
        "Titeln: Entscheidung, Datum, Bewertung und Notiz. Höchstens 10 Treffer, "
        "Firmentreffer zuerst."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "company": {
                "type": ["string", "null"],
                "description": "Firmenname wie in der Anzeige, oder null.",
            },
            "title_keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": 'Bis zu 5 Wörter aus dem Stellentitel, z. B. ["SAP", "Consultant"].',
            },
        },
        "required": ["company", "title_keywords"],
        "additionalProperties": False,
    },
    "strict": True,
}


def past_decisions(arguments, current_job_id, rows=None):
    """Answer one tool call with JSON text; bad arguments get an error the model can read."""
    company = arguments.get("company") if isinstance(arguments, dict) else None
    keywords = arguments.get("title_keywords") if isinstance(arguments, dict) else None
    company_key = normalize_company(company) if isinstance(company, str) else ""
    keywords = [
        keyword.strip()
        for keyword in (keywords if isinstance(keywords, list) else [])[:5]
        if isinstance(keyword, str) and len(keyword.strip()) >= 3
    ]
    if not company_key and not keywords:
        return json.dumps(
            {"fehler": "Firma oder mindestens ein Titelwort mit 3 Zeichen angeben."},
            ensure_ascii=False,
        )
    entries = []
    for job_id, title, row_company, status, rating, note, decided_on in (
        decided_jobs() if rows is None else rows
    ):
        if job_id == current_job_id:
            continue
        if company_key and companies_match(company_key, normalize_company(row_company or "")):
            match = "Firma"
        elif any(contains_keyword(normalize_title(title or ""), word) for word in keywords):
            match = "Titel"
        else:
            continue
        entries.append(
            {
                "titel": title,
                "firma": row_company,
                "entscheidung": DECISION_LABELS.get(status, status),
                "datum": decided_on.isoformat() if decided_on else None,
                "bewertung": rating,
                "notiz": (note or "")[:MAX_NOTE_CHARS] or None,
                "treffer": match,
            }
        )
    # Newest first, then company matches before title matches (both sorts are stable).
    entries.sort(key=lambda entry: entry["datum"] or "", reverse=True)
    entries.sort(key=lambda entry: entry["treffer"] != "Firma")
    return json.dumps({"entscheidungen": entries[:MAX_DECISIONS]}, ensure_ascii=False)
