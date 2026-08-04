"""Shared instructor normalization helpers."""

import html
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

TBA_INSTRUCTOR_VALUES = {"TBA", "TBA TBA", "TBA1 TBA1"}
_BR_TAG_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_NAME_TOKEN_RE = re.compile(r"[^\W_]+(?:[\u0027\u2019\-][^\W_]+)*", re.UNICODE)


def clean_instructor_text(value: Any) -> str:
    """Remove presentation markup while retaining the instructor text."""
    if not isinstance(value, str):
        return ""

    cleaned = html.unescape(value)
    cleaned = _BR_TAG_RE.sub(",", cleaned)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned).replace("\xa0", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    return cleaned.strip(" ,")


def instructor_identity(value: str | None) -> str:
    """Return a comparison key insensitive to markup and name ordering.

    Registrar exports alternate between ``Last, First`` and ``First Last``
    forms, and some exports wrap names in HTML. The words, rather than their
    presentation order, identify the instructor assignment. Punctuation and
    case are presentation details; added or removed name words remain real
    changes.
    """
    cleaned = unicodedata.normalize("NFKC", clean_instructor_text(value)).casefold()
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    part_tokens = [tuple(_NAME_TOKEN_RE.findall(part)) for part in parts]
    part_tokens = [tuple(sorted(tokens)) for tokens in part_tokens if tokens]
    if not part_tokens:
        return ""

    # A two-token comma pair is the registrar's ``Last, First`` spelling for
    # one person. Preserve boundaries for multi-instructor values so swapping
    # surnames between instructors remains a real assignment change.
    if len(part_tokens) == 2 and all(len(tokens) == 1 for tokens in part_tokens):
        return " ".join(sorted(token for tokens in part_tokens for token in tokens))
    return "|".join(sorted(" ".join(tokens) for tokens in part_tokens))


def normalize_instructors(raw_instructors: Iterable[Any]) -> str:
    """Return a stable comma-separated instructor string for raw row values."""
    names: list[str] = []
    identities: set[str] = set()
    for raw in raw_instructors:
        if not isinstance(raw, str):
            continue
        for part in clean_instructor_text(raw).split(","):
            name = part.strip()
            if not name or name.upper() in TBA_INSTRUCTOR_VALUES:
                continue
            identity = instructor_identity(name)
            if identity and identity not in identities:
                identities.add(identity)
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
