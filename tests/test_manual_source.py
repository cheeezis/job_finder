"""Tests for user-supplied job links."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from job_finder.models import WorkMode
from job_finder.sources import manual
from job_finder.sources.common import load_detail_cache


class ManualSourceTests(unittest.TestCase):
    def test_remote_schema_uses_applicant_region_when_job_location_is_missing(self):
        html = """
        <script type="application/ld+json">{
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Junior Python Developer",
          "description": "Python APIs und erste praktische Erfahrung.",
          "jobLocationType": "TELECOMMUTE",
          "applicantLocationRequirements": {"@type": "Country", "name": "Germany"},
          "hiringOrganization": {"@type": "Organization", "name": "Example GmbH"}
        }</script>
        """

        job = manual.job_from_page("https://example.com/jobs/remote", html)

        self.assertEqual(job.locations, ["Germany"])

    def test_visible_career_page_is_parsed_without_form_or_footer(self):
        html = """
        <html><head>
          <meta property="og:site_name" content="NCSolution">
          <meta property="og:title" content="Junior Python Entwickler (m/w/d)">
        </head><body><main>
          <h1>Junior Python Entwickler (m/w/d)</h1>
          <p>Standort</p><p>bundesweit, hybrid, u.a. Frankfurt</p>
          <p>Beschäftigungsart</p><p>Vollzeit</p>
          <h2>Deine Aufgaben</h2>
          <p>Du entwickelst Python-Anwendungen, analysierst Fehler und arbeitest
          gemeinsam mit dem Team an wartbaren Lösungen für unsere Kunden.</p>
          <h2>Dein Profil</h2>
          <p>Du hast erste Python-Kenntnisse und möchtest dich als Junior
          weiterentwickeln. SQL-Kenntnisse sind hilfreich, aber nicht zwingend.</p>
          <form><p>Interne Formularanweisung, die nicht zur Stelle gehört.</p></form>
        </main><footer>Impressum und Datenschutz</footer></body></html>
        """

        job = manual.job_from_page(
            "https://www.ncsolution.de/jobs/junior_python_entwickler/",
            html,
        )

        self.assertEqual(job.title, "Junior Python Entwickler (m/w/d)")
        self.assertEqual(job.company, "NCSolution")
        self.assertEqual(job.locations, ["bundesweit, hybrid, u.a. Frankfurt"])
        self.assertEqual(job.employment_type, "Vollzeit")
        self.assertEqual(job.work_mode, WorkMode.HYBRID)
        self.assertNotIn("Formularanweisung", job.description_clean)
        self.assertNotIn("Impressum", job.description_clean)

    def test_add_url_persists_only_the_supplied_page(self):
        html = """
        <meta property="og:site_name" content="Example GmbH">
        <main><h1>Junior Python Developer</h1>
        <p>Standort: Fulda</p>
        <p>Wir suchen einen Junior Python Developer für die Entwicklung,
        das Testen und die Wartung unserer Anwendungen im agilen Produktteam.
        Erste Kenntnisse in Python und Datenbanken sind willkommen.</p>
        </main>
        """
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "manual.json"
            with (
                patch.object(
                    manual.socket,
                    "getaddrinfo",
                    return_value=[(None, None, None, None, ("93.184.216.34", 443))],
                ),
                patch.object(
                    manual,
                    "fetch_text_with_final_url",
                    return_value=("https://example.com/jobs/python", html),
                ) as fetch,
            ):
                job = manual.add_url(
                    "https://example.com/jobs/python",
                    cache_path=cache_path,
                )

            cache = load_detail_cache(cache_path)

        fetch.assert_called_once_with(
            "https://example.com/jobs/python",
            url_validator=manual.validate_public_url,
        )
        self.assertEqual(list(cache), ["https://example.com/jobs/python"])
        self.assertEqual(job.primary_source.source, "manual")

    def test_local_network_url_is_rejected(self):
        for url in (
            "http://localhost/job",
            "http://127.0.0.1/job",
            "file:///C:/secret.txt",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                manual.validate_public_url(url)

    def test_hostname_resolving_to_private_network_is_rejected(self):
        with patch.object(
            manual.socket,
            "getaddrinfo",
            return_value=[(None, None, None, None, ("192.168.1.10", 443))],
        ):
            with self.assertRaisesRegex(ValueError, "Private Netzwerk"):
                manual.validate_public_url("https://public-name.example/job")


if __name__ == "__main__":
    unittest.main()
