"""Tests for the agent loop of one job, with a fake model instead of real calls."""

import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import ANY, patch

import httpx
import openai

from job_finder.agent import cost_guard, runner
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
    return {
        "id": response_id,
        "status": status,
        "output": list(output),
        "usage": usage,
        "tool_usage": {"web_search": {"num_requests": searches}},
    }


def message(text):
    return {"type": "message", "content": [{"type": "output_text", "text": text}]}


def decisions_call(call_id="call_1"):
    arguments = json.dumps({"company": "Beispiel GmbH", "title_keywords": ["Python"]})
    return {
        "type": "function_call",
        "name": "past_decisions",
        "arguments": arguments,
        "call_id": call_id,
    }


class FakeModel:
    """Answers with prepared replies and remembers every request."""

    def __init__(self, *replies):
        self.replies, self.requests, self.deleted = list(replies), [], []
        self.responses = self

    def create(self, **request):
        self.requests.append(request)
        answer = self.replies.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(model_dump=lambda: answer)

    def delete(self, response_id):
        self.deleted.append(response_id)


def api_error(kind):
    request = httpx.Request("POST", "https://example.com/openai/v1/responses")
    if kind == "bad_request":
        return openai.BadRequestError(
            "abgelehnt", response=httpx.Response(400, request=request), body=None
        )
    return openai.APIConnectionError(request=request)


class AgentRunnerTests(unittest.TestCase):
    def setUp(self):
        for target, name, value in (
            (cost_guard, "spent_today_and_this_month", (0, 0)),
            (runner, "past_decisions", '{"entscheidungen": []}'),
        ):
            patcher = patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.booked = self.start(cost_guard, "record_model_call")
        self.saved = self.start(runner, "save_fact_sheet")
        self.aborted = self.start(runner, "save_aborted")

    def start(self, target, name):
        patcher = patch.object(target, name)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def run_job(self, model, settings=SETTINGS):
        guard = CostGuard(settings, runner.MODEL)
        outcome = runner.write_fact_sheet(
            JOB, "version: 5\n", guard, model, settings, date(2026, 9, 25)
        )
        return outcome, guard

    def test_a_tool_round_ends_in_a_stored_fact_sheet(self):
        model = FakeModel(
            reply("resp_1", decisions_call(), searches=2),
            reply("resp_2", message(json.dumps(example_sheet()))),
        )

        outcome, guard = self.run_job(model)

        self.assertEqual(outcome, "fertig")
        first, second = model.requests
        self.assertIn("# Profil des Nutzers\n\nversion: 5", first["instructions"])
        self.assertIn("Stelle: Junior Python Developer", first["input"][0]["content"])
        self.assertIn(runner.WEB_SEARCH_TOOL, first["tools"])
        self.assertEqual(first["max_tool_calls"], 3)
        self.assertEqual(second["previous_response_id"], "resp_1")
        self.assertEqual(
            second["input"],
            [
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": '{"entscheidungen": []}',
                }
            ],
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
        job = {
            **JOB,
            "description_clean": 'Mehr: <a href="https://firma.example/karriere">Karriere',
        }
        answer = message(json.dumps(sheet))
        answer["content"][0]["annotations"] = [
            {"type": "url_citation", "url": "https://news.example/remote"}
        ]
        model = FakeModel(reply("resp_1", answer, searches=1))

        runner.write_fact_sheet(
            job,
            "version: 5\n",
            CostGuard(SETTINGS, runner.MODEL),
            model,
            SETTINGS,
            date(2026, 9, 25),
        )

        self.assertEqual(
            self.saved.call_args.args[2]["quellen"],
            [
                "https://example.com/jobs/1",
                "https://firma.example/karriere/",
                "https://news.example/remote",
            ],
        )
        self.assertEqual(model.requests[0]["include"], ["web_search_call.action.sources"])

    def test_the_search_is_withdrawn_once_the_budget_is_used(self):
        model = FakeModel(
            reply("resp_1", decisions_call(), searches=3),
            reply("resp_2", message(json.dumps(example_sheet()))),
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
        self.assertEqual(
            self.aborted.call_args.args[3], 2 * call_cost(runner.MODEL, Usage(9000, 0, 800))
        )

    def test_an_unreachable_model_stops_the_run_and_still_forgets_the_answers(self):
        model = FakeModel(reply("resp_1", decisions_call()), api_error("connection"))

        with self.assertRaisesRegex(AgentStopped, "Modell nicht erreichbar"):
            self.run_job(model)
        self.assertEqual(model.deleted, ["resp_1"])
        self.saved.assert_not_called()


if __name__ == "__main__":
    unittest.main()
