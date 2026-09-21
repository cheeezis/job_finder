"""Location and remote-work eligibility rules."""

import re

from job_finder.matching.matching_rules import (
    FOREIGN_ONLY_LOCATION_WORDS,
    GERMANY_LOCATION_WORDS,
)
from job_finder.matching.matching_text import contains_any, contains_keyword
from job_finder.text import normalize_text


def analyze_location(
    location, remote, description, *, local_places, commuter_locations, radius
):
    """Return allowed, points and label for normalized location evidence.

    Accept configured local places, full remote work compatible with
    Germany, or commuter locations meeting their remote-percentage
    threshold. Local matching uses configured aliases, not a geographic
    distance calculation. The junior-hybrid exception is applied by
    analyze_location_for_role, not by this function.
    """
    full_remote = is_full_remote(location, remote)
    if full_remote and not remote_possible_from_germany(location, description):
        return {
            "allowed": False,
            "points": 0,
            "label": "Remote-Stelle ist nicht aus Deutschland ausuebbar",
        }

    if is_local_area(location, local_places):
        radius_label = f"{radius}-km-Radius"
        if full_remote:
            return {"allowed": True, "points": 15, "label": "lokal und 100% Remote"}
        if is_hybrid(remote):
            return {
                "allowed": True,
                "points": 13,
                "label": f"{radius_label} und Hybrid",
            }
        return {"allowed": True, "points": 10, "label": f"im {radius_label}"}

    if full_remote:
        return {"allowed": True, "points": 15, "label": "100% Remote aus Deutschland"}

    commuter_location = find_commuter_location(location, commuter_locations)
    if commuter_location:
        minimum = commuter_location["minimum_remote_percentage"]
        percentage = remote_percent(remote)
        if percentage >= minimum:
            return {
                "allowed": True,
                "points": 8,
                "label": (
                    f"Pendelort {commuter_location['search_location']} mit "
                    f"{percentage}% Remote"
                ),
            }

    return {"allowed": False, "points": 0, "label": "Ort/Remote passt nicht"}


def is_local_area(location, local_places):
    """Match normalized location text against configured local aliases."""
    return contains_any(location, local_places)


def find_commuter_location(location, commuter_locations):
    """Return the first matching commuter configuration, or None."""
    for item in commuter_locations:
        excluded_aliases = [
            normalize_text(alias) for alias in item.get("excluded_aliases", [])
        ]
        if contains_any(location, excluded_aliases):
            continue
        aliases = [normalize_text(alias) for alias in item["aliases"]]
        if contains_any(location, aliases):
            return item
    return None


def is_full_remote(location, remote):
    """Recognize full remote evidence in normalized work-mode or location text."""
    if remote_percent(remote) >= 100:
        return True
    if remote in ["remote", "fully remote", "full remote"]:
        return True

    # A structured location explicitly labelled remote is stronger evidence
    # than a generic "Homeoffice possible" phrase.
    return contains_keyword(location, "remote")


def remote_possible_from_germany(location, description):
    """Check normalized location restrictions for German remote eligibility.

    Germany markers in the location or first 1,200 description
    characters take precedence. Otherwise, reject EMEA locations and
    configured foreign-only location markers. This is a text heuristic,
    not a verification of legal employment eligibility.
    """
    combined = f"{location} {description[:1200]}"
    if contains_any(combined, GERMANY_LOCATION_WORDS):
        return True
    if "emea" in location:
        return False
    return not contains_any(location, FOREIGN_ONLY_LOCATION_WORDS)


def is_hybrid(remote):
    """Recognize hybrid wording or a remote percentage between 0 and 100."""
    return (
        contains_any(remote, ["hybrid", "homeoffice", "home office"])
        or 0 < remote_percent(remote) < 100
    )


def remote_percent(remote):
    """Read the first integer percentage, returning 0 if none is present."""
    match = re.search(r"(\d+)\s*%", remote)
    return int(match.group(1)) if match else 0
