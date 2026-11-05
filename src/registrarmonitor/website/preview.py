"""Deterministic preview-visible publication state."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from registrarmonitor.config import get_timezone
from registrarmonitor.registration import derive_priority_state

from ..availability import calculate_availability
from .config import course_to_slug, semester_to_slug

PREVIEW_SCHEMA_VERSION = 1

# The site Worker refuses to read a preview state above MAX_STATE_BYTES (512_000)
# in workers/site/src/handler.ts, which turns the og:image into a 502. Keep this
# below that cap with headroom; tests/test_website_preview.py asserts the pair.
PREVIEW_STATE_BUDGET_BYTES = 480_000
_STRUCTURAL_EVENTS = frozenset({"section_added", "section_removed"})
_HASH_PLACEHOLDER = "x" * 8


def _registrar_timestamp(value: str | None) -> str | None:
    """Serialize observations with the same timezone carried by milestones."""
    if not value:
        return None
    timestamp = datetime.fromisoformat(value.replace(" ", "T"))
    timezone = get_timezone()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone)
    else:
        timestamp = timestamp.astimezone(timezone)
    return timestamp.isoformat()


def canonical_bytes(value: Any) -> bytes:
    """Serialize a preview document deterministically."""
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _short_hash(value: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_bytes(value)).digest()[:6]
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _course_last_changed(course: dict[str, Any], timestamps: list[str]) -> str | None:
    indices = [
        point.get("timestampIdx")
        for point in course.get("averageHistory", [])
        if isinstance(point.get("timestampIdx"), int)
    ]
    indices.extend(
        point.get("timestampIdx")
        for points in course.get("sectionHistory", {}).values()
        for point in points
        if isinstance(point.get("timestampIdx"), int)
    )
    indices.extend(
        event.get("timestampIdx")
        for event in course.get("events", [])
        if isinstance(event.get("timestampIdx"), int)
    )
    valid = [timestamps[index] for index in indices if 0 <= index < len(timestamps)]
    return max(valid) if valid else None


def _compact_course_timestamps(
    course: dict[str, Any], timestamps: list[str]
) -> tuple[dict[str, Any], list[str]]:
    """Keep only timestamps referenced by this course and remap their indices.

    Only the points that carry an index are copied; everything else in the
    course is shared with the input, which is never mutated.
    """
    history = course.get("averageHistory", [])
    section_history = course.get("sectionHistory", {})
    events = course.get("events", [])
    collections = [history, *section_history.values(), events]
    referenced = sorted(
        {
            point["timestampIdx"]
            for points in collections
            for point in points
            if isinstance(point.get("timestampIdx"), int)
            and 0 <= point["timestampIdx"] < len(timestamps)
        }
    )
    remap = {old: new for new, old in enumerate(referenced)}

    def remapped(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {**point, "timestampIdx": remap[point["timestampIdx"]]}
            if point.get("timestampIdx") in remap
            else dict(point)
            for point in points
        ]

    compact_course = dict(course)
    if "averageHistory" in course:
        compact_course["averageHistory"] = remapped(history)
    if "sectionHistory" in course:
        compact_course["sectionHistory"] = {
            key: remapped(points) for key, points in section_history.items()
        }
    if "events" in course:
        compact_course["events"] = remapped(events)
    return compact_course, [timestamps[index] for index in referenced]


def _history_key(point: dict[str, Any]) -> tuple[Any, ...]:
    return (point.get("fill"), point.get("enrollment"), point.get("capacity"))


def _dedupe_history(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop points repeating the previous value; charts step between changes."""
    kept: list[dict[str, Any]] = []
    for index, point in enumerate(points):
        if 0 < index < len(points) - 1 and _history_key(point) == _history_key(
            points[index - 1]
        ):
            continue
        kept.append(point)
    return kept


def _thin_history(
    points: list[dict[str, Any]], stride: int | None
) -> list[dict[str, Any]]:
    """Keep the ends and capacity-change edges, plus every ``stride``-th point.

    ``stride=None`` keeps only the protected points. Capacity-change markers in
    the chart compare consecutive points, so both edges of a change survive.
    """
    last = len(points) - 1
    kept: list[dict[str, Any]] = []
    for index, point in enumerate(points):
        previous = points[index - 1] if index > 0 else None
        following = points[index + 1] if index < last else None
        protected = (
            index in (0, last)
            or (
                previous is not None
                and previous.get("capacity") != point.get("capacity")
            )
            or (
                following is not None
                and following.get("capacity") != point.get("capacity")
            )
        )
        if protected or (stride is not None and index % stride == 0):
            kept.append(point)
    return kept


def _reduced_courses(course: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield progressively smaller courses; each stays valid for both consumers.

    Both the Worker card and the dashboard's modal fallback (which validates the
    course like a department payload) need every section, the history arrays and
    the events array present, so reductions only shorten lists. Section add or
    remove events are kept as long as possible because they drive the section
    activity timeline.
    """

    def variant(
        *,
        thin: Any = None,
        events: bool | None = None,
        history: bool = True,
    ) -> dict[str, Any]:
        reduced = dict(course)
        if "averageHistory" in course:
            reduced["averageHistory"] = (
                thin(course["averageHistory"]) if history and thin else []
            )
        if "sectionHistory" in course:
            reduced["sectionHistory"] = {
                key: thin(points) if history and thin else []
                for key, points in course["sectionHistory"].items()
            }
        if "events" in course and events is not None:
            reduced["events"] = [
                event
                for event in course["events"]
                if events and event.get("eventType") in _STRUCTURAL_EVENTS
            ]
        return reduced

    yield variant(thin=_dedupe_history)
    yield variant(thin=_dedupe_history, events=True)
    longest = max(
        (len(points) for points in course.get("sectionHistory", {}).values()),
        default=0,
    )
    longest = max(longest, len(course.get("averageHistory", [])))
    stride = 2
    while stride < longest:
        yield variant(
            thin=lambda points, stride=stride: _thin_history(
                _dedupe_history(points), stride
            ),
            events=True,
        )
        stride *= 2
    yield variant(thin=lambda points: _thin_history(points, None), events=True)
    yield variant(history=False, events=False)


def _state_size(state: dict[str, Any]) -> int:
    """Published byte size, counting the eight-character hash field."""
    return len(canonical_bytes({"hash": _HASH_PLACEHOLDER, **state}))


def _assemble_course_state(
    course: dict[str, Any],
    timestamps: list[str],
    *,
    last_changed: str | None,
    semester: str,
    milestones: list[dict[str, str]],
    published_at: str | None,
    archived: bool,
    removed: bool,
) -> dict[str, Any]:
    code = course["code"]
    compact_course, compact_timestamps = _compact_course_timestamps(course, timestamps)
    compact_timestamps = [
        normalized
        for value in compact_timestamps
        if (normalized := _registrar_timestamp(value)) is not None
    ]
    return {
        "schemaVersion": PREVIEW_SCHEMA_VERSION,
        "kind": "course",
        "semester": semester,
        "semesterSlug": semester_to_slug(semester),
        "slug": course_to_slug(code),
        "code": code,
        "title": course.get("title", ""),
        "status": "removed" if removed else "archived" if archived else "current",
        "archived": archived or removed,
        "availability": calculate_availability(course.get("sections", {})),
        "priority": derive_priority_state(
            milestones,
            at=_registrar_timestamp(published_at) or last_changed,
        ),
        "milestones": [
            {
                key: item[key]
                for key in ("time", "label", "color", "priority")
                if key in item
            }
            for item in milestones
        ],
        "lastChanged": last_changed,
        "timestamps": compact_timestamps,
        "course": compact_course,
    }


def build_course_preview_state(
    *,
    semester: str,
    course: dict[str, Any],
    timestamps: list[str],
    milestones: list[dict[str, str]],
    published_at: str | None = None,
    archived: bool = False,
    removed: bool = False,
) -> dict[str, Any]:
    """Build a content-addressed state for metadata, modal fallback, and image.

    The state must stay below ``PREVIEW_STATE_BUDGET_BYTES`` so the Worker can
    render the og:image; an oversized course is reduced deterministically.
    """
    code = course["code"]
    last_changed = _registrar_timestamp(_course_last_changed(course, timestamps))

    def assemble(candidate: dict[str, Any]) -> dict[str, Any]:
        return _assemble_course_state(
            candidate,
            timestamps,
            last_changed=last_changed,
            semester=semester,
            milestones=milestones,
            published_at=published_at,
            archived=archived,
            removed=removed,
        )

    state = assemble(course)
    for reduced in _reduced_courses(course):
        if _state_size(state) <= PREVIEW_STATE_BUDGET_BYTES:
            break
        state = assemble(reduced)
    size = _state_size(state)
    if size > PREVIEW_STATE_BUDGET_BYTES:
        raise ValueError(
            f"preview state for {code} is {size} bytes after reduction; "
            f"budget is {PREVIEW_STATE_BUDGET_BYTES}"
        )
    digest = _short_hash(state)
    return {"hash": digest, **state}


def build_semester_preview_state(
    *,
    summary: dict[str, Any],
    departments: dict[str, dict[str, Any]],
    milestones: list[dict[str, str]],
    archived: bool = False,
) -> dict[str, Any]:
    """Build the state used by semester metadata and preview cards."""
    courses = summary.get("courses", {})
    current_snapshot = summary.get("currentSnapshot") or {}
    updated = _registrar_timestamp(current_snapshot.get("observedAt"))
    open_seats = sum(
        section_type["available"]
        for department in departments.values()
        for course in department.get("courses", {}).values()
        for section_type in calculate_availability(course.get("sections", {}))["types"]
    )
    state = {
        "schemaVersion": PREVIEW_SCHEMA_VERSION,
        "kind": "semester",
        "semester": summary["semester"],
        "semesterSlug": semester_to_slug(summary["semester"]),
        "status": "archived" if archived else "current",
        "archived": archived,
        "courseCount": len(courses),
        "sectionCount": sum(item.get("sectionCount", 0) for item in courses.values()),
        "fullSectionCount": sum(
            item.get("fullSectionCount", 0) for item in courses.values()
        ),
        "openSeats": open_seats,
        "updated": updated,
        "priority": derive_priority_state(milestones, at=updated),
    }
    digest = _short_hash(state)
    return {"hash": digest, **state}


def publish_preview_state(output_root: Path, state: dict[str, Any]) -> Path:
    """Write an immutable preview document, rejecting a hash collision."""
    kind = state["kind"]
    digest = state["hash"]
    path = output_root / "data" / "previews" / kind / f"{digest}.json"
    payload = canonical_bytes(state)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"preview hash collision at {path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as output:
        output.write(payload)
    os.replace(temporary, path)
    return path
