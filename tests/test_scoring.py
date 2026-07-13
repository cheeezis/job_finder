import unittest

from job_agent.deduplication import deduplicate_jobs
from job_agent.main import score_jobs
from job_agent.scoring import score_job


def make_job(**overrides):
    job = {
        "title": "Junior Python Developer",
        "company": "Example GmbH",
        "location": "Fulda",
        "remote": "0%",
        "description": "Python APIs. Keine Berufserfahrung erforderlich.",
        "url": "https://example.test/job",
        "source": "test",
    }
    job.update(overrides)
    return job


class ScoringTests(unittest.TestCase):
    def test_fixed_score_is_between_zero_and_one_hundred(self):
        result = score_job(
            make_job(
                location="Deutschland",
                remote="100%",
                description=(
                    "Python, Machine Learning, RAG, Data Analytics, Playwright, "
                    "TypeScript, Java, Docker, Security, REST API und IoT. "
                    "Keine Berufserfahrung erforderlich."
                ),
            )
        )
        self.assertEqual(result["status"], "included")
        self.assertLessEqual(result["match_percent"], 100)
        self.assertGreaterEqual(result["match_percent"], 0)

    def test_unrelated_title_is_not_rescued_by_python_in_body(self):
        result = score_job(
            make_job(
                title="Vertriebsmitarbeiter",
                description="Unsere Entwickler arbeiten mit Python und AI.",
            )
        )
        self.assertEqual(result["status"], "excluded")
        self.assertIn("Rollenfamilie", result["reasons"][0])

    def test_dev_abbreviation_is_allowed(self):
        result = score_job(make_job(title="Junior Python Dev"))
        self.assertEqual(result["status"], "included")

    def test_homeoffice_outside_local_area_is_not_full_remote(self):
        result = score_job(
            make_job(location="Muenchen, Home-Office", remote="homeoffice")
        )
        self.assertEqual(result["status"], "excluded")
        self.assertEqual(result["reasons"][0], "Ort/Remote passt nicht")

    def test_full_remote_outside_local_area_is_allowed(self):
        result = score_job(make_job(location="Muenchen", remote="100%"))
        self.assertEqual(result["status"], "included")

    def test_remote_portugal_is_excluded(self):
        result = score_job(make_job(location="Portugal", remote="100%"))
        self.assertEqual(result["status"], "excluded")
        self.assertIn("Deutschland", result["reasons"][0])

    def test_frankfurt_requires_eighty_percent_remote(self):
        accepted = score_job(make_job(location="Frankfurt", remote="80%"))
        rejected = score_job(make_job(location="Frankfurt", remote="50%"))
        self.assertEqual(accepted["status"], "included")
        self.assertEqual(rejected["status"], "excluded")

    def test_four_required_years_are_excluded(self):
        result = score_job(
            make_job(description="Python APIs. Mindestens 4 Jahre Berufserfahrung erforderlich.")
        )
        self.assertEqual(result["status"], "excluded")
        self.assertIn("4 Jahre", result["reasons"][0])

    def test_three_required_years_remain_with_low_experience_score(self):
        result = score_job(
            make_job(
                title="Python Developer",
                description="Python APIs. 3 Jahre Berufserfahrung erforderlich.",
            )
        )
        self.assertEqual(result["status"], "included")
        self.assertEqual(result["experience_rank"], 4)
        self.assertTrue(any(reason.startswith("+3 Erfahrung") for reason in result["reasons"]))

    def test_unrelated_wuenschenswert_does_not_make_years_optional(self):
        result = score_job(
            make_job(
                description=(
                    "Mindestens 4 Jahre Berufserfahrung erforderlich. "
                    "Docker-Kenntnisse sind wuenschenswert."
                )
            )
        )
        self.assertEqual(result["status"], "excluded")

    def test_optional_experience_is_only_slightly_lower(self):
        result = score_job(
            make_job(
                title="Python Developer",
                description="Ein Jahr Berufserfahrung waere ideal, aber kein Muss.",
            )
        )
        self.assertEqual(result["status"], "included")
        self.assertEqual(result["experience_rank"], 1)

    def test_senior_is_excluded_but_mixed_junior_senior_is_reviewable(self):
        senior = score_job(make_job(title="Senior Python Developer"))
        mixed = score_job(make_job(title="Python Developer Junior/Senior"))
        self.assertEqual(senior["status"], "excluded")
        self.assertEqual(mixed["status"], "included")

    def test_incidental_sap_mention_is_not_a_hard_blocker(self):
        result = score_job(
            make_job(description="Python APIs verbinden bei Bedarf auch ein SAP-Nebensystem.")
        )
        self.assertEqual(result["status"], "included")

    def test_explicit_sap_focus_in_body_is_excluded(self):
        result = score_job(
            make_job(description="Der Schwerpunkt SAP bestimmt deine taeglichen Aufgaben.")
        )
        self.assertEqual(result["status"], "excluded")
        self.assertIn("sap", result["reasons"][0])

    def test_supported_java_role_is_allowed(self):
        result = score_job(
            make_job(title="Junior Java Software Developer", description="Java und REST APIs.")
        )
        self.assertEqual(result["status"], "included")

    def test_frontend_and_web_roles_are_general_software_development(self):
        frontend = score_job(
            make_job(title="Frontend Developer", description="TypeScript und React.")
        )
        web = score_job(
            make_job(title="Webentwickler IoT", description="JavaScript und REST APIs.")
        )
        self.assertEqual(frontend["status"], "included")
        self.assertEqual(web["status"], "included")

    def test_devops_synonyms_are_allowed(self):
        sre = score_job(
            make_job(title="Site Reliability Engineer", description="Kubernetes und Python.")
        )
        netops = score_job(
            make_job(title="SysOps-/NetOps-Engineer", description="Netzwerk und Automation.")
        )
        self.assertEqual(sre["status"], "included")
        self.assertEqual(netops["status"], "included")

    def test_unsupported_core_technology_is_excluded(self):
        result = score_job(
            make_job(title="Junior C# Software Developer", description="Reine C# Entwicklung.")
        )
        self.assertEqual(result["status"], "excluded")
        self.assertIn("c#", result["reasons"][0])

    def test_test_automation_role_is_allowed(self):
        result = score_job(
            make_job(
                title="Junior Test Automation Engineer",
                description="Playwright, Jest und API-Testautomatisierung.",
            )
        )
        self.assertEqual(result["status"], "included")

    def test_test_manager_is_excluded(self):
        result = score_job(
            make_job(
                title="IT-Testmanager / Softwaretester",
                description="Teststrategie und automatisierte Integrationstests.",
            )
        )
        self.assertEqual(result["status"], "excluded")
        self.assertIn("testmanager", result["reasons"][0])

    def test_portal_ai_boilerplate_does_not_create_ai_skill_match(self):
        result = score_job(
            make_job(
                title="Network Engineer",
                description=(
                    "Netzwerkautomatisierung mit Python. "
                    "Bei dieser Jobboerse erstellen wir fuer Stellen mithilfe von "
                    "kuenstlicher Intelligenz (KI) automatisch generierte Zusammenfassungen."
                ),
            )
        )
        self.assertEqual(result["status"], "included")
        self.assertFalse(any("AI/ML" in reason for reason in result["reasons"]))

    def test_mandatory_master_is_excluded(self):
        result = score_job(
            make_job(description="Ein Masterabschluss ist fuer diese Rolle erforderlich.")
        )
        self.assertEqual(result["status"], "excluded")
        self.assertIn("Master", result["reasons"][0])

    def test_salary_range_with_target_inside_is_allowed(self):
        result = score_job(
            make_job(description="Jahresgehalt 42.000 - 50.000 EUR brutto.")
        )
        self.assertEqual(result["status"], "included")

    def test_salary_below_minimum_is_excluded(self):
        result = score_job(make_job(description="Jahresgehalt 44.000 EUR brutto."))
        self.assertEqual(result["status"], "excluded")
        self.assertIn("45.000 EUR", result["reasons"][0])

    def test_entry_level_jobs_sort_before_required_experience(self):
        entry = make_job(
            title="Junior Java Software Developer",
            company="Entry GmbH",
            description="Java. Keine Berufserfahrung erforderlich.",
            url="https://example.test/entry",
        )
        experienced = make_job(
            title="Python Developer",
            company="Experienced GmbH",
            description="Python und AI. 1 Jahr Berufserfahrung erforderlich.",
            url="https://example.test/experienced",
        )
        results = score_jobs([experienced, entry])
        self.assertEqual(results["included"][0]["company"], "Entry GmbH")


class DeduplicationTests(unittest.TestCase):
    def test_cross_source_duplicate_is_merged(self):
        first = make_job(
            title="DevOps Engineer (m/w/d) - Junior/Senior [auch bis 100% remote moeglich]",
            company="Nexato GmbH",
            source="stepstone",
            url="https://stepstone.test/nexato",
            description="Kurz",
        )
        second = make_job(
            title="DevOps Engineer (m/w/d) - Junior/Senior",
            company="nexato",
            source="get_in_it",
            url="https://get-in-it.test/nexato",
            description="Eine deutlich laengere Beschreibung",
        )
        result = deduplicate_jobs([first, second])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sources"], ["stepstone", "get_in_it"])
        self.assertEqual(result[0]["description"], second["description"])
        self.assertEqual(len(result[0]["duplicate_urls"]), 1)

    def test_extended_company_name_is_merged(self):
        first = make_job(
            title="Network Engineer (m/w/d)",
            company="NETHINKS GmbH Uwe Bergmann",
            source="stepstone",
            url="https://stepstone.test/nethinks",
        )
        second = make_job(
            title="Network Engineer (m/w/d)",
            company="NETHINKS",
            source="get_in_it",
            url="https://get-in-it.test/nethinks",
        )
        result = deduplicate_jobs([first, second])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["sources"], ["stepstone", "get_in_it"])


if __name__ == "__main__":
    unittest.main()
