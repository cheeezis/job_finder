"""Central search settings for all source adapters.

Keep source-specific limits here so experiments with broader/narrower searches
do not require edits across multiple scraper modules.
"""

SEARCH_TERMS = [
    "Python",
    "Python Developer",
    "Developer",
    "Data Analyst",
    "AI Engineer",
    "Machine Learning",
    "KI",
]

STEPSTONE_SEARCH_TERMS = [
    "Python Developer",
    "Developer",
    "Data Analyst",
    "AI Engineer",
    "Machine Learning",
]

SEARCH_LOCATIONS = [
    "Fulda",
    "Remote",
]

FULDA_SEARCH_LOCATION = "Fulda"
FULDA_SEARCH_RADIUS_KM = 30

STEPSTONE_MAX_LINKS_PER_SEARCH = 5
STEPSTONE_MAX_TOTAL_LINKS = 25

GET_IN_IT_MAX_LINKS = 25
