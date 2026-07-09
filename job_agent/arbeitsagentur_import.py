import json
import re
import sys
from html import unescape
from pathlib import Path
from urllib.request import Request, urlopen


DEFAULT_LINKS_FILE = "data/job_links.txt"
DEFAULT_OUTPUT_FILE = "data/jobs_imported.json"


def main():
    links_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LINKS_FILE
    output_file = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT_FILE

    links = load_links(links_file)
    jobs = []

    for url in links:
        try:
            jobs.append(fetch_job(url))
            print(f"OK: {url}")
        except Exception as error:
            print(f"FEHLER: {url}")
            print(f"       {error}")

    Path(output_file).write_text(
        json.dumps(jobs, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n{len(jobs)} Job(s) gespeichert in {output_file}")


def load_links(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


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
        "source": "arbeitsagentur",
    }


def fetch_html(url):
    # User-Agent hilft, weil einige Server sehr nackte Python-Requests ablehnen.
    request = Request(url, headers={"User-Agent": "job-agent/0.1"})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8")


def extract_jobdetail(html):
    # Die Arbeitsagentur rendert die Detaildaten als JSON im Angular-SSR-State.
    match = re.search(
        r'<script id="ng-state" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise ValueError("ng-state JSON nicht gefunden")

    state = json.loads(unescape(match.group(1)))
    detail = state.get("jobdetail")
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


if __name__ == "__main__":
    main()
