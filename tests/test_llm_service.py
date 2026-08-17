"""Tests for productive LLM orchestration and caching."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_finder.llm.config import LlmSettings
from job_finder.llm.errors import LLMError
from job_finder.llm.fit_score import FIT_SCORE_VERSION
from job_finder.llm.job_analysis import JobAnalysisValidationError
from job_finder.llm.service import (
    analysis_configuration_key,
    analysis_cache_key,
    analyze_results,
    attach_result,
    load_cache,
    save_cache,
    sort_included_results,
)


def make_job(
    *,
    is_new=True,
    content_changed=False,
    title="Junior Python Developer",
):
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
        "content_changed": content_changed,
    }


def make_record(cache_key):
    """Return a minimal successful persisted analysis."""
    return {
        "cache_key": cache_key,
        "score": 90,
        "analysis": {
            "job": {
                "role_summary": "Technische Python-Einstiegsrolle.",
                "tasks": ["Python entwickeln"],
                "experience_requirement": {
                    "expectation": "none_stated",
                    "evidence_quote": "",
                },
                "technology_requirements": [],
                "other_requirements": [],
            },
            "profile_match": {
                "matches": [],
                "assessment": {
                    "dimension_ratings": {
                        "entry_fit": "excellent",
                        "working_conditions_fit": "excellent",
                        "direction_fit": "excellent",
                        "technology_head_start": "excellent",
                    },
                    "information_quality": "clear",
                    "rationale": ["Klare Einstiegsstelle."],
                },
                "uncertainties": [],
            },
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

    def test_sort_keeps_zero_llm_score_below_positive_llm_scores(self):
        jobs = [
            make_job(title="Fallback score")
            | {"llm_score": 0, "match_percent": 95, "experience_rank": 0},
            make_job(title="Positive score")
            | {"llm_score": 10, "match_percent": 20, "experience_rank": 0},
        ]

        sort_included_results(jobs)

        self.assertEqual(
            [job["title"] for job in jobs],
            ["Positive score", "Fallback score"],
        )

    def test_new_included_job_is_analyzed_and_cached(self):
        results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)

            def fake_analysis(
                _job, _profile, _settings, _client, cache_key, request_log=None
            ):
                return make_record(cache_key)

            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=fake_analysis,
            ) as analyze:
                stats = analyze_results(results, settings, client=object())

            cache = load_cache(settings.cache_path)

        self.assertEqual(stats["analyzed"], 1)
        self.assertEqual(stats["reasons"]["new_job"], 1)
        self.assertEqual(results["included"][0]["llm_score"], 90)
        self.assertEqual(results["included"][0]["llm_status"], "analyzed")
        self.assertEqual(
            results["included"][0]["llm_result"]["seniority"],
            "unspecified",
        )
        self.assertNotIn("llm_analysis", results["included"][0])
        self.assertNotIn("llm_metadata", results["included"][0])
        self.assertEqual(len(cache["analyses"]), 1)
        self.assertEqual(cache["pending"], {})
        analyze.assert_called_once()

    def test_model_call_stats_include_repairs_and_all_token_usage(self):
        results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)

            def fake_analysis(
                _job, _profile, _settings, _client, cache_key, request_log=None
            ):
                request_log.extend(
                    [
                        {
                            "stage": "job_analysis",
                            "validation_repair": False,
                            "success": True,
                            "metadata": {
                                "prompt_eval_count": 100,
                                "eval_count": 20,
                                "cached_input_tokens": 10,
                                "reasoning_tokens": 5,
                            },
                        },
                        {
                            "stage": "profile_match",
                            "validation_repair": False,
                            "success": True,
                            "metadata": {
                                "prompt_eval_count": 200,
                                "eval_count": 40,
                                "cached_input_tokens": 20,
                                "reasoning_tokens": 10,
                            },
                        },
                        {
                            "stage": "profile_match",
                            "validation_repair": True,
                            "success": True,
                            "metadata": {
                                "prompt_eval_count": 250,
                                "eval_count": 50,
                                "cached_input_tokens": 25,
                                "reasoning_tokens": 15,
                            },
                        },
                    ]
                )
                return make_record(cache_key)

            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=fake_analysis,
            ):
                stats = analyze_results(results, settings, client=object())

        self.assertEqual(
            stats["model_calls"],
            {
                "calls": 3,
                "failed": 0,
                "validation_repairs": 1,
                "usage_missing": 0,
                "input_tokens": 550,
                "output_tokens": 110,
                "cached_input_tokens": 55,
                "reasoning_tokens": 30,
            },
        )

    def test_cache_key_ignores_internal_scores_but_tracks_prompt_context(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            job = make_job()
            key = analysis_cache_key(job, "profile-v1", settings)

            internal_change = dict(job)
            internal_change.update(
                {
                    "workflow_status": "ignored",
                    "match_percent": 5,
                    "reasons": ["changed prefilter explanation"],
                    "llm_result": {"recommendation": "not_recommended"},
                }
            )
            context_change = dict(job)
            context_change["employment_type"] = "PART_TIME"

            self.assertEqual(
                analysis_cache_key(internal_change, "profile-v1", settings),
                key,
            )
            self.assertNotEqual(
                analysis_cache_key(context_change, "profile-v1", settings),
                key,
            )

    def test_cached_known_job_is_attached_without_api_call(self):
        first_results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)

            def fake_analysis(
                _job, _profile, _settings, _client, cache_key, request_log=None
            ):
                return make_record(cache_key)

            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=fake_analysis,
            ):
                analyze_results(first_results, settings, client=object())

            known_results = {
                "included": [make_job(is_new=False)],
                "excluded": [],
            }
            with patch("job_finder.llm.service.OpenAIClient") as client_class:
                stats = analyze_results(known_results, settings)

        self.assertEqual(stats["analyzed"], 0)
        self.assertEqual(stats["cached"], 1)
        self.assertEqual(stats["reasons"], {"cache_hit": 1})
        self.assertEqual(known_results["included"][0]["llm_score"], 90)
        self.assertEqual(known_results["included"][0]["llm_status"], "cached")
        self.assertEqual(stats["model_calls"]["calls"], 0)
        client_class.assert_not_called()

    def test_analysis_configuration_change_reanalyzes_known_job(self):
        first_results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)

            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=lambda _job, _profile, _settings, _client, key, request_log=None: make_record(key),
            ):
                analyze_results(first_results, settings, client=object())

            changed_settings = LlmSettings(
                model="new-test-model",
                cache_path=settings.cache_path,
            )
            known_results = {
                "included": [make_job(is_new=False)],
                "excluded": [],
            }
            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=lambda _job, _profile, _settings, _client, key, request_log=None: make_record(key),
            ) as analyze:
                stats = analyze_results(
                    known_results,
                    changed_settings,
                    client=object(),
                )

        self.assertEqual(stats["analyzed"], 1)
        self.assertEqual(stats["reasons"]["profile_or_config_changed"], 1)
        analyze.assert_called_once()

    def test_empty_cache_reanalyzes_known_job(self):
        results = {"included": [make_job(is_new=False)], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=lambda _job, _profile, _settings, _client, key, request_log=None: make_record(key),
            ):
                stats = analyze_results(results, settings, client=object())

        self.assertEqual(stats["analyzed"], 1)
        self.assertEqual(results["included"][0]["llm_status"], "analyzed")
        self.assertEqual(stats["reasons"]["uncached_known_job"], 1)

    def test_known_job_without_cache_record_is_analyzed(self):
        first_results = {"included": [make_job()], "excluded": []}
        known_results = {
            "included": [
                make_job(
                    is_new=False,
                    title="Junior Data Engineer",
                )
            ],
            "excluded": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=lambda _job, _profile, _settings, _client, key, request_log=None: make_record(key),
            ):
                analyze_results(first_results, settings, client=object())
            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=lambda _job, _profile, _settings, _client, key, request_log=None: make_record(key),
            ) as analyze:
                stats = analyze_results(known_results, settings, client=object())

        self.assertEqual(stats["analyzed"], 1)
        self.assertEqual(stats["reasons"]["uncached_known_job"], 1)
        self.assertEqual(known_results["included"][0]["llm_status"], "analyzed")
        analyze.assert_called_once()

    def test_profile_change_reanalyzes_known_included_jobs(self):
        results = {"included": [make_job(is_new=False)], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            settings.cache_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "profile_version": 1,
                        "analyses": {"old": make_record("old")},
                        "pending": {},
                    }
                ),
                encoding="utf-8",
            )

            def fake_analysis(
                _job, _profile, _settings, _client, cache_key, request_log=None
            ):
                return make_record(cache_key)

            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=fake_analysis,
            ) as analyze:
                stats = analyze_results(results, settings, client=object())
            cache = load_cache(settings.cache_path)

        self.assertEqual(stats["analyzed"], 1)
        self.assertEqual(stats["reasons"]["profile_or_config_changed"], 1)
        self.assertEqual(cache["profile_version"], 3)
        self.assertNotIn("old", cache["analyses"])
        analyze.assert_called_once()

    def test_profile_change_preserves_manually_reviewed_unchanged_job(self):
        job = make_job(is_new=False)
        job["workflow_status"] = "ignored"
        results = {"included": [job], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            previous_key = analysis_cache_key(job, 2, settings)
            settings.cache_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "profile_version": 2,
                        "analysis_version": analysis_configuration_key(settings),
                        "analyses": {previous_key: make_record(previous_key)},
                        "pending": {},
                    }
                ),
                encoding="utf-8",
            )

            with patch("job_finder.llm.service.analyze_job_record") as analyze:
                stats = analyze_results(results, settings, client=object())
            cache = load_cache(settings.cache_path)

        current_key = analysis_cache_key(job, 3, settings)
        self.assertEqual(stats["analyzed"], 0)
        self.assertEqual(stats["cached"], 1)
        self.assertIn(current_key, cache["analyses"])
        self.assertEqual(
            cache["analyses"][current_key]["metadata"]["cache_compatibility"],
            "manual_review_preserved_profile_change",
        )
        analyze.assert_not_called()

    def test_changed_known_job_is_analyzed_with_its_new_content(self):
        changed_job = make_job(is_new=False, content_changed=True)
        results = {"included": [changed_job], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)

            def fake_analysis(
                _job, _profile, _settings, _client, cache_key, request_log=None
            ):
                return make_record(cache_key)

            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=fake_analysis,
            ) as analyze:
                stats = analyze_results(results, settings, client=object())

        self.assertEqual(stats["analyzed"], 1)
        self.assertEqual(stats["reasons"]["content_or_input_changed"], 1)
        self.assertEqual(results["included"][0]["llm_score"], 90)
        analyze.assert_called_once()

    def test_client_initialization_error_marks_job_for_later_retry(self):
        results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            with patch(
                "job_finder.llm.service.OpenAIClient",
                side_effect=LLMError("API nicht erreichbar"),
            ):
                stats = analyze_results(results, settings)
            cache = load_cache(settings.cache_path)

        self.assertEqual(stats["failed"], 1)
        self.assertEqual(results["included"][0]["llm_status"], "failed")
        self.assertEqual(
            results["included"][0]["llm_error"],
            "API nicht erreichbar",
        )
        self.assertEqual(len(cache["pending"]), 1)
        self.assertEqual(stats["model_calls"]["calls"], 0)
        self.assertEqual(stats["errors"][0]["category"], "OpenAI")

    def test_failed_model_call_is_counted_without_token_usage(self):
        results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)

            def fail_analysis(
                _job, _profile, _settings, _client, _cache_key, request_log=None
            ):
                request_log.append(
                    {
                        "stage": "job_analysis",
                        "validation_repair": False,
                        "success": False,
                        "metadata": {},
                    }
                )
                raise LLMError("OpenAI-Anfrage fehlgeschlagen: 429 rate_limit")

            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=fail_analysis,
            ):
                stats = analyze_results(results, settings, client=object())

        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["model_calls"]["calls"], 1)
        self.assertEqual(stats["model_calls"]["failed"], 1)
        self.assertEqual(stats["model_calls"]["usage_missing"], 1)
        self.assertEqual(
            stats["errors"][0]["message"],
            "OpenAI begrenzt die Anfragen vorübergehend.",
        )

    def test_local_validation_failure_keeps_both_successful_model_calls(self):
        results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)

            def fail_validation(
                _job, _profile, _settings, _client, _cache_key, request_log=None
            ):
                for repair in (False, True):
                    request_log.append(
                        {
                            "stage": "job_analysis",
                            "validation_repair": repair,
                            "success": True,
                            "metadata": {
                                "prompt_eval_count": 100,
                                "eval_count": 20,
                            },
                        }
                    )
                raise JobAnalysisValidationError(
                    "Beleg kommt nicht wortgetreu in der Stellenanzeige vor"
                )

            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=fail_validation,
            ):
                stats = analyze_results(results, settings, client=object())

        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["model_calls"]["calls"], 2)
        self.assertEqual(stats["model_calls"]["failed"], 0)
        self.assertEqual(stats["model_calls"]["validation_repairs"], 1)
        self.assertEqual(stats["errors"][0]["category"], "Stellenanalyse")
        self.assertIn("nicht wortgetreu", stats["errors"][0]["message"])

    def test_unchanged_validation_failure_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            first_results = {"included": [make_job()], "excluded": []}
            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=JobAnalysisValidationError(
                    "Beleg kommt nicht wortgetreu in der Stellenanzeige vor"
                ),
            ):
                analyze_results(first_results, settings, client=object())

            known_results = {
                "included": [make_job(is_new=False)],
                "excluded": [],
            }
            with patch(
                "job_finder.llm.service.analyze_job_record"
            ) as analyze_again:
                blocked_stats = analyze_results(
                    known_results,
                    settings,
                    client=object(),
                )

        analyze_again.assert_not_called()
        self.assertEqual(blocked_stats["blocked"], 1)
        self.assertEqual(blocked_stats["failed"], 0)
        self.assertEqual(blocked_stats["model_calls"]["calls"], 0)
        self.assertEqual(blocked_stats["reasons"]["validation_blocked"], 1)
        self.assertEqual(known_results["included"][0]["llm_status"], "failed")

    def test_legacy_validation_failure_is_paused_without_one_more_call(self):
        results = {"included": [make_job(is_new=False)], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            key = analysis_cache_key(
                results["included"][0],
                3,
                settings,
            )
            save_cache(
                {
                    "profile_version": 3,
                    "analysis_version": analysis_configuration_key(settings),
                    "analyses": {},
                    "pending": {
                        key: {
                            "job_id": "test:1",
                            "status": "failed",
                            "error": "Unbekannte Profilbelege: profile.fake",
                        }
                    },
                },
                settings.cache_path,
            )
            with patch(
                "job_finder.llm.service.analyze_job_record"
            ) as analyze:
                stats = analyze_results(results, settings, client=object())
            cache = load_cache(settings.cache_path)

        analyze.assert_not_called()
        self.assertEqual(stats["blocked"], 1)
        self.assertEqual(stats["model_calls"]["calls"], 0)
        self.assertEqual(
            cache["pending"][key]["error_kind"],
            "validation",
        )

    def test_pending_known_job_is_retried(self):
        results = {"included": [make_job()], "excluded": []}

        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(directory)
            with patch(
                "job_finder.llm.service.OpenAIClient",
                side_effect=LLMError("API nicht erreichbar"),
            ):
                analyze_results(results, settings)

            known_results = {
                "included": [make_job(is_new=False)],
                "excluded": [],
            }

            def fake_analysis(
                _job, _profile, _settings, _client, cache_key, request_log=None
            ):
                return make_record(cache_key)

            with patch(
                "job_finder.llm.service.analyze_job_record",
                side_effect=fake_analysis,
            ):
                stats = analyze_results(known_results, settings, client=object())
            cache = load_cache(settings.cache_path)

        self.assertEqual(stats["analyzed"], 1)
        self.assertEqual(stats["reasons"]["retry_after_failed"], 1)
        self.assertEqual(cache["pending"], {})

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
            "job_finder.llm.service.score_two_stage_result",
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
