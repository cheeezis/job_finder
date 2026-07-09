import re


BLOCKED_TITLE_WORDS = [
    "senior",
    "lead",
    "principal",
    "head",
    "manager",
]


def score_job(job):
    title = job["title"].lower()
    location = job["location"].lower()
    remote = job.get("remote", "").lower()
    description = job.get("description", "").lower()
    full_text = f"{title} {location} {remote} {description}"

    allowed, filter_reason = passes_hard_filters(title, location, remote)
    if not allowed:
        return {
            "status": "excluded",
            "score": 0,
            "reasons": [filter_reason],
        }

    score = 0
    reasons = []

    title_points, title_reason = score_title(title)
    score += title_points
    reasons.append(title_reason)

    location_points, location_reason = score_location(location, remote)
    score += location_points
    reasons.append(location_reason)

    if has_keyword(full_text, ["python"]):
        score += 15
        reasons.append("+15 Python gefunden")

    if has_keyword(full_text, ["ai", "artificial intelligence", "machine learning", "ml", "llm"]):
        score += 15
        reasons.append("+15 AI/Machine Learning gefunden")

    if has_keyword(full_text, ["data", "daten", "analytics", "analyse", "analyst"]):
        score += 10
        reasons.append("+10 Data/Analytics gefunden")

    years = extract_required_years(full_text)
    if years:
        penalty = experience_penalty(years)
        score -= penalty
        reasons.append(f"-{penalty} wegen {years} Jahr(en) Erfahrung")
    elif "mehrjährige erfahrung" in full_text or "mehrjaehrige erfahrung" in full_text:
        score -= 30
        reasons.append("-30 wegen mehrjaehriger Erfahrung")

    score = max(0, min(100, score))

    return {
        "status": "included",
        "score": score,
        "reasons": reasons,
    }


def passes_hard_filters(title, location, remote):
    if contains_any(title, BLOCKED_TITLE_WORDS):
        return False, "Titel ist Senior/Lead/Manager-artig"

    if is_fulda_area(location):
        return True, "Fulda/Umkreis ist erlaubt"

    if is_full_remote(location, remote):
        return True, "100% Remote ist erlaubt"

    if is_frankfurt(location) and remote_percent(remote) >= 80:
        return True, "Frankfurt ist mit mindestens 80% Remote erlaubt"

    return False, "Ort/Remote passt nicht"


def score_title(title):
    strong_patterns = [
        ["junior", "python", "dev"],
        ["junior", "python", "developer"],
        ["junior", "data", "analyst"],
        ["ai", "engineer", "junior"],
        ["junior", "ai", "engineer"],
    ]

    close_patterns = [
        ["python", "developer"],
        ["python", "dev"],
        ["data", "analyst"],
        ["machine learning", "engineer"],
        ["ml", "engineer"],
        ["ai", "engineer"],
        ["backend", "python"],
    ]

    if any(all(part in title for part in pattern) for pattern in strong_patterns):
        return 40, "+40 starker Titel-Match"

    if any(all(part in title for part in pattern) for pattern in close_patterns):
        return 25, "+25 fachlich naher Titel-Match"

    return 0, "+0 Titel passt nicht besonders gut"


def score_location(location, remote):
    if is_full_remote(location, remote):
        return 30, "+30 100% Remote"

    if is_fulda_area(location):
        points = 20
        reason = "+20 Fulda/Umkreis"
        if is_hybrid(remote):
            points += 10
            reason += " und +10 Hybrid/Homeoffice"
        return points, reason

    if is_frankfurt(location) and remote_percent(remote) >= 80:
        return 20, "+20 Frankfurt mit mindestens 80% Remote"

    return 0, "+0 Standort"


def extract_required_years(text):
    matches = re.findall(r"(\d+)\s*\+?\s*(?:jahr|jahre|years|year)", text)
    if not matches:
        return 0
    return max(int(match) for match in matches)


def experience_penalty(years):
    if years <= 0:
        return 0
    if years == 1:
        return 5
    if years == 2:
        return 15
    return 30 + ((years - 3) * 15)


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


def has_keyword(text, keywords):
    return any(re.search(rf"\b{re.escape(keyword)}\b", text) for keyword in keywords)
