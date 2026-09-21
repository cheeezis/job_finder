"""Established ranking weights, separate from role recognition and user facts.

Restored from 201417f after comparison with saved review decisions. Search
locations and salary preferences continue to come from local user settings.
"""

SCORE_LIMITS = {
    "role": 30,
    "skills": 25,
    "experience": 25,
    "location": 15,
    "profile": 5,
}

ROLE_POINTS = {
    "python_ai_data": 30,
    "ai_business_analysis": 24,
    "junior_sap": 14,
    "software_development": 27,
    "testing": 23,
    "technical_consulting": 23,
    "infrastructure_automation": 20,
    "rpa_automation": 14,
    "junior_modern_workplace": 14,
    "junior_requirements": 14,
    "junior_administration": 12,
    "infrastructure": 20,
    "trainee": 23,
    "general_it": 10,
}

SKILL_GROUPS = [
    {
        "id": "python",
        "label": "Python",
        "points": 10,
        "keywords": ["python"],
    },
    {
        "id": "ai_ml",
        "label": "AI/ML/RAG/Agenten",
        "points": 10,
        "keywords": [
            "ai",
            "ki",
            "ml",
            "artificial intelligence",
            "machine learning",
            "llm",
            "large language model",
            "rag",
            "agentic",
            "ki-agent",
            "ki agent",
            "ai agent",
        ],
    },
    {
        "id": "data",
        "label": "Data/Analytics",
        "points": 7,
        "keywords": [
            "data analyst",
            "data analytics",
            "data engineer",
            "data science",
            "datenanalyse",
            "datenanalyst",
            "business intelligence",
            "zeitreihe",
            "time series",
        ],
    },
    {
        "id": "testing",
        "label": "Testautomatisierung",
        "points": 6,
        "keywords": [
            "test automation",
            "testautomatisierung",
            "playwright",
            "jest",
            "mocha",
            "chai",
            "unit-test",
            "unit test",
            "api-test",
            "api test",
            "end-to-end",
            "e2e",
        ],
    },
    {
        "id": "javascript",
        "label": "JavaScript/TypeScript/Node.js",
        "points": 5,
        "keywords": ["javascript", "typescript", "node.js", "nodejs"],
    },
    {
        "id": "java",
        "label": "Java",
        "points": 4,
        "keywords": ["java"],
    },
    {
        "id": "devops",
        "label": "DevOps/Cloud-Automatisierung",
        "points": 5,
        "keywords": [
            "ci/cd",
            "continuous integration",
            "docker",
            "kubernetes",
            "terraform",
            "infrastructure as code",
            "ansible",
            "deployment",
            "automatisierung",
            "automation",
        ],
    },
    {
        "id": "security",
        "label": "Security/Network",
        "points": 4,
        "keywords": [
            "cybersecurity",
            "cyber security",
            "it-security",
            "informationssicherheit",
            "network security",
            "firewall",
        ],
    },
    {
        "id": "web_api",
        "label": "Web/API",
        "points": 3,
        "keywords": [
            "rest api",
            "rest-api",
            "backend",
            "webanwendung",
            "web application",
        ],
    },
]
