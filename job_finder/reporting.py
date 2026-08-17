"""Compact recommendation output for review and notifications."""

import json
import re
from pathlib import Path

from job_finder.paths import RECOMMENDATIONS_JSON


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


def write_recommendations(
    results,
    json_path=RECOMMENDATIONS_JSON,
):
    """Write analyzed jobs plus jobs whose LLM result is unavailable."""
    recommendations = [
        recommendation_for_job(job)
        for job in results["included"]
        if (
            job.get("llm_result")
            or job.get("llm_status") == "failed"
            or job.get("workflow_status") == "interesting"
        )
    ]
    json_file = Path(json_path)
    json_file.parent.mkdir(parents=True, exist_ok=True)
    json_file.write_text(
        json.dumps(
            {"recommendations": recommendations},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


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
        "url": primary_url(job),
        "source_links": source_links(job),
        "international": is_international_listing(job),
    }
    llm_result = job.get("llm_result")
    if llm_result:
        return {
            **recommendation,
            "llm_score": job["llm_score"],
            "llm_unavailable": False,
            **llm_result,
        }
    return {
        **recommendation,
        "llm_score": None,
        "llm_unavailable": True,
        "recommendation": None,
        "confidence": None,
        "summary": "KI-Bewertung derzeit nicht verfügbar.",
        "tasks": [],
        "requirements": [],
        "matching_evidence": [],
        "gaps": [],
        "risks": [],
    }


def is_international_listing(job):
    """Return whether a broad location label lacks an explicit Germany scope."""
    location = " ".join(str(value) for value in job.get("locations", []))
    normalized = location.casefold()
    if "deutschland" in normalized or "germany" in normalized:
        return False
    words = set(re.findall(r"[a-zäöüß]+", normalized))
    if words & INTERNATIONAL_LOCATION_TERMS:
        return True
    source_names = {
        source.get("source")
        for source in job.get("sources", job.get("source_links", []))
        if isinstance(source, dict)
    }
    return words == {"remote"} and bool(
        source_names and source_names <= INTERNATIONAL_REMOTE_SOURCES
    )


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


def format_remote(job):
    """Return structured remote fields as display text."""
    percentage = job.get("remote_percentage")
    if percentage is not None:
        return f"{percentage}%"
    if job.get("work_mode") == "hybrid":
        return "homeoffice"
    return "0%"
