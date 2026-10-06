"""Tests for the evals: the case file, the grading and a whole run against a fake model endpoint, at no cost."""

import contextlib
import io
import json
import re
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import httpx2

from evals import __main__ as cli, harness, private_cases
from evals.grading import grade, summarize
from job_finder.agent import runner
from job_finder.agent.fact_sheet import FIXED_LIGHTS, FIXED_LINES, VERDICTS
from job_finder.agent.pricing import Usage, call_cost

DATASET = harness.load_cases()
TOKENS = {"input_tokens": 9000, "output_tokens": 800}
# The domains RFC 2606 reserves for examples, so the public cases name no real site.
EXAMPLE_HOSTS = re.compile(r"(^|\.)(example\.(org|com|net)|[a-z0-9-]+\.example)$")


def sheet(stufe="bewerben", **lights):
    return {
        **{key: {"ampel": lights.get(key, "gelb"), "text": f"{label}: Beispiel."} for key, label in FIXED_LINES},
        "zusatz": [],
        "fazit": {"stufe": stufe, "text": "Fazit"},
        "kurzgrund": "Kurzgrund.",
        "quellen": ["https://jobs.example.org/1"],
    }


def case(case_id="fall-1", fazit=("bewerben",), **expected):
    return {
        "id": case_id,
        "category": "test",
        "why": "Test.",
        "expected": {"fazit": list(fazit), **expected},
        "job": {
            "title": "Junior Python Developer",
            "company": "Datenweber GmbH",
            "sources": [{"source": "test", "url": "https://jobs.example.org/1"}],
            "description_clean": "Python im Team.",
        },
    }


class FakeEndpoint:
    """Answers the model's HTTP requests with prepared Responses API answers and remembers the requests."""

    def __init__(self, *outputs):
        self.outputs, self.requests, self.deleted = list(outputs), [], []
        transport = httpx2.MockTransport(self.handle)
        self.model = runner.agent_model("https://example.test/openai/v1/", "test", max_retries=0, transport=transport)

    def handle(self, request):
        if request.method == "DELETE":
            self.deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx2.Response(200, json={"id": self.deleted[-1], "object": "response", "deleted": True})
        self.requests.append(json.loads(request.content))
        output = self.outputs.pop(0)
        if isinstance(output, httpx2.Response):
            return output
        number = len(self.requests)
        body = {"id": f"resp_{number}", "object": "response", "created_at": 1790000000, "model": runner.MODEL}
        return httpx2.Response(200, json={**body, "status": "completed", "output": [output], "usage": TOKENS})


def content_filter():
    """What Azure answers when its content filter rejects a request before the model sees it."""
    return httpx2.Response(400, json={"error": {"message": "gefiltert", "code": "content_filter"}})


def answer(content):
    text = json.dumps(content) if isinstance(content, dict) else content
    output = [{"type": "output_text", "text": text, "annotations": []}]
    return {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "content": output}


def decisions_call():
    arguments = json.dumps({"company": "Datenweber GmbH", "title_keywords": ["Python"]})
    return {"type": "function_call", "id": "fc_1", "name": "past_decisions", "arguments": arguments, "call_id": "c1"}


class CaseFileTests(unittest.TestCase):
    def test_every_case_has_valid_expectations_and_a_reason(self):
        ids = [item["id"] for item in DATASET["cases"]]
        keys = {key for key, _label in FIXED_LINES}

        self.assertEqual(len(ids), len(set(ids)))
        for item in DATASET["cases"]:
            with self.subTest(case=item["id"]):
                expected = item["expected"]
                self.assertTrue(expected["fazit"])
                self.assertLessEqual(set(expected["fazit"]), set(VERDICTS))
                for key, lights in (expected.get("ampeln") or {}).items():
                    self.assertIn(key, keys)
                    self.assertLessEqual(set(lights), set(FIXED_LIGHTS))
                self.assertTrue(item["why"].strip() and item["category"])
                self.assertTrue(item["job"]["title"] and item["job"]["description_clean"].strip())

    def test_the_public_cases_link_only_to_example_domains(self):
        for item in DATASET["cases"]:
            urls = [source["url"] for source in item["job"]["sources"]]
            urls += runner.URL_PATTERN.findall(item["job"]["description_clean"])
            for url in urls:
                with self.subTest(case=item["id"], url=url):
                    self.assertRegex(urlparse(url).hostname, EXAMPLE_HOSTS)

    def test_the_profile_ends_with_the_places_of_the_settings(self):
        self.assertIn(
            "- Nahbereich, vor Ort gut erreichbar (um Münster, etwa 25 km): Münster, Greven, Telgte",
            DATASET["profile_text"],
        )
        self.assertIn(
            "- Pendelorte, nur mit so viel Homeoffice: Dortmund (mindestens 60 % Homeoffice)", DATASET["profile_text"]
        )
        self.assertEqual(DATASET["today"], date(2026, 10, 1))
        self.assertEqual(DATASET["decision_rows"][0][2], "Datenweber GmbH")


class GradingTests(unittest.TestCase):
    def test_a_sheet_within_the_expectations_passes_every_check(self):
        expected = case(fazit=("eher_streichen", "streichen"), ampeln={"berufseinstieg": ["rot"]})

        checks = grade(expected, sheet("streichen", berufseinstieg="rot"))

        self.assertEqual(
            checks, {"urteil": True, "richtung": True, "ampel:berufseinstieg": True, "keine_erfundene_zahl": True}
        )

    def test_a_neighbouring_verdict_is_wrong_but_on_the_right_side(self):
        checks = grade(case(fazit=("bewerben",)), sheet("erst_klaeren"))

        self.assertEqual((checks["urteil"], checks["richtung"]), (False, True))

    def test_expectations_on_both_sides_have_no_direction(self):
        checks = grade(case(fazit=("erst_klaeren", "eher_streichen")), sheet("streichen"))

        self.assertNotIn("richtung", checks)

    def test_amounts_count_as_invented_only_when_the_ad_names_no_money(self):
        invented = sheet()
        invented["gehalt"]["text"] = "Branchenüblich etwa 55k EUR, nicht belegt."
        with_salary = case()
        with_salary["job"] = {**with_salary["job"], "salary_min_eur": 50000}

        self.assertFalse(grade(case(), invented)["keine_erfundene_zahl"])
        self.assertNotIn("keine_erfundene_zahl", grade(with_salary, invented))

    def test_an_aborted_run_counts_as_a_wrong_verdict(self):
        results = [
            {"variante": "agent", "kategorie": "test", "status": "fertig", "pruefungen": {"urteil": True},
             "verworfene_quellen": ["x"], "werkzeugaufrufe": 1, "kosten_eur": "0.02", "sekunden": 4.0},
            {"variante": "agent", "kategorie": "test", "status": "abgebrochen", "pruefungen": {},
             "verworfene_quellen": [], "werkzeugaufrufe": 0, "kosten_eur": "0.01", "sekunden": 2.0},
        ]  # fmt: skip

        entry = summarize(results)["varianten"]["agent"]

        self.assertEqual((entry["urteil"], entry["abgebrochen"], entry["verworfene_links"]), ([1, 2], 1, 1))
        self.assertEqual(entry["kosten_eur"], Decimal("0.03"))


class RunTests(unittest.TestCase):
    def dataset(self, *cases):
        return {**DATASET, "cases": list(cases)}

    def test_both_variants_write_graded_sheets_without_touching_the_database(self):
        fake = FakeEndpoint(decisions_call(), answer(sheet()), answer(sheet("erst_klaeren")))

        run = harness.run_evals(self.dataset(case()), harness.VARIANTS, fake.model, Decimal("1"), "medium")

        agent, single = run["ergebnisse"]
        self.assertEqual(
            (agent["urteil"], agent["pruefungen"]["urteil"], agent["werkzeugaufrufe"]), ("bewerben", True, 1)
        )
        self.assertEqual(
            (single["urteil"], single["pruefungen"]["urteil"], single["modellaufrufe"]), ("erst_klaeren", False, 1)
        )
        # The agent's tool searched the case file's decisions; the baseline got no tools at all.
        tool_output = json.loads(fake.requests[1]["input"][0]["output"])
        self.assertEqual(tool_output["entscheidungen"][0]["firma"], "Datenweber GmbH")
        self.assertNotIn("tools", fake.requests[2])
        self.assertEqual(fake.requests[2]["text"]["format"]["type"], "json_schema")
        self.assertIn(
            "Du hast für diese Stelle höchstens 0 Websuchen.", json.dumps(fake.requests[2]["input"], ensure_ascii=False)
        )
        self.assertIn("Pendelorte", fake.requests[2]["instructions"])
        self.assertEqual(run["kosten_eur"], 3 * call_cost(runner.MODEL, Usage(9000, 0, 800)))
        self.assertEqual(fake.deleted, ["resp_1", "resp_2", "resp_3"])

    def test_the_run_stops_at_its_budget_and_keeps_what_it_has(self):
        fake = FakeEndpoint(answer(sheet()), answer(sheet()))
        dataset = self.dataset(case("fall-1"), case("fall-2"))

        run = harness.run_evals(dataset, ("einzelaufruf",), fake.model, Decimal("0.003"), "medium")

        self.assertEqual([result["fall"] for result in run["ergebnisse"]], ["fall-1"])
        self.assertIn("Budget des Laufs erreicht", run["stopp"])

    def test_an_unusable_answer_ends_only_its_case(self):
        fake = FakeEndpoint(answer("kein JSON"), answer(sheet()))
        dataset = self.dataset(case("fall-1"), case("fall-2"))

        run = harness.run_evals(dataset, ("einzelaufruf",), fake.model, Decimal("1"), "medium")

        first, second = run["ergebnisse"]
        self.assertEqual((first["status"], first["urteil"]), ("abgebrochen", None))
        self.assertIn("Steckbrief unbrauchbar", first["grund"])
        self.assertEqual(second["status"], "fertig")

    def test_a_filter_block_counts_as_defended_only_where_the_case_allows_it(self):
        fake = FakeEndpoint(content_filter(), content_filter())
        dataset = self.dataset(case("angriff", ("streichen",), blockade_ok=True), case("echte-stelle"))

        run = harness.run_evals(dataset, ("einzelaufruf",), fake.model, Decimal("1"), "medium")

        attack, real = run["ergebnisse"]
        self.assertEqual((attack["status"], attack["pruefungen"]), ("blockiert", {"urteil": True}))
        self.assertEqual((real["status"], real["pruefungen"]), ("blockiert", {"urteil": False}))
        entry = summarize(run["ergebnisse"])["varianten"]["einzelaufruf"]
        self.assertEqual((entry["blockiert"], entry["abgebrochen"], entry["urteil"]), (2, 0, [1, 2]))

    def test_a_case_sees_only_the_decisions_made_before_its_own(self):
        rows = [
            ("a", "Python Developer", "Datenweber GmbH", "ignored", None, "alt", date(2026, 9, 1)),
            ("b", "Python Developer", "Datenweber GmbH", "applied", None, "neu", date(2026, 9, 20)),
            ("c", "Python Developer", "Datenweber GmbH", "ignored", None, None, None),
        ]

        self.assertEqual(harness.decisions_before(rows, date(2026, 9, 20)), rows[:1])
        self.assertEqual(harness.decisions_before(rows, None), rows)

    def test_the_agent_searches_only_when_the_run_allows_it(self):
        fake = FakeEndpoint(answer(sheet()))

        run = harness.run_evals(self.dataset(case()), ("agent",), fake.model, Decimal("1"), "medium", searches=2)

        self.assertIn(runner.WEB_SEARCH_TOOL, fake.requests[0]["tools"])
        self.assertEqual(fake.requests[0]["max_tool_calls"], 2)
        self.assertEqual(run["websuchen"], 2)

    def test_the_report_names_data_rules_and_results(self):
        fake = FakeEndpoint(answer(sheet()))
        dataset = self.dataset(case())
        run = harness.run_evals(dataset, ("einzelaufruf",), fake.model, Decimal("1"), "medium")

        with tempfile.TemporaryDirectory() as folder:
            json_path, markdown_path = harness.write_report(
                dataset, run, ("einzelaufruf",), Decimal("1"), "medium", 1, folder, datetime(2026, 10, 1, 9, 30)
            )
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            text = markdown_path.read_text(encoding="utf-8")

        self.assertEqual(Path(json_path).name, "2026-10-01-0930-synthetisch.json")
        self.assertEqual(raw["meta"]["datensatz"]["version"], DATASET["version"])
        self.assertEqual(raw["meta"]["websuche"], "aus")
        self.assertEqual(raw["ergebnisse"][0]["steckbrief"]["fazit"]["stufe"], "bewerben")
        self.assertIn("| einzelaufruf | 1/1 (100 %) |", text)
        self.assertIn("| fall-1 | bewerben | bewerben ✓ |", text)


class PrivateCasesTests(unittest.TestCase):
    AD = {"title": "Cloud Engineer", "company": "Firma", "description_clean": "Text.", "salary_min_eur": None}

    def test_a_decision_becomes_the_expected_side(self):
        towards = private_cases.case_of(
            {"id": "m1"}, self.AD, ("m1", "Cloud Engineer", "Firma", "applied", 5, "Spannend.", date(2026, 9, 3))
        )
        away = private_cases.case_of(
            {"id": "m2"}, self.AD, ("m2", "Cloud Engineer", "Firma", "ignored", None, None, date(2026, 9, 4))
        )

        self.assertEqual(towards["expected"]["fazit"], ["bewerben", "erst_klaeren"])
        self.assertEqual(towards["why"], "Deine Entscheidung: Beworben – Spannend.")
        self.assertNotIn("salary_min_eur", towards["job"])
        self.assertEqual(
            (away["expected"]["fazit"], away["decided_on"]), (["eher_streichen", "streichen"], date(2026, 9, 4))
        )

    def test_a_limited_choice_is_balanced_and_repeatable(self):
        towards = [{"id": f"f{number}", "category": "dafür"} for number in range(5)]
        away = [{"id": f"g{number}", "category": "dagegen"} for number in range(20)]

        chosen = private_cases.balanced(towards + away, 8, seed=1)
        few_towards = private_cases.balanced(towards[:2] + away, 8, seed=1)

        self.assertEqual((len(chosen), sum(item["category"] == "dafür" for item in chosen)), (8, 4))
        self.assertEqual(chosen, private_cases.balanced(towards + away, 8, seed=1))
        self.assertEqual((len(few_towards), sum(item["category"] == "dafür" for item in few_towards)), (8, 2))


class CommandLineTests(unittest.TestCase):
    def test_a_run_needs_the_endpoint_and_sensible_limits(self):
        for argv, environ, message in (
            ([], {}, "JOBFINDER_OPENAI_ENDPOINT fehlt"),
            (["--budget", "50"], {"JOBFINDER_OPENAI_ENDPOINT": "https://example.test"}, "zwischen"),
            (["--searches", "5"], {"JOBFINDER_OPENAI_ENDPOINT": "https://example.test"}, "--searches"),
            (["--only", "gibt-es-nicht"], {"JOBFINDER_OPENAI_ENDPOINT": "https://example.test"}, "Unbekannte Fälle"),
        ):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit):
                    cli.main(argv, environ)
                self.assertIn(message, errors.getvalue())


if __name__ == "__main__":
    unittest.main()
