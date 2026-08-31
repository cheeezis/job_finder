"""Queue and send compact Discord summaries for new job recommendations."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from job_finder.paths import NOTIFICATION_STATE_FILE
from job_finder.reporting import (
    format_remote,
    format_role_group,
    is_international_listing,
    primary_url,
)


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
    current_updates = 0
    eligible_updates = 0
    default_review_updates = 0

    # A job may have been queued in an earlier run but be excluded after a
    # stricter general rule or an updated posting. It must not remain queued.
    for job in results.get("excluded", []):
        state["pending"].pop(notification_key(job), None)

    for job in results["included"]:
        key = notification_key(job)
        jobs_by_key[key] = job
        is_current_update = (
            job.get("is_new")
            or job.get("review_update_pending")
            or job.get("content_changed")
        )
        if is_current_update:
            current_updates += 1
        if not is_notifiable(job):
            state["pending"].pop(key, None)
            continue
        if is_current_update:
            eligible_updates += 1
            if is_visible_in_default_review(job):
                default_review_updates += 1
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
        "current_updates": current_updates,
        "eligible_updates": eligible_updates,
        "default_review_updates": default_review_updates,
        "already_notified": max(eligible_updates - len(candidates), 0),
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
    """Render one calm, vertically readable summary of the completed run."""
    sources = summary["sources"]
    notifications = summary.get("notifications", {})
    sent = notifications.get("sent", 0)
    failed = notifications.get("failed", 0)
    eligible = notifications.get("eligible_updates", summary["review_updates"])
    default_review = notifications.get("default_review_updates", eligible)
    hidden_by_default = max(eligible - default_review, 0)
    source_warnings = exceptional_source_text(sources)
    color = 0xD99A2B if failed or any(
        source["status"] in {"failed", "partial"} for source in sources
    ) else 0x2E8B57
    lines = [
        f"Laufzeit: **{summary['duration']}**",
        "",
        "**Ergebnis**",
        (
            f"{format_count(summary['jobs_total'])} Stellen erfasst · "
            f"{format_count(summary['jobs_new'])} neu"
        ),
        (
            f"{format_count(summary['included'])} im Vorfilter · "
            f"{format_count(summary['excluded'])} ausgeschlossen"
        ),
        "",
        "**Benachrichtigungen**",
        (
            f"{format_count(eligible)} zur Benachrichtigung · "
            f"{format_count(sent)} gesendet · {format_count(failed)} fehlgeschlagen"
        ),
        (
            f"{format_count(default_review)} direkt im Standard-Review sichtbar · "
            f"{format_count(hidden_by_default)} über Zusatzfilter"
        ),
        "",
        "**Quellen**",
        source_health_text(sources),
    ]
    if source_warnings:
        lines.append(source_warnings)
    lines.extend(["", "**Neue Treffer nach Quelle**", new_source_text(sources)])
    return {
        "embeds": [
            {
                "title": "Job Finder · Lauf abgeschlossen",
                "description": "\n".join(lines),
                "color": color,
            }
        ],
        "allowed_mentions": {"parse": []},
    }


def is_notifiable(job):
    """Return whether one prefiltered result belongs in Discord notifications."""
    return job.get("workflow_status", "new") in NOTIFIABLE_STATUSES


def is_visible_in_default_review(job):
    """Mirror the review page's default visibility for an updated job."""
    return not is_international_listing(job) and not str(
        job.get("location_precheck") or ""
    ).startswith("Junior-Hybrid")


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
    """Build one mention-safe message containing actionable job cards."""
    count = len(jobs)
    label = "Stelle" if count == 1 else "Stellen"
    return {
        "content": f"**{count} {label} zur Sichtung**",
        "embeds": [discord_embed(job) for job in jobs],
        "allowed_mentions": {"parse": []},
    }


def discord_embed(job):
    """Render a quiet, compact card with the facts needed for a first look."""
    locations = ", ".join(job.get("locations", [])) or "unbekannt"
    role = format_role_group(job)
    kind = notification_kind(job)
    remote = format_remote(job)
    return {
        "title": truncate(job["title"], 256),
        "url": primary_url(job),
        "description": truncate(
            f"**{job.get('company') or 'Unbekannte Firma'}**\n"
            f"📍 {locations} · 🏠 {remote}",
            4096,
        ),
        "color": 0x2E8B57 if kind == "Neu" else 0x3678C2,
        "fields": [
            {
                "name": "Kurzcheck",
                "value": truncate(
                    f"{kind} · {role} · Vorfilter {job.get('match_percent', 0)}/100",
                    1024,
                ),
                "inline": False,
            },
            {
                "name": "Einstieg",
                "value": truncate(job.get("experience_level") or "nicht erkannt", 1024),
                "inline": True,
            },
            {
                "name": "Standortprüfung",
                "value": truncate(
                    job.get("location_precheck") or "keine Auffälligkeit erkannt",
                    1024,
                ),
                "inline": False,
            },
        ],
        "footer": {"text": "Titel anklicken, um die Originalanzeige zu öffnen."},
    }


def format_count(value):
    """Format integer counters with German thousands separators."""
    return f"{int(value):,}".replace(",", ".")


def source_health_text(sources):
    """Summarize source coverage while keeping failures visible."""
    counts = {
        status: sum(source["status"] == status for source in sources)
        for status in ("success", "partial", "empty", "failed")
    }
    parts = [f"{len(sources)} geprüft", f"{counts['success']} erfolgreich"]
    if counts["partial"]:
        parts.append(f"{counts['partial']} teilweise")
    if counts["empty"]:
        parts.append(f"{counts['empty']} ohne Treffer")
    if counts["failed"]:
        parts.append(f"{counts['failed']} fehlgeschlagen")
    return " · ".join(parts)


def new_source_text(sources):
    """List only sources that contributed new jobs."""
    lines = [
        f"**{source['label']}** {format_count(source['new'])}"
        for source in sources
        if source.get("new")
    ]
    return truncate(" · ".join(lines) or "Keine neuen Treffer", 1024)


def exceptional_source_text(sources):
    """Keep partial and failed sources separate from successful new results."""
    warnings = [
        f"{source['label']} {source_status_label(source['status'])}"
        for source in sources
        if source["status"] in {"partial", "failed"}
    ]
    return f"⚠️ {', '.join(warnings)}" if warnings else ""


def source_status_label(status):
    """Return a compact German label for an exceptional source state."""
    return "nur teilweise geladen" if status == "partial" else "fehlgeschlagen"


def notification_kind(job):
    """Distinguish first discoveries from persistent review updates."""
    return "Neu" if job.get("is_new") else "Aktualisiert"


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
