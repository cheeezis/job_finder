import json
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from job_agent.main import print_results, score_jobs
from job_agent.memory import load_memory, save_memory, update_memory
from job_agent.sources import arbeitsagentur
from job_agent.sources import get_in_it
from job_agent.sources import stepstone


SOURCES = [
    arbeitsagentur,
    stepstone,
    get_in_it,
]

JOBS_FILE = "data/jobs_imported.json"
MEMORY_FILE = "data/seen_jobs.json"


# Windows terminals may default to cp1252; job titles often contain Unicode.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main():
    """Run the full pipeline: collect jobs, update memory, then score."""
    print("1/3 Sammle Jobs aus Quellen")
    jobs = collect_jobs()
    Path(JOBS_FILE).write_text(
        json.dumps(jobs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"{len(jobs)} Job(s) gespeichert in {JOBS_FILE}")

    print("\n2/3 Aktualisiere Job-Gedaechtnis")
    memory = load_memory(MEMORY_FILE)
    memory_stats = update_memory(jobs, memory)
    save_memory(memory, MEMORY_FILE)
    print(f'Neue Jobs: {memory_stats["new"]}')
    print(f'Bekannte Jobs: {memory_stats["known"]}')
    print(f"Gedaechtnis gespeichert in {MEMORY_FILE}")

    print("\n3/3 Bewerte Jobs")
    results = score_jobs(jobs)
    write_review_files(results)
    print_results(results)


def collect_jobs():
    """Collect jobs from all configured sources and deduplicate by URL."""
    jobs = []
    seen_urls = set()

    for source in SOURCES:
        print(f"\nQuelle: {source.SOURCE_NAME}")
        source_jobs = source.fetch_jobs()
        print(f"{len(source_jobs)} Job(s) aus {source.SOURCE_NAME}")

        for job in source_jobs:
            url = job.get("url")
            dedupe_key = canonical_url(url)
            if dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)
            jobs.append(job)

    return jobs


def canonical_url(url):
    parts = urlsplit(url or "")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def write_review_files(results):
    """Write scored results in machine-readable and human-reviewable formats."""
    scored_file = Path("data/jobs_scored.json")
    review_file = Path("data/jobs_review.md")

    scored_file.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    review_file.write_text(render_review_markdown(results), encoding="utf-8")

    print(f"Bewertete Jobs gespeichert in {scored_file}")
    print(f"Review-Datei gespeichert in {review_file}")


def render_review_markdown(results):
    lines = [
        "# Job Review",
        "",
        "Diese Datei ist zum manuellen Durchgehen und Feintuning des Scorings gedacht.",
        "",
    ]

    append_section(lines, "Passende Jobs", results["included"])
    append_section(lines, "Ausgeschlossene Jobs", results["excluded"])
    return "\n".join(lines) + "\n"


def append_section(lines, title, jobs):
    lines.extend([f"## {title}", ""])

    for job in jobs:
        score = job.get("raw_score", 0)
        percent = job.get("match_percent", 0)
        new_marker = "NEU - " if job.get("is_new") else ""
        lines.extend(
            [
                f"### {new_marker}{percent}% | {score} Punkte | {job.get('title', '')}",
                "",
                f"- Firma: {job.get('company', '')}",
                f"- Quelle: {job.get('source', '')}",
                f"- Ort: {job.get('location', '')}",
                f"- Remote: {job.get('remote', '')}",
                f"- URL: {job.get('url', '')}",
                "- Gründe:",
            ]
        )
        for reason in job.get("reasons", []):
            lines.append(f"  - {reason}")

        description = compact_description(job.get("description", ""))
        if description:
            lines.extend(["", description])

        lines.append("")


def compact_description(description, max_length=900):
    text = " ".join(str(description or "").split())
    if not text:
        return ""
    if len(text) <= max_length:
        return f"> {text}"
    return f"> {text[:max_length].rstrip()}..."


if __name__ == "__main__":
    main()
