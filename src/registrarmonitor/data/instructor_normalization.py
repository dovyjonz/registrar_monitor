"""Shared instructor normalization helpers."""

from collections.abc import Iterable, Mapping
from typing import Any


TBA_INSTRUCTOR_VALUES = {"TBA", "TBA TBA", "TBA1 TBA1"}


def normalize_instructors(raw_instructors: Iterable[Any]) -> str:
    """Return a stable comma-separated instructor string for raw row values."""
    names: list[str] = []
    for raw in raw_instructors:
        if not isinstance(raw, str):
            continue
        for part in raw.split(","):
            name = part.strip()
            if not name or name.upper() in TBA_INSTRUCTOR_VALUES:
                continue
            if name not in names:
                names.append(name)
    return ", ".join(names)


def aggregate_instructors_by_section(
    rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], str]:
    """Aggregate normalized instructors keyed by ``(course_code, section_code)``."""
    raw_by_section: dict[tuple[str, str], list[Any]] = {}
    for row in rows:
        course_code = str(row.get("Course Abbr") or "").strip()
        section_code = str(row.get("S/T") or "").strip()
        if not course_code or not section_code:
            continue
        raw_by_section.setdefault((course_code, section_code), []).append(
            row.get("Instructor")
        )
    return {
        key: normalize_instructors(raw_instructors)
        for key, raw_instructors in raw_by_section.items()
    }
