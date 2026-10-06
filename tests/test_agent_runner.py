"""Tests for the agent graph of one job: the real LangChain model talks to a fake HTTP endpoint."""

import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import date
from unittest.mock import ANY, patch

import httpx2
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from job_finder.agent import cost_guard, runner, tools
from job_finder.agent.cost_guard import AgentStopped, CostGuard
from job_finder.agent.fact_sheet import FIXED_LINES
from job_finder.agent.pricing import Usage, call_cost
from job_finder.agent.settings import agent_settings

SETTINGS = agent_settings({"agent": {"enabled": True}})


def example_sheet():
    """A made-up, valid fact sheet."""
    return {
        **{key: {"ampel": "gelb", "text": f"{label}: Beispiel."} for key, label in FIXED_LINES},
        "zusatz": [],
        "fazit": {"stufe": "bewerben", "text": "Bewerben – mittlere Priorität"},
        "kurzgrund": "Passt fachlich; offen ist nur das Gehalt.",
        "quellen": ["https://example.com/jobs/1"],
    }


JOB = {
    "id": "job:1",
    "title": "Junior Python Developer",
    "company": "Beispiel GmbH",
    "locations": ["Fulda"],
    "work_mode": "hybrid",
    "remote_percentage": 60,
    "sources": [{"source": "stepstone", "url": "https://example.com/jobs/1"}],
    "description_clean": "Wir suchen Verstärkung für unser Python-Team.",
}
TOKENS = {"input_tokens": 9000, "output_tokens": 800}


def reply(response_id, *output, searches=0, status="completed", usage=TOKENS):
    """One Responses API answer as Azure sends it, including the billed search count."""
    return {
        "id": response_id,
        "object": "response",
        "created_at": 1790000000,
        "model": runner.MODEL,
        "status": status,
        "output": list(output),
        "usage": usage,
        "tool_usage": {"web_search": {"num_requests": searches}},
    }


def message(text):
    content = [{"type": "output_text", "text": text, "annotations": []}]
    return {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": content}


def decisions_call(call_id="call_1"):
    arguments = json.dumps({"company": "Beispiel GmbH", "title_keywords": ["Python"]})
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "name": "past_decisions",
        "arguments": arguments,
        "call_id": call_id,
        "status": "completed",
    }


class FakeModel:
    """Answers the model's HTTP requests with prepared replies and remembers every request."""

    def __init__(self, *replies):
        self.replies, self.requests, self.deleted = list(replies), [], []
        transport = httpx2.MockTransport(self.handle)
        self.model = runner.agent_model("https://example.test/openai/v1/", "test", max_retries=0, transport=transport)

    def handle(self, request):
        if request.method == "DELETE":
            response_id = request.url.path.rsplit("/", 1)[-1]
            self.deleted.append(response_id)
            return httpx2.Response(200, json={"id": response_id, "object": "response", "deleted": True})
        self.requests.append(json.loads(request.content))
        answer = self.replies.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, httpx2.Response) else httpx2.Response(200, json=answer)


def api_error(kind, retry_after="7"):
    if kind == "bad_request":
        return httpx2.Response(400, json={"error": {"message": "abgelehnt", "code": "invalid_prompt"}})
    if kind == "rate_limit":
        return httpx2.Response(429, headers={"retry-after": retry_after}, json={"error": {"message": "gedrosselt"}})
    return httpx2.ConnectError("Verbindung abgebrochen")


class AgentRunnerTests(unittest.TestCase):
    def setUp(self):
        for target, name, value in (
            (cost_guard, "spent_today_and_this_month", (0, 0)),
            (runner, "past_decisions", '{"entscheidungen": []}'),
        ):
            self.enterContext(patch.object(target, name, return_value=value))
        self.booked = self.enterContext(patch.object(cost_guard, "record_model_call"))
        self.saved = self.enterContext(patch.object(runner, "save_fact_sheet"))
        self.aborted = self.enterContext(patch.object(runner, "save_aborted"))

    def run_job(self, model, settings=SETTINGS):
        guard = CostGuard(settings, runner.MODEL)
        outcome = runner.write_fact_sheet(JOB, "version: 5\n", guard, model.model, settings, date(2026, 9, 25))
        return outcome, guard

    def test_the_graph_loops_between_model_and_tools(self):
        graph = runner.job_graph("job:1", "", None, None, SETTINGS, set(), []).get_graph()

        edges = {(edge.source, edge.target) for edge in graph.edges}

        self.assertEqual(edges, {("__start__", "model"), ("model", "tools"), ("model", "__end__"), ("tools", "model")})

    def test_a_tool_round_ends_in_a_stored_fact_sheet(self):
        model = FakeModel(
            reply("resp_1", decisions_call(), searches=2), reply("resp_2", message(json.dumps(example_sheet())))
        )

        outcome, guard = self.run_job(model)

        self.assertEqual(outcome, "fertig")
        first, second = model.requests
        self.assertIn("# Profil des Nutzers\n\nversion: 5", first["instructions"])
        self.assertIn("Stelle: Junior Python Developer", json.dumps(first["input"], ensure_ascii=False))
        self.assertIn(runner.WEB_SEARCH_TOOL, first["tools"])
        self.assertEqual(first["max_tool_calls"], 3)
        self.assertEqual(second["previous_response_id"], "resp_1")
        self.assertEqual(
            second["input"], [{"type": "function_call_output", "call_id": "call_1", "output": '{"entscheidungen": []}'}]
        )
        cost = call_cost(runner.MODEL, Usage(9000, 0, 800, web_searches=2)) + call_cost(
            runner.MODEL, Usage(9000, 0, 800)
        )
        self.saved.assert_called_once_with("job:1", runner.MODEL, example_sheet(), cost)
        self.assertEqual(model.deleted, ["resp_1", "resp_2"])

    def test_only_links_the_agent_saw_stay_as_sources(self):
        sheet = example_sheet()
        sheet["quellen"] = [
            "https://example.com/jobs/1",  # the listing of the ad
            "https://firma.example/karriere/",  # a link in the ad text
            "https://news.example/remote",  # cited by the search
            "https://erfunden.example/handbuch",  # never seen: invented
            "Nutzerprofil (intern)",  # not a link at all
        ]
        job = {**JOB, "description_clean": 'Mehr: <a href="https://firma.example/karriere">Karriere'}
        answer = message(json.dumps(sheet))
        answer["content"][0]["annotations"] = [{"type": "url_citation", "url": "https://news.example/remote"}]
        model = FakeModel(reply("resp_1", answer, searches=1))

        runner.write_fact_sheet(
            job, "version: 5\n", CostGuard(SETTINGS, runner.MODEL), model.model, SETTINGS, date(2026, 9, 25)
        )

        self.assertEqual(
            self.saved.call_args.args[2]["quellen"],
            ["https://example.com/jobs/1", "https://firma.example/karriere/", "https://news.example/remote"],
        )
        self.assertEqual(model.requests[0]["include"], ["web_search_call.action.sources"])

    def test_a_draft_comes_back_unstored_with_the_dropped_sources(self):
        sheet = example_sheet()
        sheet["quellen"] = ["https://example.com/jobs/1", "https://erfunden.example/handbuch"]
        model = FakeModel(reply("resp_1", message(json.dumps(sheet))))
        guard = CostGuard(SETTINGS, runner.MODEL)

        draft, dropped = runner.draft_fact_sheet(JOB, "version: 5\n", guard, model.model, SETTINGS, date(2026, 9, 25))

        self.assertEqual(draft["quellen"], ["https://example.com/jobs/1"])
        self.assertEqual(dropped, ["https://erfunden.example/handbuch"])
        self.saved.assert_not_called()
        self.aborted.assert_not_called()
        self.assertEqual(model.deleted, ["resp_1"])

    def test_given_decisions_replace_the_stored_ones(self):
        rows = [("job:9", "Python Developer", "Beispiel GmbH", "ignored", None, "Zu weit weg.", date(2026, 9, 1))]
        model = FakeModel(reply("resp_1", decisions_call()), reply("resp_2", message(json.dumps(example_sheet()))))
        guard = CostGuard(SETTINGS, runner.MODEL)

        with patch.object(runner, "past_decisions", wraps=tools.past_decisions):
            runner.draft_fact_sheet(JOB, "version: 5\n", guard, model.model, SETTINGS, date(2026, 9, 25), rows)

        output = json.loads(model.requests[1]["input"][0]["output"])
        self.assertEqual([entry["notiz"] for entry in output["entscheidungen"]], ["Zu weit weg."])

    def test_the_search_is_withdrawn_once_the_budget_is_used(self):
        model = FakeModel(
            reply("resp_1", decisions_call(), searches=3), reply("resp_2", message(json.dumps(example_sheet())))
        )

        self.run_job(model)

        self.assertNotIn(runner.WEB_SEARCH_TOOL, model.requests[1]["tools"])
        self.assertNotIn("max_tool_calls", model.requests[1])

    def test_without_the_billing_count_every_search_query_is_booked(self):
        search = {"type": "web_search_call", "action": {"type": "search", "queries": ["a", "b"]}}
        answer = reply("resp_1", search, message(json.dumps(example_sheet())))
        del answer["tool_usage"]

        self.run_job(FakeModel(answer))

        self.assertEqual(self.booked.call_args.args[2].web_searches, 2)

    def test_unusable_answers_end_only_this_job_with_a_reason(self):
        cases = {
            "Steckbrief unbrauchbar": reply("resp_1", message("kein JSON")),
            "Antwort unvollständig (max_output_tokens)": {
                **reply("resp_1", status="incomplete"),
                "incomplete_details": {"reason": "max_output_tokens"},
            },
            "Anfrage abgelehnt": api_error("bad_request"),
        }
        for reason, answer in cases.items():
            with self.subTest(reason=reason):
                self.aborted.reset_mock()

                outcome, _guard = self.run_job(FakeModel(answer))

                self.assertEqual(outcome, "abgebrochen")
                self.aborted.assert_called_once_with("job:1", runner.MODEL, ANY, ANY)
                self.assertIn(reason, self.aborted.call_args.args[2])

    def test_a_job_that_keeps_calling_tools_stops_at_its_call_limit(self):
        settings = agent_settings({"agent": {"enabled": True, "job_max_model_calls": 2}})
        model = FakeModel(reply("resp_1", decisions_call()), reply("resp_2", decisions_call()))

        outcome, _guard = self.run_job(model, settings)

        self.assertEqual(outcome, "abgebrochen")
        self.assertIn("2 Modellaufrufe", self.aborted.call_args.args[2])
        self.assertEqual(self.aborted.call_args.args[3], 2 * call_cost(runner.MODEL, Usage(9000, 0, 800)))

    def test_a_throttled_model_is_waited_for_instead_of_ending_the_run(self):
        # As in the calibration run of 26.09.2026, when a heavy answer used up the minute.
        model = FakeModel(
            api_error("rate_limit", retry_after="7"),
            api_error("rate_limit", retry_after="soon"),
            reply("resp_1", message(json.dumps(example_sheet()))),
        )

        with patch.object(runner.time, "sleep") as sleep:
            outcome, guard = self.run_job(model)

        self.assertEqual(outcome, "fertig")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [7.0, 60.0])
        self.assertEqual(guard.model_calls, 1)

    def test_a_model_throttled_again_and_again_stops_the_run(self):
        model = FakeModel(*[api_error("rate_limit") for _ in range(runner.RATE_LIMIT_RETRIES + 1)])

        with patch.object(runner.time, "sleep"), self.assertRaisesRegex(AgentStopped, "gedrosselt"):
            self.run_job(model)
        self.saved.assert_not_called()

    def test_an_unreachable_model_stops_the_run_and_still_forgets_the_answers(self):
        model = FakeModel(reply("resp_1", decisions_call()), api_error("connection"))

        with self.assertRaisesRegex(AgentStopped, "Modell nicht erreichbar"):
            self.run_job(model)
        self.assertEqual(model.deleted, ["resp_1"])
        self.saved.assert_not_called()


# Marks every text that must stay out of logs and traces.
SECRET = "GEHEIM"


class AgentTraceTests(unittest.TestCase):
    """Logs and traces of a job carry ids, counts and the verdict, never text from profile, ad or answer."""

    @classmethod
    def setUpClass(cls):
        cls.spans = InMemorySpanExporter()
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            provider = TracerProvider()
            trace.set_tracer_provider(provider)
        provider.add_span_processor(SimpleSpanProcessor(cls.spans))

    def setUp(self):
        self.spans.clear()
        self.enterContext(patch.object(cost_guard, "spent_today_and_this_month", return_value=(0, 0)))
        self.enterContext(patch.object(cost_guard, "record_model_call"))
        self.enterContext(patch.object(runner, "save_fact_sheet"))
        self.enterContext(patch.object(runner, "save_aborted"))
        notes = json.dumps({"entscheidungen": [{"note": f"{SECRET}-NOTIZ aus dem Gespräch"}]})
        self.enterContext(patch.object(runner, "past_decisions", return_value=notes))

    def run_job(self, *replies):
        """Run one job full of secret text; return (outcome or exception, log lines, spans by name)."""
        sheet = example_sheet()
        sheet["kurzgrund"] = f"{SECRET}-KURZGRUND"
        job = {**JOB, "title": f"{SECRET} Developer", "description_clean": f"{SECRET}-ANZEIGE"}
        model = FakeModel(*[answer(sheet) if callable(answer) else answer for answer in replies])
        output = io.StringIO()
        with redirect_stdout(output):
            try:
                result = runner.write_fact_sheet(
                    job,
                    f"name: {SECRET}-PROFIL",
                    CostGuard(SETTINGS, runner.MODEL),
                    model.model,
                    SETTINGS,
                    date(2026, 9, 25),
                    run_id="run1",
                )
            except AgentStopped as stop:
                result = stop
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        return result, lines, {span.name: span for span in self.spans.get_finished_spans()}

    def assert_no_text(self, lines, spans):
        recorded = json.dumps(lines, ensure_ascii=False) + "".join(
            f"{span.name}{dict(span.attributes)}{span.status.description}{span.events}" for span in spans.values()
        )
        self.assertNotIn(SECRET, recorded)
        self.assertNotIn("Beispiel GmbH", recorded)

    def test_a_finished_job_reports_counts_and_verdict(self):
        outcome, lines, spans = self.run_job(
            reply("resp_1", decisions_call(), searches=1), lambda sheet: reply("resp_2", message(json.dumps(sheet)))
        )

        self.assertEqual(outcome, "fertig")
        (line,) = lines
        self.assertEqual(line["event"], "agent_job")
        self.assertEqual(line["run_id"], "run1")
        self.assertEqual(
            {key: line[key] for key in ("job_id", "outcome", "verdict", "model_calls", "tool_calls", "web_searches")},
            {
                "job_id": "job:1",
                "outcome": "fertig",
                "verdict": "bewerben",
                "model_calls": 2,
                "tool_calls": 1,
                "web_searches": 1,
            },
        )
        self.assertEqual((line["input_tokens"], line["output_tokens"]), (18000, 1600))
        self.assertEqual(set(spans), {"agent_job", "model_call", "tool_call"})
        job_span = spans["agent_job"]
        self.assertEqual(spans["model_call"].parent.span_id, job_span.context.span_id)
        self.assertEqual(job_span.attributes["jobfinder.verdict"], "bewerben")
        self.assertEqual(spans["tool_call"].attributes["gen_ai.tool.name"], "past_decisions")
        self.assertEqual(spans["model_call"].attributes["gen_ai.usage.output_tokens"], 800)
        self.assert_no_text(lines, spans)

    def test_an_aborted_job_names_a_fixed_reason(self):
        outcome, lines, spans = self.run_job(reply("resp_1", status="incomplete"))

        self.assertEqual(outcome, "abgebrochen")
        self.assertEqual((lines[0]["outcome"], lines[0]["reason"]), ("abgebrochen", "incomplete"))
        self.assertEqual(spans["model_call"].status.description, "JobLimitReached")
        self.assert_no_text(lines, spans)

    def test_a_stopped_run_still_reports_the_job(self):
        stop, lines, spans = self.run_job(api_error("connection"))

        self.assertIsInstance(stop, AgentStopped)
        self.assertEqual((lines[0]["outcome"], lines[0]["reason"]), ("gestoppt", "run_stopped"))
        self.assertEqual(spans["agent_job"].status.description, "AgentStopped")
        self.assert_no_text(lines, spans)


if __name__ == "__main__":
    unittest.main()
