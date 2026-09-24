"""Extraction of explicit annual salary amounts."""

import re


def extract_annual_salary(text):
    """Extract explicit annual salary ranges without guessing from unrelated numbers."""
    number = r"(?:\d{2,3}(?:[.\s]\d{3})|\d{5,6}|\d{2,3}\s*k)"
    range_pattern = rf"({number})\s*(?:-|\u2013|bis|to)\s*({number})\s*(?:eur|euro|\u20ac)"
    ranges = re.findall(range_pattern, text)
    if ranges:
        values = [(salary_number(low), salary_number(high)) for low, high in ranges]
        plausible = [
            (low, high) for low, high in values if valid_salary(low) and valid_salary(high)
        ]
        if plausible:
            return max(plausible, key=lambda item: item[1])

    salary_context_patterns = [
        rf"(?:jahresgehalt|gehalt|salary|verguetung)[^.!\n]{{0,40}}({number})\s*(?:eur|euro|\u20ac)?",
        rf"({number})\s*(?:eur|euro|\u20ac)\s*(?:brutto\s*)?(?:pro jahr|im jahr|jaehrlich|p\.a\.)",
    ]
    values = []
    for pattern in salary_context_patterns:
        values.extend(salary_number(value) for value in re.findall(pattern, text))

    plausible = [value for value in values if valid_salary(value)]
    if plausible:
        value = max(plausible)
        return value, value
    return None


def salary_number(value):
    """Parse an integer salary with grouping separators or a trailing k."""
    cleaned = str(value).lower().replace(".", "").replace(" ", "")
    if cleaned.endswith("k"):
        return int(cleaned[:-1]) * 1000
    return int(cleaned)


def valid_salary(value):
    """Check whether an annual salary falls between 20,000 and 200,000 EUR."""
    return 20_000 <= value <= 200_000
