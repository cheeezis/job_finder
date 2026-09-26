"""Join old duplicates: one remembered job where the user decided the same job more than once."""

from collections import defaultdict
from itertools import combinations

from job_finder.matching.deduplication import companies_match, normalize_company, normalize_title
from job_finder.paths import MEMORY_FILE
from job_finder.persistence.database import memory_scope, transaction
from job_finder.workflow.memory import (
    edit_memory,
    has_application_state,
    has_manual_state,
    load_memory,
    unique_values,
)

# Best decision first; an application comes before all of them.
DECISION_ORDER = ("interesting", "inquiry", "ignored")


def merge_duplicate_decisions(apply=False, path=MEMORY_FILE):
    """Plan, and with apply=True carry out, joining jobs the user decided more than once.

    Before all listings of a job shared one entry, the same job could reach
    the review twice; the user usually kept one card and declined the others
    as duplicates. Each group of decided entries with the same title and a
    matching company becomes one entry with the best decision: its history
    stays, the others' links, places and notes move to it and their
    duplicate decisions go. A group with two applications is left alone.
    Without apply nothing is written.
    """
    if not apply:
        memory = load_memory(path)
        merges, left_alone = plan_merges(memory)
        return report(memory, merges, left_alone, applied=False)
    with transaction() as connection:
        with edit_memory(path) as memory:
            merges, left_alone = plan_merges(memory)
            result = report(memory, merges, left_alone, applied=True)
            for kept_id, merged_ids in merges:
                join_entries(memory, kept_id, merged_ids)
        move_fact_sheets(connection, memory_scope(path), merges)
    return result


def plan_merges(memory):
    """Return [(kept ID, [merged IDs])] and the groups left alone."""
    merges, left_alone = [], []
    for group in decided_duplicate_groups(memory):
        if sum(has_application_state(memory[job_id]) for job_id in group) > 1:
            left_alone.append(group)
            continue
        kept_id = min(group, key=lambda job_id: keep_order(job_id, memory[job_id]))
        merges.append((kept_id, [job_id for job_id in group if job_id != kept_id]))
    return merges, left_alone


def keep_order(job_id, entry):
    """Order entries of one job: best decision first, then the oldest."""
    status = entry.get("workflow_status")
    if has_application_state(entry):
        rank = 0
    elif status in DECISION_ORDER:
        rank = 1 + DECISION_ORDER.index(status)
    else:
        rank = 1 + len(DECISION_ORDER)
    return rank, entry.get("first_seen_at") or "9999", job_id


def decided_duplicate_groups(memory):
    """Group decided entries with the same normalized title and a matching company."""
    by_title = defaultdict(list)
    companies = {}
    for job_id, entry in memory.items():
        title = normalize_title(entry.get("title") or "")
        companies[job_id] = normalize_company(entry.get("company") or "")
        if title and companies[job_id] and has_manual_state(entry):
            by_title[title].append(job_id)
    groups = []
    for title in sorted(by_title):
        related = connected(
            sorted(by_title[title]),
            lambda first, second: companies_match(companies[first], companies[second]),
        )
        groups.extend(group for group in related if len(group) > 1)
    return groups


def connected(items, related):
    """Split items into groups linked by related(first, second), keeping their order."""
    group_of = {item: [item] for item in items}
    for first, second in combinations(items, 2):
        if group_of[first] is not group_of[second] and related(first, second):
            joined = group_of[first] + group_of[second]
            for item in joined:
                group_of[item] = joined
    groups = {id(group): group for group in group_of.values()}
    return [sorted(group, key=items.index) for group in groups.values()]


def join_entries(memory, kept_id, merged_ids):
    """Move links, places and notes of the merged entries to the kept one and drop them."""
    kept = memory[kept_id]
    for job_id in merged_ids:
        other = memory.pop(job_id)
        for field in ("source_urls", "source_names", "locations"):
            kept[field] = unique_values(kept.get(field) or [], other.get(field) or [])
        if other.get("fully_remote"):
            kept["fully_remote"] = True
        notes = unique_values([kept.get("review_note") or ""], [other.get("review_note") or ""])
        if notes:
            kept["review_note"] = "\n\n".join(notes)
        if not kept.get("personal_rating") and other.get("personal_rating"):
            kept["personal_rating"] = other["personal_rating"]
        first_seen = [
            value for value in (kept.get("first_seen_at"), other.get("first_seen_at")) if value
        ]
        if first_seen:
            kept["first_seen_at"] = min(first_seen)
        last_seen = [
            value for value in (kept.get("last_seen_at"), other.get("last_seen_at")) if value
        ]
        if last_seen:
            kept["last_seen_at"] = max(last_seen)
        # A listing still online keeps the joined job alive.
        if other.get("active", True) and not kept.get("active", True):
            kept["active"] = True
            kept["missed_runs"] = other.get("missed_runs", 0)
        checks = other.get("availability_checks")
        if isinstance(checks, dict) and isinstance(kept.get("availability_checks", {}), dict):
            kept["availability_checks"] = {**checks, **kept.get("availability_checks", {})}


def move_fact_sheets(connection, scope, merges):
    """Keep one fact sheet per joined job: the kept entry's own, else the newest finished one."""
    for kept_id, merged_ids in merges:
        ids = [kept_id, *merged_ids]
        best = connection.execute(
            "SELECT job_id FROM agent_fact_sheets WHERE scope=%s AND job_id = ANY(%s) "
            "ORDER BY job_id = %s DESC, complete DESC, created_at DESC LIMIT 1",
            (scope, ids, kept_id),
        ).fetchone()
        if best is None:
            continue
        connection.execute(
            "DELETE FROM agent_fact_sheets WHERE scope=%s AND job_id = ANY(%s) AND job_id <> %s",
            (scope, ids, best[0]),
        )
        connection.execute(
            "UPDATE agent_fact_sheets SET job_id=%s WHERE scope=%s AND job_id=%s",
            (kept_id, scope, best[0]),
        )


def report(memory, merges, left_alone, *, applied):
    """Describe the planned joins before memory changes."""

    def describe(job_id):
        return {"id": job_id, "status": memory[job_id].get("workflow_status")}

    return {
        "applied": applied,
        "groups": len(merges),
        "entries_merged": sum(len(merged_ids) for _kept_id, merged_ids in merges),
        "left_alone_with_two_applications": left_alone,
        "merges": [
            {
                "title": memory[kept_id].get("title"),
                "company": memory[kept_id].get("company"),
                "kept": describe(kept_id),
                "merged": [describe(job_id) for job_id in merged_ids],
            }
            for kept_id, merged_ids in merges
        ],
    }
