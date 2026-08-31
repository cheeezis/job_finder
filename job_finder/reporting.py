"""Compact recommendation output for review and notifications."""

import re
from pathlib import Path

from job_finder.paths import RECOMMENDATIONS_JSON
from job_finder.text import text_is_mainly_english
from job_finder.storage import write_json_atomic


INTERNATIONAL_LOCATION_TERMS = {
    "anywhere",
    "emea",
    "eu",
    "europa",
    "europe",
    "global",
    "weltweit",
    "worldwide",
}
INTERNATIONAL_REMOTE_SOURCES = {"himalayas", "jobicy", "startup_jobs"}
FOREIGN_COUNTRY_TERMS = {
    "australia",
    "austria",
    "belgium",
    "canada",
    "czechia",
    "france",
    "india",
    "ireland",
    "italy",
    "netherlands",
    "poland",
    "spain",
    "sweden",
    "switzerland",
    "united kingdom",
    "united states",
}
ROLE_LABELS = {
    "general_it": "Allgemeine IT",
    "software_development": "Softwareentwicklung",
    "python_ai_data": "Python / KI / Daten",
    "technical_consulting": "Technisches Consulting",
    "infrastructure": "Infrastruktur",
    "junior_sap": "SAP-Einstieg",
    "testing": "Testing / QA",
    "junior_administration": "IT-Administration",
    "rpa_automation": "RPA / Automatisierung",
    "trainee": "Trainee",
    "junior_modern_workplace": "Modern Workplace",
    "infrastructure_automation": "Infrastruktur-Automatisierung",
    "manual_review": "Manuell hinzugefügt",
}


def write_recommendations(
    results,
    json_path=RECOMMENDATIONS_JSON,
):
    """Write every job that passed the rule-based prefilter."""
    recommendations = [
        recommendation_for_job(job)
        for job in results["included"]
    ]
    json_file = Path(json_path)
    write_json_atomic(json_file, {"recommendations": recommendations})


def recommendation_for_job(job):
    """Reduce one current job to fields needed for a decision."""
    recommendation = {
        "id": job["id"],
        "title": job["title"],
        "company": job["company"],
        "locations": job.get("locations", []),
        "work_mode": job.get("work_mode"),
        "remote_percentage": job.get("remote_percentage"),
        "career_levels": job.get("career_levels", []),
        "published_at": job.get("published_at"),
        "fetched_at": job.get("fetched_at"),
        "cache_stale": bool(job.get("cache_stale")),
        "first_seen_at": job.get("first_seen_at"),
        "match_percent": job.get("match_percent"),
        "role_group": job.get("role_group"),
        "role_label": format_role_group(job),
        "experience_level": job.get("experience_level"),
        "url": primary_url(job),
        "source_links": source_links(job),
        "international": is_international_listing(job),
    }
    if job.get("location_precheck"):
        recommendation["location_precheck"] = job["location_precheck"]
    if job.get("prefilter_warning"):
        recommendation["prefilter_warning"] = job["prefilter_warning"]
    return recommendation


def is_international_listing(job):
    """Recognize broad scopes and clearly international feed listings."""
    locations = [
        str(value).strip()
        for value in job.get("locations", [])
        if str(value).strip()
    ]
    location = " ".join(locations)
    normalized = location.casefold()
    source_names = {
        source.get("source")
        for source in job.get("sources", job.get("source_links", []))
        if isinstance(source, dict)
    }
    exclusively_from_international_feeds = bool(
        source_names and source_names <= INTERNATIONAL_REMOTE_SOURCES
    )
    words = set(re.findall(r"[a-zäöüß]+", normalized))
    if words & INTERNATIONAL_LOCATION_TERMS:
        return True
    if any(country in normalized for country in FOREIGN_COUNTRY_TERMS):
        return True
    if exclusively_from_international_feeds and text_is_mainly_english(
        f"{job.get('title', '')} {job.get('description_clean', '')}"
    ):
        return True
    return False


def primary_url(job):
    """Return the preferred listing URL from serialized source data."""
    sources = job.get("sources", [])
    application_url = next(
        (
            source.get("application_url")
            for source in sources
            if source.get("application_url")
        ),
        None,
    )
    return application_url or (sources[0].get("url", "") if sources else "")


def source_links(job):
    """Return every distinct listing URL with its source identifier."""
    links = []
    seen_urls = set()
    for source in job.get("sources", []):
        candidates = []
        if source.get("application_url"):
            candidates.append(("original", source["application_url"]))
        candidates.append((source.get("source", "listing"), source.get("url", "")))
        for source_name, url in candidates:
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            links.append({"source": source_name, "url": url})
    return links


def format_locations(job):
    """Return serialized job locations as display text."""
    return ", ".join(job.get("locations", [])) or "unbekannt"


def format_role_group(job):
    """Return a readable label for one rule-based role category."""
    value = job.get("role_group")
    return ROLE_LABELS.get(value, str(value or "Allgemeine IT").replace("_", " "))


def format_remote(job):
    """Return structured remote fields as display text."""
    percentage = job.get("remote_percentage")
    if percentage is not None:
        return f"{percentage}%"
    if job.get("work_mode") == "hybrid":
        return "homeoffice"
    return "0%"
