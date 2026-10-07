"""Settings for tests: the public example with changes, handed in without the environment."""

import yaml

from job_finder.matching.user_settings import EXAMPLE_SETTINGS_PATH, parse_user_settings, use_settings


def example_settings(**sections):
    """Return the example settings with the given keys of each section replaced."""
    values = yaml.safe_load(EXAMPLE_SETTINGS_PATH.read_text(encoding="utf-8"))
    for section, changes in sections.items():
        values.setdefault(section, {}).update(changes)
    return parse_user_settings(yaml.safe_dump(values, allow_unicode=True), "Test")


def with_settings(**sections):
    """Use changed example settings in a block or for a whole test (works as a decorator)."""
    return use_settings(example_settings(**sections))
