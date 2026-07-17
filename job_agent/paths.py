"""Generated data locations shared by the agent modules."""

from pathlib import Path


INTERNAL_DIR = Path("data/internal")
OUTPUT_DIR = Path("data/output")

JOBS_FILE = INTERNAL_DIR / "jobs.json"
MEMORY_FILE = INTERNAL_DIR / "seen_jobs.json"
ARBEITSAGENTUR_CACHE_FILE = INTERNAL_DIR / "arbeitsagentur_cache.json"
STEPSTONE_CACHE_FILE = INTERNAL_DIR / "stepstone_cache.json"
GET_IN_IT_CACHE_FILE = INTERNAL_DIR / "get_in_it_cache.json"
LLM_CACHE_FILE = INTERNAL_DIR / "llm_cache.json"
RECOMMENDATIONS_JSON = OUTPUT_DIR / "recommendations.json"
RECOMMENDATIONS_MARKDOWN = OUTPUT_DIR / "recommendations.md"
