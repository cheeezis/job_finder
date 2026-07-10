import re

from job_agent.profile import BLOCKED_FOCUS_KEYWORDS, BLOCKED_TITLE_WORDS, CLOSE_TITLE_PATTERNS
from job_agent.profile import FOCUS_CONTEXT_WORDS, NEARBY_PLACES
from job_agent.profile import OPTIONAL_EXPERIENCE_PHRASES, POINTS, PROFILE_SKILL_GROUPS
from job_agent.profile import REMOTE_LOCATION_WORDS, STRONG_TITLE_PATTERNS
from job_agent.text import normalize_text


def score_job(job):
    """Score one normalized job dict against the personal profile."""
    title = normalize_text(job["title"])
    location = normalize_text(job["location"])
    remote = normalize_text(job.get("remote", ""))
    description = normalize_text(job.get("description", ""))
    full_text = f"{title} {location} {remote} {description}"

    # Erst harte Ausschlusskriterien pruefen. Ausgeschlossene Jobs bekommen keinen Score.
    allowed, filter_reason = passes_hard_filters(title, description, location, remote)
    if not allowed:
        return {
            "status": "excluded",
            "raw_score": 0,
            "reasons": [filter_reason],
        }

    # Danach fachliche Anker pruefen: Ort/Remote allein soll keinen Job passend machen.
    title_points, title_reason = score_title(title)
    skill_matches = matching_skill_groups(f"{title} {description}")
    if title_points == 0 and not skill_matches:
        return {
            "status": "excluded",
            "raw_score": 0,
            "reasons": ["Kein fachlicher Anker gefunden"],
        }

    score = 0
    reasons = []

    score += title_points
    reasons.append(title_reason)

    # Ort/Remote bewertet nur noch Jobs, die grundsaetzlich fachlich anschlussfaehig sind.
    location_points, location_reason = score_location(location, remote)
    score += location_points
    reasons.append(location_reason)

    for skill_group in skill_matches:
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


def passes_hard_filters(title, description, location, remote):
    # Rollen-Level und Jobarten werden nur aus dem Titel hart ausgeschlossen.
    blocked_word = find_blocked_title_word(title)
    if blocked_word:
        return False, f"Titel enthaelt Ausschlusswort: {blocked_word}"

    # Fachliche Fokus-Blocker duerfen auch aus der Beschreibung kommen, aber nur
    # wenn sie dort deutlich den Schwerpunkt der Stelle beschreiben.
    blocked_focus = find_blocked_focus_keyword(title, description)
    if blocked_focus:
        return False, f"Fachlicher Fokus passt nicht: {blocked_focus}"

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
    if any(matches_pattern(title, pattern) for pattern in STRONG_TITLE_PATTERNS):
        return POINTS["strong_title"], f'+{POINTS["strong_title"]} starker Titel-Match'

    if any(matches_pattern(title, pattern) for pattern in CLOSE_TITLE_PATTERNS):
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
    return any(contains_keyword(text, word) for word in words)


def contains_keyword(text, keyword):
    return re.search(keyword_pattern(keyword), text) is not None


def keyword_pattern(keyword):
    escaped = re.escape(normalize_text(keyword))
    if re.fullmatch(r"[a-z0-9_]+", normalize_text(keyword)):
        return rf"(?<!\w){escaped}(?!\w)"
    return escaped


def matches_pattern(text, pattern):
    return all(contains_keyword(text, part) for part in pattern)


def matching_skill_groups(text):
    return [skill_group for skill_group in PROFILE_SKILL_GROUPS if has_keyword(text, skill_group["keywords"])]


def find_blocked_title_word(title):
    for word in BLOCKED_TITLE_WORDS:
        # Manche Anzeigen nennen mehrere Level, z.B. "Junior/Senior".
        # Die bleiben sichtbar, wenn "Junior" explizit im Titel steht.
        if word == "senior" and contains_keyword(title, "junior"):
            continue
        if contains_keyword(title, word):
            return word
    return None


def find_blocked_focus_keyword(title, description):
    for keyword in BLOCKED_FOCUS_KEYWORDS:
        if contains_keyword(title, keyword):
            return keyword
        if is_strong_body_focus(description, keyword):
            return keyword
    return None


def is_strong_body_focus(description, keyword):
    """Detect focus keywords in body text without banning one-off mentions."""
    relevant_text = description[:1500]
    matches = list(re.finditer(keyword_pattern(keyword), relevant_text))
    if len(matches) >= 2:
        return True
    if not matches:
        return False

    match = matches[0]
    window = relevant_text[max(0, match.start() - 80) : match.end() + 80]
    return contains_any(window, FOCUS_CONTEXT_WORDS)


def has_keyword(text, keywords):
    return any(contains_keyword(text, keyword) for keyword in keywords)
