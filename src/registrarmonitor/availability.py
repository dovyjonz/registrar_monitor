"""Shared current-course registration availability semantics."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, TypedDict

SECTION_TYPE_NAMES = {
    "L": "Lecture",
    "S": "Seminar",
    "R": "Recitation",
    "D": "Discussion",
    "B": "Lab",
    "Lb": "Lab",
    "Int": "Internship",
    "P": "Project",
    "IS": "Independent Study",
    "T": "Tutorial",
}


class _Totals(TypedDict):
    enrollment: int
    capacity: int
    available: int
    openSections: int
    sectionCount: int


class _TypeTotals(_Totals):
    type: str


def normalize_section_type(value: object) -> str:
    """Return the canonical public name for a registrar section type."""
    text = re.sub(r"\s+", " ", str(value or "Other").strip())
    return SECTION_TYPE_NAMES.get(text, text) or "Other"


def _plural_type(value: str, count: int) -> str:
    if count == 1 or value.endswith("s"):
        return value
    return f"{value}s"


def calculate_availability(sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Calculate registration places across all required section types."""
    totals: dict[str, _Totals] = defaultdict(
        lambda: {
            "enrollment": 0,
            "capacity": 0,
            "available": 0,
            "openSections": 0,
            "sectionCount": 0,
        }
    )
    for section in sections.values():
        section_type = normalize_section_type(section.get("type", section.get("t")))
        enrollment = int(section.get("currentEnrollment", section.get("ce", 0)) or 0)
        capacity = int(section.get("currentCapacity", section.get("cc", 0)) or 0)
        remaining = max(capacity - enrollment, 0)
        values = totals[section_type]
        values["enrollment"] += enrollment
        values["capacity"] += capacity
        values["available"] += remaining
        values["sectionCount"] += 1
        values["openSections"] += int(remaining > 0)

    ordered: list[_TypeTotals] = [
        {"type": section_type, **values}
        for section_type, values in sorted(totals.items(), key=lambda item: item[0])
    ]
    if not ordered:
        return {
            "kind": "seats",
            "available": 0,
            "limitingTypes": [],
            "types": [],
            "sentence": "No current sections.",
            "breakdown": "",
            "status": "empty",
            "compact": "NO SECTIONS",
        }

    available = min(item["available"] for item in ordered)
    limiting = [item["type"] for item in ordered if item["available"] == available]
    if len(ordered) == 1:
        item = ordered[0]
        noun = "seat" if available == 1 else "seats"
        sentence = (
            f"{available} {noun} open; "
            f"{item['enrollment']}/{item['capacity']} enrolled."
        )
        kind = "seats"
        status = "full" if available == 0 else "open"
        compact = "FULL" if available == 0 else "OPEN"
    else:
        noun = "place" if available == 1 else "places"
        limiting_text = " and ".join(value.lower() for value in limiting)
        if available == 0:
            if len(limiting) == 1:
                subject = f"all {limiting[0]} sections are"
                compact = f"{limiting[0].upper()} FULL"
            elif len(limiting) == 2:
                subject = f"all {limiting[0]} and {limiting[1]} sections are"
                compact = f"{limiting[0].upper()} + {limiting[1].upper()} FULL"
            else:
                subject = f"all required {'/'.join(limiting)} sections are"
                compact = "MULTIPLE TYPES FULL"
            sentence = f"No registration places — {subject} full."
            status = "required-type-full"
        else:
            sentence = (
                f"{available} registration {noun} available. "
                f"Limited by {limiting_text}."
            )
            status = "open"
            compact = "OPEN"
        kind = "registration-places"

    breakdown = ", ".join(
        f"{_plural_type(item['type'], item['sectionCount'])} "
        f"{item['openSections']}/{item['sectionCount']} open"
        for item in ordered
    )
    if breakdown:
        breakdown += "."
    return {
        "kind": kind,
        "available": available,
        "limitingTypes": limiting,
        "types": ordered,
        "sentence": sentence,
        "breakdown": breakdown,
        "status": status,
        "compact": compact,
    }
