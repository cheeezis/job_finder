import json
import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SEARCH_TERMS = [
    "Python",
    "Python Developer",
    "Data Analyst",
    "AI Engineer",
    "Machine Learning",
    "KI",
]

DEFAULT_OUTPUT_FILE = "data/job_links.txt"
SEARCH_BASE_URL = "https://www.arbeitsagentur.de/jobsuche/suche"
DETAIL_BASE_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail"


def main():
    output_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OUTPUT_FILE
    links = collect_links()

    Path(output_file).write_text("\n".join(links) + "\n", encoding="utf-8")
    print(f"\n{len(links)} Link(s) gespeichert in {output_file}")


def collect_links():
    seen = set()
    links = []

    for term in SEARCH_TERMS:
        print(f"Suche: {term}")
        results = search(term)
        print(f"  {len(results)} Treffer")

        for result in results:
            reference = result.get("referenznummer")
            if not reference:
                continue
            if "/" in reference:
                print(f"  Ueberspringe Sonder-Referenz: {reference}")
                continue

            url = f"{DETAIL_BASE_URL}/{reference}"
            if url in seen:
                continue

            seen.add(url)
            links.append(url)

    return links


def search(term):
    html = fetch_html(build_search_url(term))
    state = extract_ng_state(html)
    return state.get("suchergebnis", {}).get("ergebnisliste", [])


def build_search_url(term):
    # Erstmal suchen wir bewusst nur Fulda + 30 km.
    # Remote erkennen und bewerten wir danach im Scoring anhand der Jobdetails.
    query = {
        "angebotsart": "1",
        "was": term,
        "wo": "Fulda",
        "umkreis": "30",
    }
    return f"{SEARCH_BASE_URL}?{urlencode(query)}"


def fetch_html(url):
    request = Request(url, headers={"User-Agent": "job-agent/0.1"})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8")


def extract_ng_state(html):
    # Die Suchergebnisse liegen serverseitig gerendert im Angular-State.
    match = re.search(
        r'<script id="ng-state" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise ValueError("ng-state JSON nicht gefunden")
    return json.loads(unescape(match.group(1)))


if __name__ == "__main__":
    main()
