"""The agent for one job as a LangGraph graph: model and tool nodes in a loop, every step under the cost guard."""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Annotated, TypedDict

import httpx
import openai
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from job_finder.agent.cost_guard import AgentStopped, JobLimitReached
from job_finder.agent.fact_sheet import RESPONSE_FORMAT, parse_fact_sheet
from job_finder.agent.instructions import instructions, job_prompt
from job_finder.agent.pricing import Usage
from job_finder.agent.tools import PAST_DECISIONS_TOOL, past_decisions
from job_finder.console import log_event
from job_finder.persistence.fact_sheets import save_aborted, save_fact_sheet
from job_finder.telemetry import annotate, span

# Deployment name in Azure and key of the price table at the same time.
MODEL = "gpt-5-mini"
MAX_OUTPUT_TOKENS = 6000
# The jobs are in Germany, so the search should look there first.
WEB_SEARCH_TOOL = {"type": "web_search", "user_location": {"type": "approximate", "country": "DE"}}
# The same tool and fact sheet definitions in the shapes LangChain converts for the Responses API.
PAST_DECISIONS = {
    "type": "function",
    "function": {key: PAST_DECISIONS_TOOL[key] for key in ("name", "description", "parameters")},
}
OUTPUT_FORMAT = {key: RESPONSE_FORMAT[key] for key in ("name", "strict", "schema")}
# Stops at quotes and angle brackets, which some ad texts leave after a link.
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
# Azure throttles tokens per minute (the deployment's capacity): a heavy
# search answer can use up a minute, so the agent waits for the next one.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT_SECONDS = 60
# Each round is two graph steps; the cost guard ends a job long before this.
RECURSION_LIMIT = 50
# How a model call appears in traces (OpenTelemetry's names for generative AI).
MODEL_CALL = {"gen_ai.operation.name": "chat", "gen_ai.provider.name": "azure.ai.openai", "gen_ai.request.model": MODEL}


@dataclass
class AgentModel:
    """The chat model plus Azure's billed search count per response, which LangChain does not pass on."""

    chat: ChatOpenAI
    billed_searches: dict = field(default_factory=dict)


def agent_model(base_url, api_key, *, max_retries=2, transport=None):
    """Chat model for the Azure OpenAI v1 endpoint; api_key may be a token provider."""
    billed = {}

    def remember_billing(response):
        if response.request.method == "POST" and response.request.url.path.endswith("/responses"):
            response.read()
            try:
                body = response.json()
            except ValueError:
                return
            count = ((body.get("tool_usage") or {}).get("web_search") or {}).get("num_requests")
            if body.get("id") and count is not None:
                billed[body["id"]] = count

    http_client = httpx.Client(transport=transport, event_hooks={"response": [remember_billing]}, timeout=120)
    chat = ChatOpenAI(
        model=MODEL,
        base_url=base_url,
        api_key=api_key,
        use_responses_api=True,
        use_previous_response_id=True,
        output_version="responses/v1",
        # Without this, the response names only cited pages, not every page the search used.
        include=["web_search_call.action.sources"],
        max_retries=max_retries,
        timeout=120,
        http_client=http_client,
    )
    return AgentModel(chat, billed)


class JobState(TypedDict):
    messages: Annotated[list, add_messages]


def write_fact_sheet(job, profile_text, guard, model, settings, today, run_id=None):
    """Write and store the fact sheet of one job; return "fertig" or "abgebrochen".

    A limit, a rejected request or an unusable answer ends only this job, and
    its reason is stored. AgentStopped propagates: money used up, ledger
    unusable or model unreachable end the whole run. Every job leaves one
    agent_job log line and span with ids, counts and the verdict.
    """
    started, result = time.monotonic(), {"outcome": "fehler"}
    with span("agent_job", **{"jobfinder.run_id": run_id, "jobfinder.job_id": job["id"]}) as current:
        try:
            result = draft_and_store(job, profile_text, guard, model, settings, today)
        except AgentStopped:
            result = {"outcome": "gestoppt", "reason": "run_stopped"}
            raise
        finally:
            report_job(current, job["id"], run_id, guard, result, time.monotonic() - started)
    return result["outcome"]


def draft_and_store(job, profile_text, guard, model, settings, today):
    """Store the fact sheet or why this job ended; return the outcome, its reason or verdict."""
    try:
        sheet, _dropped = draft_fact_sheet(job, profile_text, guard, model, settings, today)
    except JobLimitReached as stop:
        save_aborted(job["id"], MODEL, str(stop), guard.job_cost)
        return {"outcome": "abgebrochen", "reason": stop.reason}
    except ValueError as error:
        save_aborted(job["id"], MODEL, f"Steckbrief unbrauchbar: {error}", guard.job_cost)
        return {"outcome": "abgebrochen", "reason": "unusable"}
    save_fact_sheet(job["id"], MODEL, sheet, guard.job_cost)
    return {"outcome": "fertig", "verdict": sheet["fazit"]["stufe"]}


def report_job(current, job_id, run_id, guard, result, seconds):
    """Log one line and annotate the span: ids, counts and the verdict, never text from the job."""
    fields = {"job_id": job_id, **result, "seconds": round(seconds, 1)}
    # Only when start_job ran for this job do the guard's counters belong to it.
    if guard.job_id == job_id:
        fields.update(
            model_calls=guard.model_calls,
            tool_calls=guard.tool_calls,
            web_searches=guard.web_searches,
            input_tokens=guard.input_tokens,
            output_tokens=guard.output_tokens,
            cost_eur=float(guard.job_cost),
        )
    annotate(current, **{f"jobfinder.{key}": value for key, value in fields.items()})
    if "cost_eur" in fields:
        fields["cost_eur"] = str(guard.job_cost)
    log_event("agent_job", run_id=run_id, **fields)


def draft_fact_sheet(job, profile_text, guard, model, settings, today, decisions=None):
    """Let the graph write the fact sheet of one job; return it checked, and the dropped sources.

    Nothing is stored, so the evals can call it as well. Raises
    JobLimitReached or ValueError when only this job fails and AgentStopped
    when the run must end. decisions replaces the stored decisions that the
    past_decisions tool searches (rows as decided_jobs returns them).
    """
    guard.start_job(job["id"])
    seen, response_ids = urls_in_ad(job), []
    graph = job_graph(job["id"], instructions(profile_text), guard, model, settings, seen, response_ids, decisions)
    prompt = job_prompt(job, today, settings.limits.job_max_web_searches)
    try:
        state = graph.invoke({"messages": [HumanMessage(prompt)]}, {"recursion_limit": RECURSION_LIMIT})
        sheet = parse_fact_sheet(state["messages"][-1].text)
    finally:
        forget(model, response_ids)
    return sheet, keep_seen_sources(sheet, seen)


def job_graph(job_id, rules, guard, model, settings, seen, response_ids, decisions=None):
    """Model node and tool node: the model asks for tools until it answers with the fact sheet."""

    def call_model(state):
        guard.before_model_call()
        with span("model_call", **MODEL_CALL) as current:
            answer = ask_model(model, rules, state["messages"], guard, settings)
            response_ids.append(answer.response_metadata.get("id"))
            usage = usage_of(answer, model.billed_searches)
            if usage:
                annotate(
                    current,
                    **{
                        "gen_ai.usage.input_tokens": usage.input_tokens,
                        "gen_ai.usage.output_tokens": usage.output_tokens,
                        "jobfinder.cached_input_tokens": usage.cached_input_tokens,
                        "jobfinder.reasoning_tokens": usage.reasoning_tokens,
                        "jobfinder.web_searches": usage.web_searches,
                    },
                )
            guard.after_model_call(usage)
            seen.update(urls_found(answer))
            if answer.response_metadata.get("status") != "completed":
                reason = (answer.response_metadata.get("incomplete_details") or {}).get("reason")
                raise JobLimitReached(
                    f"Stelle abgebrochen: Antwort unvollständig ({reason or 'unbekannt'})", "incomplete"
                )
        return {"messages": [answer]}

    def call_tools(state):
        answer = state["messages"][-1]
        return {"messages": [tool_result(call, job_id, guard, decisions) for call in requested_tools(answer)]}

    def after_model(state):
        return "tools" if requested_tools(state["messages"][-1]) else END

    graph = StateGraph(JobState)
    graph.add_node("model", call_model)
    graph.add_node("tools", call_tools)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", after_model, ["tools", END])
    graph.add_edge("tools", "model")
    return graph.compile()


def ask_model(model, rules, messages, guard, settings):
    """Send one request; offer the paid search only while the job's budget lasts."""
    tools, options = [PAST_DECISIONS], {}
    if guard.search_allowed():
        tools.append(WEB_SEARCH_TOOL)
        options["max_tool_calls"] = guard.limits.job_max_web_searches - guard.web_searches
    bound = model.chat.bind_tools(
        tools,
        response_format=OUTPUT_FORMAT,
        instructions=rules,
        reasoning={"effort": settings.reasoning_effort},
        max_output_tokens=MAX_OUTPUT_TOKENS,
        **options,
    )
    return invoke(bound, messages)


def invoke(bound, messages):
    """Call the bound model; wait out throttling, turn API errors into the guard's stops."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            return bound.invoke(messages)
        except openai.RateLimitError as error:
            throttled = error
            if attempt < RATE_LIMIT_RETRIES:
                time.sleep(retry_after(error))
        except openai.BadRequestError as error:
            reason = error.code or error.status_code
            raise JobLimitReached(f"Stelle abgebrochen: Anfrage abgelehnt ({reason})", "rejected") from error
        except openai.APIError as error:
            raise AgentStopped(f"Modell nicht erreichbar ({type(error).__name__})") from error
    raise AgentStopped("Modell gedrosselt, auch nach Wartezeit") from throttled


def retry_after(error):
    """Seconds to wait as the throttle asks, kept between 5 and 65; a minute if it does not say."""
    try:
        seconds = float(error.response.headers.get("retry-after", RATE_LIMIT_WAIT_SECONDS))
    except (AttributeError, TypeError, ValueError):
        seconds = RATE_LIMIT_WAIT_SECONDS
    return min(max(seconds, 5.0), 65.0)


def blocks(answer):
    """Return the content blocks of a model answer: text, tool calls and web searches."""
    return [block for block in answer.content if isinstance(block, dict)] if isinstance(answer.content, list) else []


def usage_of(answer, billed_searches):
    """Tokens and billed searches of one answer; None when the API reported no usage."""
    usage = answer.usage_metadata
    if not usage:
        return None
    searches = billed_searches.get(answer.response_metadata.get("id"))
    if searches is None:
        # Without the billing count, every search query of the answer counts,
        # which rather books too much than too little.
        searches = sum(
            len((block.get("action") or {}).get("queries") or [None])
            for block in blocks(answer)
            if block.get("type") == "web_search_call"
        )
    return Usage(
        input_tokens=usage.get("input_tokens"),
        cached_input_tokens=(usage.get("input_token_details") or {}).get("cache_read") or 0,
        output_tokens=usage.get("output_tokens"),
        reasoning_tokens=(usage.get("output_token_details") or {}).get("reasoning") or 0,
        web_searches=searches,
    )


def urls_in_ad(job):
    """Links the agent was shown with the job: its listings and the links in the ad text."""
    urls = {source.get("url") or "" for source in job.get("sources") or []}
    urls.update(URL_PATTERN.findall(job.get("description_clean") or ""))
    return {comparable(url) for url in urls if url}


def urls_found(answer):
    """Links from real search results of one answer: cited, used or opened pages."""
    urls = set()
    for block in blocks(answer):
        urls.update(note.get("url") or "" for note in block.get("annotations") or [])
        if block.get("type") == "web_search_call":
            action = block.get("action") or {}
            urls.update(source.get("url") or "" for source in action.get("sources") or [])
            urls.add(action.get("url") or "")
    return {comparable(url) for url in urls if url}


def keep_seen_sources(sheet, seen):
    """Reduce the sheet's sources to links the model saw; return what was dropped."""
    kept = verified_sources(sheet["quellen"], seen)
    dropped = [source for source in sheet["quellen"] if source.strip() not in kept]
    sheet["quellen"] = kept
    return dropped


def verified_sources(sources, seen):
    """Keep only links the agent actually saw; drop invented links and plain text."""
    urls = (source.strip() for source in sources)
    return list(dict.fromkeys(u for u in urls if URL_PATTERN.fullmatch(u) and comparable(u) in seen))


def comparable(url):
    """Ignore a trailing slash or punctuation, as ad texts end links in different ways."""
    return url.rstrip("/.,;:)")


def requested_tools(answer):
    """Tool calls of an answer, including those whose arguments were not valid JSON."""
    return [*answer.tool_calls, *getattr(answer, "invalid_tool_calls", [])]


def tool_result(call, job_id, guard, decisions=None):
    """Run one requested tool; the answer goes back to the model as text."""
    guard.before_tool_call()
    known = call.get("name") == PAST_DECISIONS_TOOL["name"]
    # The model names the tool, so only a known name goes into the trace.
    with span("tool_call", **{"gen_ai.tool.name": call.get("name") if known else "unbekannt"}):
        if known:
            arguments = call.get("args")
            output = past_decisions(arguments if isinstance(arguments, dict) else None, job_id, decisions)
        else:
            output = json.dumps({"fehler": f"Unbekanntes Werkzeug {call.get('name')}"})
    return ToolMessage(output, tool_call_id=call.get("id"))


def forget(model, response_ids):
    """Delete the stored responses, which Azure would otherwise keep for 30 days.

    Best effort: a failed deletion must not cost the fact sheet.
    """
    for response_id in filter(None, response_ids):
        try:
            model.chat.root_client.responses.delete(response_id)
        except openai.APIError:
            continue
