import json
from datetime import date
from pathlib import Path


MEMORY_FILE = "data/seen_jobs.json"


def load_memory(path=MEMORY_FILE):
    memory_path = Path(path)
    if not memory_path.exists():
        return {}
    return json.loads(memory_path.read_text(encoding="utf-8"))


def save_memory(memory, path=MEMORY_FILE):
    Path(path).write_text(
        json.dumps(memory, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def update_memory(jobs, memory):
    today = date.today().isoformat()
    new_count = 0
    known_count = 0

    for job in jobs:
        url = job.get("url")
        if not url:
            continue

        if url in memory:
            known_count += 1
            job["is_new"] = False
            memory[url]["last_seen"] = today
            memory[url]["title"] = job.get("title", memory[url].get("title", ""))
            memory[url]["company"] = job.get("company", memory[url].get("company", ""))
            continue

        new_count += 1
        job["is_new"] = True
        memory[url] = {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "first_seen": today,
            "last_seen": today,
            "status": "new",
        }

    return {
        "new": new_count,
        "known": known_count,
    }
