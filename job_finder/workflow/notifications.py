"""Queue and send compact Discord summaries for new job recommendations."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from job_finder.paths import NOTIFICATION_STATE_FILE
from job_finder.persistence.state_compat import (
    NOTIFICATION_STATE_VERSION as STATE_VERSION,
)
from job_finder.persistence.state_compat import decode_notification_state
from job_finder.persistence.storage import read_json, write_json_atomic
from job_finder.workflow.reporting import (
    format_remote,
    format_role_group,
    is_visible_in_default_review,
    primary_url,
)

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
    review_host=None,
    state_path=NOTIFICATION_STATE_FILE,
    client=None,
    now=None,
):
    """Update the persistent queue and optionally send eligible Discord cards.

    results contains included and excluded job dictionaries from the
    scoring pipeline. Even send=False writes queue changes to
    state_path; it only prevents delivery. With send=True, use client
    when supplied or construct a client from webhook_url.

    Return queue, eligibility and delivery counters together with
    configuration_error. Missing webhook configuration is reported in
    that field. Delivery failures remain pending for a later run and
    increment failed; filesystem and malformed-state errors propagate.
    """
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    state = load_notification_state(state_path)
    candidates, stats = _update_queue(results, state, timestamp)
    save_notification_state(state, state_path)
    if not send or not candidates:
        return stats
    if not webhook_url:
        stats["configuration_error"] = "DISCORD_WEBHOOK_URL ist nicht gesetzt"
        return stats

    webhook_client = client or DiscordWebhookClient(webhook_url)
    for chunk in notification_chunks(candidates, review_host=review_host):
        keys = [key for key, _job in chunk]
        try:
            webhook_client.send(
                discord_payload([job for _key, job in chunk], review_host=review_host)
            )
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


def _update_queue(results, state, timestamp):
    """Update pending entries and calculate counters without sending or saving."""
    jobs_by_key = {}
    queued = 0
    current_new = 0
    eligible_new = 0

    # A job may have been queued in an earlier run but be excluded after a
    # stricter general rule or an updated posting. It must not remain queued.
    for job in results.get("excluded", []):
        state["pending"].pop(notification_key(job), None)

    for job in results["included"]:
        key = notification_key(job)
        jobs_by_key[key] = job
        is_new_job = bool(job.get("is_new"))
        if is_new_job:
            current_new += 1
        if not is_notifiable(job):
            state["pending"].pop(key, None)
            continue
        if is_new_job:
            eligible_new += 1
        if is_new_job and key not in state["sent"] and key not in state["pending"]:
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
        "current_new": current_new,
        "eligible_new": eligible_new,
        "already_notified": max(eligible_new - len(candidates), 0),
        "sent": 0,
        "failed": 0,
        "configuration_error": None,
    }
    return candidates, stats


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
    eligible = notifications.get("eligible_new", summary["review_new"])
    source_warnings = exceptional_source_text(sources)
    detail_warnings = detail_failure_text(summary.get("detail_failures", []))
    color = (
        0xD99A2B
        if failed
        or detail_warnings
        or any(source["status"] in {"failed", "partial"} for source in sources)
        else 0x2E8B57
    )
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
        "",
        "**Quellen**",
        source_health_text(sources),
    ]
    if source_warnings:
        lines.append(source_warnings)
    if detail_warnings:
        lines.append(detail_warnings)
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
    """Return whether one prefiltered result belongs in Discord notifications.

    Matches what the default review actually shows; a job hidden behind an
    extra filter (Junior-Hybrid, international remote) is never notified.
    """
    return job.get(
        "workflow_status", "new"
    ) in NOTIFIABLE_STATUSES and is_visible_in_default_review(job)


def notification_key(job):
    """Identify a job independently of later content or scoring changes."""
    return job["id"]


def pending_entry(job, timestamp):
    """Create auditable retry state for one unsent job."""
    return {
        "job_id": job["id"],
        "title": job["title"],
        "attempts": 0,
        "last_error": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def notification_chunks(candidates, *, review_host=None):
    """Group jobs within Discord's embed count and character limits.

    Size each card exactly as it is sent, including the optional review link.
    """
    chunks = []
    current = []
    current_characters = 0
    for candidate in candidates:
        embed = discord_embed(candidate[1], review_host=review_host)
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


def discord_payload(jobs, *, review_host=None):
    """Build one mention-safe message containing actionable job cards."""
    count = len(jobs)
    label = "Stelle" if count == 1 else "Stellen"
    return {
        "content": f"**{count} {label} zur Sichtung**",
        "embeds": [discord_embed(job, review_host=review_host) for job in jobs],
        "allowed_mentions": {"parse": []},
    }


def discord_embed(job, *, review_host=None):
    """Render a quiet, compact card with the facts needed for a first look."""
    locations = ", ".join(job.get("locations", [])) or "unbekannt"
    role = format_role_group(job)
    remote = format_remote(job)
    fields = [
        {
            "name": "Kurzcheck",
            "value": truncate(
                f"Neu · {role} · Vorfilter {job.get('match_percent', 0)}/100",
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
    ]
    link = review_url(job, review_host)
    if link:
        fields.append(
            {"name": "Review", "value": f"[Stelle öffnen]({link})", "inline": True}
        )
    return {
        "title": truncate(job["title"], 256),
        "url": primary_url(job),
        "description": truncate(
            f"**{job.get('company') or 'Unbekannte Firma'}**\n"
            f"📍 {locations} · 🏠 {remote}",
            4096,
        ),
        "color": 0x2E8B57,
        "fields": fields,
        "footer": {"text": "Titel anklicken, um die Originalanzeige zu öffnen."},
    }


def review_url(job, review_host):
    """Build a deep link into the review page for one job, when configured."""
    if not review_host:
        return None
    return f"https://{review_host}/review?job={job['id']}"


def format_count(value):
    """Format integer counters with German thousands separators."""
    return f"{int(value):,}".replace(",", ".")


def source_health_text(sources):
    """Summarize source coverage while keeping failures visible."""
    counts = Counter(source["status"] for source in sources)
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


def detail_failure_text(detail_failures):
    """Name sources whose prefiltered candidates lack detail text."""
    warnings = [
        f"{failure['label']} {format_count(failure['failed'])} Kandidat(en)"
        for failure in detail_failures
    ]
    return f"⚠️ Details fehlen: {', '.join(warnings)}" if warnings else ""


def source_status_label(status):
    """Return a compact German label for an exceptional source state."""
    return "nur teilweise geladen" if status == "partial" else "fehlgeschlagen"


def embed_character_count(embed):
    """Count fields included in Discord's combined 6000-character limit."""
    return (
        len(embed.get("title", ""))
        + len(embed.get("description", ""))
        + sum(
            len(field.get("name", "")) + len(field.get("value", ""))
            for field in embed.get("fields", [])
        )
        + len(embed.get("footer", {}).get("text", ""))
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
    """Load delivery state and fold legacy content keys into stable job IDs."""
    state_path = Path(path)
    document = read_json(
        state_path, {"version": STATE_VERSION, "sent": {}, "pending": {}}
    )
    return decode_notification_state(document)


def save_notification_state(state, path=NOTIFICATION_STATE_FILE):
    """Persist notification state via an atomic replacement."""
    write_json_atomic(path, {"version": STATE_VERSION, **state})
