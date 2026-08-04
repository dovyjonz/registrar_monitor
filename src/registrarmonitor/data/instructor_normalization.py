"""Shared instructor normalization helpers."""

import html
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

TBA_INSTRUCTOR_VALUES = {"TBA", "TBA TBA", "TBA1 TBA1"}
_BR_TAG_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_INSTRUCTOR_SEPARATOR_RE = re.compile(r"\s*(?:[,;&]|\band\b)\s*", re.IGNORECASE)
_NAME_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Titles are presentation metadata, not part of an instructor's identity.
_NAME_PREFIXES = {
    "dr",
    "miss",
    "mr",
    "mrs",
    "ms",
    "prof",
}
_NAME_SUFFIXES = {"esq", "ii", "iii", "iv", "jr", "sr", "v"}
_PLACEHOLDER_TOKENS = {"tba", "tba1"}
_NAME_PARTICLES = {
    "al",
    "bin",
    "da",
    "de",
    "del",
    "der",
    "di",
    "ibn",
    "la",
    "le",
    "saint",
    "st",
    "van",
    "von",
}


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


def _name_tokens(value: str) -> tuple[str, ...]:
    """Return comparison tokens with typography and titles removed."""
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return tuple(
        token
        for token in _NAME_TOKEN_RE.findall(without_marks)
        if token not in _NAME_PREFIXES
    )


def _split_instructor_parts(value: str) -> list[str]:
    """Split a display value into its comma-like instructor/name parts."""
    cleaned = clean_instructor_text(value)
    return [part.strip() for part in _INSTRUCTOR_SEPARATOR_RE.split(cleaned) if part]


def _looks_like_last_first_pair(
    first: tuple[str, ...], second: tuple[str, ...]
) -> bool:
    """Identify the registrar's ``Last, First [Middle]`` spelling."""
    if not first or not second:
        return False
    if len(first) == len(second) == 1:
        return True
    if any(token in _NAME_SUFFIXES for token in first):
        return True
    if len(first) == 1 and len(second) > 1:
        return True
    if any(token in _NAME_PARTICLES for token in first):
        return True
    return False


def _person_identities(value: str | None) -> list[str]:
    """Return stable identities while retaining ordinary instructor boundaries."""
    parts = _split_instructor_parts(value or "")
    token_parts = [_name_tokens(part) for part in parts]
    token_parts = [
        tokens
        for tokens in token_parts
        if tokens and not set(tokens).issubset(_PLACEHOLDER_TOKENS)
    ]
    if not token_parts:
        return []

    merged_parts: list[tuple[str, ...]] = []
    for tokens in token_parts:
        if len(tokens) == 1 and tokens[0] in _NAME_SUFFIXES and merged_parts:
            merged_parts[-1] += tokens
        else:
            merged_parts.append(tokens)
    token_parts = merged_parts

    # Some registrar exports spell each instructor as ``Last, First`` and
    # join multiple instructors as ``Last, First, Last, First``. Only pair
    # parts when every adjacent pair has that shape; ordinary ``First Last,
    # First Last`` values therefore keep their instructor boundaries.
    if len(token_parts) > 1 and len(token_parts) % 2 == 0:
        pairs = list(zip(token_parts[::2], token_parts[1::2], strict=True))
        if all(_looks_like_last_first_pair(first, second) for first, second in pairs):
            token_parts = [first + second for first, second in pairs]

    return [" ".join(sorted(tokens)) for tokens in token_parts]


def instructor_identity(value: str | None) -> str:
    """Return a comparison key insensitive to markup and name ordering.

    Registrar exports alternate between ``Last, First`` and ``First Last``
    forms, and some exports wrap names in HTML. The words, rather than their
    presentation order, identify the instructor assignment. Punctuation and
    case are presentation details; added or removed name words remain real
    changes.
    """
    identities = _person_identities(value)
    return "|".join(sorted(identities))


def normalize_instructors(raw_instructors: Iterable[Any]) -> str:
    """Return a stable comma-separated instructor string for raw row values."""
    names: list[str] = []
    identities: set[str] = set()
    for raw in raw_instructors:
        if not isinstance(raw, str):
            continue
        for part in _split_instructor_parts(raw):
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
