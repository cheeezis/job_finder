"""Tests for the versioned LLM scoring and output contract."""

import json
import unittest

from job_agent.llm.contract import (
    ANALYSIS_SCHEMA,
    MODEL_RESPONSE_SCHEMA,
    RATING_POINTS,
    RUBRIC,
    RUBRIC_VERSION,
    SCHEMA_VERSION,
    recommendation_for_score,
)


class LlmContractTests(unittest.TestCase):
    def test_rubric_has_fixed_one_hundred_point_scale(self):
        maximum = sum(item["max_points"] for item in RUBRIC.values())

        self.assertEqual(maximum, 100)
        self.assertEqual(len(RUBRIC), 5)
        self.assertTrue(all(item["max_points"] == 20 for item in RUBRIC.values()))
        self.assertEqual(RUBRIC_VERSION, 4)
        self.assertTrue(all(item["anchors"] for item in RUBRIC.values()))

    def test_schema_uses_exactly_the_rubric_dimensions(self):
        dimensions = ANALYSIS_SCHEMA["properties"]["dimension_scores"]

        self.assertEqual(set(dimensions["required"]), set(RUBRIC))
        self.assertEqual(set(dimensions["properties"]), set(RUBRIC))
        self.assertEqual(SCHEMA_VERSION, 3)
        json.dumps(ANALYSIS_SCHEMA)

    def test_model_schema_leaves_arithmetic_fields_to_python(self):
        self.assertNotIn("overall_score", MODEL_RESPONSE_SCHEMA["properties"])
        self.assertNotIn("recommendation", MODEL_RESPONSE_SCHEMA["properties"])
        self.assertNotIn("dimension_scores", MODEL_RESPONSE_SCHEMA["properties"])
        self.assertIn("dimension_ratings", MODEL_RESPONSE_SCHEMA["properties"])
        self.assertEqual(set(RATING_POINTS.values()), {0, 5, 10, 15, 20})
        json.dumps(MODEL_RESPONSE_SCHEMA)

    def test_score_bands_have_stable_boundaries(self):
        self.assertEqual(recommendation_for_score(100), "strong_match")
        self.assertEqual(recommendation_for_score(90), "strong_match")
        self.assertEqual(recommendation_for_score(89), "match")
        self.assertEqual(recommendation_for_score(75), "match")
        self.assertEqual(recommendation_for_score(74), "borderline")
        self.assertEqual(recommendation_for_score(60), "borderline")
        self.assertEqual(recommendation_for_score(59), "not_recommended")
        self.assertEqual(recommendation_for_score(0), "not_recommended")

    def test_invalid_scores_are_rejected(self):
        for value in (-1, 101, 50.0, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                recommendation_for_score(value)


if __name__ == "__main__":
    unittest.main()
