"""Tests for the fact sheet structure and its storage."""

import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from psycopg import errors

from job_finder.agent.fact_sheet import FIXED_LINES, SCHEMA, parse_fact_sheet
from job_finder.persistence.database import transaction
from job_finder.persistence.fact_sheets import fact_sheets, save_aborted, save_fact_sheet
from job_finder.workflow import review_data


def example_sheet(**changes):
    """A made-up fact sheet in the agreed format."""
    sheet = {
        key: {"ampel": "gruen", "text": f"{label}: Beispielbegründung."}
        for key, label in FIXED_LINES
    }
    sheet["homeoffice_standort"] = {
        "ampel": "orange",
        "text": "Vorher klären: Jobbörse nennt Full Remote, die Firmenseite nur zwei Standorte.",
    }
    sheet["gehalt"] = {"ampel": "unbekannt", "text": "Keine belastbare Spanne gefunden."}
    sheet.update(
        zusatz=[{"thema": "Bewerbung", "ampel": "hinweis", "text": "Portfolio verlangt."}],
        fazit={"stufe": "erst_klaeren", "text": "Erst Homeoffice klären, dann bewerben"},
        kurzgrund="Fachlich solide; offen ist nur, ob Full Remote gilt.",
        quellen=["https://example.com/jobs/1"],
    )
    sheet.update(changes)
    return sheet


def schema_objects(node):
    """Yield every object schema, however deep."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from schema_objects(value)


class FactSheetTests(unittest.TestCase):
    def test_every_object_is_closed_as_strict_structured_output_requires(self):
        for node in schema_objects(SCHEMA):
            self.assertEqual(node["required"], list(node["properties"]))
            self.assertIs(node["additionalProperties"], False)

    def test_light_words_and_link_markup_are_removed_from_the_texts(self):
        # As in the trial of 26.09.2026: light word up front, search citation at the end.
        cited = "gruen – offen und aktuell. ([arbeitnow.co.uk](https://www.arbeitnow.co.uk/jobs/1))"
        sheet = example_sheet(
            status={"ampel": "gruen", "text": cited},
            kurzgrund="Laut [Karriereseite](https://firma.example/karriere) remote möglich.",
        )

        parsed = parse_fact_sheet(json.dumps(sheet))

        self.assertEqual(parsed["status"]["text"], "offen und aktuell.")
        self.assertEqual(parsed["kurzgrund"], "Laut Karriereseite remote möglich.")
        with self.assertRaisesRegex(ValueError, "Status: Text fehlt"):
            parse_fact_sheet(json.dumps(example_sheet(status={"ampel": "rot", "text": "rot –"})))

    def test_a_valid_sheet_passes_and_extra_lines_are_capped(self):
        extra = {"thema": "Positiv", "ampel": "gruen", "text": "Docker gewünscht."}
        sheet = example_sheet(zusatz=[extra] * 3)

        parsed = parse_fact_sheet(json.dumps(sheet))

        self.assertEqual(parsed["fazit"]["stufe"], "erst_klaeren")
        self.assertEqual(len(parsed["zusatz"]), 2)

    def test_broken_sheets_never_reach_the_review(self):
        missing_line = example_sheet()
        del missing_line["gehalt"]
        cases = {
            "kein JSON": "{",
            "fehlende Zeile": json.dumps(missing_line),
            "falsche Ampel": json.dumps(example_sheet(status={"ampel": "blau", "text": "x"})),
            "leerer Text": json.dumps(example_sheet(status={"ampel": "gruen", "text": " "})),
            "falsche Stufe": json.dumps(example_sheet(fazit={"stufe": "vielleicht", "text": "x"})),
            "leerer Kurzgrund": json.dumps(example_sheet(kurzgrund="")),
            "Zusatz ohne Thema": json.dumps(
                example_sheet(zusatz=[{"thema": "", "ampel": "gruen", "text": "x"}])
            ),
        }
        for name, text in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse_fact_sheet(text)


class FactSheetStorageTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Use scripts/test_postgres.py with an isolated test database")
        with transaction() as connection:
            self.assertTrue(connection.info.dbname.endswith("_test"))
            connection.execute("TRUNCATE agent_fact_sheets")

    def test_finished_and_aborted_sheets_are_kept_per_job(self):
        save_fact_sheet("job:1", "gpt-5-mini", example_sheet(), Decimal("0.051"))
        save_aborted("job:2", "gpt-5-mini", "Stelle abgebrochen: 8 Modellaufrufe", Decimal("0.08"))

        stored = fact_sheets(["job:1", "job:2", "job:3"])

        self.assertEqual(set(stored), {"job:1", "job:2"})
        self.assertTrue(stored["job:1"]["complete"])
        self.assertEqual(stored["job:1"]["fact_sheet"], example_sheet())
        self.assertEqual(stored["job:2"]["note"], "Stelle abgebrochen: 8 Modellaufrufe")
        self.assertIsNone(stored["job:2"]["fact_sheet"])

    def test_a_new_sheet_replaces_the_old_one(self):
        save_aborted("job:1", "gpt-5-mini", "abgebrochen", Decimal("0.08"))
        save_fact_sheet("job:1", "gpt-5-mini", example_sheet(), Decimal("0.05"))

        stored = fact_sheets()

        self.assertEqual(list(stored), ["job:1"])
        self.assertTrue(stored["job:1"]["complete"])
        self.assertEqual(stored["job:1"]["cost_eur"], Decimal("0.05"))

    def test_the_review_gets_sheets_as_json_ready_data(self):
        save_fact_sheet("job:1", "gpt-5-mini", example_sheet(), Decimal("0.051"))
        save_aborted("job:2", "gpt-5-mini", "Stelle abgebrochen: 8 Modellaufrufe", Decimal("0.08"))
        jobs = [{"id": "job:1"}, {"id": "job:2"}, {"id": "job:3"}]

        review_data.attach_fact_sheets(jobs)

        json.dumps(jobs)
        self.assertEqual(jobs[0]["fact_sheet"]["sheet"], example_sheet())
        self.assertEqual(jobs[0]["fact_sheet"]["cost_eur"], 0.051)
        self.assertEqual(jobs[0]["fact_sheet"]["model"], "gpt-5-mini")
        self.assertFalse(jobs[1]["fact_sheet"]["complete"])
        self.assertNotIn("fact_sheet", jobs[2])


class ReviewWithoutAgentTablesTests(unittest.TestCase):
    def test_the_review_keeps_working_before_the_tables_exist(self):
        missing = errors.UndefinedTable('relation "agent_fact_sheets" does not exist')
        jobs = [{"id": "job:1"}]

        with patch.object(review_data, "fact_sheets", side_effect=missing):
            self.assertEqual(review_data.attach_fact_sheets(jobs), [{"id": "job:1"}])


if __name__ == "__main__":
    unittest.main()
