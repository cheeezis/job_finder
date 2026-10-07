"""Central search settings for all source adapters.

The terms below are the defaults; the personal settings may replace them
(search.terms, search.stepstone_terms, search.commuter_terms). Everything
that depends on the settings is a function, read when a search runs.
"""

from job_finder.matching.user_settings import current_settings

SEARCH_TERMS = [
    "Junior Python Developer",
    "Python",
    "Python Developer",
    "Junior Software Developer",
    "Developer",
    "Junior Data Analyst",
    "Data Analyst",
    "Data Engineer",
    "KI Business Analyst",
    "Junior AI Engineer",
    "AI Engineer",
    "AI Developer",
    "Machine Learning Engineer",
    "Backend Developer",
    "Fullstack Developer",
    "Frontend Developer",
    "Web Developer",
    "DevOps Engineer",
    "Automation Engineer",
    "RPA Developer",
    "Site Reliability Engineer",
    "Cloud Engineer",
    "Security Engineer",
    "Network Engineer",
    "Software Architect",
    "IT Consultant",
    "Junior Requirements Engineer",
    "Microsoft 365 Junior Consultant",
    "Junior SAP Consultant",
    "Trainee SAP",
    "Software Test Engineer",
    "Test Automation Engineer",
    "QA Engineer",
    "Java Developer",
    "JavaScript Developer",
    "TypeScript Developer",
    "Trainee IT",
    "Softwareentwickler Python",
    "KI Entwickler",
    "KI",
]

# StepStone's text search produces heavily overlapping result sets. These
# broader role families cover the profile without requesting every synonym.
STEPSTONE_SEARCH_TERMS = [
    "Junior Software Developer",
    "Python Developer",
    "Data Engineer",
    "Data Analyst",
    "AI Engineer",
    "Machine Learning Engineer",
    "DevOps Engineer",
    "Cloud Engineer",
    "Security Engineer",
    "Network Engineer",
    "IT Consultant",
    "Automation Engineer",
    "Junior Requirements Engineer",
    "Software Test Engineer",
    "Junior SAP Consultant",
    "Microsoft 365 Junior Consultant",
]

COMMUTER_SEARCH_TERMS = ["Junior IT", "Junior Softwareentwickler", "Berufseinsteiger IT", "Trainee IT"]
COMMUTER_SEARCH_RADIUS_KM = 10


def search_terms():
    return current_settings().search.terms or SEARCH_TERMS


def stepstone_search_terms():
    return current_settings().search.stepstone_terms or STEPSTONE_SEARCH_TERMS


def commuter_search_terms():
    return current_settings().search.commuter_terms or COMMUTER_SEARCH_TERMS


def local_search_location():
    return current_settings().search.local_location


def local_search_postal_code():
    return current_settings().search.local_postal_code


def local_search_radius_km():
    return current_settings().search.local_radius_km


def studysmarter_search_location():
    return current_settings().matching.preferred_location_label


def search_locations():
    return [local_search_location(), "Remote"]


def stepstone_search_locations():
    return [local_search_postal_code(), "Remote"]


def commuter_search_locations():
    return [item.search_location for item in current_settings().matching.commuter_locations]


def route_origin():
    """Name the home location the review's route links start from."""
    return f"{local_search_postal_code()} {local_search_location()}".strip()
