"""Tests for profile-matching evaluation against cached job analyses."""

import json
import tempfile
import unittest
from pathlib import Path

from job_agent.llm.job_analysis import JOB_ANALYSIS_PROMPT_VERSION
from llm_evaluation.benchmark import BenchmarkDataError, load_benchmark_data
from llm_evaluation.profile_matching import (
    job_analysis_cache_path,
    load_job_analysis_cache,
    run_profile_match_evaluation,
    write_profile_match_result,
)


ANALYSIS_MODEL = "analysis:test"


def make_cached_analysis():
    """Return a minimal valid analysis for the first development fixture."""
    return {
        "role_summary": "Datenanalyse und Prozessoptimierung mit KI.",
        "primary_role_family": "data",
        "secondary_role_families": ["ai_ml"],
        "seniority": "unspecified",
        "seniority_evidence_quote": "",
        "experience_requirement": {
            "expectation": "practical_experience",
            "priority": "explicit_requirement",
            "minimum_years": None,
            "evidence_quote": "Erfahrung in Python",
        },
        "tasks": [],
        "technology_requirements": [],
        "other_requirements": [],
        "uncertainties": [],
    }


def write_cache(directory, **overrides):
    """Write one complete development cache with configurable metadata."""
    inputs, _ = load_benchmark_data()
    cases = inputs["cases"][:3]
    cache = {
        "model": ANALYSIS_MODEL,
        "mode": "job_analysis",
        "split": "development",
        "prompt_version": JOB_ANALYSIS_PROMPT_VERSION,
        "schema_version": 2,
        "results": [
            {
                "job_id": case["job_id"],
                "valid": True,
                "analysis": make_cached_analysis(),
            }
            for case in cases
        ],
        **overrides,
    }
    path = job_analysis_cache_path(ANALYSIS_MODEL, "development", directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache), encoding="utf-8")
    return path


def make_profile_match():
    """Return matches for role, tasks, and experience requirements."""
    return {
        "matches": [
            {
                "requirement_id": "role",
                "status": "partially_met",
                "profile_evidence_paths": ["profile.summary"],
                "explanation": "Die fachliche Richtung ist teilweise belegt.",
                "missing_or_uncertain": [],
            },
            {
                "requirement_id": "tasks",
                "status": "unknown",
                "profile_evidence_paths": [],
                "explanation": "Es wurden keine Aufgaben extrahiert.",
                "missing_or_uncertain": ["Aufgaben"],
            },
            {
                "requirement_id": "experience",
                "status": "partially_met",
                "profile_evidence_paths": ["experience[0].type"],
                "explanation": "Praktische Erfahrung ist teilweise belegt.",
                "missing_or_uncertain": [],
            },
        ],
        "uncertainties": [],
    }


class ProfileMatchingEvaluationTests(unittest.TestCase):
    def test_cache_is_loaded_and_revalidated(self):
        with tempfile.TemporaryDirectory() as directory:
            write_cache(directory)

            prepared = load_job_analysis_cache(
                ANALYSIS_MODEL,
                limit=1,
                results_dir=directory,
            )

        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0][1]["primary_role_family"], "data")

    def test_cache_with_old_prompt_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            write_cache(directory, prompt_version=2)

            with self.assertRaisesRegex(BenchmarkDataError, "prompt_version"):
                load_job_analysis_cache(
                    ANALYSIS_MODEL,
                    limit=1,
                    results_dir=directory,
                )

    def test_profile_match_evaluation_uses_cached_analysis(self):
        class FakeClient:
            def chat(self, **_kwargs):
                return make_profile_match(), {}

        with tempfile.TemporaryDirectory() as directory:
            write_cache(directory)
            result = run_profile_match_evaluation(
                ANALYSIS_MODEL,
                "match:test",
                FakeClient(),
                limit=1,
                results_dir=directory,
            )

        self.assertEqual(result["mode"], "profile_match")
        self.assertEqual(result["analysis_model"], ANALYSIS_MODEL)
        self.assertEqual(result["model"], "match:test")
        self.assertEqual(result["summary"]["valid_responses"], 1)

    def test_profile_match_result_filename_contains_both_models(self):
        result = {
            "analysis_model": "analysis-model",
            "model": "match-model",
            "split": "development",
        }

        with tempfile.TemporaryDirectory() as directory:
            path = write_profile_match_result(result, directory)

            self.assertEqual(
                path,
                Path(directory)
                / "analysis-model-analysis__match-model-match-development.json",
            )


if __name__ == "__main__":
    unittest.main()
