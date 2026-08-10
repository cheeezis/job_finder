"""Load local search settings without publishing personal values."""

from pathlib import Path

import yaml


PROJECT_DIR = Path(__file__).resolve().parent.parent
LOCAL_SETTINGS_PATH = PROJECT_DIR / "user_settings.local.yaml"
EXAMPLE_SETTINGS_PATH = PROJECT_DIR / "user_settings.example.yaml"
SETTINGS_PATH = (
    LOCAL_SETTINGS_PATH if LOCAL_SETTINGS_PATH.exists() else EXAMPLE_SETTINGS_PATH
)


def load_user_settings(path=SETTINGS_PATH):
    settings_path = Path(path)
    try:
        values = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"Einstellungen konnten nicht gelesen werden: {settings_path}") from error
    except yaml.YAMLError as error:
        raise ValueError(f"Einstellungen enthalten ungueltiges YAML: {settings_path}") from error

    if not isinstance(values, dict):
        raise ValueError("Einstellungen muessen ein Objekt sein")
    search = require_mapping(values.get("search"), "search")
    matching = require_mapping(values.get("matching"), "matching")
    require_text(search.get("local_location"), "search.local_location")
    require_text(search.get("local_postal_code"), "search.local_postal_code")
    require_positive_int(search.get("local_radius_km"), "search.local_radius_km")
    require_text(
        matching.get("preferred_location_label"),
        "matching.preferred_location_label",
    )
    require_text_list(matching.get("local_places"), "matching.local_places")
    require_text_list(
        matching.get("profile_domain_keywords"),
        "matching.profile_domain_keywords",
        allow_empty=True,
    )
    require_optional_positive_int(
        matching.get("salary_target_eur"),
        "matching.salary_target_eur",
    )
    require_optional_positive_int(
        matching.get("salary_minimum_eur"),
        "matching.salary_minimum_eur",
    )
    return values


def require_mapping(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} muss ein Objekt sein")
    return value


def require_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} muss ein nicht-leerer Text sein")
    return value


def require_positive_int(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} muss eine positive Ganzzahl sein")
    return value


def require_optional_positive_int(value, name):
    if value is None:
        return None
    return require_positive_int(value, name)


def require_text_list(value, name, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{name} muss eine Liste sein")
    for item in value:
        require_text(item, name)
    return value


USER_SETTINGS = load_user_settings()
