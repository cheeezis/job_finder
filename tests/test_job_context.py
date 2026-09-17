"""Regression cases for misleading vacancy context and conflicting metadata."""

import unittest
from unittest.mock import patch

from test_scoring import make_job

from job_finder import scoring
from job_finder.job_context import plain_description, requirement_text
from job_finder.main import score_jobs
from job_finder.reporting import recommendation_for_job
from job_finder.scoring import score_job


class ContextSettings(unittest.TestCase):
    def setUp(self):
        settings = patch.multiple(
            scoring,
            LOCAL_PLACES=["teststadt"],
            COMMUTER_LOCATIONS=[],
            PREFERRED_ROLE_GROUPS=[],
            PROFILE_DOMAIN_KEYWORDS=[],
            SALARY_TARGET=None,
            SALARY_MINIMUM=None,
        )
        settings.start()
        self.addCleanup(settings.stop)

    def score(self, **values):
        return score_job(make_job(location="teststadt", **values))


class ContextScoringTests(ContextSettings):
    def test_customer_requirements_word_does_not_cut_off_applicant_requirements(self):
        result = self.score(
            title="Software Developer",
            description=(
                "Responsibilities: Turn customer requirements into software. "
                "Requirements: At least 5 years of professional experience. "
                "Benefits: Training."
            ),
        )
        self.assertEqual(result["filter_status"], "excluded")
        self.assertIn("5 Jahre", result["reasons"][0])

    def test_experience_in_opening_summary_is_not_lost_at_later_heading(self):
        result = self.score(
            title="Data Engineer",
            description=(
                "You bring 6-8 years of experience in data engineering. "
                "Your profile: Python and SQL. Benefits: Remote work."
            ),
        )
        self.assertEqual(result["filter_status"], "excluded")

    def test_education_and_learning_benefits_are_not_training_vacancies(self):
        base = self.score(description="Erste Erfahrung mit Python.")
        for description in [
            "Dein Profil: Abgeschlossene Ausbildung oder Studium. "
            "Erste Erfahrung mit Python. Benefits: Weiterbildung und Mentoring.",
            "Erste Erfahrung mit Python. Wir bieten individuelle Weiterbildung.",
            "Erste Erfahrung mit Python, etwa im Praktikum oder als Werkstudent.",
            "Wir suchen Entwickler mit abgeschlossener Ausbildung. "
            "Erste Erfahrung mit Python.",
        ]:
            with self.subTest(description=description):
                result = self.score(description=description)
                self.assertEqual(result["match_percent"], base["match_percent"])
                self.assertFalse(any("Studienformat" in r for r in result["reasons"]))

    def test_actual_student_and_training_jobs_still_receive_warning(self):
        for title, employment, description in [
            ("Praktikum Python Developer", None, "Python APIs."),
            ("Dualer Student Informatik", None, "Python APIs."),
            ("Python Developer", "Internship", "Python APIs."),
            (
                "Python Developer",
                None,
                "Wir suchen einen Praktikanten für Python APIs.",
            ),
        ]:
            with self.subTest(title=title, employment=employment):
                result = self.score(
                    title=title, employment_type=employment, description=description
                )
                self.assertEqual(result["filter_status"], "included")
                self.assertTrue(any("Studienformat" in r for r in result["reasons"]))

    def test_team_leadership_is_not_overruled_by_first_ai_experience(self):
        for title in [
            "Teamleader IT Applications",
            "Junior IT Consultant",
            "IT Consultant",
        ]:
            with self.subTest(title=title):
                result = self.score(
                    title=title,
                    description=(
                        "Deine Aufgaben: Fachliche und disziplinarische Führung des Teams. "
                        "Dein Profil: Erfahrung in der Führung kleiner Teams. "
                        "Erste Erfahrungen in KI sind ein Plus."
                    ),
                )
                self.assertEqual(result["filter_status"], "excluded")
                self.assertIn("Führung", result["reasons"][0])

    def test_working_with_team_lead_and_optional_leadership_stays_allowed(self):
        result = self.score(
            description=(
                "Deine Aufgaben: Zusammenarbeit mit dem Teamleiter. "
                "Dein Profil: Keine Führungserfahrung erforderlich. "
                "Benefits: Schulung durch Experten mit 8 Jahren Berufserfahrung."
            )
        )
        self.assertEqual(result["filter_status"], "included")
        optional = self.score(description="Dein Profil: Führungserfahrung von Vorteil.")
        self.assertEqual(optional["filter_status"], "included")

    def test_senior_metadata_is_not_overruled_by_teaching_beginners(self):
        result = self.score(
            title="Cloud Consultant",
            career_levels=["Senior"],
            description=(
                "Aufgaben: Angebote auch für Berufseinsteiger entwickeln. "
                "Dein Profil: Python und SQL."
            ),
        )
        self.assertEqual(result["filter_status"], "excluded")
        self.assertIn("Karrierestufe", result["reasons"][0])

    def test_students_in_tasks_do_not_make_the_teacher_an_entry_role(self):
        result = self.score(
            title="Junior Dozent Cloud Computing",
            description=(
                "Aufgaben: Wir helfen Berufseinsteigern ohne Erfahrung beim Start. "
                "Qualifikation: Fundierte Praxis-Erfahrung in Cloud Computing erforderlich."
            ),
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertNotEqual(result["experience_rank"], 0)

    def test_project_experience_is_not_treated_as_required_employment(self):
        result = self.score(
            title="Python Developer",
            description=(
                "Dein Profil: 1-2 Jahre praktische Erfahrung, z. B. durch Projekte "
                "oder im Studium. Benefits: Weiterbildung."
            ),
        )
        self.assertEqual(result["experience_rank"], 0)
        self.assertIn("Projekterfahrung", result["experience_level"])

    def test_professional_years_are_not_overruled_by_other_project_skills(self):
        result = self.score(
            description=(
                "Dein Profil: 3 Jahre Berufserfahrung sind erforderlich. "
                "Python-Erfahrung aus Projekten ist ein Plus."
            )
        )
        self.assertEqual(result["experience_rank"], 4)

    def test_first_experience_in_optional_skill_does_not_override_main_requirements(
        self,
    ):
        result = self.score(
            title="Software Developer",
            description=(
                "Dein Profil: Erfahrung in der Softwareentwicklung erforderlich. "
                "Erste Erfahrungen mit KI sind ein Plus."
            ),
        )
        self.assertNotEqual(result["experience_rank"], 0)

    def test_short_contract_is_below_equivalent_permanent_job(self):
        permanent = self.score(title="Junior Fullstack Developer unbefristet")
        short = self.score(title="Junior Fullstack Developer befristet 2 Monate")
        self.assertGreater(permanent["match_percent"], short["match_percent"])
        self.assertTrue(any("Befristung" in r for r in short["reasons"]))

    def test_false_it_roles_are_excluded_but_technical_network_role_remains(self):
        for title in [
            "Business Developer",
            "Junior Immigration Lawyer - Partner Network",
            "Direkt-Vertrieb Netzwerk Gesundheit",
            "CNC-Programmierer",
        ]:
            with self.subTest(title=title):
                self.assertEqual(self.score(title=title)["filter_status"], "excluded")
        self.assertEqual(
            self.score(title="Junior Network Engineer")["filter_status"], "included"
        )

    def test_html_and_flattened_headings_keep_the_same_requirements(self):
        html = "<h2>Dein Profil</h2><p>Erste Erfahrung mit Python.</p><h2>Benefits</h2><p>Weiterbildung.</p>"
        flat = "Dein Profil Erste Erfahrung mit Python. Benefits Weiterbildung."
        self.assertEqual(
            requirement_text(plain_description(html)),
            requirement_text(plain_description(flat)),
        )


class RemoteEvidenceTests(ContextSettings):
    def test_holidays_and_monthly_offsites_are_not_weekly_office_requirements(self):
        for description in [
            "Die Stelle ist zu 100% Remote möglich. 30 + 2 Tage Urlaub.",
            "100% Remote. Drei Tage pro Jahr im Büro für unser Teamtreffen.",
            "100% Remote. Ein Tag pro Monat im Büro.",
        ]:
            with self.subTest(description=description):
                result = score_job(
                    make_job(location="Berlin", remote="100%", description=description)
                )
                self.assertEqual(result["filter_status"], "included")
                self.assertIn("100% Remote", result["location_precheck"])

    def test_junior_with_uncertain_portal_flag_is_not_mistaken_for_confirmed_hybrid(
        self,
    ):
        result = score_job(
            make_job(location="Berlin", remote="100%", source="arbeitnow")
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertIn("unklar", result["location_precheck"])
        self.assertNotIn("Junior-Hybrid", result["location_precheck"])

    def test_lower_text_percentage_also_corrects_partial_portal_percentage(self):
        result = self.score(remote="80%", description="Homeoffice bis 40%.")
        self.assertEqual(result["remote_percentage"], 40)
        self.assertIn("widersprechen", result["prefilter_warning"])

    def test_concrete_remote_limit_overrides_portal_and_is_shown_in_review(self):
        job = make_job(
            location="Bonn",
            remote="100%",
            description=("Junior Consultant. Ort: Köln (Home Office bis 40%)."),
        )
        before = job.to_dict()
        result = score_jobs([job])["included"][0]
        self.assertIn("Junior-Hybrid", result["location_precheck"])
        self.assertEqual(result["remote_percentage"], 40)
        recommendation = recommendation_for_job(result)
        self.assertEqual(recommendation["remote_percentage"], 40)
        self.assertIn("widersprechen", recommendation["prefilter_warning"])
        self.assertEqual(job.to_dict(), before)

    def test_zero_remote_and_weekly_presence_override_portal_full_remote(self):
        for description in [
            "Kein Homeoffice möglich.",
            "0% Remote.",
            "5 Tage pro Woche im Büro.",
        ]:
            with self.subTest(description=description):
                result = score_job(
                    make_job(location="Berlin", remote="100%", description=description)
                )
                self.assertEqual(result["filter_status"], "excluded")

    def test_remote_location_label_cannot_override_a_concrete_limit(self):
        result = score_job(
            make_job(
                title="Software Developer",
                location="Berlin Remote",
                remote="100%",
                description="Homeoffice bis 40%.",
            )
        )
        self.assertEqual(result["filter_status"], "excluded")

    def test_boolean_portal_flag_is_uncertain_not_one_hundred_percent(self):
        result = score_job(
            make_job(
                title="Software Developer",
                location="Berlin",
                remote="100%",
                source="arbeitnow",
                description="Softwareentwicklung mit Python.",
            )
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertIsNone(result["remote_percentage"])
        self.assertIn("unklar", result["prefilter_warning"])
        self.assertIn("+0 Standort", " ".join(result["reasons"]))

    def test_explicit_full_remote_is_not_reduced_by_general_hybrid_word(self):
        result = score_job(
            make_job(
                location="Berlin",
                remote="100%",
                source="arbeitnow",
                description="100% Remote möglich. Arbeit an hybriden Cloud-Systemen.",
            )
        )
        self.assertEqual(result["filter_status"], "included")
        self.assertNotIn("prefilter_warning", result)

    def test_local_and_commuter_filters_remain_configured(self):
        with patch.object(
            scoring,
            "COMMUTER_LOCATIONS",
            [
                {
                    "search_location": "Pendelstadt",
                    "aliases": ["Pendelstadt"],
                    "minimum_remote_percentage": 60,
                }
            ],
        ):
            for percent, expected in [(60, "included"), (40, "excluded")]:
                result = score_job(
                    make_job(
                        title="Software Developer",
                        location="Pendelstadt",
                        remote="100%",
                        description=f"Homeoffice bis {percent}%.",
                    )
                )
                self.assertEqual(result["filter_status"], expected)
