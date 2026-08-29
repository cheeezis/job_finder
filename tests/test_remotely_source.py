"""Tests for the public Remotely.de source adapter."""

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from job_finder.models import Job, JobSource, WorkMode
from job_finder.sources import remotely
from job_finder.sources.common import (
    ListingUnavailableError,
    load_detail_cache,
    save_detail_cache,
)


DETAIL_HTML = """
<html><body>
  <p class="text-xs font-semibold uppercase tracking-wide text-foreground">
    Example GmbH
  </p>
  <h1>Junior Python Developer (m/w/d)</h1>
  <h3>Eckdaten</h3>
  <div><svg class="lucide lucide-map-pin"></svg>Berlin</div>
  <div><svg class="lucide lucide-tag"></svg>Computer Software</div>
  <div><svg class="lucide lucide-calendar-days"></svg>vor 6 Tagen</div>
  <h3>Arbeitsmodell</h3>
  <span><svg class="lucide lucide-globe"></svg>Vollständig remote</span>
  <a data-apply-cta="true" href="https://example.test/apply?from=remotely">
    Jetzt bewerben
  </a>
  <div class="prose prose-sm max-w-none">
    <h2>Deine Aufgaben</h2><p>Python, APIs und Cloud-Plattformen.</p>
    <ul><li>Services entwickeln</li></ul>
  </div>
</body></html>
"""


class RemotelySourceTests(unittest.TestCase):
    def remotely_job(self, identifier, application_url):
        return Job(
            id=f"remotely:{identifier}",
            title="Junior Python Developer",
            company="Example GmbH",
            locations=["Remote"],
            sources=[
                JobSource(
                    source="remotely",
                    url=f"https://www.remotely.de/job/{identifier}",
                    application_url=application_url,
                )
            ],
            description_raw="Python",
            description_clean="Python",
            work_mode=WorkMode.REMOTE,
            remote_percentage=100,
        )

    def test_extract_detail_links_normalizes_and_removes_duplicates(self):
        html = """
          <a href="/job/example-one">One</a>
          <a href="https://www.remotely.de/job/example-one?tracking=1">Duplicate</a>
          <a href="/remote-jobs">Not a detail</a>
          <a href="/job/example-two/">Two</a>
        """

        self.assertEqual(
            remotely.extract_detail_links(html),
            [
                "https://www.remotely.de/job/example-one",
                "https://www.remotely.de/job/example-two",
            ],
        )

    def test_initial_scan_only_collects_rolling_seven_day_window(self):
        client = Mock()
        client.get.side_effect = [
            '<a href="/job/one">One vor 2 Tagen</a>',
            '<a href="/job/two">Two vor 28 Tagen</a>',
            '<a href="/job/three">Three vor 35 Tagen</a>',
        ]

        links = remotely.collect_links(
            client,
            known_urls={"https://www.remotely.de/job/already-cached"},
            today=date(2026, 3, 20),
            initial_scan=True,
        )

        self.assertEqual(
            links,
            [
                "https://www.remotely.de/job/one",
            ],
        )
        self.assertEqual(client.get.call_count, 3)
        self.assertEqual(
            client.get.call_args_list[1].args[0],
            "https://www.remotely.de/alle-jobs/seite/2",
        )

    def test_followup_scan_still_returns_complete_recent_window(self):
        client = Mock()
        client.get.side_effect = [
            '<a href="/job/new">New vor 1 Tagen</a>',
            '<a href="/job/known-one">Known vor 12 Tagen</a>',
            '<a href="/job/known-two">Known vor 13 Tagen</a>',
        ]

        links = remotely.collect_links(
            client,
            known_urls={
                "https://www.remotely.de/job/known-one",
                "https://www.remotely.de/job/known-two",
            },
            today=date(2026, 3, 20),
        )

        self.assertEqual(client.get.call_count, 3)
        self.assertEqual(links, ["https://www.remotely.de/job/new"])

    def test_promoted_old_card_is_filtered_but_does_not_control_boundary(self):
        entries = remotely.extract_list_entries(
            """
            <a href="/job/promoted">
              <div class="border-b-featured-border">Promoted vor 1 Monaten</div>
            </a>
            <a href="/job/regular">Regular vor 35 Tagen</a>
            """
        )

        self.assertTrue(entries[0]["promoted"])
        self.assertTrue(
            remotely.page_is_before_cutoff(
                entries,
                date(2026, 3, 13),
                date(2026, 3, 20),
            )
        )
        self.assertFalse(
            remotely.entry_is_recent(
                entries[0], date(2026, 3, 13), date(2026, 3, 20)
            )
        )

    def test_job_from_html_reads_visible_semantic_fields(self):
        job = remotely.job_from_html(
            "https://www.remotely.de/job/example",
            DETAIL_HTML,
            today=date(2026, 8, 28),
        )

        self.assertEqual(job.id, "remotely:example")
        self.assertEqual(job.title, "Junior Python Developer (m/w/d)")
        self.assertEqual(job.company, "Example GmbH")
        self.assertEqual(job.locations, ["Berlin"])
        self.assertIn("Python, APIs", job.description_clean)
        self.assertIs(job.work_mode, WorkMode.REMOTE)
        self.assertEqual(job.remote_percentage, 100)
        self.assertEqual(job.published_at.isoformat(), "2026-08-22")
        self.assertEqual(
            job.sources[0].application_url,
            "https://example.test/apply?from=remotely",
        )

    def test_job_from_html_rejects_already_filled_listing(self):
        html = """
          <html><body><h1>Bereits vergeben</h1>
          <p>Lass dir die nächste nicht entgehen.</p></body></html>
        """

        with self.assertRaises(ListingUnavailableError):
            remotely.job_from_html(
                "https://www.remotely.de/job/closed",
                html,
                today=date(2026, 8, 29),
            )

    def test_translation_text_in_script_does_not_reject_active_listing(self):
        html = DETAIL_HTML.replace(
            "</body>",
            """
            <script>
              window.messages = {"heading": "Bereits vergeben",
                "body": "Diese Stelle ist bereits vergeben."};
            </script></body>
            """,
        )

        job = remotely.job_from_html(
            "https://www.remotely.de/job/active",
            html,
            today=date(2026, 8, 29),
        )

        self.assertEqual(job.id, "remotely:active")

    def test_fetch_jobs_reuses_fresh_detail_cache(self):
        now = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
        url = "https://www.remotely.de/job/cached"
        cached = Job(
            id="remotely:cached",
            title="Cached",
            company="Example GmbH",
            locations=["Remote"],
            sources=[JobSource(source="remotely", url=url)],
            description_raw="Python",
            description_clean="Python",
            work_mode=WorkMode.REMOTE,
            remote_percentage=100,
            fetched_at=now,
        )
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "remotely.json"
            save_detail_cache(cache_path, {url: cached})
            with (
                patch.object(remotely, "collect_links", return_value=[url]),
                patch.object(remotely, "fetch_job") as fetch_job,
            ):
                jobs = remotely.fetch_jobs(cache_path, client=Mock(), now=now)

        self.assertEqual(jobs, [cached])
        fetch_job.assert_not_called()

    def test_fetch_jobs_removes_closed_listing_from_stale_cache(self):
        now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
        url = "https://www.remotely.de/job/now-closed"
        cached = Job(
            id="remotely:now-closed",
            title="Old cached job",
            company="Example GmbH",
            locations=["Remote"],
            sources=[JobSource(source="remotely", url=url)],
            description_raw="Python",
            description_clean="Python",
            work_mode=WorkMode.REMOTE,
            remote_percentage=100,
            fetched_at=now - timedelta(days=2),
        )
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "remotely.json"
            save_detail_cache(cache_path, {url: cached})
            with (
                patch.object(remotely, "collect_links", return_value=[url]),
                patch.object(
                    remotely,
                    "fetch_job",
                    side_effect=ListingUnavailableError("closed"),
                ),
            ):
                jobs = remotely.fetch_jobs(cache_path, client=Mock(), now=now)
            remaining_cache = load_detail_cache(cache_path)

        self.assertEqual(jobs, [])
        self.assertEqual(remaining_cache, {})

    def test_linkedin_check_removes_closed_and_redirected_candidates(self):
        active_url = "https://de.linkedin.com/jobs/view/active-at-example-101"
        closed_url = "https://de.linkedin.com/jobs/view/closed-at-example-102"
        redirected_url = "https://de.linkedin.com/jobs/view/expired-at-example-103"
        jobs = [
            self.remotely_job("active", active_url),
            self.remotely_job("closed", closed_url),
            self.remotely_job("redirected", redirected_url),
            self.remotely_job(
                "not-prefiltered",
                "https://de.linkedin.com/jobs/view/other-at-example-104",
            ),
        ]

        def fetcher(url, headers=None):
            if url == active_url:
                return url, "<main>Jetzt bewerben</main>"
            if url == closed_url:
                return url, "<main>Es werden keine Bewerbungen mehr angenommen.</main>"
            return "https://de.linkedin.com/jobs/entwickler-stellen", "search"

        with tempfile.TemporaryDirectory() as directory:
            removed = remotely.enrich_candidate_jobs(
                jobs,
                {"remotely:active", "remotely:closed", "remotely:redirected"},
                status_cache_path=Path(directory) / "linkedin.json",
                fetcher=fetcher,
                now=datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
                sleeper=lambda _seconds: None,
            )

        self.assertEqual(removed, 2)
        self.assertEqual(
            [job.id for job in jobs],
            ["remotely:active", "remotely:not-prefiltered"],
        )

    def test_linkedin_status_is_reused_for_one_day(self):
        url = "https://de.linkedin.com/jobs/view/closed-at-example-105"
        now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "linkedin.json"
            first_jobs = [self.remotely_job("first", url)]
            remotely.enrich_candidate_jobs(
                first_jobs,
                {"remotely:first"},
                status_cache_path=status_path,
                fetcher=lambda _url, headers=None: (
                    url,
                    "No longer accepting applications",
                ),
                now=now,
                sleeper=lambda _seconds: None,
            )
            second_jobs = [self.remotely_job("second", url)]
            fetcher = Mock(side_effect=AssertionError("must use status cache"))
            removed = remotely.enrich_candidate_jobs(
                second_jobs,
                {"remotely:second"},
                status_cache_path=status_path,
                fetcher=fetcher,
                now=now + timedelta(hours=12),
                sleeper=lambda _seconds: None,
            )

        self.assertEqual(removed, 1)
        fetcher.assert_not_called()


if __name__ == "__main__":
    unittest.main()
