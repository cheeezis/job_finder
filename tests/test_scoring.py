"""Regression tests for deterministic scoring and deduplication."""

import unittest
from unittest.mock import patch

from job_agent.deduplication import deduplicate_jobs
from job_agent.main import score_jobs
from job_agent.models import Job, JobSource
from job_agent.remote import classify_remote, detect_remote
from job_agent.scoring import score_job
from job_agent.profile import LOCAL_PLACES


def make_job(**overrides):
    values = {
        "title": "Junior Python Developer",
        "company": "Example GmbH",
        "location": LOCAL_PLACES[0],
        "remote": "0%",
        "description": "Python APIs. Keine Berufserfahrung erforderlich.",
        "url": "https://example.test/job",
        "source": "test",
        "salary_min_eur": None,
        "salary_max_eur": None,
    }
    values.update(overrides)
    work_mode, remote_percentage = classify_remote(values["remote"])
    return Job(
        id=f'{values["source"]}:{values["url"]}',
        title=values["title"],
        company=values["company"],
        locations=[values["location"]],
        sources=[
            JobSource(
                source=values["source"],
                url=values["url"],
            )
        ],
        description_raw=values["description"],
        description_clean=values["description"],
        work_mode=work_mode,
        remote_percentage=remote_percentage,
        salary_min_eur=values["salary_min_eur"],
        salary_max_eur=values["salary_max_eur"],
    )


class ScoringTests(unittest.TestCase):
    def test_commuter_location_uses_configured_remote_threshold(self):
        locations = [
            {
                "search_location": "Beispielstadt",
                "aliases": ["Beispielstadt"],
                "minimum_remote_percentage": 60,
            }
        ]
        with patch("job_agent.scoring.COMMUTER_LOCATIONS", locations):
            accepted = score_job(make_job(location="Beispielstadt", remote="60%"))
            rejected = score_job(make_job(location="Beispielstadt", remote="40%"))

        self.assertEqual(accepted["filter_status"], "included")
        self.assertIn("Pendelort Beispielstadt", accepted["reasons"][3])
        self.assertEqual(rejected["filter_status"], "excluded")

    def test_remote_days_are_converted_to_weekly_percentage(self):
        self.assertEqual(
            detect_remote("Drei Tage pro Woche im Homeoffice"),
            "60%",
        )
        self.assertEqual(
            detect_remote("Zwei Präsenztage pro Woche"),
            "60%",
        )

    def test_commuter_location_reads_remote_days_from_description(self):
        locations = [
            {
                "search_location": "Beispielstadt",
                "aliases": ["Beispielstadt"],
                "minimum_remote_percentage": 60,
            }
        ]
        job = make_job(
            location="Beispielstadt",
            remote="homeoffice",
            description=(
                "Python APIs. Keine Berufserfahrung erforderlich. "
                "Drei Tage pro Woche im Homeoffice."
            ),
        )

        with patch("job_agent.scoring.COMMUTER_LOCATIONS", locations):
            result = score_job(job)

        self.assertEqual(result["filter_status"], "included")
        self.assertIn("60% Remote", result["reasons"][3])

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
        self.assertEqual(result["filter_status"], "included")
        self.assertLessEqual(result["match_percent"], 100)
        self.assertGreaterEqual(result["match_percent"], 0)

    def test_unrelated_title_is_not_rescued_by_python_in_body(self):
        result = score_job(
            make_job(
                title="Vertriebsmitarbeiter",
                description="Unsere Entwickler arbeiten mit Python und AI.",
            )
        )
        self.assertEqual(result["filter_status"], "excluded")
        self.assertIn("IT-Rolle", result["reasons"][0])

    def test_dev_abbreviation_is_allowed(self):
        result = score_job(make_job(title="Junior Python Dev"))
        self.assertEqual(result["filter_status"], "included")

    def test_broad_it_title_reaches_personal_review(self):
        result = score_job(
            make_job(
                title="Developer Node.js / TypeScript",
                location="Deutschland",
                remote="100%",
                description="Erfahrung ist idealerweise vorhanden.",
            )
        )

        self.assertEqual(result["filter_status"], "included")
        self.assertEqual(result["role_group"], "general_it")

    def test_non_it_remote_role_stays_excluded(self):
        result = score_job(
            make_job(
                title="Junior Sales Manager",
                location="Deutschland",
                remote="100%",
            )
        )

        self.assertEqual(result["filter_status"], "excluded")

    def test_junior_it_manager_reaches_personal_review(self):
        result = score_job(
            make_job(
                title="Junior IT Project Manager",
                location="Deutschland",
                remote="100%",
            )
        )

        self.assertEqual(result["filter_status"], "included")

    def test_it_leadership_role_stays_excluded(self):
        result = score_job(
            make_job(
                title="Leitung IT-Entwicklung",
                location="Deutschland",
                remote="100%",
            )
        )

        self.assertEqual(result["filter_status"], "excluded")

    def test_apprenticeships_are_excluded_from_a_post_degree_job_search(self):
        result = score_job(
            make_job(
                title="Auszu\u00adbil\u00addende Fachinformatiker Anwendungsentwicklung (m/w/d)",
            )
        )

        self.assertEqual(result["filter_status"], "excluded")
        self.assertIn("auszubildende", result["reasons"][0])

    def test_homeoffice_outside_local_area_is_not_full_remote(self):
        result = score_job(
            make_job(location="Muenchen, Home-Office", remote="homeoffice")
        )
        self.assertEqual(result["filter_status"], "excluded")
        self.assertEqual(result["reasons"][0], "Ort/Remote passt nicht")

    def test_full_remote_outside_local_area_is_allowed(self):
        result = score_job(make_job(location="Muenchen", remote="100%"))
        self.assertEqual(result["filter_status"], "included")

    def test_remote_portugal_is_excluded(self):
        result = score_job(make_job(location="Portugal", remote="100%"))
        self.assertEqual(result["filter_status"], "excluded")
        self.assertIn("Deutschland", result["reasons"][0])

    def test_commuter_rule_honors_excluded_alias_and_threshold(self):
        locations = [
            {
                "search_location": "Beispielstadt",
                "aliases": ["Beispielstadt"],
                "excluded_aliases": ["Beispielstadt-West"],
                "minimum_remote_percentage": 80,
            }
        ]
        with patch("job_agent.scoring.COMMUTER_LOCATIONS", locations):
            accepted = score_job(make_job(location="Beispielstadt", remote="80%"))
            rejected = score_job(make_job(location="Beispielstadt", remote="60%"))
            wrong_city = score_job(make_job(location="Beispielstadt-West", remote="80%"))

        self.assertEqual(accepted["filter_status"], "included")
        self.assertEqual(rejected["filter_status"], "excluded")
        self.assertEqual(wrong_city["filter_status"], "excluded")

    def test_four_required_years_are_excluded(self):
        result = score_job(
            make_job(description="Python APIs. Mindestens 4 Jahre Berufserfahrung erforderlich.")
        )
        self.assertEqual(result["filter_status"], "excluded")
        self.assertIn("4 Jahre", result["reasons"][0])

    def test_three_required_years_remain_with_low_experience_score(self):
        result = score_job(
            make_job(
                title="Junior Python Developer",
                description="Python APIs. 3 Jahre Berufserfahrung erforderlich.",
            )
        )
        self.assertEqual(result["filter_status"], "included")
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
        self.assertEqual(result["filter_status"], "excluded")

    def test_optional_experience_is_only_slightly_lower(self):
        result = score_job(
            make_job(
                title="Python Developer",
                description="Ein Jahr Berufserfahrung waere ideal, aber kein Muss.",
            )
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertEqual(result["experience_rank"], 1)

    def test_required_experience_wins_over_separate_optional_experience(self):
        result = score_job(
            make_job(
                title="AI Integration & Automation Engineer",
                description=(
                    "Experience in software development, ideally in backend "
                    "development. Experience with Python and cloud technologies. "
                    "Ideally, you also have practical experience with LLMs."
                ),
            )
        )

        self.assertEqual(result["filter_status"], "included")
        self.assertEqual(result["experience_rank"], 4)
        self.assertTrue(
            any(reason.startswith("+8 Erfahrung") for reason in result["reasons"])
        )

    def test_non_junior_strong_experience_is_excluded(self):
        german = score_job(
            make_job(
                title="Webentwickler IoT",
                description="Du hast bereits Berufserfahrung im Data Engineering.",
            )
        )
        english = score_job(
            make_job(
                title="Frontend Engineer",
                description="Deep previous experience with React is required.",
            )
        )
        self.assertEqual(german["filter_status"], "excluded")
        self.assertEqual(english["filter_status"], "excluded")

    def test_junior_role_with_strong_experience_remains_reviewable(self):
        result = score_job(
            make_job(
                title="Junior Data Engineer",
                description="Mehrjaehrige Erfahrung mit Python ist wuenschenswert.",
            )
        )
        self.assertEqual(result["filter_status"], "included")

    def test_skill_experience_without_professional_signal_remains_reviewable(self):
        result = score_job(
            make_job(
                title="Data Engineer",
                description=(
                    "Erfahrungen in der Analyse grosser Datenbestaende und Erfahrung "
                    "in Python."
                ),
            )
        )
        self.assertEqual(result["filter_status"], "included")

    def test_company_entry_level_boilerplate_does_not_define_the_vacancy(self):
        result = score_job(
            make_job(
                title="IT-Consultant / Business Analyst",
                description=(
                    "Unser Team umfasst Experten mit 25 Jahren Berufserfahrung, "
                    "aber auch junge Berufseinsteiger. Idealerweise Erfahrung in "
                    "der Softwareentwicklung."
                ),
            )
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertNotEqual(result["experience_rank"], 0)

    def test_abbreviated_minimum_years_are_detected(self):
        result = score_job(
            make_job(
                title="Junior QA Automation Engineer",
                description=(
                    "Mit deiner mehrjaehrigen praktischen Erfahrung "
                    "(mind. 3 Jahre) in QA."
                ),
            )
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertEqual(result["experience_level"], "3 Jahr(e) gefordert")

    def test_non_junior_one_to_three_years_receive_a_strong_penalty(self):
        result = score_job(
            make_job(
                title="Automation Engineer",
                description=(
                    "1-2 Jahre Entwicklungserfahrung mit RPA und UiPath in einer "
                    "kommerziellen Umgebung."
                ),
            )
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertEqual(result["experience_level"], "2 Jahr(e) gefordert")
        self.assertTrue(
            any(reason.startswith("+8 Erfahrung") for reason in result["reasons"])
        )

    def test_senior_is_excluded_but_mixed_junior_senior_is_reviewable(self):
        senior = score_job(make_job(title="Senior Python Developer"))
        mixed = score_job(make_job(title="Python Developer Junior/Senior"))
        self.assertEqual(senior["filter_status"], "excluded")
        self.assertEqual(mixed["filter_status"], "included")

    def test_expert_titles_are_excluded_as_experienced_roles(self):
        german = score_job(make_job(title="Digitalisierungs- und KI-Experte"))
        english = score_job(make_job(title="AI Platform Expert"))

        self.assertEqual(german["filter_status"], "excluded")
        self.assertEqual(english["filter_status"], "excluded")

    def test_advanced_and_non_job_titles_are_excluded(self):
        titles_and_reasons = [
            ("Staff Software Engineer", "staff"),
            ("Founding Cloud Infrastructure Engineer", "founding"),
            ("Solutions Architect - DACH", "architect"),
            ("Software Architekt", "architekt"),
            (
                "Data Science & AI Weiterbildung mit IHK-Abschluss",
                "weiterbildung",
            ),
        ]

        for title, reason in titles_and_reasons:
            with self.subTest(title=title):
                result = score_job(make_job(title=title))
                self.assertEqual(result["filter_status"], "excluded")
                self.assertIn(reason, result["reasons"][0])

    def test_blocked_staff_word_does_not_match_staffing(self):
        result = score_job(make_job(title="Staffing Software Developer"))

        self.assertEqual(result["filter_status"], "included")

    def test_incidental_sap_mention_is_not_a_hard_blocker(self):
        result = score_job(
            make_job(description="Python APIs verbinden bei Bedarf auch ein SAP-Nebensystem.")
        )
        self.assertEqual(result["filter_status"], "included")

    def test_explicit_sap_focus_reaches_personal_review(self):
        result = score_job(
            make_job(description="Der Schwerpunkt SAP bestimmt deine taeglichen Aufgaben.")
        )
        self.assertEqual(result["filter_status"], "included")

    def test_supported_java_role_is_allowed(self):
        result = score_job(
            make_job(title="Junior Java Software Developer", description="Java und REST APIs.")
        )
        self.assertEqual(result["filter_status"], "included")

    def test_frontend_and_web_roles_are_general_software_development(self):
        frontend = score_job(
            make_job(title="Frontend Developer", description="TypeScript und React.")
        )
        web = score_job(
            make_job(title="Webentwickler IoT", description="JavaScript und REST APIs.")
        )
        self.assertEqual(frontend["filter_status"], "included")
        self.assertEqual(web["filter_status"], "included")

    def test_devops_synonyms_are_allowed(self):
        sre = score_job(
            make_job(title="Site Reliability Engineer", description="Kubernetes und Python.")
        )
        netops = score_job(
            make_job(title="SysOps-/NetOps-Engineer", description="Netzwerk und Automation.")
        )
        self.assertEqual(sre["filter_status"], "included")
        self.assertEqual(netops["filter_status"], "included")

    def test_ai_business_analyst_is_allowed(self):
        result = score_job(
            make_job(
                title="KI Business Analyst",
                description="Analyse und Umsetzung datengetriebener KI Use Cases.",
            )
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertEqual(result["role_group"], "ai_business_analysis")

    def test_infrastructure_automation_is_allowed(self):
        result = score_job(
            make_job(
                title="Automation Engineer",
                location="Deutschland",
                remote="100%",
                description=(
                    "Infrastructure as Code, Terraform und automatisierte Deployments."
                ),
            )
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertEqual(result["role_group"], "infrastructure_automation")

    def test_rpa_is_allowed_with_lower_role_score(self):
        result = score_job(
            make_job(
                title="Junior Automation Engineer",
                description="RPA-Loesungen mit UiPath und Power Automate.",
            )
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertEqual(result["role_group"], "rpa_automation")
        self.assertTrue(any(reason.startswith("+14 Rolle") for reason in result["reasons"]))

    def test_rpa_with_ci_cd_remains_process_automation(self):
        result = score_job(
            make_job(
                title="Automationsentwickler",
                description="UiPath RPA, REST APIs, CI/CD und Testautomatisierung.",
            )
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertEqual(result["role_group"], "rpa_automation")

    def test_industrial_automation_without_it_context_is_not_allowed(self):
        result = score_job(
            make_job(
                title="Automation Engineer",
                description="Planung und Inbetriebnahme industrieller Produktionsanlagen.",
            )
        )
        self.assertEqual(result["filter_status"], "excluded")

    def test_microsoft_365_roles_reach_personal_review(self):
        junior = score_job(
            make_job(
                title="Microsoft 365 Junior Consultant",
                location="Deutschland",
                remote="100%",
                description="Cloud, Copilot und Automatisierung mit PowerShell.",
            )
        )
        experienced = score_job(
            make_job(
                title="Modern Workplace Engineer",
                description="Microsoft 365, Cloud und PowerShell.",
            )
        )
        self.assertEqual(junior["filter_status"], "included")
        self.assertEqual(experienced["filter_status"], "included")

        experienced_consultant = score_job(
            make_job(
                title="Technical Consultant Microsoft 365",
                description="Microsoft 365, Cloud und PowerShell.",
            )
        )
        self.assertEqual(experienced_consultant["filter_status"], "included")

    def test_sap_roles_reach_personal_review(self):
        junior = score_job(
            make_job(
                title="Junior SAP Consultant",
                description="Einarbeitung in SAP und keine Berufserfahrung erforderlich.",
            )
        )
        experienced = score_job(
            make_job(
                title="SAP IT Consultant",
                description="Mehrjaehrige SAP-Erfahrung wird vorausgesetzt.",
            )
        )
        self.assertEqual(junior["filter_status"], "included")
        self.assertEqual(junior["role_group"], "junior_sap")
        self.assertEqual(experienced["filter_status"], "included")

    def test_junior_abap_role_is_reviewable(self):
        result = score_job(
            make_job(
                title="Junior ABAP Entwickler",
                description="Traineeprogramm mit umfassender Einarbeitung.",
            )
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertEqual(result["role_group"], "junior_sap")

    def test_requirements_roles_reach_personal_review(self):
        junior = score_job(
            make_job(
                title="Junior Requirements Engineer",
                description="Technische Anforderungen fuer ein Entwicklungsteam.",
            )
        )
        experienced = score_job(
            make_job(
                title="Requirements Engineer",
                description="Technische Anforderungen fuer ein Entwicklungsteam.",
            )
        )
        self.assertEqual(junior["filter_status"], "included")
        self.assertEqual(experienced["filter_status"], "included")

    def test_specialist_applications_and_support_reach_personal_review(self):
        payroll = score_job(make_job(title="Technical Consultant Lohnmigration"))
        healthcare = score_job(make_job(title="ORBIS Anwendungsbetreuer"))
        support = score_job(make_job(title="IT-Supportmitarbeiter 2nd Level"))
        self.assertEqual(payroll["filter_status"], "included")
        self.assertEqual(healthcare["filter_status"], "included")
        self.assertEqual(support["filter_status"], "included")

    def test_system_administration_reaches_personal_review(self):
        junior = score_job(
            make_job(
                title="Junior Systemadministrator / DevOps",
                description="Cloud, Security und Automatisierung mit PowerShell.",
            )
        )
        experienced = score_job(
            make_job(
                title="Systemadministrator / DevOps",
                description="Cloud, Security und Automatisierung mit PowerShell.",
            )
        )
        self.assertEqual(junior["filter_status"], "included")
        self.assertEqual(junior["role_group"], "junior_administration")
        self.assertEqual(experienced["filter_status"], "included")

    def test_unfamiliar_core_technology_reaches_personal_review(self):
        result = score_job(
            make_job(title="Junior C# Software Developer", description="Reine C# Entwicklung.")
        )
        self.assertEqual(result["filter_status"], "included")

    def test_test_automation_role_is_allowed(self):
        result = score_job(
            make_job(
                title="Junior Test Automation Engineer",
                description="Playwright, Jest und API-Testautomatisierung.",
            )
        )
        self.assertEqual(result["filter_status"], "included")

    def test_test_manager_is_excluded(self):
        result = score_job(
            make_job(
                title="IT-Testmanager / Softwaretester",
                description="Teststrategie und automatisierte Integrationstests.",
            )
        )
        self.assertEqual(result["filter_status"], "excluded")
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
        self.assertEqual(result["filter_status"], "included")
        self.assertFalse(any("AI/ML" in reason for reason in result["reasons"]))

    def test_mandatory_master_is_excluded(self):
        result = score_job(
            make_job(description="Ein Masterabschluss ist fuer diese Rolle erforderlich.")
        )
        self.assertEqual(result["filter_status"], "excluded")
        self.assertIn("Master", result["reasons"][0])

    @patch("job_agent.scoring.SALARY_TARGET", 83_000)
    @patch("job_agent.scoring.SALARY_MINIMUM", 73_000)
    def test_salary_range_with_target_inside_is_allowed(self):
        result = score_job(
            make_job(description="Jahresgehalt 42.000 - 50.000 EUR brutto.")
        )
        self.assertEqual(result["filter_status"], "included")

    @patch("job_agent.scoring.SALARY_TARGET", 83_000)
    @patch("job_agent.scoring.SALARY_MINIMUM", 73_000)
    def test_salary_below_minimum_is_a_warning_not_an_exclusion(self):
        result = score_job(make_job(description="Jahresgehalt 44.000 EUR brutto."))
        self.assertEqual(result["filter_status"], "included")
        self.assertTrue(
            any("unter persoenlichem Minimum" in reason for reason in result["reasons"])
        )

    @patch("job_agent.scoring.SALARY_TARGET", 83_000)
    @patch("job_agent.scoring.SALARY_MINIMUM", 73_000)
    def test_structured_salary_below_minimum_is_a_warning(self):
        result = score_job(
            make_job(salary_min_eur=40_000, salary_max_eur=44_000)
        )

        self.assertEqual(result["filter_status"], "included")
        self.assertTrue(
            any("unter persoenlichem Minimum" in reason for reason in result["reasons"])
        )

    @patch("job_agent.scoring.SALARY_TARGET", None)
    @patch("job_agent.scoring.SALARY_MINIMUM", None)
    def test_missing_salary_preferences_disable_salary_warnings(self):
        result = score_job(make_job(description="Jahresgehalt 30.000 EUR brutto."))

        self.assertEqual(result["filter_status"], "included")
        self.assertFalse(
            any("Gehalt unter" in reason for reason in result["reasons"])
        )

    def test_structured_minimum_without_maximum_is_not_an_upper_limit(self):
        result = score_job(make_job(salary_min_eur=40_000))

        self.assertEqual(result["filter_status"], "included")

    def test_jobs_sort_by_score_before_experience_level(self):
        entry = make_job(
            title="Junior Java Software Developer",
            company="Entry GmbH",
            description="Java. Keine Berufserfahrung erforderlich.",
            url="https://example.test/entry",
        )
        experienced = make_job(
            title="Junior Python Developer",
            company="Experienced GmbH",
            description="Python und AI. 1 Jahr Berufserfahrung erforderlich.",
            url="https://example.test/experienced",
        )
        results = score_jobs([experienced, entry])
        self.assertEqual(results["included"][0]["company"], "Experienced GmbH")


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
        self.assertEqual(
            [source.source for source in result[0].sources],
            ["stepstone", "get_in_it"],
        )
        self.assertEqual(result[0].description_clean, second.description_clean)
        self.assertEqual(len(result[0].sources), 2)
        self.assertEqual(result[0].id, first.id)

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
        self.assertEqual(
            [source.source for source in result[0].sources],
            ["stepstone", "get_in_it"],
        )


if __name__ == "__main__":
    unittest.main()
