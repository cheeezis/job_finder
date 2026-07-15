"""Tests for the blind local-LLM benchmark."""

import json
import unittest

from job_agent.llm_benchmark import (
    AnalysisValidationError,
    build_messages,
    load_benchmark_data,
    summarize_results,
    validate_analysis,
)
from job_agent.llm_profile import load_llm_profile


def make_analysis(score=70, recommendation="match"):
    """Return a complete analysis matching the versioned schema."""
    dimension_score = score // 5
    dimensions = {
        "role_fit": dimension_score,
        "technology_fit": dimension_score,
        "experience_fit": dimension_score,
        "location_fit": dimension_score,
        "task_fit": dimension_score,
    }
    dimensions["task_fit"] += score - sum(dimensions.values())
    return {
        "overall_score": score,
        "recommendation": recommendation,
        "confidence": "medium",
        "summary": "Nachvollziehbare Testbewertung.",
        "dimension_scores": dimensions,
        "key_tasks": [],
        "key_requirements": [],
        "matching_evidence": [],
        "gaps": [],
        "risks": [],
        "uncertainties": [],
        "hard_conflicts": [],
    }


class LlmBenchmarkTests(unittest.TestCase):
    def test_prompt_is_blind_to_human_labels_and_rule_score(self):
        inputs, _ = load_benchmark_data()
        messages = build_messages(load_llm_profile(), inputs["cases"][0])
        prompt = messages[1]["content"]

        self.assertNotIn("expected_recommendation", prompt)
        self.assertNotIn("rule_score", prompt)
        self.assertNotIn("notes", prompt)
        self.assertIn("description_clean", prompt)
        json.loads(prompt)

    def test_valid_analysis_accepts_consistent_score_and_label(self):
        validate_analysis(make_analysis())

    def test_validation_rejects_wrong_dimension_total(self):
        analysis = make_analysis()
        analysis["overall_score"] = 71

        with self.assertRaises(AnalysisValidationError):
            validate_analysis(analysis)

    def test_validation_rejects_wrong_score_band(self):
        analysis = make_analysis(recommendation="strong_match")

        with self.assertRaises(AnalysisValidationError):
            validate_analysis(analysis)

    def test_summary_counts_invalid_responses_as_non_matches(self):
        results = [
            {
                "valid": True,
                "expected_recommendation": "match",
                "analysis": make_analysis(),
                "elapsed_seconds": 2.0,
            },
            {
                "valid": False,
                "expected_recommendation": "not_recommended",
                "error": "invalid",
                "elapsed_seconds": 4.0,
            },
        ]

        summary = summarize_results(results)

        self.assertEqual(summary["valid_response_rate"], 0.5)
        self.assertEqual(summary["exact_match_rate"], 0.5)
        self.assertEqual(summary["average_seconds_per_job"], 3.0)


if __name__ == "__main__":
    unittest.main()
