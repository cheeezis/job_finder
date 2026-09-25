"""Central search settings for all source adapters."""

from job_finder.matching.user_settings import USER_SETTINGS

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

LOCAL_SEARCH_LOCATION = USER_SETTINGS["search"]["local_location"]
LOCAL_SEARCH_POSTAL_CODE = USER_SETTINGS["search"]["local_postal_code"]
LOCAL_SEARCH_RADIUS_KM = USER_SETTINGS["search"]["local_radius_km"]
STUDYSMARTER_LOCAL_SEARCH_LOCATION = USER_SETTINGS["matching"]["preferred_location_label"]

SEARCH_LOCATIONS = [LOCAL_SEARCH_LOCATION, "Remote"]

STEPSTONE_SEARCH_LOCATIONS = [LOCAL_SEARCH_POSTAL_CODE, "Remote"]

COMMUTER_SEARCH_LOCATIONS = [
    item["search_location"] for item in USER_SETTINGS["matching"].get("commuter_locations", [])
]
COMMUTER_SEARCH_TERMS = [
    "Junior IT",
    "Junior Softwareentwickler",
    "Berufseinsteiger IT",
    "Trainee IT",
]
COMMUTER_SEARCH_RADIUS_KM = 10
