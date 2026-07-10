import re

from job_agent.profile import BLOCKED_TITLE_WORDS, CLOSE_TITLE_PATTERNS, NEARBY_PLACES
from job_agent.profile import OPTIONAL_EXPERIENCE_PHRASES, POINTS, PROFILE_SKILL_GROUPS
from job_agent.profile import REMOTE_LOCATION_WORDS, STRONG_TITLE_PATTERNS


def score_job(job):
    """Score one normalized job dict against the personal profile."""
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

    for skill_group in PROFILE_SKILL_GROUPS:
        if has_keyword(full_text, skill_group["keywords"]):
            score += skill_group["points"]
            reasons.append(f'+{skill_group["points"]} {skill_group["label"]}')

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
    """Return title fit points before detailed skill matching happens."""
    if any(all(part in title for part in pattern) for pattern in STRONG_TITLE_PATTERNS):
        return POINTS["strong_title"], f'+{POINTS["strong_title"]} starker Titel-Match'

    if any(all(part in title for part in pattern) for pattern in CLOSE_TITLE_PATTERNS):
        return POINTS["close_title"], f'+{POINTS["close_title"]} fachlich naher Titel-Match'

    return 0, "+0 Titel passt nicht besonders gut"


def score_location(location, remote):
    """Score only locations that already passed the hard location filter."""
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
    return contains_any(text, OPTIONAL_EXPERIENCE_PHRASES)


def is_fulda_area(location):
    return contains_any(location, NEARBY_PLACES)


def is_frankfurt(location):
    return "frankfurt" in location or "ffm" in location


def is_full_remote(location, remote):
    return contains_any(location, REMOTE_LOCATION_WORDS) or remote in ["100%", "100", "full", "fully remote", "remote"]


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
