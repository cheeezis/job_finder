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
    """Write analyzed jobs plus current interesting jobs awaiting LLM output."""
    recommendations = [
        recommendation_for_job(job)
        for job in results["included"]
        if job.get("llm_result") or job.get("workflow_status") == "interesting"
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


def render_recommendations(recommendations):
    """Render final recommendations without prefilter diagnostics."""
    lines = ["# Job-Empfehlungen", ""]
    if not recommendations:
        return "\n".join(lines + ["Keine KI-Empfehlungen in diesem Lauf.", ""])

    for job in recommendations:
        if job.get("llm_unavailable"):
            lines.extend(
                [
                    f"## KI offen | {job['title']}",
                    "",
                    f"- Firma: {job['company']}",
                    f"- Ort: {format_locations(job)}",
                    f"- Remote: {format_remote(job)}",
                    f"- URL: {job['url']}",
                    "",
                    job["summary"],
                    "",
                ]
            )
            continue
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
    application_url = next(
        (
            source.get("application_url")
            for source in sources
            if source.get("application_url")
        ),
        None,
    )
    return application_url or (sources[0].get("url", "") if sources else "")


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
