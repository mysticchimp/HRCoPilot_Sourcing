"""LinkedIn ID maps — ported from contra6_source2.py. Do not invent IDs."""

# seniorityLevelIds / yearsOfExperienceIds are string[] — pass strings.
SENIORITY_MAP = {
    "in training": "100",
    "entry level": "110",
    "senior": "120",
    "strategic": "130",
    "entry level manager": "200",
    "experienced manager": "210",
    "director": "220",
    "vice president": "300",
    "cxo": "310",
    "owner / partner": "320",
    "owner": "320",
    "partner": "320",
}

YEARS_MAP = {
    "less than 1 year": "1",
    "1 to 2 years": "2",
    "3 to 5 years": "3",
    "6 to 10 years": "4",
    "more than 10 years": "5",
}

# LinkedIn canonical function IDs (same taxonomy as the CLI script).
FUNCTION_MAP = {
    "accounting": "1",
    "administrative": "2",
    "arts and design": "3",
    "business development": "4",
    "community and social services": "5",
    "consulting": "6",
    "education": "7",
    "engineering": "8",
    "entrepreneurship": "9",
    "finance": "10",
    "healthcare services": "11",
    "human resources": "12",
    "information technology": "13",
    "legal": "14",
    "marketing": "15",
    "media and communications": "16",
    "military and protective services": "17",
    "operations": "18",
    "product management": "19",
    "program and project management": "20",
    "purchasing": "21",
    "quality assurance": "22",
    "real estate": "23",
    "research": "24",
    "sales": "25",
    "support": "26",
}

DEFAULT_LOCATION = "United Arab Emirates"

# Functions where leaving the anchor blank floods the pool.
BROAD_FUNCTIONS = {
    "human resources",
    "sales",
    "finance",
    "marketing",
    "administrative",
}

# Display labels (title-cased) for closed-set validation prompts.
FUNCTION_LABELS = sorted({k.title() for k in FUNCTION_MAP})
YEARS_LABELS = [
    "Less than 1 year",
    "1 to 2 years",
    "3 to 5 years",
    "6 to 10 years",
    "More than 10 years",
]
