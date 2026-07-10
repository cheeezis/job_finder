"""Personal matching profile used by the local scoring rules.

This is intentionally separate from the scraper code: sources collect jobs,
while this file describes what makes a job interesting for this profile.
"""

POINTS = {
    "strong_title": 40,
    "close_title": 25,
    "full_remote": 30,
    "fulda_area": 20,
    "fulda_hybrid_bonus": 10,
    "frankfurt_80_remote": 20,
    "python": 15,
    "ai_ml": 15,
    "typescript_node": 10,
    "testing": 8,
    "data_analytics": 10,
    "many_years_experience_penalty": 30,
}

# Laufender Lernschwerpunkt: IBM RAG and Agentic AI Professional Certificate.
AI_ML_KEYWORDS = [
    "ai",
    "artificial intelligence",
    "machine learning",
    "ml",
    "llm",
    "rag",
    "agentic",
    "agenten",
]

PROFILE_SKILL_GROUPS = [
    {
        "points": POINTS["python"],
        "label": "Python gefunden",
        "keywords": ["python"],
    },
    {
        "points": POINTS["ai_ml"],
        "label": "AI/Machine Learning gefunden",
        "keywords": AI_ML_KEYWORDS,
    },
    {
        "points": POINTS["typescript_node"],
        "label": "TypeScript/Node.js gefunden",
        "keywords": ["typescript", "javascript", "node", "node.js"],
    },
    {
        "points": POINTS["testing"],
        "label": "Testing-Erfahrung passt",
        "keywords": ["playwright", "jest", "mocha", "chai", "unit-test", "unit-tests", "e2e", "end-to-end"],
    },
    {
        "points": POINTS["data_analytics"],
        "label": "Data/Analytics gefunden",
        "keywords": ["data", "daten", "analytics", "analyse", "analyst"],
    },
]

# Titel mit diesen Begriffen passen grundsaetzlich nicht zu deiner Suche.
BLOCKED_TITLE_WORDS = [
    "senior",
    "lead",
    "principal",
    "head",
    "manager",
    "werkstudent",
    "working student",
    "praktikum",
    "praktikant",
    "praktikanten",
    "thesis",
    "abschlussarbeit",
    "bachelorarbeit",
]

STRONG_TITLE_PATTERNS = [
    ["junior", "python", "dev"],
    ["junior", "python", "developer"],
    ["junior", "data", "analyst"],
    ["ai", "engineer", "junior"],
    ["junior", "ai", "engineer"],
    ["junior", "ai", "developer"],
    ["junior", "softwareentwickler", "python"],
    ["junior", "software", "developer"],
    ["junior", "backend", "developer"],
]

# Close matches are allowed because many entry-level roles avoid the word
# "Junior" even when the requirements are realistic for a graduate.
CLOSE_TITLE_PATTERNS = [
    ["python", "developer"],
    ["python", "dev"],
    ["data", "analyst"],
    ["data", "engineer"],
    ["machine learning", "engineer"],
    ["ml", "engineer"],
    ["ai", "engineer"],
    ["ai", "developer"],
    ["backend", "python"],
    ["backend", "developer"],
    ["software", "developer"],
    ["softwareentwickler"],
    ["anwendungsentwickler"],
    ["fullstack", "developer"],
    ["fullstack", "entwickler"],
]

# Local places that are realistic without moving away from the Fulda area.
NEARBY_PLACES = [
    "fulda",
    "kuenzell",
    "kunzell",
    "eichenzell",
    "petersberg",
    "huenfeld",
    "hunfeld",
    "neuhof",
    "flieden",
    "burghaun",
    "schluechtern",
    "schluchtern",
    "bad hersfeld",
]

REMOTE_LOCATION_WORDS = [
    "remote",
    "home-office",
    "homeoffice",
    "home office",
]

OPTIONAL_EXPERIENCE_PHRASES = [
    "kein muss",
    "nicht erforderlich",
    "nicht notwendig",
    "keine berufserfahrung",
    "keine erfahrung",
]
