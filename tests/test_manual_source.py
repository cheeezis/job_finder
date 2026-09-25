"""Tests for user-supplied job links."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from job_finder.models import WorkMode
from job_finder.sources import manual
from job_finder.sources.common import (
    fetch_diagnostics,
    load_detail_cache,
    reset_fetch_diagnostics,
    save_detail_cache,
)


def career_page(title):
    return f"""<meta property="og:site_name" content="Example GmbH">
        <main><h1>{title}</h1><p>Standort: Fulda</p>
        <p>Wir suchen Verstärkung für die Entwicklung, das Testen und die Wartung
        unserer Anwendungen im agilen Produktteam mit Python und Datenbanken.
        Erste Erfahrungen mit Tests und Versionsverwaltung sind willkommen.</p></main>"""


class ManualSourceTests(unittest.TestCase):
    def test_join_style_escaped_description_is_readable(self):
        html = """<script type="application/ld+json">{
            "@type": "JobPosting",
            "title": "Data Engineer &amp; Business Analyst",
            "description": "&lt;h2 id=&quot;aufgaben&quot;&gt;Aufgaben&lt;/h2&gt;&lt;p&gt;Python &amp;amp; SQL&lt;/p&gt;",
            "hiringOrganization": {"name": "ApoVid GmbH"},
            "jobLocationType": "TELECOMMUTE",
            "applicantLocationRequirements": {"name": "Deutschland"}
        }</script>"""

        job = manual.job_from_page("https://join.com/companies/example/12345678", html)

        self.assertEqual(job.title, "Data Engineer & Business Analyst")
        self.assertEqual(job.description_clean, "Aufgaben Python & SQL")
        self.assertEqual(job.company, "ApoVid GmbH")
        self.assertEqual(job.locations, ["Deutschland"])
        self.assertEqual(job.work_mode, WorkMode.REMOTE)

    def test_role_main_keeps_nested_content_and_excludes_footer(self):
        description = "Softwareentwicklung mit Python und SQL im Produktteam. " * 5
        html = f"""<meta property="og:site_name" content="Ecoplan CRM">
            <header>Navigation</header><div id="main" role="main">
            <div><h1>Softwareentwickler (m/w/d)</h1></div>
            <article><p>Standort: Fulda</p></article>
            <form><input><img src="example.png"><p>Formulartext</p></form>
            <div><p>{description}</p></div>
            <div id="footer"><p>Footertext</p></div>
            </div><p>Außerhalb</p>"""

        job = manual.job_from_page("https://example.com/softwareentwickler", html)

        self.assertEqual(job.title, "Softwareentwickler (m/w/d)")
        self.assertEqual(job.company, "Ecoplan CRM")
        self.assertEqual(job.locations, ["Fulda"])
        self.assertIn(description.strip(), job.description_clean)
        for excluded in ("Navigation", "Formulartext", "Footertext", "Außerhalb"):
            self.assertNotIn(excluded, job.description_clean)
        self.assertNotIn("Außerhalb", job.description_raw)

    def test_unmarked_page_is_still_rejected(self):
        with self.assertRaisesRegex(ValueError, "Kein Hauptinhalt"):
            manual.job_from_page(
                "https://example.com", "<h1>Website</h1><p>Generic content</p>" * 20
            )

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

        job = manual.job_from_page("https://www.ncsolution.de/jobs/junior_python_entwickler/", html)

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
                job = manual.add_url("https://example.com/jobs/python", cache_path=cache_path)

            cache = load_detail_cache(cache_path)

        fetch.assert_called_once_with(
            "https://example.com/jobs/python", url_validator=manual.validate_public_url
        )
        self.assertEqual(list(cache), ["https://example.com/jobs/python"])
        self.assertEqual(job.primary_source.source, "manual")

    def test_fetch_jobs_refreshes_stale_pages_and_keeps_manual_input(self):
        now = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)
        ages = {"fresh": 1, "stale-ok": 10, "stale-error": 10, "too-old": 20}
        cache = {}
        for name, days in ages.items():
            url = f"https://example.com/{name}"
            cache[url] = manual.job_from_page(url, career_page(name))
            cache[url].fetched_at = now - timedelta(days=days)

        def fetch(url, url_validator):
            if url.endswith("/stale-ok"):
                return "https://example.com/moved", career_page("moved")
            raise OSError("offline")

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "manual.json"
            save_detail_cache(cache_path, cache)
            reset_fetch_diagnostics()
            output = io.StringIO()
            with (
                patch.object(manual, "fetch_text_with_final_url", side_effect=fetch) as fetched,
                redirect_stdout(output),
            ):
                jobs = manual.fetch_jobs(cache_path, now=now)
            saved = load_detail_cache(cache_path)

        self.assertEqual(
            [(job.title, job.cache_stale) for job in jobs],
            [("fresh", False), ("moved", False), ("stale-error", True)],
        )
        self.assertEqual(
            [call.args[0].rsplit("/", 1)[1] for call in fetched.call_args_list],
            ["stale-ok", "stale-error", "too-old"],
        )
        self.assertEqual(
            [url.rsplit("/", 1)[1] for url in saved], ["fresh", "moved", "stale-error", "too-old"]
        )
        self.assertEqual(fetch_diagnostics()["failed_segments"], 2)
        self.assertIn("WARNUNG Manuell: 2 Detailseite(n) nicht erreichbar", output.getvalue())

    def test_local_network_url_is_rejected(self):
        for url in ("http://localhost/job", "http://127.0.0.1/job", "file:///C:/secret.txt"):
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
