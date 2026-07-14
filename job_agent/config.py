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

LOCAL_SEARCH_LOCATION = "Exampletown"
LOCAL_SEARCH_RADIUS_KM = 30

SEARCH_LOCATIONS = [
    LOCAL_SEARCH_LOCATION,
    "Remote",
]

STEPSTONE_SEARCH_LOCATIONS = [
    "12345",
    "Remote",
]
STEPSTONE_SEARCH_RADIUS_KM = LOCAL_SEARCH_RADIUS_KM

GET_IN_IT_SEARCH_TERMS = SEARCH_TERMS
GET_IN_IT_SEARCH_LOCATIONS = SEARCH_LOCATIONS
