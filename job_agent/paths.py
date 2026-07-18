"""Generated data locations shared by the agent modules."""

from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
INTERNAL_DIR = DATA_DIR / "internal"
OUTPUT_DIR = DATA_DIR / "output"
LOG_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"

JOBS_FILE = INTERNAL_DIR / "jobs.json"
MEMORY_FILE = INTERNAL_DIR / "seen_jobs.json"
ARBEITSAGENTUR_CACHE_FILE = INTERNAL_DIR / "arbeitsagentur_cache.json"
STEPSTONE_CACHE_FILE = INTERNAL_DIR / "stepstone_cache.json"
GET_IN_IT_CACHE_FILE = INTERNAL_DIR / "get_in_it_cache.json"
ARBEITNOW_CACHE_FILE = INTERNAL_DIR / "arbeitnow_cache.json"
JUMO_CACHE_FILE = INTERNAL_DIR / "jumo_cache.json"
EDAG_CACHE_FILE = INTERNAL_DIR / "edag_cache.json"
CSS_CACHE_FILE = INTERNAL_DIR / "css_cache.json"
PROEMION_CACHE_FILE = INTERNAL_DIR / "proemion_cache.json"
NETHINKS_CACHE_FILE = INTERNAL_DIR / "nethinks_cache.json"
LLM_CACHE_FILE = INTERNAL_DIR / "llm_cache.json"
NOTIFICATION_STATE_FILE = INTERNAL_DIR / "notifications.json"
RECOMMENDATIONS_JSON = OUTPUT_DIR / "recommendations.json"
RECOMMENDATIONS_MARKDOWN = OUTPUT_DIR / "recommendations.md"
