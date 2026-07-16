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


def make_profile_match(technology_statuses, experience_status="met"):
    """Return complete evidence matches for the compact extracted job."""
    matches = [
        make_match("role", "met"),
        make_match("tasks", "met"),
        make_match("experience", experience_status),
    ]
    matches.extend(
        make_match(f"technology:0:{index}", status)
        for index, status in enumerate(technology_statuses)
    )
    return {"matches": matches, "uncertainties": []}


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

    def test_broad_unclear_stack_uses_minimum_count_and_remains_borderline(self):
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
                ["met", "met", "not_met", "not_met", "not_met"],
                experience_status="partially_met",
            ),
        )

        self.assertEqual(result["dimension_ratings"]["technology_fit"], "good")
        self.assertEqual(result["dimension_ratings"]["experience_fit"], "weak")
        self.assertEqual(result["overall_score"], 70)
        self.assertEqual(result["recommendation"], "borderline")

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

        self.assertGreaterEqual(result["overall_score"], 75)
        self.assertEqual(result["recommendation"], "not_recommended")
        self.assertTrue(result["hard_conflicts"])

    def test_local_job_is_a_good_location_fit(self):
        job = {
            "location_precheck": "im 30-km-Radius",
            "work_mode": "onsite",
        }

        self.assertEqual(score_location(job), "good")

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
