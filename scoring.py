import re


POINTS = {
    "strong_title": 40,
    "close_title": 25,
    "full_remote": 30,
    "fulda_area": 20,
    "fulda_hybrid_bonus": 10,
    "frankfurt_80_remote": 20,
    "python": 15,
    "ai_ml": 15,
    "data_analytics": 10,
    "many_years_experience_penalty": 30,
}

# Titel mit diesen Begriffen passen grundsaetzlich nicht zu deiner Suche.
BLOCKED_TITLE_WORDS = [
    "senior",
    "lead",
    "principal",
    "head",
    "manager",
    "werkstudent",
    "working student",
]


def score_job(job):
    title = job["title"].lower()
    location = job["location"].lower()
    remote = job.get("remote", "").lower()
    description = job.get("description", "").lower()
    full_text = f"{title} {location} {remote} {description}"

    # Erst harte Ausschlusskriterien pruefen. Ausgeschlossene Jobs bekommen keinen Score.
    allowed, filter_reason = passes_hard_filters(title, location, remote)
    if not allowed:
        return {
            "status": "excluded",
            "raw_score": 0,
            "reasons": [filter_reason],
        }

    score = 0
    reasons = []

    # Danach nur noch erlaubte Jobs bewerten und die Gruende sammelbar machen.
    title_points, title_reason = score_title(title)
    score += title_points
    reasons.append(title_reason)

    location_points, location_reason = score_location(location, remote)
    score += location_points
    reasons.append(location_reason)

    if has_keyword(full_text, ["python"]):
        score += POINTS["python"]
        reasons.append(f'+{POINTS["python"]} Python gefunden')

    if has_keyword(full_text, ["ai", "artificial intelligence", "machine learning", "ml", "llm"]):
        score += POINTS["ai_ml"]
        reasons.append(f'+{POINTS["ai_ml"]} AI/Machine Learning gefunden')

    if has_keyword(full_text, ["data", "daten", "analytics", "analyse", "analyst"]):
        score += POINTS["data_analytics"]
        reasons.append(f'+{POINTS["data_analytics"]} Data/Analytics gefunden')

    years = extract_required_years(full_text)
    if years and not experience_is_optional(full_text):
        penalty = experience_penalty(years)
        score -= penalty
        reasons.append(f"-{penalty} wegen {years} Jahr(en) Erfahrung")
    elif "mehrjaehrige erfahrung" in full_text and not experience_is_optional(full_text):
        score -= POINTS["many_years_experience_penalty"]
        reasons.append(f'-{POINTS["many_years_experience_penalty"]} wegen mehrjaehriger Erfahrung')

    # Negative Scores helfen beim Sortieren nicht und werden auf 0 gesetzt.
    score = max(0, score)

    return {
        "status": "included",
        "raw_score": score,
        "reasons": reasons,
    }


def passes_hard_filters(title, location, remote):
    # Diese Begriffe machen den Job direkt uninteressant, unabhaengig von Ort oder Skills.
    blocked_word = find_first_match(title, BLOCKED_TITLE_WORDS)
    if blocked_word:
        return False, f"Titel enthaelt Ausschlusswort: {blocked_word}"

    # Fulda und nahe Orte sind immer moeglich, weil kein Umzug noetig ist.
    if is_fulda_area(location):
        return True, "Fulda/Umkreis ist erlaubt"

    # 100% Remote ist immer moeglich, egal wo die Firma sitzt.
    if is_full_remote(location, remote):
        return True, "100% Remote ist erlaubt"

    # Frankfurt ist nur akzeptabel, wenn der Remote-Anteil hoch genug ist.
    if is_frankfurt(location) and remote_percent(remote) >= 80:
        return True, "Frankfurt ist mit mindestens 80% Remote erlaubt"

    return False, "Ort/Remote passt nicht"


def score_title(title):
    # Starker Match: Titel entspricht sehr nah einer deiner Zielrollen.
    strong_patterns = [
        ["junior", "python", "dev"],
        ["junior", "python", "developer"],
        ["junior", "data", "analyst"],
        ["ai", "engineer", "junior"],
        ["junior", "ai", "engineer"],
        ["junior", "ai", "developer"],
        ["junior", "softwareentwickler", "python"],
    ]

    # Naher Match: fachlich interessant, aber nicht exakt deine Wunschrolle.
    close_patterns = [
        ["python", "developer"],
        ["python", "dev"],
        ["data", "analyst"],
        ["data", "engineer"],
        ["machine learning", "engineer"],
        ["ml", "engineer"],
        ["ai", "engineer"],
        ["ai", "developer"],
        ["backend", "python"],
        ["werkstudent", "python"],
    ]

    if any(all(part in title for part in pattern) for pattern in strong_patterns):
        return POINTS["strong_title"], f'+{POINTS["strong_title"]} starker Titel-Match'

    if any(all(part in title for part in pattern) for pattern in close_patterns):
        return POINTS["close_title"], f'+{POINTS["close_title"]} fachlich naher Titel-Match'

    return 0, "+0 Titel passt nicht besonders gut"


def score_location(location, remote):
    if is_full_remote(location, remote):
        return POINTS["full_remote"], f'+{POINTS["full_remote"]} 100% Remote'

    if is_fulda_area(location):
        points = POINTS["fulda_area"]
        reason = f'+{POINTS["fulda_area"]} Fulda/Umkreis'
        if is_hybrid(remote):
            points += POINTS["fulda_hybrid_bonus"]
            reason += f' und +{POINTS["fulda_hybrid_bonus"]} Hybrid/Homeoffice'
        return points, reason

    if is_frankfurt(location) and remote_percent(remote) >= 80:
        return POINTS["frankfurt_80_remote"], f'+{POINTS["frankfurt_80_remote"]} Frankfurt mit mindestens 80% Remote'

    return 0, "+0 Standort"


def extract_required_years(text):
    # Zaehlt nur Jahresangaben, die klar mit Erfahrung verbunden sind.
    # So wird z.B. "35 Jahre Unternehmensgeschichte" nicht als Erfahrung gelesen.
    patterns = [
        r"(\d+)\s*\+?\s*(?:jahr|jahre|years|year)[^.!\n]{0,50}(?:erfahrung|berufserfahrung|experience)",
        r"(?:erfahrung|berufserfahrung|experience)[^.!\n]{0,50}(\d+)\s*\+?\s*(?:jahr|jahre|years|year)",
    ]
    matches = []
    for pattern in patterns:
        matches.extend(re.findall(pattern, text))

    years = [int(match) for match in matches if int(match) <= 10]
    if not years:
        return 0
    return max(years)


def experience_penalty(years):
    # Ein Jahr ist bei Junior-Stellen oft okay; ab drei Jahren wird es deutlich strenger.
    if years <= 0:
        return 0
    if years == 1:
        return 5
    if years == 2:
        return 15
    return 30 + ((years - 3) * 15)


def experience_is_optional(text):
    # Wenn Erfahrung nur optional ist, soll sie den Score nicht verschlechtern.
    optional_phrases = [
        "kein muss",
        "nicht erforderlich",
        "nicht notwendig",
        "keine berufserfahrung",
        "keine erfahrung",
    ]
    return contains_any(text, optional_phrases)


def is_fulda_area(location):
    nearby_places = [
        "fulda",
        "kuenzell",
        "kunzell",
        "eichenzell",
        "petersberg",
        "huenfeld",
        "hunfeld",
        "neuhof",
        "schluechtern",
        "schluchtern",
        "bad hersfeld",
    ]
    return contains_any(location, nearby_places)


def is_frankfurt(location):
    return "frankfurt" in location or "ffm" in location


def is_full_remote(location, remote):
    return "remote" in location or remote in ["100%", "100", "full", "fully remote", "remote"]


def is_hybrid(remote):
    return contains_any(remote, ["hybrid", "homeoffice", "home office"]) or remote_percent(remote) > 0


def remote_percent(remote):
    match = re.search(r"(\d+)\s*%", remote)
    if not match:
        return 0
    return int(match.group(1))


def contains_any(text, words):
    return any(word in text for word in words)


def find_first_match(text, words):
    for word in words:
        if word in text:
            return word
    return None


def has_keyword(text, keywords):
    return any(re.search(rf"\b{re.escape(keyword)}\b", text) for keyword in keywords)
