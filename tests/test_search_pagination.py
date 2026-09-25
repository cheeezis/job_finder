"""Search loops of Arbeitsagentur and get-in-IT: paging, stopping and commuter searches."""

import json
import unittest
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
