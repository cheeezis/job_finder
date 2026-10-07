"""Load search settings without publishing personal values.

Containers receive the YAML text in JOBFINDER_USER_SETTINGS (in Azure from a
Key Vault secret), because the ignored local file is not part of the image.
Without that variable the local file is used, and without it the example.

Pydantic models check the settings; current_settings() reads them on first
use, not on import, and use_settings() lets tests and the demo hand in their
own without touching the environment.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PrivateAttr, StrictInt, ValidationError

from job_finder.paths import PROJECT_DIR

SETTINGS_ENV = "JOBFINDER_USER_SETTINGS"
LOCAL_SETTINGS_PATH = PROJECT_DIR / "user_settings.local.yaml"
EXAMPLE_SETTINGS_PATH = PROJECT_DIR / "user_settings.example.yaml"
SETTINGS_PATH = LOCAL_SETTINGS_PATH if LOCAL_SETTINGS_PATH.exists() else EXAMPLE_SETTINGS_PATH


def nonblank(value):
    if not value.strip():
        raise ValueError("darf nicht leer sein")
    return value


Text = Annotated[str, AfterValidator(nonblank)]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
TextList = Annotated[list[Text], Field(min_length=1)]


class Section(BaseModel):
    # Unknown keys, e.g. from older settings files, are ignored as before.
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


class CommuterLocation(Section):
    search_location: Text
    aliases: TextList
    excluded_aliases: list[Text] = []
    minimum_remote_percentage: Annotated[StrictInt, Field(gt=0, le=100)]


class SearchSettings(Section):
    local_location: Text
    local_postal_code: Text
    local_radius_km: PositiveInt
    # Without these the search uses the built-in terms (job_finder/matching/config.py).
    terms: TextList | None = None
    stepstone_terms: TextList | None = None
    commuter_terms: TextList | None = None


class MatchingSettings(Section):
    preferred_location_label: Text
    local_places: TextList
    commuter_locations: list[CommuterLocation] = []
    profile_domain_keywords: list[Text]
    salary_target_eur: PositiveInt | None = None
    salary_minimum_eur: PositiveInt | None = None


class SourceSettings(Section):
    # Company career pages to search; without the list, all of them.
    companies: list[Text] | None = None


class UserSettings(Section):
    search: SearchSettings
    matching: MatchingSettings
    sources: SourceSettings = SourceSettings()
    _mapping: dict = PrivateAttr(default_factory=dict)
    _source: str = PrivateAttr(default="")

    @property
    def mapping(self) -> dict[str, Any]:
        """Return the parsed YAML as it was written; the agent reads its own section from it."""
        return self._mapping

    @property
    def source(self) -> str:
        """Name where the settings came from: the variable or the file."""
        return self._source


def load_user_settings(path=SETTINGS_PATH):
    """Load and check the settings of a YAML file; raise ValueError naming the first problem.

    See user_settings.example.yaml for the format. An explicit path is
    useful for isolated validation and tests.
    """
    settings_path = Path(path)
    try:
        text = settings_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"Einstellungen konnten nicht gelesen werden: {settings_path}") from error
    return parse_user_settings(text, settings_path)


def parse_user_settings(text, source):
    """Check settings YAML; source names its origin in messages and in UserSettings.source."""
    try:
        values = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"Einstellungen enthalten ungueltiges YAML: {source}") from error
    if not isinstance(values, dict):
        raise ValueError("Einstellungen muessen ein Objekt sein")
    try:
        settings = UserSettings.model_validate(values)
    except ValidationError as error:
        problem = error.errors()[0]
        field = ".".join(str(part) for part in problem["loc"])
        raise ValueError(f"Einstellung {field} ist ungueltig: {problem['msg']} ({source})") from None
    settings._mapping = values
    settings._source = str(getattr(source, "name", source))
    return settings


def configured_user_settings(environ=os.environ):
    """Return the active settings and the name of their source."""
    if environ.get(SETTINGS_ENV):
        settings = parse_user_settings(environ[SETTINGS_ENV], SETTINGS_ENV)
    else:
        settings = load_user_settings()
    return settings, settings.source


_active: list[UserSettings] = []


def current_settings():
    """Return the settings in use, reading them on first use."""
    if not _active:
        _active.append(configured_user_settings()[0])
    return _active[-1]


@contextmanager
def use_settings(settings):
    """Use the given settings until the block ends, e.g. in a test or the demo."""
    _active.append(settings)
    try:
        yield settings
    finally:
        _active.pop()
