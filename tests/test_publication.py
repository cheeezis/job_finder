"""Publication contracts with explicit files or in-memory managed snapshots."""

import json
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from job_finder.models import Job, JobSource
from job_finder.paths import JOBS_FILE
from job_finder.persistence import storage
from job_finder.workflow.reporting import build_recommendations

JOBS_DATASET = "internal/jobs.json"
RECOMMENDATIONS_DATASET = "output/recommendations.json"


def listing(source, url):
    return {"source": source, "url": url}


def job(job_id, url, *, locations=None):
    return Job(
        id=job_id,
        title="Junior Developer",
        company="Example GmbH",
        locations=locations or [],
        sources=[JobSource("fresh", url)],
        description_raw="Python",
        description_clean="Python",
    )


@contextmanager
def managed_documents(documents):
    state = deepcopy(documents)
    with (
        patch.object(storage, "transaction", side_effect=lambda: nullcontext(object())),
        patch.object(storage, "lock"),
        patch.object(
            storage, "read_dataset", side_effect=lambda name, default=None: deepcopy(state.get(name, default))
        ),
        patch.object(
            storage, "write_dataset", side_effect=lambda name, value: state.__setitem__(name, deepcopy(value))
        ),
    ):
        yield state


def publish(jobs, document, previous, excluded=frozenset()):
    with managed_documents(previous) as state:
        storage.publish_results(
            jobs,
            {},
            jobs_path=JOBS_FILE,
            recommendation_builder=lambda _results: deepcopy(document),
            exclude_sources=excluded,
        )
    return state[JOBS_DATASET], state[RECOMMENDATIONS_DATASET]


class PublicationTests(unittest.TestCase):
    def test_explicit_files_replace_views_without_managed_retention(self):
        current = job("new", "https://fresh.test/new")
        document = {"batch": None, "recommendations": [{"id": "new", "optional": None}]}
        old = {"id": "manual", "sources": [listing("manual", "https://manual.test/old")]}
        with tempfile.TemporaryDirectory() as directory:
            jobs_path = Path(directory) / "jobs.json"
            recommendations_path = Path(directory) / "recommendations.json"
            storage.write_json_atomic(jobs_path, [old])
            storage.write_json_atomic(recommendations_path, {"old_header": True, "recommendations": [old]})

            storage.publish_results(
                [current],
                {},
                jobs_path=jobs_path,
                recommendation_builder=lambda _results: deepcopy(document),
                recommendations_path=recommendations_path,
                exclude_sources={"manual"},
            )

            self.assertEqual(json.loads(jobs_path.read_text(encoding="utf-8")), [current.to_dict()])
            self.assertEqual(json.loads(recommendations_path.read_text(encoding="utf-8")), document)

    def test_managed_publication_preserves_new_header_and_retained_listing_order(self):
        known = listing("partial", "https://shared.test/known")
        added = listing("partial", "https://partial.test/extra")
        employer = listing("original", "https://company.test/apply")
        manual_links = [listing("manual", "https://manual.test/old"), listing("fresh", "https://fresh.test/manual")]
        partial_links = [added, listing("fresh", "https://fresh.test/obsolete"), employer]
        old_jobs = [
            {"id": "shared", "locations": ["Previous", "Current"], "sources": [known, added]},
            {"id": "manual", "sources": manual_links, "unknown": {"values": [None]}},
            {"id": "partial", "sources": partial_links, "optional": None},
            {"id": "obsolete", "sources": [listing("fresh", "https://fresh.test/obsolete")]},
        ]
        old_cards = [{**entry, "source_links": entry["sources"]} for entry in old_jobs]
        previous = {JOBS_DATASET: old_jobs, RECOMMENDATIONS_DATASET: {"old_header": True, "recommendations": old_cards}}
        first = job("new", "https://fresh.test/new")
        shared = job("shared", "https://shared.test/known", locations=["Current", "Other"])
        cards = [
            {"id": "new", "source_links": first.to_dict()["sources"], "optional": None},
            {"id": "shared", "source_links": shared.to_dict()["sources"], "locations": ["Current", "Other"]},
        ]
        document = {"batch": None, "metadata": {"tags": ["current"]}, "recommendations": cards}

        jobs, recommendations = publish([first, shared], document, previous, {"partial"})

        expected_shared = {
            **shared.to_dict(),
            "sources": [*shared.to_dict()["sources"], added],
            "locations": ["Current", "Other", "Previous"],
        }
        expected_card = {
            **cards[1],
            "source_links": [*cards[1]["source_links"], added],
            "locations": ["Current", "Other", "Previous"],
        }
        self.assertEqual(
            jobs, [first.to_dict(), expected_shared, old_jobs[1], {**old_jobs[2], "sources": [added, employer]}]
        )
        self.assertEqual(
            recommendations,
            {
                **document,
                "recommendations": [
                    cards[0],
                    expected_card,
                    old_cards[1],
                    {**old_cards[2], "source_links": [added, employer]},
                ],
            },
        )

    def test_duplicate_current_ids_merge_skipped_links_into_the_first_position(self):
        first = job("same", "https://fresh.test/first", locations=["First"])
        second = job("same", "https://fresh.test/second", locations=["Second"])
        skipped = listing("partial", "https://partial.test/old")
        previous = {
            JOBS_DATASET: [{"id": "same", "sources": [skipped], "locations": ["Previous"]}],
            RECOMMENDATIONS_DATASET: {
                "recommendations": [{"id": "same", "source_links": [skipped], "locations": ["Previous"]}]
            },
        }
        cards = [
            {"id": item.id, "source_links": item.to_dict()["sources"], "locations": item.locations}
            for item in (first, second)
        ]

        jobs, recommendations = publish([first, second], {"recommendations": cards}, previous, {"partial"})

        self.assertEqual(
            jobs,
            [
                {
                    **first.to_dict(),
                    "sources": [*first.to_dict()["sources"], skipped],
                    "locations": ["First", "Previous"],
                },
                second.to_dict(),
            ],
        )
        self.assertEqual(
            recommendations["recommendations"],
            [
                {**cards[0], "source_links": [*cards[0]["source_links"], skipped], "locations": ["First", "Previous"]},
                cards[1],
            ],
        )

    def test_empty_runs_retain_manual_and_only_excluded_automatic_listings(self):
        manual = {"id": "manual", "sources": [listing("manual", "https://manual.test/old")]}
        skipped = listing("partial", "https://partial.test/old")
        employer = listing("original", "https://company.test/apply")
        partial = {"id": "partial", "sources": [skipped, employer]}
        obsolete = {"id": "obsolete", "sources": [listing("fresh", "https://fresh.test/old")]}
        old_jobs = [manual, partial, obsolete]
        old_cards = [{**entry, "source_links": entry["sources"]} for entry in old_jobs]
        previous = {JOBS_DATASET: old_jobs, RECOMMENDATIONS_DATASET: {"recommendations": old_cards}}
        for excluded, retained in ((set(), 1), ({"partial"}, 2)):
            with self.subTest(excluded=excluded):
                jobs, document = publish([], {"recommendations": []}, previous, excluded)
                self.assertEqual(jobs, old_jobs[:retained])
                self.assertEqual(document, {"recommendations": old_cards[:retained]})

    def test_explicit_recommendation_api_preserves_presence_null_and_input_order(self):
        first = job("first", "https://fresh.test/first").to_dict()
        first.pop("published_at")
        first.update(match_percent=None, location_precheck=None, prefilter_warning=None)
        second = {**job("second", "https://fresh.test/second").to_dict(), "match_percent": 0}
        results = {"included": [second, first], "excluded": [job("excluded", "https://fresh.test/excluded").to_dict()]}
        original = deepcopy(results)
        document = build_recommendations(results)

        self.assertEqual(list(document), ["recommendations"])
        zero, nullable = document["recommendations"]
        self.assertEqual([zero["id"], nullable["id"]], ["second", "first"])
        self.assertEqual(zero["match_percent"], 0)
        self.assertIn("match_percent", nullable)
        self.assertIsNone(nullable["match_percent"])
        self.assertIn("published_at", nullable)
        self.assertIsNone(nullable["published_at"])
        self.assertNotIn("location_precheck", nullable)
        self.assertNotIn("prefilter_warning", nullable)
        self.assertEqual(results, original)
