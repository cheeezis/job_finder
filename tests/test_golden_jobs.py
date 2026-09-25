"""Golden master: the complete Job output of every source parser for fixed inputs.

Refactors must keep tests/fixtures/golden_jobs.json unchanged. After an intended
behaviour change, regenerate it with JOBFINDER_UPDATE_GOLDEN=1 and review the diff.
"""

import json
import os
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from job_finder.sources import (
    arbeitnow,
    arbeitsagentur,
    compose_it,
    edag,
    german_tech_jobs,
    get_in_it,
    himalayas,
    jobicy,
    manual,
    remotely,
    startup_jobs,
    stepstone,
    studysmarter,
)
from job_finder.sources.company_careers import job_from_json_ld

GOLDEN = Path(__file__).parent / "fixtures" / "golden_jobs.json"
FEED_TIME = datetime(2026, 8, 17, tzinfo=timezone.utc)
POSTING = {
    "@type": "JobPosting",
    "title": "Junior Python Developer",
    "hiringOrganization": {"name": "Example GmbH"},
    "jobLocation": {"address": {"addressLocality": "Fulda"}},
    "description": "<p>Python und APIs, Homeoffice</p>",
    "employmentType": "FULL_TIME",
    "datePosted": "2026-07-10",
    "baseSalary": {
        "currency": "EUR",
        "value": {"minValue": 73_000, "maxValue": 91_000, "unitText": "YEAR"},
    },
}
REMOTELY_PAGE = """<html><body>
  <div class="h-14 w-14 rounded-[14px] bg-company-mark"><span>E-</span></div>
  <div class="min-w-0 flex-1">
    <p class="flex flex-wrap items-center gap-2 text-body font-semibold text-foreground">
      Example GmbH
    </p>
  </div>
  <span class="text-meta text-text-muted sm:ml-auto">vor 6 Tagen</span>
  <h1>Junior Python Developer (m/w/d)</h1>
  <h3>Eckdaten</h3>
  <div><span>Berlin</span><span>Computer Software</span></div>
  <h3>Arbeitsmodell</h3>
  <span><svg class="lucide lucide-globe"></svg>Vollständig remote</span>
  <a data-apply-cta="true" href="https://example.test/apply?from=remotely">
    Jetzt bewerben
  </a>
  <div class="prose prose-sm max-w-none">
    <h2>Deine Aufgaben</h2><p>Python, APIs und Cloud-Plattformen.</p>
    <ul><li>Services entwickeln</li></ul>
  </div>
</body></html>"""
STUDYSMARTER_PAGE = """<script type="application/ld+json">{
  "@context": "https://schema.org", "@type": "JobPosting",
  "title": "Junior Python Developer (m/w/d)",
  "description": "<p>Entwicklung mit Python und teilweise Homeoffice.</p>",
  "datePosted": "2026-08-20", "employmentType": "FULL_TIME",
  "hiringOrganization": {"@type": "Organization", "name": "Example GmbH"},
  "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Fulda"}},
  "estimatedSalary": {"@type": "MonetaryAmount", "currency": "EUR",
    "value": {"minValue": 50000, "maxValue": 60000, "unitText": "YEAR"}}
}</script>"""
FEED = """<?xml version="1.0" encoding="UTF-8"?><jobs><job id="feed-1" category="IT">
  <id><![CDATA[feed-1]]></id>
  <title><![CDATA[Junior Python Developer (m/w/d)]]></title>
  <link><![CDATA[https://germantechjobs.de/jobs/example]]></link>
  <apply_url><![CDATA[https://company.test/apply]]></apply_url>
  <country><![CDATA[Germany]]></country><location><![CDATA[Full Remote]]></location>
  <city><![CDATA[Berlin]]></city><salary><![CDATA[50.000 - 65.000 € per year]]></salary>
  <company-name><![CDATA[Example GmbH]]></company-name><job-type><![CDATA[Full-Time]]></job-type>
  <pubdate><![CDATA[16.08.2026]]></pubdate>
  <description><![CDATA[<p>Python und APIs. Fully remote.</p>]]></description>
</job></jobs>"""
COMPOSE_IT_PAGE = """
  <span class="elementor-icon-list-text">Festanstellung</span>
  <span class="elementor-icon-list-text">Fulda (Stadtmitte)</span>
  <h1>IT-Supporter (m/w/d)</h1>
  <div data-elementor-type="wp-post" data-elementor-id="3364">
    <h2>Was du mitbringen solltest</h2>
    <p>Abgeschlossene IT-Ausbildung. Hybrid (Büro / HomeOffice).</p>
  </div>
  <div id="bewerberform">Bewerbungsformular mit irrelevanten Feldern</div>"""
EDAG_PAGE = """
  <div class="short-facts"><span>EDAG Engineering GmbH</span><span>Hybrides Arbeiten möglich</span>
    <span>Fulda</span><span>Absolventen</span><span>Vollzeit</span></div>
  <div class="breadcrumb"></div>
  <h2 class="title h2">Junior Python Developer (m/w/d)</h2>
  <div class="teaser">Python und APIs</div>
  <div class="description"><div>Einsteiger willkommen</div></div>
  </div></div><div class="actions">"""
JOIN_PAGE = """<script type="application/ld+json">{
  "@type": "JobPosting", "title": "Data Engineer &amp; Business Analyst",
  "description": "&lt;h2&gt;Aufgaben&lt;/h2&gt;&lt;p&gt;Python &amp;amp; SQL&lt;/p&gt;",
  "hiringOrganization": {"name": "ApoVid GmbH"}, "jobLocationType": "TELECOMMUTE",
  "applicantLocationRequirements": {"name": "Deutschland"}
}</script>"""
CAREER_PAGE = """<meta property="og:site_name" content="Ecoplan CRM">
  <header>Navigation</header><div id="main" role="main">
  <div><h1>Softwareentwickler (m/w/d)</h1></div><article><p>Standort: Fulda</p></article>
  <div><p>{text}</p></div><div id="footer"><p>Footertext</p></div></div>"""


def json_ld(posting):
    return f'<script type="application/ld+json">{json.dumps(posting)}</script>'


def build_jobs():
    """Parse every fixture with network access replaced by the fixed pages above."""
    jobs = {
        "arbeitnow": arbeitnow.job_from_record(
            {
                "slug": "junior-python-developer-123",
                "company_name": "Example GmbH",
                "title": "Junior Python Developer",
                "description": "<p>Python und APIs</p>",
                "remote": True,
                "url": "https://www.arbeitnow.com/jobs/example/123",
                "job_types": ["full_time"],
                "location": "Fulda",
                "created_at": 1783641600,
            }
        ),
        "himalayas": himalayas.job_from_record(
            {
                "guid": "https://himalayas.app/companies/example/jobs/junior-developer",
                "applicationLink": "https://himalayas.app/companies/example/jobs/junior-developer",
                "title": "Junior Developer",
                "companyName": "Example GmbH",
                "description": "<p>Python und APIs</p>",
                "employmentType": "Full Time",
                "seniority": ["Entry-level"],
                "locationRestrictions": [{"alpha2": "DE", "name": "Germany"}],
                "currency": "EUR",
                "salaryPeriod": "annual",
                "minSalary": 73000,
                "maxSalary": 91000,
                "pubDate": 1786838400000,
            }
        ),
        "jobicy": jobicy.job_from_record(
            {
                "id": 123,
                "url": "https://jobicy.com/jobs/junior-developer",
                "jobTitle": "Junior Developer",
                "companyName": "Example GmbH",
                "jobType": "Full Time",
                "jobGeo": "Germany",
                "jobLevel": "Entry-Level, Junior",
                "jobDescription": "<p>Python und APIs</p>",
                "pubDate": "2026-08-16 09:30:00",
                "salaryMin": "73000",
                "salaryMax": 91000,
                "salaryCurrency": "EUR",
                "salaryPeriod": "yearly",
            }
        ),
        "startup_jobs": startup_jobs.job_from_record(
            {
                "id": "job-1",
                "title": "Junior Software Engineer",
                "url": "https://startup.jobs/example-job",
                "published_at": "2026-08-16T09:30:00Z",
                "employment_type": "full-time",
                "workplace_type": "remote",
                "location": {"city": "Berlin", "country": "Germany", "country_code": "DE"},
                "salary_data": {"min": 83000, "max": 101000, "currency": "EUR", "interval": "year"},
                "company": {"name": "Example GmbH"},
                "description_html": "<p>Python und APIs</p>",
            }
        ),
        "remotely": remotely.job_from_html(
            "https://www.remotely.de/job/example", REMOTELY_PAGE, today=date(2026, 8, 28)
        ),
        "get_in_it_summary": get_in_it.summary_job_from_record(
            {
                "id": 311970,
                "title": "(Junior) DevOps Software Engineer (m/w/d)",
                "url": "/jobsuche/p311970",
                "homeOffice": True,
                "careers": [{"name": "System Engineering / Admin"}],
                "locations": [{"name": "München"}],
                "company": {"title": "Reply Deutschland SE"},
            }
        ),
        "german_tech_jobs": german_tech_jobs.parse_feed(FEED, FEED_TIME)[0][0],
        "company_json_ld": job_from_json_ld(
            "example_company",
            "Fallback GmbH",
            "https://careers.example.test/job/123",
            json_ld(POSTING),
        ),
        "compose_it": compose_it.job_from_html(
            compose_it.SOURCE_NAME,
            compose_it.COMPANY,
            "https://compose-it.de/job/it-supporter/",
            COMPOSE_IT_PAGE,
        ),
        "edag": edag.job_from_html(
            edag.SOURCE_NAME,
            edag.COMPANY,
            "https://www.edag.com/de/karriere/stellenanzeigen/detail/junior-python-developer-58815",
            EDAG_PAGE,
        ),
        "manual_json_ld": manual.job_from_page(
            "https://join.com/companies/example/12345678", JOIN_PAGE
        ),
        "manual_visible": manual.job_from_page(
            "https://example.com/softwareentwickler",
            CAREER_PAGE.format(text="Softwareentwicklung mit Python und SQL im Produktteam. " * 5),
        ),
    }

    summary = studysmarter.summary_job_from_record(
        {
            "id": 12345678,
            "link": "https://talents.studysmarter.de/companies/example/junior-python-developer-12345678/",
            "company_name": "Example GmbH",
            "is_remote_positions": "completely",
            "salary": {"salary_type": "ai_predicted"},
        }
    )
    jobs["studysmarter_summary"] = serialize(summary)
    jobs["studysmarter_detail"] = studysmarter.enrich_summary_job(summary, STUDYSMARTER_PAGE)

    agency = {
        "stellenangebotsTitel": "Junior Python Developer",
        "firma": "Example GmbH",
        "stellenlokationen": [{"adresse": {"ort": "Fulda"}}],
        "stellenangebotsBeschreibung": "<p>Python und APIs</p>",
        "homeofficemoeglich": True,
        "homeofficetyp": "AUSSCHLIESSLICH",
        "externeURL": "https://example.test/apply",
        "arbeitszeitVollzeit": True,
        "datumErsteVeroeffentlichung": "2026-07-10",
    }
    state = json.dumps({"jobdetail": agency})
    with patch.object(
        arbeitsagentur,
        "fetch_text",
        return_value=f'<script id="ng-state" type="application/json">{state}</script>',
    ):
        jobs["arbeitsagentur"] = arbeitsagentur.fetch_job(
            "https://www.arbeitsagentur.de/jobsuche/jobdetail/123-S"
        )

    client = Mock()
    client.get.return_value = (
        json_ld(POSTING)
        + '<script>{"metaData":{"contractType":"Berufseinstieg/Trainee, Feste Anstellung"}}</script>'
    )
    jobs["stepstone"] = stepstone.fetch_job(
        "https://www.stepstone.de/stellenangebote--Junior-Python-Developer--14250000-inline.html",
        client,
    )

    career_posting = {
        **POSTING,
        "description": "<p>Karrierestufe: Absolventinnen &amp; Absolventen; "
        "Berufserfahrene Beschäftigungsgrad: Vollzeit</p>",
    }
    with patch.object(get_in_it, "fetch_text", return_value=json_ld(career_posting)):
        jobs["get_in_it_detail"] = get_in_it.fetch_job("https://www.get-in-it.de/jobsuche/p309921")
    return {name: job if isinstance(job, dict) else serialize(job) for name, job in jobs.items()}


def serialize(job):
    """Serialize a job; a current fetch time only records that it was set."""
    values = job.to_dict()
    if values.get("fetched_at") and values["fetched_at"] != FEED_TIME.isoformat():
        values["fetched_at"] = "<now>"
    return values


class GoldenJobTests(unittest.TestCase):
    def test_every_parser_still_produces_the_recorded_jobs(self):
        actual = build_jobs()
        if os.environ.get("JOBFINDER_UPDATE_GOLDEN") == "1":
            GOLDEN.write_text(
                json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))

        self.assertEqual(sorted(actual), sorted(expected))
        for name, values in expected.items():
            with self.subTest(name):
                self.assertEqual(actual[name], values)
