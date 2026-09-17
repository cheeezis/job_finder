"""Conservative context extraction for vacancy requirements and employment."""

import re

from job_finder.matching_text import contains_any
from job_finder.text import html_to_text, normalize_text

SECTION_HEADINGS = {
    "requirements": (
        "dein profil",
        "ihr profil",
        "euer profil",
        "das bringst du mit",
        "was du mitbringst",
        "was sie mitbringen",
        "deine qualifikationen",
        "zentrale anforderungen",
        "fachliche voraussetzungen",
        "qualifikation",
        "qualifikationen",
        "requirements",
        "qualifications",
        "your profile",
        "what you bring",
        "what we're looking for",
        "das solltest du mitbringen",
        "du bringst mit",
        "das zeichnet dich aus",
        "must haves",
        "must-have",
    ),
    "tasks": (
        "deine aufgaben",
        "ihre aufgaben",
        "dein job",
        "deine mission",
        "verantwortungsbereiche",
        "aufgaben",
        "responsibilities",
        "your role",
    ),
    "benefits": (
        "was wir bieten",
        "wir bieten dir",
        "wir bieten ihnen",
        "das bieten wir",
        "das halten wir fuer dich bereit",
        "warum wir",
        "benefits",
        "leistungen",
        "what we offer",
        "our offer",
        "das haben wir zu bieten",
    ),
    "company": ("ueber uns", "about us", "dein weg zu uns", "jetzt bewerben"),
}
HEADING_KINDS = {
    heading: kind for kind, headings in SECTION_HEADINGS.items() for heading in headings
}
HEADING_PATTERN = re.compile(
    r"(?m)(?:"
    + "|".join(
        (r"(?<!\w)" if " " in heading else r"(?:^|(?<=\n))[ \t]*") + re.escape(heading)
        for heading in sorted(HEADING_KINDS, key=len, reverse=True)
    )
    + r")(?!\w)\s*:?"
)


def plain_description(text):
    """Normalize plain or HTML descriptions while retaining block boundaries."""
    if re.search(r"</?(?:p|li|h[1-6]|div|ul|br)\b", text, re.I):
        text = re.sub(r"<br\s*/?>|</(?:p|li|h[1-6]|div)>", "\n", text, flags=re.I)
        text = "\n".join(html_to_text(line) for line in text.splitlines())
    # Portals often flatten headings and bullets. Retain capitalized headings
    # before lowercasing, without turning ordinary "customer requirements" or
    # "deine Aufgaben erfüllen" into section boundaries.
    headings = re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(h) for h in HEADING_KINDS) + r")(?!\w)",
        re.I,
    )
    text = headings.sub(
        lambda match: ("\n" if match.group()[0].isupper() else "") + match.group(), text
    )
    return normalize_text(text)


def description_sections(text):
    """Separate common headings, including headings in flattened portal text."""
    sections = {kind: [] for kind in ["unknown", *SECTION_HEADINGS]}
    start, kind = 0, "unknown"
    for match in HEADING_PATTERN.finditer(text):
        sections[kind].append(text[start : match.start()])
        kind = HEADING_KINDS[match.group().strip().rstrip(":").strip()]
        start = match.end()
    sections[kind].append(text[start:])
    return {kind: "\n".join(parts).strip() for kind, parts in sections.items()}


def requirement_text(description):
    """Use applicant sections; otherwise omit known benefits and company prose."""
    sections = description_sections(description)
    if sections["requirements"]:
        # Explicit requirements can also appear in the opening summary.
        introduction = [
            clause
            for clause in re.split(r"[.!?;\n]", sections["unknown"])
            if re.search(
                r"\b(?:du (?:hast|bringst)|you (?:have|bring)|mindestens|erforderlich)\b",
                clause,
            )
        ]
        return "\n".join([*introduction, sections["requirements"]]).strip()
    return "\n".join([sections["unknown"], sections["tasks"]]).strip()


def leadership_required(title, description):
    """Recognize actual people management, not leading a technical discussion."""
    if contains_any(
        title, ["teamlead", "teamleader", "team lead", "teamleiter", "head of"]
    ):
        return True
    requirements = requirement_text(description)
    patterns = [
        r"\b(?:fuehrungserfahrung|personalverantwortung)\b",
        r"\berfahrung\s+(?:in|mit)\s+der\s+fuehrung\b",
        r"\bexperience\s+(?:in\s+)?(?:managing|leading)\s+(?:a\s+)?teams?\b",
    ]
    for clause in re.split(r"[.!?;\n]", requirements):
        if any(re.search(pattern, clause) for pattern in patterns) and not contains_any(
            clause,
            [
                "keine",
                "nicht erforderlich",
                "not required",
                "kein muss",
                "wuenschenswert",
                "von vorteil",
                "idealerweise",
                "nice to have",
            ],
        ):
            return True
    tasks = description_sections(description)["tasks"] or description
    return bool(
        re.search(
            r"\b(?:fachliche und disziplinarische fuehrung|"
            r"disziplinarische fuehrung|du fuehrst unser team|sie fuehren unser team)\b",
            tasks,
        )
    )


def employment_evidence(title, employment, description):
    """Use job labels and hiring sentences, never qualifications or benefits."""
    hiring = []
    for clause in re.split(r"[.!?;\n]", description):
        if re.search(
            r"\b(?:wir suchen|gesucht|we are hiring|we are looking for|"
            r"die stelle ist|anstellungsart|beschaeftigungsart|"
            r"beschaeftigung erfolgt|befristet(?:es|e|en)?\s+(?:auf|fuer))\b",
            clause,
        ):
            hiring.append(
                re.split(
                    r"\b(?:mit|abgeschlossen\w*|erfahrung\w*|kenntnisse)\b",
                    clause,
                    maxsplit=1,
                )[0]
            )
    return " ".join([title, employment, *hiring])


def non_it_title(title):
    """Reject clear non-IT occupations before the broad IT keyword fallback."""
    return contains_any(
        title,
        [
            "business developer",
            "business development",
            "immigration lawyer",
            "rechtsanwalt",
            "direkt-vertrieb",
            "direktvertrieb",
            "leadgenerierung",
            "cnc-programmierer",
            "cnc programmierer",
        ],
    )
