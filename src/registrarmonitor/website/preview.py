"""Deterministic preview-visible publication state."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .availability import calculate_availability
from .config import course_to_slug, semester_to_slug

PREVIEW_SCHEMA_VERSION = 1


def canonical_bytes(value: Any) -> bytes:
    """Serialize a preview document deterministically."""
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _short_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()[:12]


def derive_priority_state(
    milestones: list[dict[str, str]], *, at: str | None
) -> dict[str, Any] | None:
    """Derive compact current/next priority copy from normalized milestones."""
    if not at:
        return None
    now = datetime.fromisoformat(at)

    def milestone_time(item: dict[str, str]) -> datetime:
        value = datetime.fromisoformat(item["time"])
        if value.tzinfo is None and now.tzinfo is not None:
            return value.replace(tzinfo=now.tzinfo)
        if value.tzinfo is not None and now.tzinfo is None:
            return value.replace(tzinfo=None)
        return value

    ordered = sorted(milestones, key=milestone_time)
    priorities = [item for item in ordered if item.get("priority")]
    reached = [item for item in priorities if milestone_time(item) <= now]
    upcoming = [item for item in priorities if milestone_time(item) > now]
    if not reached and not upcoming:
        return None
    current = reached[-1] if reached else None
    next_item = upcoming[0] if upcoming else None
    if current:
        priority = current.get("priority")
    elif next_item:
        priority = next_item.get("priority")
    else:
        return None
    eligible = list(
        dict.fromkeys(item["label"] for item in reached if item.get("label"))
    )
    label = f"PRIORITY {priority}"
    if current and current.get("label") == "ALL":
        label += " — ALL"
    return {
        "label": label,
        "eligible": eligible,
        "next": next(
            (
                {"label": item["label"], "time": item["time"]}
                for item in ordered
                if milestone_time(item) > now and item.get("label")
            ),
            None,
        ),
    }


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
    """Keep only timestamps referenced by this course and remap their indices."""
    compact_course = deepcopy(course)
    collections = [compact_course.get("averageHistory", [])]
    collections.extend(compact_course.get("sectionHistory", {}).values())
    collections.append(compact_course.get("events", []))
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
    for points in collections:
        for point in points:
            old = point.get("timestampIdx")
            if old in remap:
                point["timestampIdx"] = remap[old]
    return compact_course, [timestamps[index] for index in referenced]


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
    """Build a content-addressed state for metadata, modal fallback, and image."""
    code = course["code"]
    last_changed = _course_last_changed(course, timestamps)
    compact_course, compact_timestamps = _compact_course_timestamps(course, timestamps)
    state = {
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
        "priority": derive_priority_state(milestones, at=published_at or last_changed),
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
        "updated": current_snapshot.get("observedAt"),
        "priority": derive_priority_state(
            milestones, at=current_snapshot.get("observedAt")
        ),
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
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    return path
