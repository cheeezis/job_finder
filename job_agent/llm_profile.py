"""Load the personal facts supplied to language models."""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml


PROFILE_PATH = Path(__file__).resolve().parent.parent / "profile.yaml"


class ProfileValidationError(ValueError):
    """Raised when the LLM profile is missing required information."""


@dataclass(frozen=True)
class LlmProfile:
    """Validated, JSON-compatible personal profile and its version."""

    version: int
    data: dict


def load_llm_profile(path=PROFILE_PATH):
    """Load and validate a profile without affecting search or scoring."""
    profile_path = Path(path)
    try:
        document = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ProfileValidationError(
            f"Profil konnte nicht gelesen werden: {profile_path}"
        ) from error
    except yaml.YAMLError as error:
        raise ProfileValidationError(
            f"Profil enthaelt ungueltiges YAML: {profile_path}"
        ) from error

    root = require_mapping(document, "profile.yaml")
    version = require_positive_int(root.get("version"), "version")
    profile = require_mapping(root.get("profile"), "profile")
    require_text(profile.get("summary"), "profile.summary")
    require_nonnegative_int(
        profile.get("professional_experience_years"),
        "profile.professional_experience_years",
    )
    require_mapping(profile.get("location"), "profile.location")

    for section in (
        "education",
        "experience",
        "projects",
        "current_learning",
        "languages",
        "truth_rules",
    ):
        require_list(root.get(section), section)

    for section in ("skills", "strengths", "career_preferences"):
        require_mapping(root.get(section), section)

    for rule in root["truth_rules"]:
        require_text(rule, "truth_rules")

    for index, course in enumerate(root["current_learning"]):
        item = require_mapping(course, f"current_learning[{index}]")
        require_text(item.get("name"), f"current_learning[{index}].name")
        require_text(item.get("status"), f"current_learning[{index}].status")

    return LlmProfile(
        version=version,
        data=make_json_compatible(root),
    )


def require_mapping(value, name):
    if not isinstance(value, dict):
        raise ProfileValidationError(f"{name} muss ein Objekt sein")
    return value


def require_list(value, name):
    if not isinstance(value, list) or not value:
        raise ProfileValidationError(f"{name} muss eine nicht-leere Liste sein")
    return value


def require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ProfileValidationError(f"{name} muss ein nicht-leerer Text sein")
    return value


def require_positive_int(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ProfileValidationError(f"{name} muss eine positive Ganzzahl sein")
    return value


def require_nonnegative_int(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProfileValidationError(
            f"{name} muss eine nicht-negative Ganzzahl sein"
        )
    return value


def make_json_compatible(value):
    """Convert YAML date objects recursively for later JSON prompts."""
    if isinstance(value, dict):
        return {key: make_json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [make_json_compatible(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
