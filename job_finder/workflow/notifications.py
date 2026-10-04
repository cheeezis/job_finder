"""Queue and send compact Discord summaries for new job recommendations."""

import json
from collections import Counter
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request, urlopen

from job_finder.models import format_remote
from job_finder.paths import NOTIFICATION_STATE_FILE, RECOMMENDATIONS_JSON
from job_finder.persistence.database import in_transaction, lock, session_lock, transaction
from job_finder.persistence.storage import dataset_name, read_json, read_object, write_json_atomic
from job_finder.workflow.memory import load_workflow_statuses
from job_finder.workflow.reporting import (
    format_role_group,
    is_international_listing,
    is_visible_in_default_review,
    primary_url,
)

NOTIFIABLE_STATUSES = {"new", "review", "interesting", "inquiry", "waiting"}
MAX_EMBEDS = 10
MAX_EMBED_CHARACTERS = 6000
HEALTH_LABELS = {"partial": "teilweise", "empty": "ohne Treffer", "failed": "fehlgeschlagen"}
STATE_VERSION = 3
WARNING_COLOR = 0xD99A2B


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
            headers={"Content-Type": "application/json", "User-Agent": "job-finder/1.0"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status not in {200, 204}:
                    raise NotificationError(f"Discord antwortete mit HTTP {response.status}")
        except HTTPError as error:
            raise NotificationError(f"Discord antwortete mit HTTP {error.code}") from error
        except (URLError, TimeoutError, OSError) as error:
            raise NotificationError("Discord ist nicht erreichbar") from error


def process_notifications(
    results, *, send=False, webhook_url=None, review_host=None, state_path=NOTIFICATION_STATE_FILE, client=None
):
    """Queue jobs, then optionally dispatch their committed notification orders.

    The finder uses queue_notifications inside its publication transaction
    and deliver_notifications after commit. This wrapper also supports
    explicitly selected JSON exports and isolated notification tests.
    """
    if send and in_transaction():
        raise RuntimeError("Discord-Versand ist erst nach dem Commit erlaubt.")
    stats = queue_notifications(results, state_path=state_path)
    if not send:
        return stats
    return deliver_notifications(
        webhook_url=webhook_url, review_host=review_host, state_path=state_path, client=client, stats=stats
    )


@contextmanager
def edit_notification_state(path=NOTIFICATION_STATE_FILE):
    """Serialize read-modify-write operations and join a publication transaction."""
    name = dataset_name(path)
    with transaction() if name is not None else nullcontext() as connection:
        if name is not None:
            lock(connection, "dataset:" + name)
        state = load_notification_state(path)
        original = deepcopy(state)
        yield state
        if state != original:
            save_notification_state(state, path)


def queue_notifications(results, *, state_path=NOTIFICATION_STATE_FILE, aliases=None):
    """Persist stable job-ID orders with all card data, without external requests."""
    with edit_notification_state(state_path) as state:
        _merge_notification_ids(state, aliases or {})
        _candidates, stats = _update_queue(results, state, datetime.now(UTC).isoformat())
    return stats


def deliver_notifications(
    *, webhook_url=None, review_host=None, state_path=NOTIFICATION_STATE_FILE, client=None, stats=None
):
    """Retry durable orders independently of this run's source collection.

    A session lock prevents parallel senders, while queue edits and delivery
    acknowledgements use short transactions. A crash after Discord accepts a
    message but before acknowledgement can cause a repeat on the next run.
    """
    if in_transaction():
        raise RuntimeError("Discord-Versand ist erst nach dem Commit erlaubt.")
    stats = dict(stats) if stats is not None else notification_stats()
    name = dataset_name(state_path)
    delivery_lock = (
        session_lock("notification-delivery:" + name, busy_message="Ein anderer Discord-Versand ist bereits aktiv.")
        if name is not None
        else nullcontext()
    )
    with delivery_lock:
        candidates = _delivery_candidates(state_path)
        stats["ready"] = len(candidates)
        if not candidates:
            return stats
        if not webhook_url:
            stats["configuration_error"] = "DISCORD_WEBHOOK_URL ist nicht gesetzt"
            return stats
        webhook_client = client or DiscordWebhookClient(webhook_url)
        remaining = [key for key, _job in candidates]
        while remaining:
            # Re-read and size the next chunk: publication or review may have
            # updated or cancelled orders during the previous Discord request.
            current = dict(_delivery_candidates(state_path))
            refreshed = [(key, current[key]) for key in remaining if key in current]
            if not refreshed:
                break
            chunk = notification_chunks(refreshed, review_host=review_host)[0]
            attempted = {key for key, _job in chunk}
            remaining = [key for key in remaining if key not in attempted]
            timestamp = datetime.now(UTC).isoformat()
            error_text = None
            try:
                webhook_client.send(discord_payload([job for _key, job in chunk], review_host=review_host))
            except NotificationError as error:
                error_text = str(error)
                stats["failed"] += len(chunk)
            else:
                stats["sent"] += len(chunk)
            with edit_notification_state(state_path) as state:
                for key, _job in chunk:
                    entry = state["pending"].get(key)
                    if error_text is not None:
                        if entry is None:
                            continue
                        entry["attempts"] = entry.get("attempts", 0) + 1
                        entry["last_error"] = error_text
                        entry["updated_at"] = timestamp
                    else:
                        state["pending"].pop(key, None)
                        state["sent"][key] = {
                            "job_id": key,
                            "sent_at": timestamp,
                            "event_key": (entry or {}).get("event_key", f"job-found:{key}"),
                        }
    return stats


def _delivery_candidates(state_path):
    """Refresh eligibility and recover pre-outbox entries without resending sent jobs."""
    managed = dataset_name(state_path) is not None
    with edit_notification_state(state_path) as state:
        statuses = load_workflow_statuses(state["pending"]) if managed else {}
        recommendations = (
            {job["id"]: job for job in read_object(RECOMMENDATIONS_JSON, {}).get("recommendations", [])}
            if managed and any(not entry.get("payload") for entry in state["pending"].values())
            else {}
        )
        candidates = []
        for key, entry in list(state["pending"].items()):
            # The finder saves memory and the order together. An absent memory
            # ID has since been merged/deleted; its obsolete card must not send.
            if managed and (key not in statuses or statuses[key] not in NOTIFIABLE_STATUSES):
                state["pending"].pop(key)
                continue
            job = entry.get("payload")
            if not job and key in recommendations:
                # Old pending entries did not contain their card. Recover from the
                # committed publication without interpreting the job as a new find.
                old = recommendations[key]
                job = notification_job({**old, "sources": old.get("source_links", [])})
                entry["payload"] = job
            if not job:
                continue  # Keep unrecoverable legacy orders until the source returns.
            status = statuses.get(key, job.get("workflow_status", "new"))
            job = {**job, "workflow_status": status}
            if not is_notifiable(job):
                state["pending"].pop(key)
                continue
            candidates.append((key, job))
        return candidates


def notification_stats(**values):
    """Create queue and delivery counters for one invocation."""
    return {
        "queued": 0,
        "ready": 0,
        "current_new": 0,
        "eligible_new": 0,
        "already_notified": 0,
        "sent": 0,
        "failed": 0,
        "configuration_error": None,
        **values,
    }


def _merge_notification_ids(state, aliases):
    """Carry a finding's event and delivery state over to its canonical job ID."""
    for delivery_state in ("sent", "pending"):
        for old, target in aliases.items():
            seen = {old}
            while target in aliases and target not in seen:
                seen.add(target)
                target = aliases[target]
            if target == old or old not in state[delivery_state]:
                continue
            entry = state[delivery_state][old]
            if delivery_state == "pending":
                state["pending"].pop(old)
            # Keep sent old IDs as well: an already notified source ID must not
            # start a second event if it is encountered again in a later run.
            if delivery_state == "sent" or target not in state["sent"]:
                canonical = {**entry, "job_id": target, "event_key": entry.get("event_key", f"job-found:{old}")}
                if canonical.get("payload"):
                    canonical["payload"] = {**canonical["payload"], "id": target}
                state[delivery_state].setdefault(target, canonical)
            if target in state["sent"]:
                state["pending"].pop(target, None)


def _update_queue(results, state, timestamp):
    """Update pending entries and calculate counters without sending or saving."""
    jobs_by_key = {}
    queued = 0
    current_new = 0
    eligible_new = 0

    # A job may have been queued in an earlier run but be excluded after a
    # stricter general rule or an updated posting. It must not remain queued.
    for job in results.get("excluded", []):
        state["pending"].pop(job["id"], None)

    for job in results["included"]:
        key = job["id"]
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
        elif key in state["pending"]:
            state["pending"][key]["payload"] = notification_job(job)

    candidates = [
        (key, jobs_by_key[key]) for key in state["pending"] if key in jobs_by_key and is_notifiable(jobs_by_key[key])
    ]
    stats = notification_stats(
        queued=queued,
        ready=len(candidates),
        current_new=current_new,
        eligible_new=eligible_new,
        already_notified=max(eligible_new - len(candidates), 0),
    )
    return candidates, stats


def send_run_summary(summary, *, webhook_url):
    """Send one compact operational summary after a requested Job Finder run."""
    if not webhook_url:
        return "DISCORD_WEBHOOK_URL ist nicht gesetzt"

    try:
        DiscordWebhookClient(webhook_url).send(run_summary_payload(summary))
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
        WARNING_COLOR
        if failed or detail_warnings or any(source["status"] in {"failed", "partial"} for source in sources)
        else 0x2E8B57
    )
    lines = [
        f"Laufzeit: **{summary['duration']}**",
        "",
        "**Ergebnis**",
        (f"{format_count(summary['jobs_total'])} Stellen erfasst · {format_count(summary['jobs_new'])} neu"),
        (f"{format_count(summary['included'])} im Vorfilter · {format_count(summary['excluded'])} ausgeschlossen"),
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
        "embeds": [{"title": "Job Finder · Lauf abgeschlossen", "description": "\n".join(lines), "color": color}],
        "allowed_mentions": {"parse": []},
    }


def send_warning(title, text, *, webhook_url, client=None):
    """Send one short warning about a part of the run that did not finish normally.

    Return None when it was sent, otherwise the reason as text for the log.
    """
    if client is None:
        if not webhook_url:
            return "DISCORD_WEBHOOK_URL ist nicht gesetzt"
        client = DiscordWebhookClient(webhook_url)
    payload = {
        "embeds": [{"title": title, "description": text, "color": WARNING_COLOR}],
        "allowed_mentions": {"parse": []},
    }
    try:
        client.send(payload)
    except NotificationError as error:
        return str(error)
    return None


def is_notifiable(job):
    """Return whether one prefiltered result belongs in Discord notifications.

    Matches what the default review actually shows; a job hidden behind an
    extra filter (Junior-Hybrid, international remote) is never notified.
    """
    return (
        job.get("workflow_status", "new") in NOTIFIABLE_STATUSES
        and not job.get("international", False)
        and is_visible_in_default_review(job)
    )


def notification_job(job):
    """Keep only durable card facts and visibility; never store webhook credentials."""
    fields = (
        "id",
        "title",
        "company",
        "locations",
        "sources",
        "work_mode",
        "remote_percentage",
        "match_percent",
        "role_group",
        "experience_level",
        "location_precheck",
        "workflow_status",
    )
    return {
        **{key: deepcopy(job[key]) for key in fields if key in job},
        "international": job.get("international", is_international_listing(job)),
    }


def pending_entry(job, timestamp):
    """Create auditable retry state for one unsent job."""
    return {
        "job_id": job["id"],
        "event_key": f"job-found:{job['id']}",
        "title": job["title"],
        "attempts": 0,
        "last_error": None,
        "created_at": timestamp,
        "updated_at": timestamp,
        "payload": notification_job(job),
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
        if current and (len(current) >= MAX_EMBEDS or current_characters + characters > MAX_EMBED_CHARACTERS):
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
    remote = format_remote(job.get("remote_percentage"), job.get("work_mode"))
    fields = [
        embed_field("Kurzcheck", f"Neu · {role} · Vorfilter {job.get('match_percent', 0)}/100"),
        embed_field("Einstieg", job.get("experience_level") or "nicht erkannt", inline=True),
        embed_field("Standortprüfung", job.get("location_precheck") or "keine Auffälligkeit erkannt"),
    ]
    link = review_url(job, review_host)
    if link:
        fields.append({"name": "Review", "value": f"[Stelle öffnen]({link})", "inline": True})
    return {
        "title": truncate(job["title"], 256),
        "url": primary_url(job),
        "description": truncate(f"**{job.get('company') or 'Unbekannte Firma'}**\n📍 {locations} · 🏠 {remote}", 4096),
        "color": 0x2E8B57,
        "fields": fields,
        "footer": {"text": "Titel anklicken, um die Originalanzeige zu öffnen."},
    }


def embed_field(name, value, *, inline=False):
    """Build one embed field within Discord's 1024-character value limit."""
    return {"name": name, "value": truncate(value, 1024), "inline": inline}


def review_url(job, review_host):
    """Build a deep link into the review page for one job, when configured."""
    return f"https://{review_host}/review?job={job['id']}" if review_host else None


def format_count(value):
    """Format integer counters with German thousands separators."""
    return f"{int(value):,}".replace(",", ".")


def source_health_text(sources):
    """Summarize source coverage while keeping failures visible."""
    counts = Counter(source["status"] for source in sources)
    parts = [f"{len(sources)} geprüft", f"{counts['success']} erfolgreich"]
    parts += [f"{counts[key]} {label}" for key, label in HEALTH_LABELS.items() if counts[key]]
    return " · ".join(parts)


def new_source_text(sources):
    """List only sources that contributed new jobs."""
    lines = [f"**{source['label']}** {format_count(source['new'])}" for source in sources if source.get("new")]
    return truncate(" · ".join(lines) or "Keine neuen Treffer", 1024)


def exceptional_source_text(sources):
    """Keep partial and failed sources separate from successful new results."""
    warnings = [
        f"{source['label']} " + ("nur teilweise geladen" if source["status"] == "partial" else "fehlgeschlagen")
        for source in sources
        if source["status"] in {"partial", "failed"}
    ]
    return f"⚠️ {', '.join(warnings)}" if warnings else ""


def detail_failure_text(detail_failures):
    """Name sources whose prefiltered candidates lack detail text."""
    warnings = [f"{failure['label']} {format_count(failure['failed'])} Kandidat(en)" for failure in detail_failures]
    return f"⚠️ Details fehlen: {', '.join(warnings)}" if warnings else ""


def embed_character_count(embed):
    """Count fields included in Discord's combined 6000-character limit."""
    return (
        len(embed.get("title", ""))
        + len(embed.get("description", ""))
        + sum(len(field.get("name", "")) + len(field.get("value", "")) for field in embed.get("fields", []))
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
    query = dict(parse_qsl(parts.query, keep_blank_values=True)) | {"wait": "true"}
    return parts._replace(query=urlencode(query)).geturl()


def load_notification_state(path=NOTIFICATION_STATE_FILE):
    """Load the delivery state keyed by stable job IDs."""
    document = read_json(path, {"version": STATE_VERSION, "sent": {}, "pending": {}})
    return decode_notification_state(document)


def decode_notification_state(document):
    """Decode the current version into stable sent and pending job-ID mappings."""
    if document.get("version") != STATE_VERSION:
        raise ValueError("Benachrichtigungsstatus verwendet eine unbekannte Version")
    sent = {entry.get("job_id", key): entry for key, entry in document.get("sent", {}).items()}
    pending = {
        entry["job_id"]: entry
        for entry in document.get("pending", {}).values()
        if entry.get("job_id") and entry["job_id"] not in sent
    }
    return {"sent": sent, "pending": pending}


def save_notification_state(state, path=NOTIFICATION_STATE_FILE):
    """Persist notification state via an atomic replacement."""
    write_json_atomic(path, {"version": STATE_VERSION, **state})
