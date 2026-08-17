"""Tests for profile-independent LLM job analysis."""

import json
import unittest

from job_finder.llm.job_analysis import (
    JOB_ANALYSIS_SCHEMA,
    SYSTEM_PROMPT,
    JobAnalysisValidationError,
    analyze_job,
    build_job_analysis_messages,
    job_analysis_input,
    validate_job_analysis,
)


def make_job_analysis():
    """Return one complete, valid extraction result."""
    return {
        "role_summary": "Juniorrolle fuer interne KI-Loesungen.",
        "primary_role_family": "ai_ml",
        "secondary_role_families": ["consulting_business_analysis"],
        "seniority": "junior_entry",
        "seniority_basis": "explicit_label",
        "seniority_evidence_quote": "Junior IT KI Consultant",
        "experience_requirement": {
            "expectation": "first_exposure",
            "priority": "explicit_requirement",
            "minimum_years": None,
            "evidence_quote": "Erste praktische Erfahrungen mit Daten und APIs",
        },
        "tasks": ["Interne KI-Loesungen entwickeln"],
        "technology_requirements": [
            {
                "priority": "explicit_requirement",
                "selection": "single",
                "minimum_count": 1,
                "technologies": [
                    {"name": "Python", "expected_level": "basic"}
                ],
                "evidence_quote": "Python-Grundkenntnisse",
            }
        ],
        "other_requirements": [
            {
                "category": "language",
                "priority": "explicit_requirement",
                "requirement": "Deutsch C1",
                "evidence_quote": "Deutschkenntnisse Niveau C1",
            }
        ],
        "uncertainties": [],
    }


def source_job():
    """Return job text containing every evidence quote in the sample analysis."""
    return {
        "title": "Junior IT KI Consultant",
        "description_clean": (
            "Erste praktische Erfahrungen mit Daten und APIs. "
            "Python-Grundkenntnisse. Deutschkenntnisse Niveau C1."
        ),
    }


class LlmJobAnalysisTests(unittest.TestCase):
    def test_prompt_keeps_alternative_qualifications_together(self):
        self.assertIn("Zerlege Alternativen nicht", SYSTEM_PROMPT)

    def test_prompt_contains_job_but_no_profile_or_score(self):
        job = {
            "title": "Junior AI Engineer",
            "description_clean": "Python-Grundkenntnisse erforderlich",
        }

        messages = build_job_analysis_messages(job)
        prompt = json.loads(messages[1]["content"])

        self.assertEqual(prompt["job"], job)
        self.assertNotIn("profile", prompt)
        self.assertNotIn("score_bands", prompt)
        self.assertNotIn("recommendation", JOB_ANALYSIS_SCHEMA["properties"])
        self.assertNotIn("overall_score", JOB_ANALYSIS_SCHEMA["properties"])

    def test_prompt_excludes_prefilter_and_workflow_fields(self):
        job = source_job() | {
            "company": "Example GmbH",
            "sources": [{"url": "https://example.test/job"}],
            "workflow_status": "interesting",
            "rule_score": 95,
            "match_percent": 95,
            "role_group": "ai_ml",
            "reasons": ["Profilbezug"],
            "llm_result": {"recommendation": "strong_match"},
        }

        prompt_job = job_analysis_input(job)

        self.assertEqual(prompt_job, source_job())
        self.assertNotIn("company", prompt_job)
        self.assertNotIn("match_percent", prompt_job)
        self.assertNotIn("workflow_status", prompt_job)

    def test_portal_career_level_is_valid_seniority_evidence(self):
        class FakeClient:
            def chat(self, **kwargs):
                self.call = kwargs
                return analysis, {}

        analysis = make_job_analysis()
        analysis["seniority_evidence_quote"] = (
            "Karrierestufe (Portal-Metadatum): Berufseinstieg/Trainee"
        )
        job = source_job() | {"career_levels": ["Berufseinstieg/Trainee"]}
        client = FakeClient()

        result, _ = analyze_job(job, "test-model", client)

        self.assertEqual(result["seniority"], "junior_entry")
        prompt = json.loads(client.call["messages"][1]["content"])
        self.assertIn("Karrierestufe (Portal-Metadatum)", prompt["job"]["description_clean"])

    def test_complete_job_analysis_is_valid(self):
        analysis = make_job_analysis()

        self.assertIs(validate_job_analysis(analysis), analysis)

    def test_unknown_fields_are_rejected(self):
        analysis = make_job_analysis()
        analysis["recommendation"] = "strong_match"

        with self.assertRaises(JobAnalysisValidationError):
            validate_job_analysis(analysis)

    def test_experience_years_must_be_explicit_integer_or_null(self):
        analysis = make_job_analysis()
        analysis["experience_requirement"]["minimum_years"] = "zwei"

        with self.assertRaises(JobAnalysisValidationError):
            validate_job_analysis(analysis)

    def test_minimum_count_cannot_exceed_technology_count(self):
        analysis = make_job_analysis()
        group = analysis["technology_requirements"][0]
        group["selection"] = "minimum_count"
        group["minimum_count"] = 2

        with self.assertRaises(JobAnalysisValidationError):
            validate_job_analysis(analysis)

    def test_primary_role_cannot_be_repeated_as_secondary(self):
        analysis = make_job_analysis()
        analysis["secondary_role_families"] = ["ai_ml"]

        with self.assertRaises(JobAnalysisValidationError):
            validate_job_analysis(analysis)

    def test_unspecified_seniority_cannot_invent_evidence(self):
        analysis = make_job_analysis()
        analysis["seniority"] = "unspecified"

        with self.assertRaises(JobAnalysisValidationError):
            validate_job_analysis(analysis)

    def test_inferred_entry_signal_cannot_be_classified_as_junior(self):
        analysis = make_job_analysis()
        analysis["seniority_basis"] = "inferred_from_experience"
        analysis["seniority_evidence_quote"] = "Erste Erfahrungen mit C++"

        with self.assertRaisesRegex(
            JobAnalysisValidationError,
            "ausdrueckliches Label",
        ):
            validate_job_analysis(analysis)

    def test_paraphrased_evidence_is_rejected(self):
        analysis = make_job_analysis()
        analysis["technology_requirements"][0]["evidence_quote"] = (
            "Python ist von Vorteil"
        )

        with self.assertRaises(JobAnalysisValidationError):
            validate_job_analysis(analysis, source_job()["description_clean"])

    def test_typographic_hyphen_in_evidence_matches_ascii_hyphen(self):
        analysis = make_job_analysis()
        job = source_job()
        description = job["description_clean"].replace(
            "Python-Grundkenntnisse",
            "Python\u2011Grundkenntnisse",
        )
        source_text = f"{job['title']}\n{description}"

        validate_job_analysis(analysis, source_text)

    def test_soft_hyphens_do_not_break_evidence_matching(self):
        analysis = make_job_analysis()
        job = source_job()
        analysis["technology_requirements"][0]["evidence_quote"] = (
            "Python\u00ad-Grundkenntnisse"
        )

        validate_job_analysis(
            analysis,
            f"{job['title']}\n{job['description_clean']}",
        )

    def test_analyze_job_uses_profile_free_schema(self):
        class FakeClient:
            def __init__(self):
                self.call = None

            def chat(self, **kwargs):
                self.call = kwargs
                return make_job_analysis(), {"eval_count": 10}

        client = FakeClient()
        job = source_job()

        analysis, metadata = analyze_job(job, "test-model", client)

        self.assertEqual(analysis["primary_role_family"], "ai_ml")
        self.assertEqual(metadata["eval_count"], 10)
        self.assertEqual(client.call["model"], "test-model")
        self.assertEqual(client.call["output_schema"], JOB_ANALYSIS_SCHEMA)
        prompt = json.loads(client.call["messages"][1]["content"])
        self.assertNotIn("profile", prompt)

    def test_analyze_job_normalizes_mechanical_all_of_count(self):
        class InconsistentClient:
            def chat(self, **_kwargs):
                analysis = make_job_analysis()
                group = analysis["technology_requirements"][0]
                group["selection"] = "all_of"
                group["technologies"].append(
                    {"name": "REST", "expected_level": "basic"}
                )
                return analysis, {}

        analysis, _metadata = analyze_job(
            source_job(),
            "test-model",
            InconsistentClient(),
        )

        self.assertEqual(analysis["technology_requirements"][0]["minimum_count"], 2)

    def test_invalid_model_response_is_attached_to_validation_error(self):
        class FakeClient:
            def chat(self, **_kwargs):
                analysis = make_job_analysis()
                analysis["seniority_evidence_quote"] = "Erfundenes Zitat"
                return analysis, {}

        with self.assertRaises(JobAnalysisValidationError) as raised:
            analyze_job(source_job(), "test-model", FakeClient())

        self.assertEqual(
            raised.exception.raw_analysis["seniority_evidence_quote"],
            "Erfundenes Zitat",
        )

    def test_invalid_response_is_retried_with_validation_feedback(self):
        class RepairingClient:
            def __init__(self):
                self.calls = []

            def chat(self, **kwargs):
                self.calls.append(kwargs)
                analysis = make_job_analysis()
                if len(self.calls) == 1:
                    analysis["seniority_evidence_quote"] = "Erfundenes Zitat"
                return analysis, {}

        client = RepairingClient()
        request_log = []

        analysis, metadata = analyze_job(
            source_job(),
            "test-model",
            client,
            request_log=request_log,
        )

        self.assertEqual(analysis["seniority"], "junior_entry")
        self.assertEqual(metadata["validation_retries"], 1)
        self.assertEqual(len(client.calls), 2)
        retry_message = client.calls[1]["messages"][-1]["content"]
        self.assertIn("Beleg kommt nicht wortgetreu", retry_message)
        self.assertEqual(
            [(entry["stage"], entry["validation_repair"]) for entry in request_log],
            [("job_analysis", False), ("job_analysis", True)],
        )
        self.assertTrue(all(entry["success"] for entry in request_log))


if __name__ == "__main__":
    unittest.main()
