"""Switch and cost limits of the agent from the personal settings.

The agent runs only with ``agent.enabled: true``. A missing or invalid
``agent`` section keeps it off without stopping the finder; ``reason`` says
why, so the run log can name it.
"""

from dataclasses import dataclass
from decimal import Decimal

DEFAULT_LIMITS = {
    "job_max_cost_eur": Decimal("0.08"),
    "job_max_model_calls": 8,
    "job_max_tool_calls": 6,
    "job_max_web_searches": 3,
    "daily_max_cost_eur": Decimal("1.00"),
    "monthly_max_cost_eur": Decimal("20.00"),
}
# Catch typos such as a missing decimal point or an extra zero. These are not
# budgets; the monthly ceiling stays well below the student credit.
CEILINGS = {
    "job_max_cost_eur": Decimal("0.25"),
    "job_max_model_calls": 40,
    "job_max_tool_calls": 30,
    "job_max_web_searches": 10,
    "daily_max_cost_eur": Decimal("5"),
    "monthly_max_cost_eur": Decimal("50"),
}
# 0 switches the paid web search off while the agent keeps working.
MAY_BE_ZERO = {"job_max_web_searches"}


@dataclass(frozen=True)
class AgentLimits:
    job_max_cost_eur: Decimal
    job_max_model_calls: int
    job_max_tool_calls: int
    job_max_web_searches: int
    daily_max_cost_eur: Decimal
    monthly_max_cost_eur: Decimal


@dataclass(frozen=True)
class AgentSettings:
    enabled: bool
    limits: AgentLimits
    reason: str = ""
    # How long the model thinks before it answers; more costs more output tokens.
    reasoning_effort: str = "low"


REASONING_EFFORTS = ("minimal", "low", "medium", "high")


def agent_settings(values):
    """Read the agent section of the parsed settings; problems switch it off."""
    section = values.get("agent")
    if section is None:
        return AgentSettings(False, AgentLimits(**DEFAULT_LIMITS), "Abschnitt agent fehlt")
    try:
        limits = parse_limits(section)
    except ValueError as error:
        return AgentSettings(False, AgentLimits(**DEFAULT_LIMITS), str(error))
    effort = section.get("reasoning_effort", "low")
    if effort not in REASONING_EFFORTS:
        reason = f"agent.reasoning_effort muss einer von {', '.join(REASONING_EFFORTS)} sein"
        return AgentSettings(False, limits, reason)
    if section.get("enabled") is not True:
        return AgentSettings(False, limits, "agent.enabled ist nicht true", effort)
    return AgentSettings(True, limits, reasoning_effort=effort)


def parse_limits(section):
    """Merge given limits into the defaults; unknown keys are errors, not ignored."""
    if not isinstance(section, dict):
        raise ValueError("agent muss ein Objekt sein")
    unknown = sorted(section.keys() - DEFAULT_LIMITS.keys() - {"enabled", "reasoning_effort"})
    if unknown:
        raise ValueError(f"Unbekannte Einstellung agent.{unknown[0]}")
    limits = dict(DEFAULT_LIMITS)
    for name in DEFAULT_LIMITS.keys() & section.keys():
        limits[name] = parse_limit(name, section[name])
    if not (
        limits["job_max_cost_eur"] <= limits["daily_max_cost_eur"] <= limits["monthly_max_cost_eur"]
    ):
        raise ValueError(
            "agent: job_max_cost_eur <= daily_max_cost_eur <= monthly_max_cost_eur muss gelten"
        )
    return AgentLimits(**limits)


def parse_limit(name, value):
    """Return a positive count or euro amount up to its ceiling (some may be 0)."""
    ceiling = CEILINGS[name]
    if isinstance(ceiling, Decimal):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"agent.{name} muss ein Eurobetrag sein")
        value = Decimal(str(value))
    elif isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"agent.{name} muss eine Ganzzahl sein")
    may_be_zero = name in MAY_BE_ZERO
    if (value < 0 if may_be_zero else value <= 0) or value > ceiling:
        lower = "mindestens 0" if may_be_zero else "größer als 0"
        raise ValueError(f"agent.{name} muss {lower} und höchstens {ceiling} sein")
    return value
