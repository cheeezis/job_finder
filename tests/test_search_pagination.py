"""Search loops and record collection of Arbeitsagentur and get-in-IT."""

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from job_finder.sources import arbeitsagentur, get_in_it


def ng_state_page(search_result):
    state = json.dumps({"suchergebnis": search_result})
    return f'<script id="ng-state" type="application/json">{state}</script>'


def query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


class ArbeitsagenturSearchTests(unittest.TestCase):
    def run_search(self, pages):
        requested = []

        def fetch(url):
            requested.append(query(url))
            return ng_state_page(pages[int(query(url)["page"])])

        with patch.object(arbeitsagentur, "fetch_text", side_effect=fetch):
            results = arbeitsagentur.search("python", location="Fulda", radius=25)
        return [result["referenznummer"] for result in results], requested

    def test_search_pages_until_the_announced_total_is_reached(self):
        references, requested = self.run_search(
            {
                1: {
                    "ergebnisliste": [{"referenznummer": "A"}, {"referenznummer": "B"}],
                    "maxErgebnisse": 3,
                },
                2: {
                    "ergebnisliste": [{"referenznummer": "B"}, {"referenznummer": "C"}],
                    "maxErgebnisse": 3,
                },
            }
        )

        self.assertEqual(references, ["A", "B", "C"])
        self.assertEqual([request["page"] for request in requested], ["1", "2"])
        self.assertEqual(
            requested[0],
            {"angebotsart": "1", "was": "python", "wo": "Fulda", "umkreis": "25", "page": "1"},
        )

    def test_search_stops_when_a_page_brings_nothing_new(self):
        references, requested = self.run_search(
            {
                1: {"stellenangebote": [{"referenznummer": "A"}], "maxErgebnisse": 10},
                2: {"stellenangebote": [{"referenznummer": "A"}], "maxErgebnisse": 10},
            }
        )

        self.assertEqual(references, ["A"])
        self.assertEqual([request["page"] for request in requested], ["1", "2"])

    def test_links_skip_invalid_references_and_duplicates_across_searches(self):
        results = {
            "python": [
                {"referenznummer": "10000-1"},
                {"referenznummer": "10000-9/2"},
                {"titel": "ohne Referenz"},
                {"referenznummer": "10000-2"},
            ],
            "java": [{"referenznummer": "10000-2"}, {"referenznummer": "10000-3"}],
        }
        searches = []

        def search(term, location, radius):
            searches.append((term, location, radius))
            return results[term]

        settings = {
            "SEARCH_TERMS": ["python"],
            "LOCAL_SEARCH_LOCATION": "Fulda",
            "LOCAL_SEARCH_RADIUS_KM": 25,
            "COMMUTER_SEARCH_TERMS": ["java"],
            "COMMUTER_SEARCH_LOCATIONS": ["Kassel"],
            "COMMUTER_SEARCH_RADIUS_KM": 10,
        }
        with (
            patch.multiple(arbeitsagentur, **settings),
            patch.object(arbeitsagentur, "search", side_effect=search),
        ):
            links = arbeitsagentur.collect_links()

        detail = "https://www.arbeitsagentur.de/jobsuche/jobdetail"
        self.assertEqual(links, [f"{detail}/10000-1", f"{detail}/10000-2", f"{detail}/10000-3"])
        self.assertEqual(searches, [("python", "Fulda", 25), ("java", "Kassel", 10)])

    def test_missing_angular_state_names_the_script(self):
        with self.assertRaisesRegex(ValueError, "^ng-state JSON nicht gefunden$"):
            arbeitsagentur.extract_ng_state("<html></html>")


class GetInItSearchTests(unittest.TestCase):
    def test_api_search_pages_with_location_filters_until_nothing_is_new(self):
        pages = {
            0: {"items": {"results": [{"id": 1}, {"id": 2}]}, "total": 5},
            2: {"items": {"results": [{"id": 2}, {"id": 3}]}, "total": 5},
            4: {"items": {"results": []}, "total": 5},
        }
        for location, location_filter in (
            ("remote", {"filter[homeOffice]": "1"}),
            ("Fulda", {"filter[state]": "5"}),
        ):
            with self.subTest(location=location):
                requests = []

                def fetch(url, headers):
                    requests.append((query(url), headers))
                    return pages[int(query(url)["start"])]

                with patch.object(get_in_it, "fetch_json", side_effect=fetch):
                    results = get_in_it.search_api(7, location)

                self.assertEqual([result["id"] for result in results], [1, 2, 3])
                self.assertEqual(
                    [params["start"] for params, _headers in requests], ["0", "2", "4"]
                )
                self.assertEqual(
                    requests[0][0],
                    {
                        "start": "0",
                        "limit": "39",
                        "filter[thematic_priority]": "7",
                        **location_filter,
                    },
                )
                self.assertEqual(
                    requests[0][1],
                    {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                )

    def test_records_keep_the_first_record_per_id_or_url_and_warn_about_failures(self):
        searches = [
            {"priority_id": 1, "location": "Fulda"},
            {"priority_id": 2, "location": "remote"},
            {"priority_id": 3, "location": "Fulda"},
        ]
        answers = {
            1: [
                {"id": 7, "title": "first"},
                {"url": "https://example.test/u1"},
                {"title": "no id"},
            ],
            2: RuntimeError("offline"),
            3: [{"id": 7, "title": "second"}, {"id": 8}],
        }

        def search(priority_id, location):
            answer = answers[priority_id]
            if isinstance(answer, Exception):
                raise answer
            return answer

        output = io.StringIO()
        with (
            patch.object(get_in_it, "build_api_searches", return_value=iter(searches)),
            patch.object(get_in_it, "search_api", side_effect=search),
            redirect_stdout(output),
        ):
            records = get_in_it.collect_records()

        self.assertEqual(
            records, [{"id": 7, "title": "first"}, {"url": "https://example.test/u1"}, {"id": 8}]
        )
        self.assertEqual(output.getvalue(), "WARNUNG get-in-IT: 1 Suche(n) fehlgeschlagen\n")

    def test_searches_map_terms_to_unique_priorities_per_location_mode(self):
        settings = {
            "GET_IN_IT_SEARCH_TERMS": ["python dev", "remote java"],
            "GET_IN_IT_SEARCH_LOCATIONS": ["Fulda", "remote"],
            "COMMUTER_SEARCH_TERMS": ["python"],
            "COMMUTER_SEARCH_LOCATIONS": ["Kassel"],
            "TERM_PRIORITY_RULES": [(("python",), [1, 2]), (("java",), [2, 3])],
        }
        with patch.multiple(get_in_it, **settings):
            searches = list(get_in_it.build_api_searches())

        self.assertEqual(
            [(search["priority_id"], search["location"]) for search in searches],
            [(1, "Fulda"), (2, "Fulda"), (1, "remote"), (2, "remote"), (3, "Fulda"), (3, "remote")],
        )


def next_data_page(job):
    state = json.dumps({"props": {"initialState": {"jobJob": {"job": job}}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{state}</script>'


class GetInItPostingFallbackTests(unittest.TestCase):
    def test_json_ld_wins_over_next_data(self):
        json_ld = '<script type="application/ld+json">{"@type": "JobPosting", "title": "From LD"}</script>'
        page = json_ld + next_data_page({"header": {"title": "From state"}})

        self.assertEqual(get_in_it.extract_job_posting(page)["title"], "From LD")

    def test_next_data_supplies_an_embedded_posting_or_builds_one(self):
        embedded = {"@type": "JobPosting", "title": "Embedded"}
        page = next_data_page(
            {
                "metaData": [
                    {"name": "other"},
                    {"name": "schema_org:job_posting", "content": json.dumps(embedded)},
                ]
            }
        )
        self.assertEqual(get_in_it.extract_job_posting(page), embedded)

        built = get_in_it.extract_job_posting(
            next_data_page(
                {
                    "id": 42,
                    "header": {
                        "title": "Built",
                        "companyName": "Example GmbH",
                        "locations": ["Fulda"],
                    },
                    "content": "<p>Text</p>",
                }
            )
        )
        self.assertEqual(built["@type"], "JobPosting")
        self.assertEqual(built["title"], "Built")
        self.assertEqual(built["hiringOrganization"], {"name": "Example GmbH"})
        self.assertEqual(
            built["jobLocation"],
            [
                {
                    "@type": "Place",
                    "address": {
                        "@type": "PostalAddress",
                        "addressLocality": "Fulda",
                        "addressCountry": "DE",
                    },
                }
            ],
        )
        self.assertEqual(built["description"], "<p>Text</p>")
        self.assertEqual(built["url"], "https://www.get-in-it.de/jobsuche/p42")

    def test_missing_postings_raise_value_errors_with_their_messages(self):
        broken = next_data_page({"metaData": [{"name": "schema_org:job_posting", "content": "{"}]})
        cases = [
            (next_data_page(None), "^JobPosting JSON-LD nicht gefunden$"),
            (broken, "^JobPosting JSON-LD nicht gefunden$"),
            ("<html></html>", "^__NEXT_DATA__ JSON nicht gefunden$"),
        ]
        for page, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                get_in_it.extract_job_posting(page)
