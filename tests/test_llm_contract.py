"""Tests for the versioned LLM scoring and output contract."""

import json
import unittest

from job_agent.llm.contract import (
    ANALYSIS_SCHEMA,
    RUBRIC,
    RUBRIC_VERSION,
    SCHEMA_VERSION,
    recommendation_for_analysis,
    recommendation_for_score,
)


class LlmContractTests(unittest.TestCase):
    def test_rubric_prioritizes_entry_and_working_conditions(self):
        maximum = sum(item["max_points"] for item in RUBRIC.values())

        self.assertEqual(maximum, 100)
        self.assertEqual(
            {name: item["max_points"] for name, item in RUBRIC.items()},
            {
                "entry_fit": 50,
                "working_conditions_fit": 30,
                "direction_fit": 15,
                "technology_head_start": 5,
            },
        )
        self.assertEqual(RUBRIC_VERSION, 6)
        self.assertTrue(all(item["anchors"] for item in RUBRIC.values()))

    def test_schema_uses_exactly_the_rubric_dimensions(self):
        dimensions = ANALYSIS_SCHEMA["properties"]["dimension_scores"]

        self.assertEqual(set(dimensions["required"]), set(RUBRIC))
        self.assertEqual(set(dimensions["properties"]), set(RUBRIC))
        self.assertEqual(SCHEMA_VERSION, 4)
        json.dumps(ANALYSIS_SCHEMA)

    def test_score_bands_have_stable_boundaries(self):
        self.assertEqual(recommendation_for_score(100), "strong_match")
        self.assertEqual(recommendation_for_score(80), "strong_match")
        self.assertEqual(recommendation_for_score(79), "match")
        self.assertEqual(recommendation_for_score(60), "match")
        self.assertEqual(recommendation_for_score(59), "borderline")
        self.assertEqual(recommendation_for_score(40), "borderline")
        self.assertEqual(recommendation_for_score(39), "not_recommended")
        self.assertEqual(recommendation_for_score(0), "not_recommended")

    def test_hard_conflict_overrides_score_band(self):
        self.assertEqual(
            recommendation_for_analysis(95, ["Seniorniveau"]),
            "not_recommended",
        )

    def test_invalid_scores_are_rejected(self):
        for value in (-1, 101, 50.0, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                recommendation_for_score(value)


if __name__ == "__main__":
    unittest.main()
