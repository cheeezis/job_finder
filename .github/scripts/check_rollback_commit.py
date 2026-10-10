"""Check an image's CI commit against revision 0007 without database access."""

import os
import re
import subprocess
import sys

# First main commit requiring the table model after contract revision 0007.
CONTRACT_COMMIT = "c55e17787bbe6e95cc6666771f9f890f3c4c4cf6"


def check(tags, *, repository=".", contract_commit=CONTRACT_COMMIT, allow_precontract=False, reason=""):
    """Fail closed on unknown provenance; only a known older commit can be overridden."""
    prefixes = {tag.removeprefix("sha-") for tag in tags if re.fullmatch(r"sha-[0-9a-f]{12}", tag)}
    if not prefixes:
        raise ValueError("Kein überprüfbarer CI-Commit für dieses Image.")
    commits = set()
    try:
        for prefix in prefixes:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", "--end-of-options", prefix + "^{commit}"],
                cwd=repository,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode:
                raise ValueError("Image-Commit fehlt oder ist mehrdeutig; Rollback abgebrochen.")
            commits.add(result.stdout.strip())
        if len(commits) != 1:
            raise ValueError("Mehrere Image-Commits; Rollback abgebrochen.")
        commit = commits.pop()
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", contract_commit, commit],
            cwd=repository,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("Commit-Herkunft konnte nicht geprüft werden.") from error
    if ancestry.returncode not in (0, 1):
        raise ValueError("Contract-Grenze konnte nicht geprüft werden.")
    if ancestry.returncode == 1 and not (allow_precontract and reason.strip()):
        raise ValueError("Image liegt vor Contract 0007; explizite Freigabe mit Begründung erforderlich.")
    return commit


def main():
    try:
        check(
            sys.stdin.read().split(),
            allow_precontract=os.environ.get("ALLOW_PRECONTRACT") == "true",
            reason=os.environ.get("ROLLBACK_REASON", ""),
        )
    except ValueError as error:
        print(f"::error::{error}")
        return 1
    print("Image-Commit und Contract-Grenze geprüft.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
