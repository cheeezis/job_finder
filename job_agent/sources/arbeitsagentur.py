from job_agent.arbeitsagentur_import import fetch_job
from job_agent.arbeitsagentur_search import collect_links


SOURCE_NAME = "arbeitsagentur"


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

