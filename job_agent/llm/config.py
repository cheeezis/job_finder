"""Central configuration for productive LLM analysis."""

from dataclasses import dataclass
from pathlib import Path

from job_agent.paths import LLM_CACHE_FILE


@dataclass(frozen=True)
class LlmSettings:
    """Model, inference, and cache settings used by the agent."""

    model: str = "gpt-5.4-mini"
    timeout_seconds: int = 300
    reasoning_effort: str = "low"
    max_output_tokens: int = 8192
    cache_path: Path = LLM_CACHE_FILE


DEFAULT_LLM_SETTINGS = LlmSettings()
