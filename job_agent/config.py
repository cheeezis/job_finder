"""Central search settings for all source adapters.

Keep source-specific limits here so experiments with broader/narrower searches
do not require edits across multiple scraper modules.
"""

SEARCH_TERMS = [
    "Junior Python Developer",
    "Python",
    "Python Developer",
    "Junior Software Developer",
    "Developer",
    "Junior Data Analyst",
    "Data Analyst",
    "Junior AI Engineer",
    "AI Engineer",
    "AI Developer",
    "Machine Learning Engineer",
    "Backend Developer",
    "Fullstack Developer",
    "DevOps Engineer",
    "Softwareentwickler Python",
    "KI Entwickler",
    "KI",
]

STEPSTONE_SEARCH_TERMS = [
    "Junior Python Developer",
    "Python Developer",
    "Junior Software Developer",
    "Developer",
    "Junior Data Analyst",
    "Data Analyst",
    "Junior AI Engineer",
    "AI Engineer",
    "AI Developer",
    "Machine Learning Engineer",
    "Backend Developer",
    "Fullstack Developer",
    "DevOps Engineer",
    "Softwareentwickler Python",
    "KI Entwickler",
]

SEARCH_LOCATIONS = [
    "Fulda",
    "Remote",
]

FULDA_SEARCH_LOCATION = "Fulda"
FULDA_SEARCH_RADIUS_KM = 30

STEPSTONE_MAX_LINKS_PER_SEARCH = 50
STEPSTONE_MAX_TOTAL_LINKS = 1000

GET_IN_IT_SEARCH_TERMS = STEPSTONE_SEARCH_TERMS
GET_IN_IT_SEARCH_LOCATIONS = SEARCH_LOCATIONS
GET_IN_IT_MAX_LINKS_PER_SEARCH = 50
GET_IN_IT_MAX_LINKS = 1000
