"""Central search settings for all source adapters."""

SEARCH_TERMS = [
    "Junior Python Developer",
    "Python",
    "Python Developer",
    "Junior Software Developer",
    "Developer",
    "Junior Data Analyst",
    "Data Analyst",
    "Data Engineer",
    "Junior AI Engineer",
    "AI Engineer",
    "AI Developer",
    "Machine Learning Engineer",
    "Backend Developer",
    "Fullstack Developer",
    "Frontend Developer",
    "Web Developer",
    "DevOps Engineer",
    "Site Reliability Engineer",
    "Cloud Engineer",
    "Security Engineer",
    "Network Engineer",
    "Software Architect",
    "IT Consultant",
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

# Standalone terms are useful for Arbeitsagentur, but too broad on StepStone.
STEPSTONE_BROAD_TERMS = {"Python", "KI"}
STEPSTONE_SEARCH_TERMS = [
    term for term in SEARCH_TERMS if term not in STEPSTONE_BROAD_TERMS
]

LOCAL_SEARCH_LOCATION = "Exampletown"
LOCAL_SEARCH_RADIUS_KM = 30

SEARCH_LOCATIONS = [
    LOCAL_SEARCH_LOCATION,
    "Remote",
]

GET_IN_IT_SEARCH_TERMS = STEPSTONE_SEARCH_TERMS
GET_IN_IT_SEARCH_LOCATIONS = SEARCH_LOCATIONS
