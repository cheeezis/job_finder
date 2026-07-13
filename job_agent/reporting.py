"""Machine-readable and compact human-readable scoring reports."""

import json
from collections import Counter, defaultdict
from pathlib import Path


SCORED_FILE = "data/jobs_scored.json"
REVIEW_FILE = "data/jobs_review.md"
EXCLUDED_EXAMPLES_PER_REASON = 12


def write_review_files(results, scored_path=SCORED_FILE, review_path=REVIEW_FILE):
    """Write every result as JSON and a compact Markdown review."""
    scored_file = Path(scored_path)
    review_file = Path(review_path)
    scored_file.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    review_file.write_text(render_review_markdown(results), encoding="utf-8")

    print(f"Bewertete Jobs gespeichert in {scored_file}")
    print(f"Review-Datei gespeichert in {review_file}")


def render_review_markdown(results):
    included = results["included"]
    excluded = results["excluded"]
    lines = [
        "# Job Review",
        "",
        f"- Passend: {len(included)}",
        f"- Ausgeschlossen: {len(excluded)}",
        "- Prozentwerte stammen aus einer festen 0-100-Rubrik.",
        "",
    ]

    append_job_section(lines, "Passende Jobs", included, include_description=True)
    append_exclusion_summary(lines, excluded)
    append_job_section(
        lines,
        "Ausgeschlossene Beispiele zum Gegenpruefen",
        select_excluded_examples(excluded),
        include_description=False,
    )
    return "\n".join(lines) + "\n"


def append_exclusion_summary(lines, excluded):
    lines.extend(["## Ausschluss-Uebersicht", ""])
    counts = Counter(job.get("reasons", ["Unbekannt"])[0] for job in excluded)
    for reason, count in counts.most_common():
        lines.append(f"- {count} x {reason}")
    lines.append("")


def select_excluded_examples(excluded):
    grouped = defaultdict(list)
    for job in excluded:
        reason = job.get("reasons", ["Unbekannt"])[0]
        if len(grouped[reason]) < EXCLUDED_EXAMPLES_PER_REASON:
            grouped[reason].append(job)

    examples = []
    for reason in sorted(grouped):
        examples.extend(grouped[reason])
    return examples


def append_job_section(lines, title, jobs, include_description):
    lines.extend([f"## {title}", ""])
    if not jobs:
        lines.extend(["Keine Jobs in dieser Gruppe.", ""])
        return

    for job in jobs:
        score = job.get("match_percent", 0)
        new_marker = "NEU - " if job.get("is_new") else ""
        lines.extend(
            [
                f"### {new_marker}{score}% | {job.get('title', '')}",
                "",
                "- Bewertung: [ ] passt  [ ] vielleicht  [ ] passt nicht",
                f"- Firma: {job.get('company', '')}",
                f"- Quelle: {format_sources(job)}",
                f"- Ort: {job.get('location', '')}",
                f"- Remote: {job.get('remote', '')}",
                f"- Erfahrung: {job.get('experience_level', '')}",
                f"- URL: {job.get('url', '')}",
                "- Gruende:",
            ]
        )
        for reason in job.get("reasons", []):
            lines.append(f"  - {reason}")

        if include_description:
            description = compact_description(job.get("description", ""))
            if description:
                lines.extend(["", description])

        lines.append("")


def format_sources(job):
    sources = job.get("sources") or [job.get("source", "")]
    duplicate_count = len(job.get("duplicate_urls", []))
    suffix = f" ({duplicate_count} Duplikat(e) zusammengefuehrt)" if duplicate_count else ""
    return ", ".join(source for source in sources if source) + suffix


def compact_description(description, max_length=700):
    text = " ".join(str(description or "").split())
    if not text:
        return ""
    if len(text) <= max_length:
        return f"> {text}"
    return f"> {text[:max_length].rstrip()}..."
