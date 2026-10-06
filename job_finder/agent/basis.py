"""The basis a fact sheet is written on, stamped onto it so the review can show when it changed.

Each part is a short hash or a name. The review only shows that a part
changed; nothing is evaluated again until the user asks for it.
"""

import hashlib
import json

import yaml

from job_finder.agent.instructions import MAX_AD_CHARS, RULES, profile_with_places
from job_finder.agent.runner import GRAPH_VERSION, MODEL

# In the order the review names them.
PARTS = ("profile", "rules", "ad", "model", "graph")
AD_FIELDS = (
    "title",
    "company",
    "locations",
    "work_mode",
    "remote_percentage",
    "employment_type",
    "salary_min_eur",
    "salary_max_eur",
    "published_at",
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]


def run_basis(profile_text, values, settings):
    """Return the parts every job of a run shares.

    The profile counts by content, not by its text: comments, quoting or line
    endings differ between the local file and the Key Vault copy without
    changing anything the agent reads.
    """
    return {
        "profile": digest([yaml.safe_load(profile_text), profile_with_places("", values)]),
        "rules": digest(RULES),
        "model": f"{MODEL} {settings.reasoning_effort}",
        "graph": GRAPH_VERSION,
    }


def job_basis(run, ad):
    """Add the ad's part: the facts and the text the agent is shown."""
    facts = {field: ad.get(field) for field in AD_FIELDS}
    facts["sources"] = sorted(source.get("url") or "" for source in ad.get("sources") or [])
    facts["text"] = (ad.get("description_clean") or "").strip()[:MAX_AD_CHARS]
    return {**run, "ad": digest(facts)}


def changed_parts(stored, current):
    """Name the parts of the basis that differ; an empty list means the sheet is current."""
    return [part for part in PARTS if stored.get(part) != current.get(part)]
