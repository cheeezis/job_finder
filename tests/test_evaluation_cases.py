"""Tests for the blind, hand-labelled model comparison fixture."""

import json
import unittest
from pathlib import Path

import yaml

from job_agent.llm_contract import RUBRIC_VERSION


TEST_INPUTS_PATH = Path("evaluation/llm_test_inputs.json")
EXPECTED_RESULTS_PATH = Path("evaluation/llm_expected_results.yaml")


class EvaluationCasesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = json.loads(TEST_INPUTS_PATH.read_text(encoding="utf-8"))
        cls.expected = yaml.safe_load(
            EXPECTED_RESULTS_PATH.read_text(encoding="utf-8")
        )

    def test_test_set_contains_twenty_five_unique_jobs(self):
        input_ids = [case["job_id"] for case in self.inputs["cases"]]
        expected_ids = [case["job_id"] for case in self.expected["cases"]]

        self.assertEqual(len(input_ids), 25)
        self.assertEqual(len(input_ids), len(set(input_ids)))
        self.assertEqual(input_ids, expected_ids)
        self.assertEqual(self.inputs["rubric_version"], RUBRIC_VERSION)
        self.assertEqual(self.expected["rubric_version"], RUBRIC_VERSION)

    def test_model_inputs_contain_full_descriptions_without_answers(self):
        forbidden_fields = {
            "expected_recommendation",
            "notes",
            "rule_score",
            "match_score",
            "score_reasons",
        }
        description_lengths = []

        for case in self.inputs["cases"]:
            with self.subTest(job_id=case["job_id"]):
                self.assertFalse(forbidden_fields.intersection(case))
                self.assertTrue(case["title"])
                self.assertTrue(case["company"])
                self.assertTrue(case["description_clean"])
                self.assertTrue(case["source_url"].startswith("https://"))
                description_lengths.append(len(case["description_clean"]))

        # The review report is shortened to 700 characters; the fixture is not.
        self.assertGreater(max(description_lengths), 700)

    def test_manual_labels_are_complete_and_use_known_values(self):
        allowed = set(self.expected["label_options"])

        for case in self.expected["cases"]:
            with self.subTest(job_id=case["job_id"]):
                self.assertIn(case["expected_recommendation"], allowed)
                self.assertTrue(case["notes"])


if __name__ == "__main__":
    unittest.main()
