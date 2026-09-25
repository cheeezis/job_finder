"""The agent for one job: model calls and tool calls in a loop, every step under the cost guard."""

import json

import openai

from job_finder.agent.cost_guard import AgentStopped, JobLimitReached
from job_finder.agent.fact_sheet import RESPONSE_FORMAT, parse_fact_sheet
from job_finder.agent.instructions import instructions, job_prompt
from job_finder.agent.pricing import Usage
from job_finder.agent.tools import PAST_DECISIONS_TOOL, past_decisions
from job_finder.persistence.fact_sheets import save_aborted, save_fact_sheet

# Deployment name in Azure and key of the price table at the same time.
MODEL = "gpt-5-mini"
MAX_OUTPUT_TOKENS = 6000
# The jobs are in Germany, so the search should look there first.
WEB_SEARCH_TOOL = {"type": "web_search", "user_location": {"type": "approximate", "country": "DE"}}


def write_fact_sheet(job, profile_text, guard, client, settings, today):
    """Write and store the fact sheet of one job; return "fertig" or "abgebrochen".

    A limit, a rejected request or an unusable answer ends only this job, and
    its reason is stored. AgentStopped propagates: money used up, ledger
    unusable or model unreachable end the whole run.
    """
    job_id = job["id"]
    guard.start_job(job_id)
    rules = instructions(profile_text)
    prompt = job_prompt(job, today, settings.limits.job_max_web_searches)
    items, previous, response_ids = [{"role": "user", "content": prompt}], None, []
    try:
        while True:
            guard.before_model_call()
            data = ask_model(client, rules, items, previous, guard, settings)
            response_ids.append(data["id"])
            guard.after_model_call(usage_of(data))
            if data.get("status") != "completed":
                reason = (data.get("incomplete_details") or {}).get("reason") or data.get("status")
                raise JobLimitReached(f"Stelle abgebrochen: Antwort unvollständig ({reason})")
            calls = [item for item in data.get("output", []) if item.get("type") == "function_call"]
            if not calls:
                sheet = parse_fact_sheet(output_text(data))
                save_fact_sheet(job_id, MODEL, sheet, guard.job_cost)
                return "fertig"
            items = [tool_result(call, job_id, guard) for call in calls]
            previous = data["id"]
    except JobLimitReached as stop:
        save_aborted(job_id, MODEL, str(stop), guard.job_cost)
        return "abgebrochen"
    except ValueError as error:
        save_aborted(job_id, MODEL, f"Steckbrief unbrauchbar: {error}", guard.job_cost)
        return "abgebrochen"
    finally:
        forget(client, response_ids)


def ask_model(client, rules, items, previous, guard, settings):
    """Send one request; offer the paid search only while the job's budget lasts."""
    request = {
        "model": MODEL,
        "instructions": rules,
        "input": items,
        "tools": [PAST_DECISIONS_TOOL],
        "text": {"format": RESPONSE_FORMAT},
        "reasoning": {"effort": settings.reasoning_effort},
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    if previous:
        request["previous_response_id"] = previous
    if guard.search_allowed():
        request["tools"] = [PAST_DECISIONS_TOOL, WEB_SEARCH_TOOL]
        request["max_tool_calls"] = guard.limits.job_max_web_searches - guard.web_searches
    try:
        return client.responses.create(**request).model_dump()
    except openai.BadRequestError as error:
        reason = error.code or error.status_code
        raise JobLimitReached(f"Stelle abgebrochen: Anfrage abgelehnt ({reason})") from error
    except openai.APIError as error:
        raise AgentStopped(f"Modell nicht erreichbar ({type(error).__name__})") from error


def usage_of(data):
    """Tokens and billed searches of one response; None when the API reported no usage."""
    usage = data.get("usage")
    if not usage:
        return None
    searches = ((data.get("tool_usage") or {}).get("web_search") or {}).get("num_requests")
    if searches is None:
        # Without the billing count, every search query of the response counts,
        # which rather books too much than too little.
        searches = sum(
            len((item.get("action") or {}).get("queries") or [None])
            for item in data.get("output", [])
            if item.get("type") == "web_search_call"
        )
    return Usage(
        input_tokens=usage.get("input_tokens"),
        cached_input_tokens=(usage.get("input_tokens_details") or {}).get("cached_tokens") or 0,
        output_tokens=usage.get("output_tokens"),
        reasoning_tokens=(usage.get("output_tokens_details") or {}).get("reasoning_tokens") or 0,
        web_searches=searches,
    )


def output_text(data):
    return "".join(
        part.get("text", "")
        for item in data.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content") or []
        if part.get("type") == "output_text"
    )


def tool_result(call, job_id, guard):
    """Run one requested tool; the answer goes back to the model as text."""
    guard.before_tool_call()
    if call.get("name") == PAST_DECISIONS_TOOL["name"]:
        try:
            arguments = json.loads(call.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = None
        output = past_decisions(arguments, job_id)
    else:
        output = json.dumps({"fehler": f"Unbekanntes Werkzeug {call.get('name')}"})
    return {"type": "function_call_output", "call_id": call.get("call_id"), "output": output}


def forget(client, response_ids):
    """Delete the stored responses, which Azure would otherwise keep for 30 days.

    Best effort: a failed deletion must not cost the fact sheet.
    """
    for response_id in response_ids:
        try:
            client.responses.delete(response_id)
        except openai.APIError:
            continue
