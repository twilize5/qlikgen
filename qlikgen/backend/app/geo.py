"""
Name-based geographic field detection.

A Qlik map choropleth needs a dimension Qlik can resolve to named areas
(countries, states, counties, postal areas, cities). We can't know that from
DuckDB types, so we infer it from the column name — the same heuristic a person
uses. Used by the plan validator (a map must sit on a geo field) and fed to the
LLM planner as a hint. Deliberately conservative: better to miss a geo field
(map falls back) than to build a choropleth on "Segment".
"""
from __future__ import annotations

import re

# Substrings that indicate a named-area geo field, matched case-insensitively on
# word boundaries so "Category" doesn't match "cat" etc.
_AREA_PATTERNS = [
    r"country",
    r"state",
    r"province",
    r"county",
    r"city",
    r"town",
    r"postal",
    r"post ?code",
    r"\bzip\b",
]
_POINT_PATTERNS = [r"latitude", r"longitude", r"\blat\b", r"\blong\b", r"\blng\b"]

_AREA_RE = re.compile("|".join(_AREA_PATTERNS), re.IGNORECASE)
_POINT_RE = re.compile("|".join(_POINT_PATTERNS), re.IGNORECASE)


def geo_kind(name: str) -> str | None:
    """Return "area", "point", or None for a column name."""
    if _POINT_RE.search(name or ""):
        return "point"
    if _AREA_RE.search(name or ""):
        return "area"
    return None


def is_area_field(name: str) -> bool:
    return geo_kind(name) == "area"


def area_fields(columns) -> list[str]:
    """Names of columns that look like named-area geo fields, in column order."""
    return [c.name for c in columns if is_area_field(c.name)]
