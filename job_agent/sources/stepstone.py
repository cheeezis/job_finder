import json
import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen


SOURCE_NAME = "stepstone"
LINKS_FILE = "data/stepstone_links.txt"
SEARCH_BASE_URL = "https://www.stepstone.de/jobs"
SEARCH_TERMS = [
    "Python Developer",
    "Data Analyst",
    "AI Engineer",
    "Machine Learning",
]
SEARCH_LOCATIONS = [
    "Fulda",
    "Remote",
]
MAX_LINKS_PER_SEARCH = 5
MAX_TOTAL_SEARCH_LINKS = 25


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self):
        return " ".join(self.parts)


def fetch_jobs():
    links = collect_links()
    if not links:
        print("Keine StepStone-Links gefunden")
        return []

    jobs = []
    for url in links:
        try:
            jobs.append(fetch_job(url))
            print(f"OK: {url}")
        except Exception as error:
            print(f"FEHLER: {url}")
            print(f"       {error}")
    return jobs


def collect_links():
    links = []
    seen = set()

    for url in load_links(LINKS_FILE):
        add_link(url, links, seen)

    for url in search_links():
        add_link(url, links, seen)

    return links


def load_links(path):
    links_path = Path(path)
    if not links_path.exists():
        return []

    lines = links_path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def search_links():
    links = []
    seen = set()

    for term in SEARCH_TERMS:
        for location in SEARCH_LOCATIONS:
            search_url = build_search_url(term, location)
            print(f"Suche StepStone: {term} / {location}")
            try:
                html = fetch_html(search_url)
            except Exception as error:
                print(f"  FEHLER Suche: {error}")
                continue

            found_links = extract_detail_links(html)[:MAX_LINKS_PER_SEARCH]
            print(f"  {len(found_links)} Link(s)")
            for url in found_links:
                add_link(url, links, seen)
                if len(links) >= MAX_TOTAL_SEARCH_LINKS:
                    return links

    return links


def build_search_url(term, location):
    return f"{SEARCH_BASE_URL}/{quote(term.replace(' ', '-'))}/in-{quote(location)}"


def extract_detail_links(html):
    matches = re.findall(
        r'https://www\.stepstone\.de/stellenangebote--[^"\'<> ]+?\.html[^"\'<> ]*'
        r'|/stellenangebote--[^"\'<> ]+?\.html[^"\'<> ]*',
        html,
    )
    links = []
    seen = set()

    for match in matches:
        url = urljoin("https://www.stepstone.de", unescape(match))
        if url in seen:
            continue
        seen.add(url)
        links.append(url)

    return links


def add_link(url, links, seen):
    if url in seen:
        return
    seen.add(url)
    links.append(url)


def fetch_job(url):
    html = fetch_html(url)
    posting = extract_job_posting(html)
    description = html_to_text(posting.get("description", ""))
    location = format_location(posting.get("jobLocation"))

    return {
        "title": posting.get("title", ""),
        "company": clean_company(posting.get("hiringOrganization", {}).get("name", "")),
        "location": location,
        "remote": detect_remote(description, location),
        "description": description,
        "url": posting.get("url") or url,
        "external_url": url,
        "source": SOURCE_NAME,
    }


def fetch_html(url):
    request = Request(url, headers={"User-Agent": "job-agent/0.1"})
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8")


def extract_job_posting(html):
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    )

    for script in scripts:
        data = json.loads(unescape(script.strip()))
        postings = data if isinstance(data, list) else [data]
        for item in postings:
            if item.get("@type") == "JobPosting":
                return item

    raise ValueError("JobPosting JSON-LD nicht gefunden")


def html_to_text(html):
    parser = TextExtractor()
    parser.feed(html)
    return parser.text()


def clean_company(company):
    return re.sub(r"_20\d{2}-.+$", "", company).strip()


def format_location(job_location):
    locations = job_location if isinstance(job_location, list) else [job_location]
    cities = []

    for location in locations:
        if not location:
            continue
        address = location.get("address", {})
        city = address.get("addressLocality")
        if city and city not in cities:
            cities.append(city)

    return ", ".join(cities) or "unbekannt"


def detect_remote(description, location=""):
    text = f"{description} {location}".lower()
    if "100% remote" in text or "fully remote" in text:
        return "100%"
    if any(word in text for word in ["homeoffice", "home office", "remote", "mobiles arbeiten", "hybrid"]):
        return "homeoffice"
    return "0%"
