"""Generated data locations shared by the Job Finder modules."""

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
INTERNAL_DIR = DATA_DIR / "internal"
OUTPUT_DIR = DATA_DIR / "output"
LOG_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"
# Mount a persistent directory here when the review runs in a container.
APPLICATION_DOCUMENTS_DIR = Path(
    os.environ.get("JOBFINDER_DOCUMENTS_DIR", INTERNAL_DIR / "application_documents")
)

JOBS_FILE = INTERNAL_DIR / "jobs.json"
MEMORY_FILE = INTERNAL_DIR / "job_finder.sqlite3"
REMOTELY_LINKEDIN_STATUS_FILE = INTERNAL_DIR / "remotely_linkedin_status.json"
MANUAL_CACHE_FILE = INTERNAL_DIR / "manual_jobs_cache.json"
NOTIFICATION_STATE_FILE = INTERNAL_DIR / "notifications.json"
RECOMMENDATIONS_JSON = OUTPUT_DIR / "recommendations.json"


def cache_file(source):
    """Return the runtime cache location of one source."""
    return INTERNAL_DIR / f"{source}_cache.json"
