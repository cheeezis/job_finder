"""Tests for productive LLM orchestration and caching."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_agent.llm.config import LlmSettings
from job_agent.llm.errors import LLMError
from job_agent.llm.fit_score import FIT_SCORE_VERSION
from job_agent.llm.service import analyze_results, attach_result, load_cache


def make_job(*, is_new=True, title="Junior Python Developer"):
    """Return one included serialized job row."""
    return {
        "id": "test:1",
        "title": title,
        "description_clean": "Python, Einstieg ohne Berufserfahrung.",
        "locations": ["Fulda"],
        "work_mode": "hybrid",
        "remote_percentage": None,
        "location_precheck": "30-km-Radius",
        "match_percent": 80,
        "experience_rank": 0,
        "is_new": is_new,
    }


def make_record(cache_key):
    """Return a minimal successful persisted analysis."""
    return {
        "cache_key": cache_key,
        "score": 90,
        "analysis": {
            "job": {
                "tasks": ["Python entwickeln"],
                "experience_requirement": {
                    "expectation": "none_stated",
                    "evidence_quote": "",
                },
                "technology_requirements": [],
                "other_requirements": [],
            },
            "profile_match": {"matches": []},
            "fit": {
                "recommendation": "strong_match",
                "confidence": "high",
                "summary": "Sehr passende Einstiegsstelle.",
                "matching_evidence": [],
                "gaps": [],
                "risks": [],
            },
        },
        "metadata": {
            "model": "test-model",
            "fit_score_version": FIT_SCORE_VERSION,
        },
    }


class LlmServiceTests(unittest.TestCase):
    def settings(self, directory):
        return LlmSettings(
            model="test-model",
            cache_path=Path(directory) / "llm-cache.json",
        )

    def test_new_included_job_is_analyzed_and_cached(self):
        results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)

            def fake_analysis(_job, _profile, _settings, _client, cache_key):
                return make_record(cache_key)

            with patch(
                "job_agent.llm.service.analyze_job_record",
                side_effect=fake_analysis,
            ) as analyze:
                stats = analyze_results(results, settings, client=object())

            cache = load_cache(settings.cache_path)

        self.assertEqual(stats["analyzed"], 1)
        self.assertEqual(results["included"][0]["llm_score"], 90)
        self.assertEqual(results["included"][0]["llm_status"], "analyzed")
        self.assertNotIn("llm_analysis", results["included"][0])
        self.assertNotIn("llm_metadata", results["included"][0])
        self.assertEqual(len(cache), 1)
        analyze.assert_called_once()

    def test_cached_known_job_is_attached_without_api_call(self):
        first_results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)

            def fake_analysis(_job, _profile, _settings, _client, cache_key):
                return make_record(cache_key)

            with patch(
                "job_agent.llm.service.analyze_job_record",
                side_effect=fake_analysis,
            ):
                analyze_results(first_results, settings, client=object())

            known_results = {
                "included": [make_job(is_new=False)],
                "excluded": [],
            }
            with patch("job_agent.llm.service.OpenAIClient") as client_class:
                stats = analyze_results(known_results, settings)

        self.assertEqual(stats["analyzed"], 0)
        self.assertEqual(stats["cached"], 1)
        self.assertEqual(known_results["included"][0]["llm_score"], 90)
        self.assertEqual(known_results["included"][0]["llm_status"], "cached")
        client_class.assert_not_called()

    def test_known_uncached_job_is_skipped_by_default(self):
        results = {"included": [make_job(is_new=False)], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            with patch("job_agent.llm.service.OpenAIClient") as client_class:
                stats = analyze_results(results, settings)

        self.assertEqual(stats["eligible"], 0)
        self.assertNotIn("llm_score", results["included"][0])
        client_class.assert_not_called()

    def test_client_initialization_error_marks_job_for_later_retry(self):
        results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            with patch(
                "job_agent.llm.service.OpenAIClient",
                side_effect=LLMError("API nicht erreichbar"),
            ):
                stats = analyze_results(results, settings)

        self.assertEqual(stats["failed"], 1)
        self.assertEqual(results["included"][0]["llm_status"], "failed")
        self.assertEqual(
            results["included"][0]["llm_error"],
            "API nicht erreichbar",
        )

    def test_stale_cached_score_is_recomputed_without_llm_call(self):
        job = make_job(is_new=False)
        record = make_record("cache-key")
        record["metadata"]["fit_score_version"] = FIT_SCORE_VERSION - 1
        updated_fit = {
            "overall_score": 85,
            "recommendation": "match",
            "confidence": "medium",
            "summary": "Passende Stelle.",
            "matching_evidence": [],
            "gaps": [],
            "risks": [],
        }

        with patch(
            "job_agent.llm.service.score_two_stage_result",
            return_value=updated_fit,
        ) as score:
            changed = attach_result(job, record, "cached")

        self.assertTrue(changed)
        self.assertEqual(job["llm_score"], 85)
        self.assertEqual(
            record["metadata"]["fit_score_version"],
            FIT_SCORE_VERSION,
        )
        score.assert_called_once()


if __name__ == "__main__":
    unittest.main()
