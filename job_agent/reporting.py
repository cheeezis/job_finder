"""Machine-readable and compact human-readable scoring reports."""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path


SCORED_FILE = "data/jobs_scored.json"
REVIEW_FILE = "data/jobs_review.md"
FEEDBACK_FILE = "data/job_feedback.json"
EXCLUDED_EXAMPLES_PER_REASON = 12


def write_review_files(
    results,
    scored_path=SCORED_FILE,
    review_path=REVIEW_FILE,
    feedback_path=FEEDBACK_FILE,
):
    """Write every result as JSON and a compact Markdown review."""
    scored_file = Path(scored_path)
    review_file = Path(review_path)
    feedback_file = Path(feedback_path)
    feedback = load_feedback(feedback_file)
    if review_file.exists():
        feedback.update(parse_review_feedback(review_file.read_text(encoding="utf-8")))

    scored_file.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    feedback_file.write_text(
        json.dumps(feedback, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    review_file.write_text(
        render_review_markdown(results, feedback),
        encoding="utf-8",
    )

    print(f"Bewertete Jobs gespeichert in {scored_file}")
    print(f"Review-Datei gespeichert in {review_file}")


def render_review_markdown(results, feedback=None):
    """Render scoring results as a compact manual-review document."""
    feedback = feedback or {}
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

    append_job_section(
        lines,
        "Passende Jobs",
        included,
        include_description=True,
        feedback=feedback,
    )
    append_exclusion_summary(lines, excluded)
    append_job_section(
        lines,
        "Ausgeschlossene Beispiele zum Gegenpruefen",
        select_excluded_examples(excluded),
        include_description=False,
        feedback=feedback,
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


def append_job_section(lines, title, jobs, include_description, feedback):
    lines.extend([f"## {title}", ""])
    if not jobs:
        lines.extend(["Keine Jobs in dieser Gruppe.", ""])
        return

    for job in jobs:
        score = job.get("match_percent", 0)
        new_marker = "NEU - " if job.get("is_new") else ""
        url = primary_url(job)
        lines.extend(
            [
                f"### {new_marker}{score}% | {job.get('title', '')}",
                "",
                format_feedback_line(feedback.get(url)),
                f"- Firma: {job.get('company', '')}",
                f"- Quelle: {format_sources(job)}",
                f"- Ort: {format_locations(job)}",
                f"- Remote: {format_remote(job)}",
                f"- Erfahrung: {job.get('experience_level', '')}",
                f"- URL: {url}",
                "- Gruende:",
            ]
        )
        for reason in job.get("reasons", []):
            lines.append(f"  - {reason}")

        if include_description:
            description = compact_description(job.get("description_clean", ""))
            if description:
                lines.extend(["", description])

        lines.append("")


def format_sources(job):
    sources = job.get("sources", [])
    names = list(
        dict.fromkeys(
            source.get("source", "")
            for source in sources
            if source.get("source")
        )
    )
    duplicate_count = max(0, len(sources) - 1)
    suffix = ""
    if duplicate_count:
        suffix = f" ({duplicate_count} Duplikat(e) zusammengefuehrt)"
    return ", ".join(names) + suffix


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


def compact_description(description, max_length=700):
    text = " ".join(str(description or "").split())
    if not text:
        return ""
    if len(text) <= max_length:
        return f"> {text}"
    return f"> {text[:max_length].rstrip()}..."


def load_feedback(path):
    """Load persisted manual ratings, tolerating a missing feedback file."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_review_feedback(markdown):
    """Extract checked ratings from an existing Markdown review."""
    feedback = {}
    current_rating = None

    for line in markdown.splitlines():
        if line.startswith("### "):
            current_rating = None
            continue

        if line.startswith("- Bewertung:"):
            current_rating = parse_feedback_line(line)
            continue

        if line.startswith("- URL:") and current_rating:
            url = line.removeprefix("- URL:").strip()
            if url:
                feedback[url] = current_rating

    return feedback


def parse_feedback_line(line):
    """Return the checked rating from one review line."""
    options = [
        ("passt nicht", r"\[[xX]\]\s*passt nicht"),
        ("vielleicht", r"\[[xX]\]\s*vielleicht"),
        ("passt", r"\[[xX]\]\s*passt(?:\s|$)"),
    ]
    for rating, pattern in options:
        if re.search(pattern, line):
            return rating
    return None


def format_feedback_line(rating):
    """Render one checkbox line with an optional persisted rating."""
    return (
        f"- Bewertung: {'[x]' if rating == 'passt' else '[ ]'} passt  "
        f"{'[x]' if rating == 'vielleicht' else '[ ]'} vielleicht  "
        f"{'[x]' if rating == 'passt nicht' else '[ ]'} passt nicht"
    )
