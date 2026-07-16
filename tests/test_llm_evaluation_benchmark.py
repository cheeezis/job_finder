"""Tests for the blind local-LLM evaluation benchmark."""

import json
import tempfile
import unittest
from pathlib import Path

from llm_evaluation.benchmark import (
    AnalysisValidationError,
    build_messages,
    finalize_analysis,
    load_benchmark_data,
    load_job_analysis_split,
    run_job_analysis_evaluation,
    run_model_benchmark,
    summarize_results,
    validate_analysis,
    write_job_analysis_result,
)
from job_agent.llm.profile_loader import load_llm_profile


def make_analysis(score=75, recommendation="match"):
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
        "dimension_ratings": {
            "role_fit": "good",
            "technology_fit": "good",
            "experience_fit": "good",
            "location_fit": "good",
            "task_fit": "good",
        },
        "key_tasks": [],
        "key_requirements": [],
        "matching_evidence": [],
        "gaps": [],
        "risks": [],
        "uncertainties": [],
        "hard_conflicts": [],
    }


def make_model_response():
    response = make_analysis(score=75)
    del response["overall_score"]
    del response["recommendation"]
    del response["dimension_scores"]
    return response


class LlmBenchmarkTests(unittest.TestCase):
    def test_job_analysis_splits_are_complete_and_disjoint(self):
        inputs, _ = load_benchmark_data()
        split_data = load_job_analysis_split()
        splits = split_data["splits"]

        all_split_ids = [
            job_id
            for split_name in ("development", "holdout", "reserve")
            for job_id in splits[split_name]
        ]
        input_ids = [case["job_id"] for case in inputs["cases"]]

        self.assertEqual(len(all_split_ids), len(set(all_split_ids)))
        self.assertEqual(set(all_split_ids), set(input_ids))
        self.assertEqual(splits["development"], input_ids[:3])

    def test_job_analysis_defaults_to_development_split(self):
        class FakeClient:
            def chat(self, **_kwargs):
                raise RuntimeError("Client darf fuer limit=0 nicht laufen")

        result = run_job_analysis_evaluation(
            "test-model",
            FakeClient(),
            limit=0,
        )

        self.assertEqual(result["split"], "development")
        self.assertEqual(result["summary"]["jobs"], 0)

    def test_job_analysis_result_filename_contains_split(self):
        result = {
            "model": "gemma3:12b",
            "split": "holdout",
            "results": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = write_job_analysis_result(result, directory)

            self.assertEqual(
                path,
                Path(directory) / "gemma3-12b-job-analysis-holdout.json",
            )

    def test_existing_fit_benchmark_still_returns_results(self):
        class FakeClient:
            def chat(self, **_kwargs):
                return make_model_response(), {}

        result = run_model_benchmark("test-model", FakeClient(), limit=1)

        self.assertEqual(result["model"], "test-model")
        self.assertEqual(result["summary"]["jobs"], 1)
        self.assertEqual(len(result["results"]), 1)

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

    def test_finalization_derives_total_and_score_band(self):
        response = make_model_response()
        response["dimension_ratings"]["role_fit"] = "excellent"
        response["dimension_ratings"]["technology_fit"] = "excellent"
        response["dimension_ratings"]["experience_fit"] = "excellent"
        analysis = finalize_analysis(response)

        self.assertEqual(analysis["overall_score"], 90)
        self.assertEqual(analysis["recommendation"], "strong_match")

    def test_validation_rejects_wrong_dimension_total(self):
        analysis = make_analysis()
        analysis["overall_score"] = 76

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
        self.assertEqual(summary["within_one_band_rate"], 0.5)
        self.assertEqual(summary["mean_band_distance"], 0.0)
        self.assertEqual(summary["dangerous_false_positives"], 0)
        self.assertEqual(summary["missed_positive_jobs"], 0)
        self.assertEqual(summary["average_seconds_per_job"], 3.0)

    def test_summary_detects_dangerous_false_positive(self):
        result = {
            "valid": True,
            "expected_recommendation": "borderline",
            "analysis": make_analysis(score=90, recommendation="strong_match"),
            "elapsed_seconds": 1.0,
        }

        summary = summarize_results([result])

        self.assertEqual(summary["mean_band_distance"], 2.0)
        self.assertEqual(summary["within_one_band_rate"], 0.0)
        self.assertEqual(summary["dangerous_false_positives"], 1)

    def test_summary_detects_missed_positive_job(self):
        result = {
            "valid": True,
            "expected_recommendation": "match",
            "analysis": make_analysis(score=65, recommendation="borderline"),
            "elapsed_seconds": 1.0,
        }

        summary = summarize_results([result])

        self.assertEqual(summary["missed_positive_jobs"], 1)
        self.assertEqual(summary["dangerous_false_positives"], 0)


if __name__ == "__main__":
    unittest.main()
