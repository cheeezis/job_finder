"""Shared helpers for source search plans."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchQuery:
    """One configured combination of a search term and location."""

    term: str
    location: str


def iter_search_queries(terms, locations):
    """Yield all term/location combinations used by source adapters."""
    for term in terms:
        for location in locations:
            yield SearchQuery(term=term, location=location)


def append_unique(value, values, seen):
    """Append value once and return whether it was added."""
    if value in seen:
        return False

    seen.add(value)
    values.append(value)
    return True


def unique_in_order(values):
    """Return unique values without changing their first-seen order."""
    result = []
    seen = set()

    for value in values:
        append_unique(value, result, seen)

    return result
