"""Tests for evidence-based matching between job facts and profile facts."""

import json
import unittest

from job_finder.llm.profile_loader import LlmProfile
from job_finder.llm.profile_match import (
    PROFILE_MATCH_SCHEMA,
    ProfileMatchValidationError,
    build_profile_match_messages,
    collect_profile_evidence_paths,
    flatten_job_requirements,
    match_job_to_profile,
    validate_profile_match,
)


def make_job_analysis():
    """Return a compact extracted job with one technology and one requirement."""
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
            "evidence_quote": "Erste praktische Erfahrungen",
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


def make_test_profile():
    """Return a synthetic profile independent of public example settings."""
    return LlmProfile(
        version=1,
        data={
            "profile": {
                "summary": "Fiktives Testprofil.",
                "professional_experience_years": 0,
                "location": {},
            },
            "education": [],
            "experience": [{"type": "Praktikum"}],
            "projects": [{"implemented": ["KI-Prototyp"]}],
            "skills": {
                "programming": [{"name": "Python", "level": "basic"}]
            },
            "certifications": [],
            "current_learning": [],
            "strengths": {"items": []},
            "career_preferences": {
                "direction": {"primary": ["AI Developer"]}
            },
            "languages": [{"name": "Deutsch", "level": "C1"}],
            "truth_rules": ["Keine Angaben erfinden"],
        },
    )


def make_match():
    """Return a valid profile match for the compact extracted job."""
    def item(requirement_id, evidence_path):
        return {
            "requirement_id": requirement_id,
            "status": "met",
            "profile_evidence_paths": [evidence_path],
            "explanation": "Die Anforderung ist im Profil belegt.",
            "missing_or_uncertain": [],
        }

    return {
        "matches": [
            item("role", "career_preferences.direction.primary[0]"),
            item("tasks", "projects[0].implemented[0]"),
            {
                **item("experience", "experience[0].type"),
                "status": "partially_met",
                "missing_or_uncertain": ["Regulaere Berufserfahrung"],
            },
            item("technology:0:0", "skills.programming[0].name"),
            item("other:0", "languages[0].level"),
        ],
        "assessment": {
            "dimension_ratings": {
                "entry_fit": "excellent",
                "working_conditions_fit": "excellent",
                "direction_fit": "excellent",
                "technology_head_start": "good",
            },
            "information_quality": "clear",
            "rationale": ["Klare Einstiegsrolle mit passenden Bedingungen."],
        },
        "uncertainties": [],
    }


class LlmProfileMatchTests(unittest.TestCase):
    def setUp(self):
        self.profile = make_test_profile()
        self.job_analysis = make_job_analysis()

    def test_prompt_contains_profile_and_job_analysis_but_no_score(self):
        messages = build_profile_match_messages(
            self.profile,
            self.job_analysis,
            {
                "locations": ["Fulda"],
                "work_mode": "remote",
                "remote_percentage": 100,
                "location_precheck": "100% Remote aus Deutschland",
            },
        )
        prompt = json.loads(messages[1]["content"])

        self.assertEqual(prompt["profile_version"], self.profile.version)
        self.assertEqual(
            prompt["requirements"],
            flatten_job_requirements(self.job_analysis),
        )
        self.assertIn(
            "skills.programming[0].name",
            prompt["allowed_profile_evidence_paths"],
        )
        self.assertIn("entry_fit", prompt["evaluation_rubric"])
        self.assertEqual(prompt["job_context"]["work_mode"], "remote")
        self.assertNotIn("overall_score", PROFILE_MATCH_SCHEMA["properties"])
        self.assertNotIn("recommendation", PROFILE_MATCH_SCHEMA["properties"])

    def test_job_requirements_are_flattened_with_stable_ids(self):
        requirements = flatten_job_requirements(self.job_analysis)

        self.assertEqual(
            [requirement["id"] for requirement in requirements],
            ["role", "tasks", "experience", "technology:0:0", "other:0"],
        )

    def test_profile_paths_include_nested_scalar_values(self):
        paths = collect_profile_evidence_paths(self.profile.data)

        self.assertIn("skills.programming[0].level", paths)

    def test_complete_profile_match_is_valid(self):
        match = make_match()

        self.assertIs(
            validate_profile_match(match, self.job_analysis, self.profile),
            match,
        )

    def test_positive_match_requires_profile_evidence(self):
        match = make_match()
        match["matches"][3]["profile_evidence_paths"] = []

        with self.assertRaises(ProfileMatchValidationError):
            validate_profile_match(match, self.job_analysis, self.profile)

    def test_assessment_requires_all_priority_dimensions(self):
        match = make_match()
        del match["assessment"]["dimension_ratings"]["entry_fit"]

        with self.assertRaises(ProfileMatchValidationError):
            validate_profile_match(match, self.job_analysis, self.profile)

    def test_unknown_profile_path_is_rejected(self):
        match = make_match()
        match["matches"][0]["profile_evidence_paths"] = ["skills.magic"]

        with self.assertRaisesRegex(
            ProfileMatchValidationError,
            "Unbekannte Profilbelege",
        ):
            validate_profile_match(match, self.job_analysis, self.profile)

    def test_every_requirement_must_be_matched_once(self):
        match = make_match()
        match["matches"] = match["matches"][:-1]

        with self.assertRaisesRegex(
            ProfileMatchValidationError,
            "genau einmal",
        ):
            validate_profile_match(match, self.job_analysis, self.profile)

    def test_matching_attaches_profile_version(self):
        class FakeClient:
            def chat(self, **_kwargs):
                return make_match(), {"eval_count": 12}

        result, metadata = match_job_to_profile(
            self.job_analysis,
            self.profile,
            "test-model",
            FakeClient(),
        )

        self.assertEqual(result["profile_version"], self.profile.version)
        self.assertEqual(result["match"]["matches"][0]["status"], "met")
        self.assertEqual(metadata["eval_count"], 12)

    def test_invalid_match_is_retried_with_validation_feedback(self):
        class RepairingClient:
            def __init__(self):
                self.calls = []

            def chat(self, **kwargs):
                self.calls.append(kwargs)
                match = make_match()
                if len(self.calls) == 1:
                    match["matches"][0]["profile_evidence_paths"] = []
                return match, {}

        client = RepairingClient()
        request_log = []

        result, metadata = match_job_to_profile(
            self.job_analysis,
            self.profile,
            "test-model",
            client,
            request_log=request_log,
        )

        self.assertEqual(result["match"]["matches"][0]["status"], "met")
        self.assertEqual(metadata["validation_retries"], 1)
        self.assertEqual(len(client.calls), 2)
        retry_message = client.calls[1]["messages"][-1]["content"]
        self.assertIn("braucht Profilbeleg", retry_message)
        self.assertEqual(
            [(entry["stage"], entry["validation_repair"]) for entry in request_log],
            [("profile_match", False), ("profile_match", True)],
        )
        self.assertTrue(all(entry["success"] for entry in request_log))


if __name__ == "__main__":
    unittest.main()
