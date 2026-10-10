"""Reject missing or inconsistent release phases before any Azure sign-in."""

import os

PHASE_VALUES = {
    "TF_VAR_runtime_identity_phase": {"legacy", "prepare", "split"},
    "TF_VAR_runtime_access_verified": {"true", "false"},
    "TF_VAR_database_auth_phase": {"password", "prepare", "entra"},
    "TF_VAR_database_entra_verified": {"true", "false"},
}


def validate(environ):
    """Mirror the phase prerequisites in runtime_access.tf and database_auth.tf."""
    for key, allowed in PHASE_VALUES.items():
        if environ.get(key) not in allowed:
            raise ValueError(f"{key}: fehlt oder ist ungültig.")
    split = environ["TF_VAR_runtime_identity_phase"] == "split"
    runtime_verified = environ["TF_VAR_runtime_access_verified"] == "true"
    auth = environ["TF_VAR_database_auth_phase"]
    if split and not runtime_verified:
        raise ValueError("split benötigt TF_VAR_runtime_access_verified=true.")
    if auth != "password" and not (split and runtime_verified):
        raise ValueError("Entra-Vorbereitung benötigt akzeptierte split-Zugänge.")
    if auth == "entra" and environ["TF_VAR_database_entra_verified"] != "true":
        raise ValueError("entra benötigt TF_VAR_database_entra_verified=true.")


def main():
    try:
        validate(os.environ)
    except ValueError as error:
        print(f"::error::{error}")
        return 1
    print("Release-Phasen vollständig und konsistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
