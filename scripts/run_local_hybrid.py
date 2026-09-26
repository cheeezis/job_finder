"""Local half of the hybrid worker schedule: only StepStone and Remotely.

Both are blocked from Azure IPs, so they run from here against the shared
Azure database instead; see docs/postgresql.md for the reliable-sources half
that runs in Azure.

Runs the image the Azure worker currently uses, looked up through the host's
az-CLI session on every start, so both halves always share one code version.
Running in Docker rather than the local .venv also sidestepped stale reads
seen from a native Windows psycopg connection to the Azure database; that
root cause is still unexplained.

Inside the container there is no Managed Identity or az-CLI session, so Blob
access needs a scoped service principal - see infrastructure/storage.tf
(storage_blob_data_contributor_local_docker) and .env.docker-local
(gitignored, not the same credential as any Azure-hosted identity).

A Windows task starts this unattended, where a failure would leave no trace.
So the script starts Docker Desktop when its engine does not answer (and
stops it again afterwards), keeps each run's output in data/logs for two
weeks and reports every failure to Discord.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from job_finder.workflow.notifications import DiscordWebhookClient, NotificationError  # noqa: E402

REGISTRY = "acrjobfinder"
RESOURCE_GROUP = "rg-jobfinder"
WORKER_JOB = "jobfinder-worker"
STORAGE_ACCOUNT = "stjobfindere64bfdce"
STORAGE_CONTAINER = "application-documents"
REVIEW_HOST = "jobfinder-review.ashyisland-3b6e9522.francecentral.azurecontainerapps.io"
LOCAL_ONLY_SOURCES = "stepstone,remotely"
LOG_DIR = PROJECT_DIR / "data" / "logs"
LOG_DAYS = 14
DOCKER_START_SECONDS = 300
DOCKER_READY_SECONDS = 60


class RunFailed(Exception):
    """A step of the run failed; the message is short and free of secrets."""


class HybridLog:
    """Print each line and keep it in this run's log file."""

    def __init__(self, directory=None, now=None):
        directory = Path(directory or LOG_DIR)
        now = now or datetime.now()
        directory.mkdir(parents=True, exist_ok=True)
        remove_old_logs(directory, now)
        self.path = directory / f"hybrid-{now:%Y%m%d-%H%M%S}.log"
        self.file = self.path.open("w", encoding="utf-8")

    def line(self, text):
        print(text, flush=True)
        self.file.write(f"{text}\n")
        self.file.flush()

    def close(self):
        self.file.close()


def remove_old_logs(directory, now):
    """Delete this script's logs older than LOG_DAYS days."""
    cutoff = (now - timedelta(days=LOG_DAYS)).timestamp()
    for path in directory.glob("hybrid-*.log"):
        if path.stat().st_mtime < cutoff:
            path.unlink()


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
        # The container is removed after the run, so a ZIP backup would be lost.
        "JOBFINDER_SKIP_RUN_BACKUP": "1",
        "AZURE_CLIENT_ID": service_principal["AZURE_CLIENT_ID"],
        "AZURE_TENANT_ID": service_principal["AZURE_TENANT_ID"],
        "AZURE_CLIENT_SECRET": service_principal["AZURE_CLIENT_SECRET"],
    }
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if webhook:
        values["DISCORD_WEBHOOK_URL"] = webhook
    # The image only carries the example settings; the personal file stays on this disk.
    settings = PROJECT_DIR / "user_settings.local.yaml"
    if settings.exists():
        values["JOBFINDER_USER_SETTINGS"] = settings.read_text(encoding="utf-8")
    return values


def az(*args, log):
    """Run an Azure CLI command through the host session and return its output."""
    executable = shutil.which("az")
    if executable is None:
        raise RunFailed("Azure CLI (az) nicht gefunden")
    result = subprocess.run(
        [executable, *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode:
        log.line(result.stderr.strip())
        raise RunFailed(f"az {' '.join(args[:2])} fehlgeschlagen, az-Anmeldung prüfen")
    return result.stdout.strip()


def deployed_image(log):
    """Return the image the Azure worker currently runs."""
    return az(
        "containerapp",
        "job",
        "show",
        "--name",
        WORKER_JOB,
        "--resource-group",
        RESOURCE_GROUP,
        "--query",
        "properties.template.containers[0].image",
        "--output",
        "tsv",
        log=log,
    )


def docker_answers():
    """Return whether the Docker engine answers."""
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def start_docker(log):
    """Start Docker Desktop when its engine does not answer; return whether this run did."""
    if docker_answers():
        return False
    log.line("Docker Desktop läuft nicht und wird gestartet …")
    try:
        subprocess.run(
            ["docker", "desktop", "start", "--timeout", str(DOCKER_START_SECONDS)],
            capture_output=True,
            timeout=DOCKER_START_SECONDS + 60,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RunFailed("Docker Desktop ließ sich nicht starten") from error
    # The engine may need a moment after Docker Desktop reports itself started.
    deadline = time.monotonic() + DOCKER_READY_SECONDS
    while not docker_answers():
        if time.monotonic() > deadline:
            raise RunFailed("Docker Desktop ließ sich nicht starten")
        time.sleep(5)
    log.line("Docker Desktop läuft.")
    return True


def stop_docker(log):
    """Stop Docker Desktop again after a run that started it."""
    try:
        subprocess.run(["docker", "desktop", "stop"], capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        log.line("Docker Desktop ließ sich nicht beenden.")
        return
    log.line("Docker Desktop wieder beendet.")


def pull(image, log):
    """Fetch the worker's image from the registry."""
    az("acr", "login", "--name", REGISTRY, log=log)
    result = subprocess.run(
        ["docker", "pull", "--quiet", image],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        log.line(result.stderr.strip())
        raise RunFailed("das Image ließ sich nicht laden")


def run_container(image, log):
    """Run the finder in the container with its output in the log; return its exit code."""
    env_args = []
    for key, value in container_environment().items():
        env_args += ["-e", f"{key}={value}"]
    # This command line carries secrets; only the container's output is logged.
    command = [
        "docker",
        "run",
        "--rm",
        *env_args,
        "--entrypoint",
        "python",
        image,
        "run_finder.py",
        "--only-sources",
        LOCAL_ONLY_SOURCES,
    ]
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as process:
        for line in process.stdout:
            log.line(line.rstrip("\n"))
    return process.returncode


def report_failure(reason, log):
    """Tell Discord that the local run failed and which log holds the details."""
    log.line(f"Lokaler Lauf fehlgeschlagen: {reason}")
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        log.line("Keine Discord-Meldung: DISCORD_WEBHOOK_URL ist nicht gesetzt.")
        return
    message = f"⚠️ Lokaler Lauf (StepStone/Remotely) fehlgeschlagen: {reason}. Log: {log.path.name}"
    try:
        DiscordWebhookClient(webhook).send({"content": message})
    except NotificationError as error:
        log.line(f"Discord-Meldung fehlgeschlagen: {error}")


def main():
    """Run the containerized finder with only StepStone and Remotely; report any failure."""
    log = HybridLog()
    started_docker = False
    try:
        started_docker = start_docker(log)
        image = deployed_image(log)
        log.line(f"Image des Azure-Workers: {image}")
        pull(image, log)
        returncode = run_container(image, log)
        if returncode:
            raise RunFailed(f"der Lauf im Container endete mit Code {returncode}")
        log.line("Lokaler Lauf beendet.")
    except RunFailed as failure:
        report_failure(str(failure), log)
        raise SystemExit(1) from failure
    except Exception as error:
        report_failure(f"unerwarteter Fehler ({type(error).__name__})", log)
        raise
    finally:
        if started_docker:
            stop_docker(log)
        log.close()


if __name__ == "__main__":
    # Redirected output must not crash on characters the console encoding lacks.
    sys.stdout.reconfigure(errors="replace")
    main()
