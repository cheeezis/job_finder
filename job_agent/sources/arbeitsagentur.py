import json
import re
from html import unescape
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SOURCE_NAME = "arbeitsagentur"
SEARCH_TERMS = [
    "Python",
    "Python Developer",
    "Developer",
    "Data Analyst",
    "AI Engineer",
    "Machine Learning",
    "KI",
]

SEARCH_BASE_URL = "https://www.arbeitsagentur.de/jobsuche/suche"
DETAIL_BASE_URL = "https://www.arbeitsagentur.de/jobsuche/jobdetail"


def fetch_jobs():
    """Search Arbeitsagentur and return imported job details."""
    links = collect_links()
    jobs = []

    for url in links:
        try:
            job = fetch_job(url)
            job["source"] = SOURCE_NAME
            jobs.append(job)
            print(f"OK: {url}")
        except Exception as error:
            print(f"FEHLER: {url}")
            print(f"       {error}")

    return jobs


def collect_links():
    """Collect unique detail URLs from all configured Arbeitsagentur searches."""
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
    # Fulda + 30 km bleibt die harte lokale Suche; Remote wird danach bewertet.
    query = {
        "angebotsart": "1",
        "was": term,
        "wo": "Fulda",
        "umkreis": "30",
    }
    return f"{SEARCH_BASE_URL}?{urlencode(query)}"


def fetch_job(url):
    html = fetch_html(url)
    detail = extract_jobdetail(html)

    return {
        "title": detail.get("stellenangebotsTitel", ""),
        "company": detail.get("firma", ""),
        "location": format_locations(detail),
        "remote": format_remote(detail),
        "description": detail.get("stellenangebotsBeschreibung", ""),
        "url": url,
        "external_url": detail.get("externeURL", ""),
        "source": SOURCE_NAME,
    }


def fetch_html(url):
    request = Request(url, headers={"User-Agent": "job-agent/0.1"})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8")


def extract_ng_state(html):
    # Arbeitsagentur liefert Such- und Detaildaten als Angular-SSR-State aus.
    match = re.search(
        r'<script id="ng-state" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise ValueError("ng-state JSON nicht gefunden")
    return json.loads(unescape(match.group(1)))


def extract_jobdetail(html):
    detail = extract_ng_state(html).get("jobdetail")
    if not detail:
        raise ValueError("jobdetail im ng-state JSON nicht gefunden")
    return detail


def format_locations(detail):
    locations = []
    for location in detail.get("stellenlokationen", []):
        address = location.get("adresse", {})
        city = address.get("ort")
        if city and city not in locations:
            locations.append(city)

    return ", ".join(locations) or "unbekannt"


def format_remote(detail):
    if not detail.get("homeofficemoeglich"):
        return "0%"

    remote_type = detail.get("homeofficetyp", "")
    if remote_type == "AUSSCHLIESSLICH":
        return "100%"
    if remote_type == "NACH_VEREINBARUNG":
        return "homeoffice"
    return "homeoffice"
