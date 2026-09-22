"""Local half of the hybrid worker schedule: only StepStone and Remotely.

Both are blocked from Azure IPs, so they run from here against the shared
Azure database instead; see docs/postgresql.md for the reliable-sources half
that runs in Azure.

Runs inside the same Docker image as the Azure worker rather than the local
.venv: a native Windows psycopg connection to the Azure database was found to
return stale reads (a data snapshot from hours earlier, never catching up),
while the identical code running in a Linux container connects correctly.
The root cause wasn't identified; running in Docker sidesteps it.

Needs a scoped service principal for Blob access, since there's no Managed
Identity or interactive az-CLI session inside the container - see
infrastructure/storage.tf (storage_blob_data_contributor_local_docker) and
.env.docker-local (gitignored, not the same credential as any Azure-hosted
identity).
"""

import os
import re
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
IMAGE = "acrjobfinder.azurecr.io/jobfinder:azure-v9"
STORAGE_ACCOUNT = "stjobfindere64bfdce"
STORAGE_CONTAINER = "application-documents"
REVIEW_HOST = "jobfinder-review.ashyisland-3b6e9522.francecentral.azurecontainerapps.io"
LOCAL_ONLY_SOURCES = {"stepstone", "remotely"}
ALL_SOURCE_NAMES = [
    "arbeitnow",
    "arbeitsagentur",
    "bytewerk",
    "compose_it",
    "css",
    "edag",
    "german_tech_jobs",
    "get_in_it",
    "himalayas",
    "jobicy",
    "jumo",
    "manual",
    "nethinks",
    "proemion",
    "remotely",
    "rhoenenergie",
    "startup_jobs",
    "stepstone",
    "studysmarter",
]


def read_dotenv(path):
    """Read simple KEY=VALUE lines, ignoring comments and blanks."""
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key] = value
    return values


def container_environment():
    """Build the -e KEY=VALUE pairs the container needs, none of it inherited."""
    postgres = read_dotenv(PROJECT_DIR / ".env.postgres-azure")
    database_url = re.sub(
        r"sslrootcert=[^&]+",
        "sslrootcert=/etc/ssl/certs/ca-certificates.crt",
        postgres["JOBFINDER_DATABASE_URL"],
    )
    service_principal = read_dotenv(PROJECT_DIR / ".env.docker-local")
    values = {
        "JOBFINDER_DATABASE_URL": database_url,
        "JOBFINDER_DOCUMENTS_BACKEND": "blob",
        "JOBFINDER_STORAGE_ACCOUNT": STORAGE_ACCOUNT,
        "JOBFINDER_STORAGE_CONTAINER": STORAGE_CONTAINER,
        "JOBFINDER_REVIEW_HOST": REVIEW_HOST,
        "AZURE_CLIENT_ID": service_principal["AZURE_CLIENT_ID"],
        "AZURE_TENANT_ID": service_principal["AZURE_TENANT_ID"],
        "AZURE_CLIENT_SECRET": service_principal["AZURE_CLIENT_SECRET"],
    }
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if webhook:
        values["DISCORD_WEBHOOK_URL"] = webhook
    return values


def main():
    """Run the containerized finder with every source excluded except StepStone and Remotely."""
    exclude = ",".join(
        name for name in ALL_SOURCE_NAMES if name not in LOCAL_ONLY_SOURCES
    )
    env_args = []
    for key, value in container_environment().items():
        env_args += ["-e", f"{key}={value}"]
    command = [
        "docker",
        "run",
        "--rm",
        *env_args,
        "--entrypoint",
        "python",
        IMAGE,
        "run_finder.py",
        "--exclude-sources",
        exclude,
    ]
    result = subprocess.run(command)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
