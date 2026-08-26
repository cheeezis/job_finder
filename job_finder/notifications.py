"""Queue and send compact Discord summaries for new job recommendations."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from job_finder.paths import NOTIFICATION_STATE_FILE
from job_finder.reporting import format_remote, format_role_group, primary_url


STATE_VERSION = 2
NOTIFIABLE_STATUSES = {"new", "review", "interesting", "inquiry"}
MAX_EMBEDS = 10
MAX_EMBED_CHARACTERS = 6000


class NotificationError(RuntimeError):
    """A Discord delivery failed without exposing the secret webhook URL."""


class DiscordWebhookClient:
    """Send JSON messages to one incoming Discord webhook."""

    def __init__(self, webhook_url, timeout=20):
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send(self, payload):
        """Post one message and wait for Discord's delivery confirmation."""
        request = Request(
            webhook_url_with_confirmation(self.webhook_url),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "job-finder/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status not in {200, 204}:
                    raise NotificationError(
                        f"Discord antwortete mit HTTP {response.status}"
                    )
        except HTTPError as error:
            raise NotificationError(
                f"Discord antwortete mit HTTP {error.code}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise NotificationError("Discord ist nicht erreichbar") from error


def process_notifications(
    results,
    *,
    send=False,
    webhook_url=None,
    state_path=NOTIFICATION_STATE_FILE,
    client=None,
    now=None,
):
    """Queue eligible jobs and optionally send pending Discord summaries."""
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    state = load_notification_state(state_path)
    jobs_by_key = {}
    queued = 0

    # A job may have been queued in an earlier run but be excluded after a
    # stricter general rule or an updated posting. It must not remain queued.
    for job in results.get("excluded", []):
        state["pending"].pop(notification_key(job), None)

    for job in results["included"]:
        key = notification_key(job)
        jobs_by_key[key] = job
        if not is_notifiable(job):
            state["pending"].pop(key, None)
            continue
        is_current_update = job.get("is_new") or job.get("content_changed")
        if (
            is_current_update
            and key not in state["sent"]
            and key not in state["pending"]
        ):
            state["pending"][key] = pending_entry(job, timestamp)
            queued += 1

    candidates = [
        (key, jobs_by_key[key])
        for key in state["pending"]
        if key in jobs_by_key and is_notifiable(jobs_by_key[key])
    ]
    stats = {
        "queued": queued,
        "ready": len(candidates),
        "sent": 0,
        "failed": 0,
        "configuration_error": None,
    }
    save_notification_state(state, state_path)
    if not send or not candidates:
        return stats
    if not webhook_url:
        stats["configuration_error"] = "DISCORD_WEBHOOK_URL ist nicht gesetzt"
        return stats

    webhook_client = client or DiscordWebhookClient(webhook_url)
    for chunk in notification_chunks(candidates):
        keys = [key for key, _job in chunk]
        try:
            webhook_client.send(discord_payload([job for _key, job in chunk]))
        except NotificationError as error:
            for key in keys:
                entry = state["pending"][key]
                entry["attempts"] += 1
                entry["last_error"] = str(error)
                entry["updated_at"] = timestamp
            stats["failed"] += len(keys)
        else:
            for key in keys:
                entry = state["pending"].pop(key)
                state["sent"][key] = {
                    "job_id": entry["job_id"],
                    "sent_at": timestamp,
                }
            stats["sent"] += len(keys)
        save_notification_state(state, state_path)
    return stats


def send_run_summary(summary, *, webhook_url, client=None):
    """Send one compact operational summary after a requested Job Finder run."""
    if not webhook_url:
        return "DISCORD_WEBHOOK_URL ist nicht gesetzt"

    webhook_client = client or DiscordWebhookClient(webhook_url)
    try:
        webhook_client.send(run_summary_payload(summary))
    except NotificationError as error:
        return str(error)
    return None


def run_summary_payload(summary):
    """Render a compact, mention-safe summary that fits one Discord message."""
    sources = summary["sources"]
    lines = [
        "**📊 Job Finder – Lauf abgeschlossen**",
        "",
        f"Laufzeit: {summary['duration']}",
        f"Jobs: {summary['jobs_total']} gesamt "
        f"({summary['jobs_new']} neu, {summary['jobs_known']} bekannt)",
        f"Vorfilter: {summary['included']} zur Prüfung, "
        f"{summary['excluded']} ausgeschlossen",
        f"Review: {summary['review_updates']} neu oder aktualisiert",
    ]
    lines.extend(["", "**Quellen**"])
    for source in sources:
        if source["status"] == "failed":
            lines.append(f"• {source['label']}: Fehler")
        elif source["jobs"]:
            lines.append(
                f"• {source['label']}: {source['jobs']} Stellen, "
                f"{source['new']} neu"
            )
        else:
            lines.append(f"• {source['label']}: keine Treffer")
    return {
        "content": "\n".join(lines),
        "allowed_mentions": {"parse": []},
    }


def is_notifiable(job):
    """Return whether one prefiltered result belongs in Discord notifications."""
    return job.get("workflow_status", "new") in NOTIFIABLE_STATUSES


def notification_key(job):
    """Identify one job content version independently of score changes."""
    payload = {
        "id": job["id"],
        "title": job.get("title"),
        "company": job.get("company"),
        "locations": job.get("locations", []),
        "description_clean": job.get("description_clean"),
        "work_mode": job.get("work_mode"),
        "remote_percentage": job.get("remote_percentage"),
        "employment_type": job.get("employment_type"),
        "career_levels": job.get("career_levels", []),
        "salary_min_eur": job.get("salary_min_eur"),
        "salary_max_eur": job.get("salary_max_eur"),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def pending_entry(job, timestamp):
    """Create auditable retry state for one unsent content version."""
    return {
        "job_id": job["id"],
        "title": job["title"],
        "attempts": 0,
        "last_error": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def notification_chunks(candidates):
    """Group jobs within Discord's embed count and character limits."""
    chunks = []
    current = []
    current_characters = 0
    for candidate in candidates:
        embed = discord_embed(candidate[1])
        characters = embed_character_count(embed)
        if current and (
            len(current) >= MAX_EMBEDS
            or current_characters + characters > MAX_EMBED_CHARACTERS
        ):
            chunks.append(current)
            current = []
            current_characters = 0
        current.append(candidate)
        current_characters += characters
    if current:
        chunks.append(current)
    return chunks


def discord_payload(jobs):
    """Build one mention-safe Discord summary message."""
    count = len(jobs)
    label = "Stelle" if count == 1 else "Stellen"
    return {
        "content": f"**{count} neue oder aktualisierte {label} im Vorfilter**",
        "embeds": [discord_embed(job) for job in jobs],
        "allowed_mentions": {"parse": []},
    }


def discord_embed(job):
    """Render the compact fields needed to decide whether to open a job."""
    locations = ", ".join(job.get("locations", [])) or "unbekannt"
    role = format_role_group(job)
    return {
        "title": truncate(job["title"], 256),
        "url": primary_url(job),
        "color": 0x176B54,
        "fields": [
            {
                "name": "Firma",
                "value": truncate(job.get("company") or "unbekannt", 1024),
                "inline": True,
            },
            {
                "name": "Ort",
                "value": truncate(locations, 1024),
                "inline": True,
            },
            {
                "name": "Remote",
                "value": format_remote(job),
                "inline": True,
            },
            {
                "name": "Vorfilter",
                "value": f"{job.get('match_percent', 0)}/100",
                "inline": True,
            },
            {
                "name": "Bereich",
                "value": truncate(role, 1024),
                "inline": True,
            },
            {
                "name": "Einstieg",
                "value": truncate(job.get("experience_level") or "nicht erkannt", 1024),
                "inline": True,
            },
            {
                "name": "Standortprüfung",
                "value": truncate(job.get("location_precheck") or "nicht erkannt", 1024),
                "inline": False,
            },
        ],
    }


def embed_character_count(embed):
    """Count fields included in Discord's combined 6000-character limit."""
    return (
        len(embed.get("title", ""))
        + len(embed.get("description", ""))
        + sum(
            len(field.get("name", "")) + len(field.get("value", ""))
            for field in embed.get("fields", [])
        )
    )


def truncate(value, limit):
    """Keep user-supplied text inside one Discord field limit."""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def webhook_url_with_confirmation(webhook_url):
    """Request a response only after Discord has stored the message."""
    parts = urlsplit(webhook_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["wait"] = "true"
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def load_notification_state(path=NOTIFICATION_STATE_FILE):
    """Load sent versions and pending delivery attempts."""
    state_path = Path(path)
    if not state_path.exists():
        return {"sent": {}, "pending": {}}
    document = json.loads(state_path.read_text(encoding="utf-8"))
    if document.get("version") == 1:
        # Version 1 queued only positive LLM recommendations. Preserve its sent
        # content keys, but do not release an obsolete pending backlog.
        return {"sent": document.get("sent", {}), "pending": {}}
    if document.get("version") != STATE_VERSION:
        raise ValueError("Benachrichtigungsstatus verwendet eine unbekannte Version")
    return {
        "sent": document.get("sent", {}),
        "pending": document.get("pending", {}),
    }


def save_notification_state(state, path=NOTIFICATION_STATE_FILE):
    """Persist notification state via an atomic replacement."""
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = state_path.with_suffix(f"{state_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(
            {"version": STATE_VERSION, **state},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(state_path)
