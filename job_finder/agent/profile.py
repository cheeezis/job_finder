"""The personal profile the agent compares every job with.

Containers receive the YAML text in JOBFINDER_PROFILE (in Azure from a Key
Vault secret), because the ignored local file is not part of the image.
Without that variable the local file is used; without either, the agent
stays off.
"""

import os

import yaml

from job_finder.paths import PROJECT_DIR

PROFILE_ENV = "JOBFINDER_PROFILE"
LOCAL_PROFILE_PATH = PROJECT_DIR / "profile.local.yaml"


def configured_profile(environ=os.environ, path=LOCAL_PROFILE_PATH):
    """Return the profile text as written, comments included, and the name of its source.

    Raise ValueError when no profile exists or it is not a YAML mapping; the
    agent then stays off and names the reason.
    """
    if environ.get(PROFILE_ENV):
        text, source = environ[PROFILE_ENV], PROFILE_ENV
    elif path.exists():
        text, source = path.read_text(encoding="utf-8"), path.name
    else:
        raise ValueError(f"Profil fehlt: weder {PROFILE_ENV} noch {path.name}")
    try:
        values = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"Profil ist kein gültiges YAML ({source})") from error
    if not isinstance(values, dict):
        raise ValueError(f"Profil muss ein YAML-Objekt sein ({source})")
    return text, source
