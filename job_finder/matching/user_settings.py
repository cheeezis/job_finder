"""Load search settings without publishing personal values.

Containers receive the YAML text in JOBFINDER_USER_SETTINGS (in Azure from a
Key Vault secret), because the ignored local file is not part of the image.
Without that variable the local file is used, and without it the example.
"""

import os
from pathlib import Path

import yaml

from job_finder.paths import PROJECT_DIR

SETTINGS_ENV = "JOBFINDER_USER_SETTINGS"
LOCAL_SETTINGS_PATH = PROJECT_DIR / "user_settings.local.yaml"
EXAMPLE_SETTINGS_PATH = PROJECT_DIR / "user_settings.example.yaml"
SETTINGS_PATH = LOCAL_SETTINGS_PATH if LOCAL_SETTINGS_PATH.exists() else EXAMPLE_SETTINGS_PATH


def load_user_settings(path=SETTINGS_PATH):
    """Load and validate the search and matching sections of a YAML file.

    Return the parsed mapping without filling in missing optional keys
    and ignore unknown keys. Raise ValueError for unreadable files, invalid YAML or invalid
    field values. See user_settings.example.yaml for the input schema.

    The default path is selected at module import: use the local file
    when present, otherwise the example. USER_SETTINGS is also loaded
    at import, so running processes need a restart after configuration
    changes. An explicit path is useful for isolated validation/tests.
    """
    settings_path = Path(path)
    try:
        text = settings_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"Einstellungen konnten nicht gelesen werden: {settings_path}") from error
    return parse_user_settings(text, settings_path)


def parse_user_settings(text, source):
    """Validate settings YAML; source only names its origin in error messages."""
    try:
        values = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"Einstellungen enthalten ungueltiges YAML: {source}") from error

    if not isinstance(values, dict):
        raise ValueError("Einstellungen muessen ein Objekt sein")
    search = require_mapping(values.get("search"), "search")
    matching = require_mapping(values.get("matching"), "matching")
    require_text(search.get("local_location"), "search.local_location")
    require_text(search.get("local_postal_code"), "search.local_postal_code")
    require_positive_int(search.get("local_radius_km"), "search.local_radius_km")
    require_text(matching.get("preferred_location_label"), "matching.preferred_location_label")
    require_text_list(matching.get("local_places"), "matching.local_places")
    require_commuter_locations(
        matching.get("commuter_locations", []), "matching.commuter_locations"
    )
    require_text_list(
        matching.get("profile_domain_keywords"),
        "matching.profile_domain_keywords",
        allow_empty=True,
    )
    require_optional_positive_int(matching.get("salary_target_eur"), "matching.salary_target_eur")
    require_optional_positive_int(matching.get("salary_minimum_eur"), "matching.salary_minimum_eur")
    return values


def require_mapping(value, name):
    """Return a dictionary value or raise ValueError naming its setting."""
    if not isinstance(value, dict):
        raise ValueError(f"{name} muss ein Objekt sein")
    return value


def require_text(value, name):
    """Return nonblank text or raise ValueError naming its setting."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} muss ein nicht-leerer Text sein")
    return value


def require_positive_int(value, name):
    """Accept positive integers, excluding booleans, or raise ValueError."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} muss eine positive Ganzzahl sein")
    return value


def require_optional_positive_int(value, name):
    """Accept None or a positive integer for an optional setting."""
    if value is None:
        return None
    return require_positive_int(value, name)


def require_text_list(value, name, allow_empty=False):
    """Validate text entries and the optional empty-list allowance."""
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{name} muss eine Liste sein")
    for item in value:
        require_text(item, name)
    return value


def require_commuter_locations(value, name):
    """Validate commuter aliases and remote thresholds from 1 to 100."""
    if not isinstance(value, list):
        raise ValueError(f"{name} muss eine Liste sein")
    for index, item in enumerate(value):
        item_name = f"{name}[{index}]"
        require_mapping(item, item_name)
        require_text(item.get("search_location"), f"{item_name}.search_location")
        require_text_list(item.get("aliases"), f"{item_name}.aliases")
        require_text_list(
            item.get("excluded_aliases", []), f"{item_name}.excluded_aliases", allow_empty=True
        )
        percentage = require_positive_int(
            item.get("minimum_remote_percentage"), f"{item_name}.minimum_remote_percentage"
        )
        if percentage > 100:
            raise ValueError(f"{item_name}.minimum_remote_percentage darf hoechstens 100 sein")
    return value


def configured_user_settings(environ=os.environ):
    """Return the active settings and the name of their source."""
    if environ.get(SETTINGS_ENV):
        return parse_user_settings(environ[SETTINGS_ENV], SETTINGS_ENV), SETTINGS_ENV
    return load_user_settings(), SETTINGS_PATH.name


USER_SETTINGS, SETTINGS_SOURCE = configured_user_settings()
