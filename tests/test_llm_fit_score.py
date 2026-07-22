"""Tests for deterministic scoring after the two LLM stages."""

import unittest

from jsonschema import validate

from job_agent.llm.contract import ANALYSIS_SCHEMA
from job_agent.llm.fit_score import score_location, score_two_stage_result


def make_job_analysis(
    role="ai_ml",
    seniority="junior_entry",
    expectation="first_exposure",
    technology_count=1,
    minimum_count=1,
    expected_level="practical",
):
    """Return a compact, internally consistent extracted job."""
    return {
        "role_summary": "Technische Einstiegsrolle.",
        "primary_role_family": role,
        "secondary_role_families": [],
        "seniority": seniority,
        "seniority_basis": (
            "explicit_label" if seniority == "junior_entry"
            else "inferred_from_experience" if seniority in {"mid", "senior"}
            else "not_stated"
        ),
        "seniority_evidence_quote": "Junior" if seniority == "junior_entry" else "",
        "experience_requirement": {
            "expectation": expectation,
            "priority": "explicit_requirement",
            "minimum_years": None,
            "evidence_quote": "Erste Erfahrung",
        },
        "tasks": ["Software entwickeln"],
        "technology_requirements": [
            {
                "priority": "explicit_requirement",
                "selection": "minimum_count" if technology_count > 1 else "single",
                "minimum_count": minimum_count,
                "technologies": [
                    {
                        "name": f"Technologie {index}",
                        "expected_level": expected_level,
                    }
                    for index in range(technology_count)
                ],
                "evidence_quote": "Technologien",
            }
        ],
        "other_requirements": [],
        "uncertainties": [],
    }


def make_profile_match(
    technology_statuses,
    experience_status="met",
    task_status="met",
    ratings=None,
    information_quality="clear",
):
    """Return complete evidence matches for the compact extracted job."""
    matches = [
        make_match("role", "met"),
        make_match("tasks", task_status),
        make_match("experience", experience_status),
    ]
    matches.extend(
        make_match(f"technology:0:{index}", status)
        for index, status in enumerate(technology_statuses)
    )
    return {
        "matches": matches,
        "assessment": {
            "dimension_ratings": ratings
            or {
                "entry_fit": "excellent",
                "working_conditions_fit": "excellent",
                "direction_fit": "excellent",
                "technology_head_start": "excellent",
            },
            "information_quality": information_quality,
            "rationale": ["Nachvollziehbare persoenliche Bewertung."],
        },
        "uncertainties": [],
    }


def make_match(requirement_id, status):
    evidence = ["profile.summary"] if status in {"met", "partially_met"} else []
    gaps = [] if status == "met" else [f"Luecke bei {requirement_id}"]
    return {
        "requirement_id": requirement_id,
        "status": status,
        "profile_evidence_paths": evidence,
        "explanation": f"Bewertung fuer {requirement_id}",
        "missing_or_uncertain": gaps,
    }


class TwoStageFitScoreTests(unittest.TestCase):
    def test_perfect_ai_entry_job_is_a_strong_match(self):
        job = {
            "location_precheck": "100% Remote aus Deutschland",
            "work_mode": "remote",
        }
        result = score_two_stage_result(
            job,
            make_job_analysis(),
            make_profile_match(["met"]),
        )

        validate(instance=result, schema=ANALYSIS_SCHEMA)
        self.assertEqual(result["overall_score"], 100)
        self.assertEqual(result["recommendation"], "strong_match")

    def test_broad_minimum_stack_with_partial_experience_is_borderline(self):
        job = {
            "location_precheck": "100% Remote aus Deutschland",
            "work_mode": "remote",
        }
        analysis = make_job_analysis(
            role="software_development",
            seniority="unspecified",
            expectation="practical_experience",
            technology_count=5,
            minimum_count=2,
            expected_level="unclear",
        )
        result = score_two_stage_result(
            job,
            analysis,
            make_profile_match(
                ["met", "partially_met", "not_met", "not_met", "not_met"],
                experience_status="partially_met",
                ratings={
                    "entry_fit": "good",
                    "working_conditions_fit": "excellent",
                    "direction_fit": "good",
                    "technology_head_start": "partial",
                },
            ),
        )

        self.assertEqual(result["dimension_ratings"]["entry_fit"], "partial")
        self.assertEqual(result["dimension_scores"]["technology_head_start"], 2)
        self.assertEqual(result["overall_score"], 59)
        self.assertEqual(result["recommendation"], "borderline")

    def test_uncertain_entry_and_neutral_direction_is_not_recommended(self):
        job = {
            "location_precheck": "100% Remote aus Deutschland",
            "work_mode": "remote",
        }
        result = score_two_stage_result(
            job,
            make_job_analysis(role="frontend", seniority="unspecified"),
            make_profile_match(
                ["partially_met"],
                ratings={
                    "entry_fit": "partial",
                    "working_conditions_fit": "excellent",
                    "direction_fit": "partial",
                    "technology_head_start": "partial",
                },
                information_quality="sufficient",
            ),
        )

        self.assertEqual(result["overall_score"], 39)
        self.assertEqual(result["recommendation"], "not_recommended")

    def test_senior_signal_blocks_an_otherwise_high_score(self):
        job = {
            "location_precheck": "100% Remote aus Deutschland",
            "work_mode": "remote",
        }
        result = score_two_stage_result(
            job,
            make_job_analysis(seniority="senior"),
            make_profile_match(["met"]),
        )

        self.assertEqual(result["overall_score"], 39)
        self.assertEqual(result["recommendation"], "not_recommended")
        self.assertTrue(result["hard_conflicts"])

    def test_realistic_but_not_explicit_entry_job_is_capped_as_match(self):
        job = {
            "location_precheck": "100% Remote aus Deutschland",
            "work_mode": "remote",
        }
        result = score_two_stage_result(
            job,
            make_job_analysis(seniority="unspecified"),
            make_profile_match(
                ["met"],
                ratings={
                    "entry_fit": "good",
                    "working_conditions_fit": "excellent",
                    "direction_fit": "excellent",
                    "technology_head_start": "excellent",
                },
            ),
        )

        self.assertEqual(result["overall_score"], 79)
        self.assertEqual(result["recommendation"], "match")

    def test_explicit_entry_job_is_not_downgraded_for_missing_later_experience(self):
        job = {
            "location_precheck": "im 30-km-Radius",
            "work_mode": "onsite",
        }
        result = score_two_stage_result(
            job,
            make_job_analysis(
                seniority="junior_entry",
                expectation="practical_experience",
                technology_count=3,
                minimum_count=3,
            ),
            make_profile_match(
                ["partially_met", "partially_met", "partially_met"],
                experience_status="partially_met",
                ratings={
                    "entry_fit": "partial",
                    "working_conditions_fit": "good",
                    "direction_fit": "good",
                    "technology_head_start": "partial",
                },
            ),
        )

        self.assertEqual(result["dimension_ratings"]["entry_fit"], "excellent")
        self.assertEqual(result["overall_score"], 88)
        self.assertEqual(result["recommendation"], "strong_match")

    def test_vague_advertisement_is_capped_as_borderline(self):
        job = {
            "location_precheck": "100% Remote aus Deutschland",
            "work_mode": "remote",
        }
        result = score_two_stage_result(
            job,
            make_job_analysis(),
            make_profile_match(["met"], information_quality="vague"),
        )

        self.assertEqual(result["overall_score"], 59)
        self.assertEqual(result["recommendation"], "borderline")

    def test_local_job_is_a_good_location_fit(self):
        job = {
            "location_precheck": "im 30-km-Radius",
            "work_mode": "onsite",
        }

        self.assertEqual(score_location(job), "good")

    def test_deterministic_remote_check_corrects_working_condition_rating(self):
        job = {
            "location_precheck": "100% Remote aus Deutschland",
            "work_mode": "remote",
        }
        result = score_two_stage_result(
            job,
            make_job_analysis(),
            make_profile_match(
                ["met"],
                ratings={
                    "entry_fit": "excellent",
                    "working_conditions_fit": "partial",
                    "direction_fit": "excellent",
                    "technology_head_start": "excellent",
                },
            ),
        )

        self.assertEqual(
            result["dimension_ratings"]["working_conditions_fit"],
            "excellent",
        )

    def test_unmet_language_requirement_is_a_risk_not_a_hard_conflict(self):
        job = {
            "location_precheck": "im 30-km-Radius",
            "work_mode": "onsite",
        }
        analysis = make_job_analysis()
        analysis["other_requirements"] = [
            {
                "category": "language",
                "priority": "explicit_requirement",
                "requirement": "Fliessend Englisch",
                "evidence_quote": "Fliessend Englisch",
            }
        ]
        profile_match = make_profile_match(["met"])
        profile_match["matches"].append(make_match("other:0", "not_met"))

        result = score_two_stage_result(job, analysis, profile_match)

        self.assertEqual(result["hard_conflicts"], [])
        self.assertIn("Luecke bei other:0", result["risks"])


if __name__ == "__main__":
    unittest.main()
