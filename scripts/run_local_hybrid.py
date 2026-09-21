"""Local half of the hybrid worker schedule: only StepStone and Remotely.

Both are blocked from Azure IPs, so they run from here against the shared
Azure database instead; see docs/postgresql.md for the reliable-sources half
that runs in Azure. Sets its own environment for this one subprocess call
only, so a plain `python run_finder.py` still defaults to the local
development database.
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOCAL_ONLY_SOURCES = {"stepstone", "remotely"}
ALL_SOURCE_NAMES = [
    "arbeitnow",
    "arbeitsagentur",
    "bytewerk",
    "compose_it",
    "css",
    "edag",
    "german_tech_jobs",
    "get_in_it",
    "himalayas",
    "jobicy",
    "jumo",
    "manual",
    "nethinks",
    "proemion",
    "remotely",
    "rhoenenergie",
    "startup_jobs",
    "stepstone",
    "studysmarter",
]


def azure_environment():
    """Layer Azure connection details onto the current environment."""
    env = dict(os.environ)
    for line in (
        (PROJECT_DIR / ".env.postgres-azure").read_text(encoding="utf-8").splitlines()
    ):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key] = value
    env["JOBFINDER_DOCUMENTS_BACKEND"] = "blob"
    env["JOBFINDER_STORAGE_ACCOUNT"] = "stjobfindere64bfdce"
    env["JOBFINDER_STORAGE_CONTAINER"] = "application-documents"
    env["JOBFINDER_REVIEW_HOST"] = (
        "jobfinder-review.ashyisland-3b6e9522.francecentral.azurecontainerapps.io"
    )
    return env


def main():
    """Run the finder with every source excluded except StepStone and Remotely."""
    exclude = ",".join(
        name for name in ALL_SOURCE_NAMES if name not in LOCAL_ONLY_SOURCES
    )
    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_DIR / "run_finder.py"),
            "--exclude-sources",
            exclude,
        ],
        cwd=PROJECT_DIR,
        env=azure_environment(),
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
