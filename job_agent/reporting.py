"""Compact machine- and human-readable recommendation output."""

import json
from pathlib import Path

from job_agent.paths import RECOMMENDATIONS_JSON, RECOMMENDATIONS_MARKDOWN


RECOMMENDATION_LABELS = {
    "strong_match": "sehr passend",
    "match": "passend",
    "borderline": "vielleicht passend",
    "not_recommended": "nicht empfohlen",
}
CONFIDENCE_LABELS = {
    "high": "hoch",
    "medium": "mittel",
    "low": "niedrig",
}


def write_recommendations(
    results,
    json_path=RECOMMENDATIONS_JSON,
    markdown_path=RECOMMENDATIONS_MARKDOWN,
):
    """Write only jobs that have a final LLM recommendation."""
    recommendations = [
        recommendation_for_job(job)
        for job in results["included"]
        if job.get("llm_result")
    ]
    json_file = Path(json_path)
    markdown_file = Path(markdown_path)
    json_file.parent.mkdir(parents=True, exist_ok=True)
    markdown_file.parent.mkdir(parents=True, exist_ok=True)
    json_file.write_text(
        json.dumps(
            {"recommendations": recommendations},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    markdown_file.write_text(
        render_recommendations(recommendations),
        encoding="utf-8",
    )
    print(f"KI-Empfehlungen gespeichert in {json_file}")
    print(f"Lesbare Empfehlungen gespeichert in {markdown_file}")


def recommendation_for_job(job):
    """Reduce one analyzed job to fields needed for a decision."""
    llm_result = job["llm_result"]
    return {
        "id": job["id"],
        "title": job["title"],
        "company": job["company"],
        "locations": job.get("locations", []),
        "work_mode": job.get("work_mode"),
        "remote_percentage": job.get("remote_percentage"),
        "published_at": job.get("published_at"),
        "url": primary_url(job),
        "llm_score": job["llm_score"],
        **llm_result,
    }


def render_recommendations(recommendations):
    """Render final recommendations without prefilter diagnostics."""
    lines = ["# Job-Empfehlungen", ""]
    if not recommendations:
        return "\n".join(lines + ["Keine KI-Empfehlungen in diesem Lauf.", ""])

    for job in recommendations:
        lines.extend(
            [
                f"## {job['llm_score']}% | {job['title']}",
                "",
                f"- Firma: {job['company']}",
                f"- Ort: {format_locations(job)}",
                f"- Remote: {format_remote(job)}",
                f"- Empfehlung: {RECOMMENDATION_LABELS[job['recommendation']]}",
                f"- Sicherheit: {CONFIDENCE_LABELS[job['confidence']]}",
                f"- URL: {job['url']}",
                "",
                job["summary"],
                "",
            ]
        )
        append_list(lines, "Wichtigste Aufgaben", job.get("tasks", []))
        append_list(lines, "Wichtigste Anforderungen", job.get("requirements", []))
        append_list(lines, "Passende Erfahrungen", job.get("matching_evidence", []))
        append_list(lines, "Luecken", job.get("gaps", []))
        append_list(lines, "Risiken", job.get("risks", []))
    return "\n".join(lines)


def append_list(lines, heading, values):
    """Append one non-empty recommendation section."""
    if not values:
        return
    lines.extend([f"### {heading}", ""])
    lines.extend(f"- {value}" for value in values)
    lines.append("")


def primary_url(job):
    """Return the preferred listing URL from serialized source data."""
    sources = job.get("sources", [])
    return sources[0].get("url", "") if sources else ""


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
