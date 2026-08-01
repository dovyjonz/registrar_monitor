"""Immutable static-data publication with an atomic semester pointer."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from numbers import Real
from pathlib import Path
from typing import Any

PublicationHook = Callable[[str, str], None]

MANIFEST_VERSION = 1
DATA_MODEL_VERSION_V2 = 2
DATA_MODEL_VERSION_V3 = 3
SUMMARY_SCHEMA_VERSION = 1
DEPARTMENT_SCHEMA_VERSION = 1
EMPTY_GENERATED_AT = "1970-01-01T00:00:00+00:00"

_MISSING = object()
_SUMMARY_FORBIDDEN_KEYS = {
    "data",
    "sn",
    "snapshots",
    "averageHistory",
    "history",
    "sectionHistory",
    "events",
    "ah",
    "h",
    "ev",
}


@dataclass(frozen=True)
class PublicationResult:
    status: str
    pointer_path: Path
    manifest_path: Path
    build_id: str
    blobs_written: int


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_once(path: Path, payload: bytes) -> bool:
    """Write immutable content, rejecting a hash-address collision."""
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"immutable artifact conflicts with existing file: {path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    return True


def _replace_json(path: Path, value: Any) -> None:
    payload = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected an object in {path}")
    return value


def _value(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first present key, supporting verbose and minified input."""
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{context} must be an object")
    return value


def _list(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be an array")
    return value


def _number(value: Any, context: str, *, default: float = 0.0) -> float | int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{context} must be a number")
    return value


def _integer(value: Any, context: str, *, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{context} must be an integer")
    return value


def _text(value: Any, context: str, *, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError(f"{context} must be a string")
    return value


def _copy_json_value(value: Any) -> Any:
    """Copy JSON-compatible input without relying on source insertion order."""
    return json.loads(_canonical_bytes(value))


def _snapshot_timestamp(snapshot: dict[str, Any], context: str) -> str:
    timestamp = _value(snapshot, "timestamp", "ts")
    return _text(timestamp, f"{context}.timestamp")


def _source_snapshot_timestamps(data: dict[str, Any]) -> list[str]:
    snapshots = _list(_value(data, "snapshots", "sn", default=[]), "data.snapshots")
    return [
        _snapshot_timestamp(
            _mapping(snapshot, f"data.snapshots[{index}]"), f"data.snapshots[{index}]"
        )
        for index, snapshot in enumerate(snapshots)
    ]


def _resolve_timestamp(
    value: dict[str, Any],
    snapshot_timestamps: list[str],
    context: str,
    *,
    required: bool,
) -> str | None:
    """Resolve a legacy snapshot index or direct timestamp to a string."""
    index = _value(value, "timestampIdx", "snapshotIdx", "i", default=_MISSING)
    if index is not _MISSING:
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError(f"{context}.timestampIdx must be an integer")
        if index < 0 or index >= len(snapshot_timestamps):
            raise ValueError(f"{context}.timestampIdx is outside the snapshot table")
        return snapshot_timestamps[index]

    timestamp = _value(
        value,
        "timestamp",
        "snapshotTimestamp",
        "ts",
        "st",
        default=_MISSING,
    )
    if timestamp is not _MISSING:
        return _text(timestamp, f"{context}.timestamp")
    if required:
        raise ValueError(f"{context} has no timestamp reference")
    return None


def _normalise_snapshot(
    snapshot: dict[str, Any], context: str, *, require_timestamp: bool = True
) -> dict[str, Any]:
    """Normalize a snapshot identity used by v3 summaries and manifests."""
    snapshot_id = _value(snapshot, "id", "snapshotId", default=_MISSING)
    if snapshot_id is _MISSING:
        raise ValueError(f"{context}.id is required")
    if isinstance(snapshot_id, bool) or not isinstance(snapshot_id, int):
        raise TypeError(f"{context}.id must be an integer")
    observed_at = _value(snapshot, "observedAt", "timestamp", "ts")
    if require_timestamp:
        observed_at = _text(observed_at, f"{context}.observedAt")
    elif observed_at is not None:
        observed_at = _text(observed_at, f"{context}.observedAt")
    overall_fill = _number(
        _value(snapshot, "overallFill", "of"),
        f"{context}.overallFill",
    )
    return {
        "id": snapshot_id,
        "observedAt": observed_at,
        "overallFill": overall_fill,
    }


def _latest_snapshot(data: dict[str, Any]) -> dict[str, Any] | None:
    snapshots = _list(_value(data, "snapshots", "sn", default=[]), "data.snapshots")
    if not snapshots:
        return None
    candidates = [
        _mapping(snapshot, f"data.snapshots[{index}]")
        for index, snapshot in enumerate(snapshots)
    ]
    candidates.sort(
        key=lambda snapshot: (
            _snapshot_timestamp(snapshot, "data.snapshots"),
            _value(snapshot, "id", "snapshotId", default=-1),
        )
    )
    return _normalise_snapshot(candidates[-1], "data.currentSnapshot")


def _normalise_milestones(milestones: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for index, milestone in enumerate(milestones):
        item = _mapping(milestone, f"milestones[{index}]")
        normalised: dict[str, str] = {}
        for key, value in item.items():
            normalised[_text(key, f"milestones[{index}] key")] = _text(
                value, f"milestones[{index}].{key}"
            )
        result.append(normalised)
    return sorted(
        result,
        key=lambda item: (
            item.get("time", ""),
            _canonical_bytes(item),
        ),
    )


def _normalise_history_point(
    point: dict[str, Any],
    snapshot_timestamps: list[str],
    context: str,
    *,
    section: bool,
) -> tuple[str, dict[str, Any]]:
    timestamp = _resolve_timestamp(
        point,
        snapshot_timestamps,
        context,
        required=True,
    )
    if timestamp is None:
        raise ValueError(f"{context} has no timestamp reference")
    payload: dict[str, Any] = {
        "fill": _number(_value(point, "fill", "f"), f"{context}.fill"),
    }
    if section:
        payload["enrollment"] = _integer(
            _value(point, "enrollment", "e"),
            f"{context}.enrollment",
        )
        payload["capacity"] = _integer(
            _value(point, "capacity", "c"),
            f"{context}.capacity",
        )
    return timestamp, payload


def _compact_timestamped_points(
    points: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    """Sort points and discard consecutive states that add no information."""
    ordered = sorted(
        points,
        key=lambda item: (item[0], _canonical_bytes(item[1])),
    )
    compacted: list[tuple[str, dict[str, Any]]] = []
    for timestamp, payload in ordered:
        if compacted and compacted[-1][1] == payload:
            continue
        compacted.append((timestamp, payload))
    return compacted


def _normalise_event(
    event: dict[str, Any],
    snapshot_timestamps: list[str],
    context: str,
) -> tuple[str | None, dict[str, Any]]:
    timestamp = _resolve_timestamp(
        event,
        snapshot_timestamps,
        context,
        required=False,
    )
    key_map = {
        "eventType": "eventType",
        "et": "eventType",
        "sectionCode": "sectionCode",
        "sc": "sectionCode",
        "oldValue": "oldValue",
        "ov": "oldValue",
        "newValue": "newValue",
        "nv": "newValue",
    }
    ignored = {
        "timestampIdx",
        "snapshotIdx",
        "i",
        "timestamp",
        "snapshotTimestamp",
        "ts",
        "st",
    }
    result: dict[str, Any] = {}
    for key, value in event.items():
        if key in ignored:
            continue
        result[key_map.get(key, key)] = _copy_json_value(value)
    return timestamp, result


def _normalise_section(
    section: dict[str, Any],
    context: str,
) -> dict[str, Any]:
    return {
        "sectionId": _value(section, "sectionId", "sid"),
        "type": _text(_value(section, "type", "t"), f"{context}.type"),
        "instructor": _text(
            _value(section, "instructor", "in"),
            f"{context}.instructor",
        ),
        "currentEnrollment": _integer(
            _value(section, "currentEnrollment", "ce"),
            f"{context}.currentEnrollment",
        ),
        "currentCapacity": _integer(
            _value(section, "currentCapacity", "cc"),
            f"{context}.currentCapacity",
        ),
        "currentFill": _number(
            _value(section, "currentFill", "cf"),
            f"{context}.currentFill",
        ),
    }


def _normalise_course(
    code: str,
    course: dict[str, Any],
    snapshot_timestamps: list[str],
) -> tuple[dict[str, Any], list[str]]:
    context = f"course {code}"
    department = _text(
        _value(course, "department", "d"),
        f"{context}.department",
        default=code.split()[0] if code else "Other",
    )
    title = _text(_value(course, "title", "ti"), f"{context}.title")
    section_values = _mapping(
        _value(course, "sections", "s", default={}),
        f"{context}.sections",
    )

    current_sections: dict[str, dict[str, Any]] = {}
    section_histories: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    all_section_fills: list[float | int] = []
    for section_code, raw_section in sorted(
        section_values.items(), key=lambda item: str(item[0])
    ):
        section_code = _text(section_code, f"{context}.section key")
        section = _mapping(raw_section, f"{context}.sections.{section_code}")
        current = _normalise_section(section, f"{context}.sections.{section_code}")
        current_sections[section_code] = current
        all_section_fills.append(current["currentFill"])
        history_values = _value(section, "history", "h", default=[])
        history = _list(
            history_values,
            f"{context}.sections.{section_code}.history",
        )
        section_histories[section_code] = [
            _normalise_history_point(
                _mapping(point, f"{context}.sections.{section_code}.history[{index}]"),
                snapshot_timestamps,
                f"{context}.sections.{section_code}.history[{index}]",
                section=True,
            )
            for index, point in enumerate(history)
        ]

    average_history_values = _value(
        course,
        "averageHistory",
        "ah",
        default=None,
    )
    if average_history_values is not None:
        average_history = [
            _normalise_history_point(
                _mapping(point, f"{context}.averageHistory[{index}]"),
                snapshot_timestamps,
                f"{context}.averageHistory[{index}]",
                section=False,
            )
            for index, point in enumerate(
                _list(average_history_values, f"{context}.averageHistory")
            )
        ]
    else:
        fills_by_timestamp: defaultdict[str, list[float | int]] = defaultdict(list)
        for points in section_histories.values():
            for timestamp, point in points:
                fills_by_timestamp[timestamp].append(point["fill"])
        average_history = [
            (timestamp, {"fill": sum(fills) / len(fills)})
            for timestamp, fills in sorted(fills_by_timestamp.items())
        ]

    event_values = _value(course, "events", "ev", default=[])
    events = [
        _normalise_event(
            _mapping(event, f"{context}.events[{index}]"),
            snapshot_timestamps,
            f"{context}.events[{index}]",
        )
        for index, event in enumerate(_list(event_values, f"{context}.events"))
    ]

    average_history = _compact_timestamped_points(average_history)
    section_histories = {
        section_code: _compact_timestamped_points(points)
        for section_code, points in section_histories.items()
    }
    events.sort(key=lambda item: (item[0] or "", _canonical_bytes(item[1])))

    average_fill = _number(
        _value(course, "averageFill", "af"),
        f"{context}.averageFill",
        default=(
            sum(all_section_fills) / len(all_section_fills)
            if all_section_fills
            else 0.0
        ),
    )
    is_filled_value = _value(course, "isFilled", "if", default=None)
    if is_filled_value is None:
        sections_by_type: defaultdict[str, list[float | int]] = defaultdict(list)
        for section in current_sections.values():
            sections_by_type[section["type"]].append(section["currentFill"])
        is_filled = any(
            fills and all(fill >= 1.0 for fill in fills)
            for fills in sections_by_type.values()
        )
    elif not isinstance(is_filled_value, bool):
        raise TypeError(f"{context}.isFilled must be a boolean")
    else:
        is_filled = is_filled_value

    timestamps = [timestamp for timestamp, _ in average_history]
    timestamps.extend(
        timestamp for points in section_histories.values() for timestamp, _ in points
    )
    timestamps.extend(timestamp for timestamp, _ in events if timestamp is not None)

    course_payload = {
        "code": code,
        "title": title,
        "department": department,
        "averageFill": average_fill,
        "isFilled": is_filled,
        "sections": current_sections,
        "averageHistory": average_history,
        "sectionHistory": section_histories,
        "events": events,
    }
    return course_payload, timestamps


def _materialise_course_history(
    course: dict[str, Any],
    timestamp_indices: dict[str, int],
) -> dict[str, Any]:
    def materialise_points(
        points: list[tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        return [
            {"timestampIdx": timestamp_indices[timestamp], **payload}
            for timestamp, payload in points
        ]

    result = dict(course)
    result["averageHistory"] = materialise_points(course["averageHistory"])
    result["sectionHistory"] = {
        section_code: materialise_points(points)
        for section_code, points in sorted(course["sectionHistory"].items())
    }
    result["events"] = [
        {
            **event,
            **(
                {"timestampIdx": timestamp_indices[timestamp]}
                if timestamp is not None
                else {}
            ),
        }
        for timestamp, event in course["events"]
    ]
    return result


def _validate_snapshot(value: Any, context: str, *, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    snapshot = _mapping(value, context)
    _normalise_snapshot(snapshot, context)


def _validate_summary_v3(summary: dict[str, Any], semester: str) -> None:
    if summary.get("schemaVersion") != SUMMARY_SCHEMA_VERSION:
        raise ValueError("semester summary has an unsupported schemaVersion")
    if summary.get("kind") != "semester-summary":
        raise ValueError("semester summary has an unsupported kind")
    if summary.get("semester") != semester:
        raise ValueError("semester summary identity does not match the manifest")
    forbidden = _SUMMARY_FORBIDDEN_KEYS.intersection(summary)
    if forbidden:
        raise ValueError(
            "semester summary contains lazy or global fields: "
            + ", ".join(sorted(forbidden))
        )
    last_report_time = summary.get("lastReportTime")
    if last_report_time is not None:
        _text(last_report_time, "summary.lastReportTime")
    snapshot_count = summary.get("snapshotCount")
    if (
        isinstance(snapshot_count, bool)
        or not isinstance(snapshot_count, int)
        or snapshot_count < 0
    ):
        raise ValueError("summary.snapshotCount must be a non-negative integer")
    _validate_snapshot(
        summary.get("currentSnapshot"), "summary.currentSnapshot", allow_none=True
    )
    milestones = _list(summary.get("milestones"), "summary.milestones")
    for index, milestone in enumerate(milestones):
        _mapping(milestone, f"summary.milestones[{index}]")
    courses = _mapping(summary.get("courses"), "summary.courses")
    required = {
        "code",
        "department",
        "title",
        "averageFill",
        "isFilled",
        "sectionCount",
        "fullSectionCount",
    }
    for code, value in courses.items():
        course = _mapping(value, f"summary.courses.{code}")
        forbidden_course_fields = _SUMMARY_FORBIDDEN_KEYS.intersection(course)
        if forbidden_course_fields:
            raise ValueError(
                f"summary course {code} contains lazy fields: "
                + ", ".join(sorted(forbidden_course_fields))
            )
        if course.get("code") != code:
            raise ValueError(f"summary course key {code!r} does not match its code")
        missing = required.difference(course)
        if missing:
            raise ValueError(
                f"summary.courses.{code} is missing {', '.join(sorted(missing))}"
            )
        _text(course["department"], f"summary.courses.{code}.department")
        _text(course["title"], f"summary.courses.{code}.title")
        _number(course["averageFill"], f"summary.courses.{code}.averageFill")
        if not isinstance(course["isFilled"], bool):
            raise TypeError(f"summary.courses.{code}.isFilled must be a boolean")
        for key in ("sectionCount", "fullSectionCount"):
            count = course[key]
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(
                    f"summary.courses.{code}.{key} must be a non-negative integer"
                )


def _validate_index(index: Any, timestamp_count: int, context: str) -> int:
    if isinstance(index, bool) or not isinstance(index, int):
        raise TypeError(f"{context}.timestampIdx must be an integer")
    if index < 0 or index >= timestamp_count:
        raise ValueError(f"{context}.timestampIdx is outside the department table")
    return index


def _validate_department_v3(
    department_payload: dict[str, Any], semester: str, department: str
) -> None:
    if department_payload.get("schemaVersion") != DEPARTMENT_SCHEMA_VERSION:
        raise ValueError(f"department {department} has an unsupported schemaVersion")
    if department_payload.get("kind") != "department-detail":
        raise ValueError(f"department {department} has an unsupported kind")
    if department_payload.get("semester") != semester:
        raise ValueError(
            f"department {department} semester does not match the manifest"
        )
    if department_payload.get("department") != department:
        raise ValueError(f"department payload identity does not match {department}")
    timestamps = _list(
        department_payload.get("timestamps"),
        f"department {department}.timestamps",
    )
    if any(not isinstance(timestamp, str) for timestamp in timestamps):
        raise TypeError(f"department {department}.timestamps must contain strings")
    if timestamps != sorted(set(timestamps)):
        raise ValueError(
            f"department {department}.timestamps must be sorted and unique"
        )
    courses = _mapping(
        department_payload.get("courses"),
        f"department {department}.courses",
    )
    referenced: set[int] = set()
    for code, value in courses.items():
        course = _mapping(value, f"department {department}.courses.{code}")
        if course.get("code") != code:
            raise ValueError(f"department course key {code!r} does not match its code")
        if course.get("department") != department:
            raise ValueError(f"department course {code} has the wrong department")
        _text(course.get("title"), f"department {department}.courses.{code}.title")
        _number(
            course.get("averageFill"),
            f"department {department}.courses.{code}.averageFill",
        )
        if not isinstance(course.get("isFilled"), bool):
            raise TypeError(f"department course {code}.isFilled must be a boolean")
        sections = _mapping(
            course.get("sections"),
            f"department {department}.courses.{code}.sections",
        )
        for section_code, value in sections.items():
            section = _mapping(
                value,
                f"department {department}.courses.{code}.sections.{section_code}",
            )
            _text(section.get("type"), "section.type")
            _text(section.get("instructor"), "section.instructor")
            _integer(section.get("currentEnrollment"), "section.currentEnrollment")
            _integer(section.get("currentCapacity"), "section.currentCapacity")
            _number(section.get("currentFill"), "section.currentFill")

        average_history = _list(
            course.get("averageHistory"),
            f"department {department}.courses.{code}.averageHistory",
        )
        for index, point in enumerate(average_history):
            point = _mapping(
                point,
                f"department {department}.courses.{code}.averageHistory[{index}]",
            )
            referenced.add(
                _validate_index(
                    point.get("timestampIdx"),
                    len(timestamps),
                    f"department {department}.courses.{code}.averageHistory[{index}]",
                )
            )
            _number(point.get("fill"), "history.fill")

        section_history = _mapping(
            course.get("sectionHistory"),
            f"department {department}.courses.{code}.sectionHistory",
        )
        for section_code, points in section_history.items():
            for index, point in enumerate(
                _list(
                    points,
                    f"department {department}.courses.{code}.sectionHistory.{section_code}",
                )
            ):
                point = _mapping(
                    point,
                    f"department {department}.courses.{code}.sectionHistory.{section_code}[{index}]",
                )
                referenced.add(
                    _validate_index(
                        point.get("timestampIdx"),
                        len(timestamps),
                        f"department {department}.courses.{code}.sectionHistory.{section_code}[{index}]",
                    )
                )
                _number(point.get("fill"), "section history.fill")
                _integer(point.get("enrollment"), "section history.enrollment")
                _integer(point.get("capacity"), "section history.capacity")

        for index, event in enumerate(
            _list(
                course.get("events"),
                f"department {department}.courses.{code}.events",
            )
        ):
            event = _mapping(
                event,
                f"department {department}.courses.{code}.events[{index}]",
            )
            if "timestampIdx" in event:
                referenced.add(
                    _validate_index(
                        event["timestampIdx"],
                        len(timestamps),
                        f"department {department}.courses.{code}.events[{index}]",
                    )
                )
    if referenced != set(range(len(timestamps))):
        raise ValueError(
            f"department {department}.timestamps contains an unreferenced timestamp"
        )


def _validate_legacy_payloads(
    summary: dict[str, Any], departments: dict[str, dict[str, Any]]
) -> None:
    """Keep the phase-1 compatibility path safe without imposing the v3 shape."""
    if not isinstance(summary, dict):
        raise TypeError("legacy summary must be an object")
    if not isinstance(departments, dict):
        raise TypeError("legacy departments must be an object")
    for department, payload in departments.items():
        if not isinstance(department, str) or not isinstance(payload, dict):
            raise TypeError("legacy department payloads must be named objects")


def build_frontend_payloads_v3(
    *,
    data: dict[str, Any],
    milestones: list[dict[str, str]],
    semester: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Build the versioned summary and lazy department-detail payloads."""
    data = _mapping(data, "data")
    snapshot_timestamps = _source_snapshot_timestamps(data)
    latest_snapshot = _latest_snapshot(data)
    source_last_report = _value(data, "lastReportTime", "lrt")
    last_report_time = (
        _text(source_last_report, "data.lastReportTime")
        if source_last_report is not None
        else (latest_snapshot["observedAt"] if latest_snapshot else None)
    )

    source_courses = _mapping(
        _value(data, "courses", "cr", default={}),
        "data.courses",
    )
    summary_courses: dict[str, dict[str, Any]] = {}
    departments: dict[str, dict[str, Any]] = {}
    for raw_code, raw_course in sorted(
        source_courses.items(), key=lambda item: str(item[0])
    ):
        code = _text(raw_code, "data.courses key")
        course = _mapping(raw_course, f"data.courses.{code}")
        course_payload, timestamps = _normalise_course(
            code,
            course,
            snapshot_timestamps,
        )
        department = course_payload["department"]
        department_payload = departments.setdefault(
            department,
            {
                "schemaVersion": DEPARTMENT_SCHEMA_VERSION,
                "kind": "department-detail",
                "semester": semester,
                "department": department,
                "timestamps": [],
                "courses": {},
            },
        )
        department_payload["courses"][code] = course_payload
        department_payload["timestamps"].extend(timestamps)

        sections = course_payload["sections"]
        summary_courses[code] = {
            "code": code,
            "department": department,
            "title": course_payload["title"],
            "averageFill": course_payload["averageFill"],
            "isFilled": course_payload["isFilled"],
            "sectionCount": len(sections),
            "fullSectionCount": sum(
                1 for section in sections.values() if section["currentFill"] >= 1.0
            ),
        }

    for department_payload in departments.values():
        timestamps = sorted(set(department_payload["timestamps"]))
        department_payload["timestamps"] = timestamps
        timestamp_indices = {
            timestamp: index for index, timestamp in enumerate(timestamps)
        }
        department_payload["courses"] = {
            code: _materialise_course_history(course, timestamp_indices)
            for code, course in sorted(department_payload["courses"].items())
        }

    summary = {
        "schemaVersion": SUMMARY_SCHEMA_VERSION,
        "kind": "semester-summary",
        "semester": semester,
        "lastReportTime": last_report_time,
        "snapshotCount": len(snapshot_timestamps),
        "currentSnapshot": latest_snapshot,
        "milestones": _normalise_milestones(milestones),
        "courses": {code: summary_courses[code] for code in sorted(summary_courses)},
    }
    _validate_summary_v3(summary, semester)
    for department, department_payload in departments.items():
        _validate_department_v3(department_payload, semester, department)
    return summary, departments


def build_legacy_frontend_payloads(
    *,
    data: dict[str, Any],
    milestones: list[dict[str, str]],
    semester: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Compatibility adapter for the pre-v3 frontend payload shape."""
    summary_courses: dict[str, Any] = {}
    departments: dict[str, dict[str, Any]] = {}

    for code, course_value in sorted(data.get("cr", {}).items()):
        course = dict(_mapping(course_value, f"data.cr.{code}"))
        department = str(course.get("d") or code.split()[0])
        departments.setdefault(
            department,
            {"semester": semester, "department": department, "courses": {}},
        )["courses"][code] = _copy_json_value(course_value)

        course.pop("ah", None)
        course.pop("ev", None)
        course["s"] = {
            section_code: {key: value for key, value in section.items() if key != "h"}
            for section_code, section in course.get("s", {}).items()
        }
        summary_courses[code] = course

    summary_data = dict(data)
    summary_data["cr"] = summary_courses
    return (
        {"data": summary_data, "milestones": milestones, "semester": semester},
        departments,
    )


def publish_semester(
    output_root: Path,
    *,
    semester_slug: str,
    semester: str,
    current_snapshot: dict[str, Any] | None,
    summary: dict[str, Any],
    departments: dict[str, dict[str, Any]],
    generated_at: str | None = None,
    hook: PublicationHook | None = None,
) -> PublicationResult:
    """Publish blobs and an immutable manifest, replacing the pointer last."""
    root = output_root.resolve()
    semester_root = root / "data" / semester_slug
    pointer_path = semester_root / "manifest.json"
    blobs_root = root / "data" / "blobs"
    manifests_root = semester_root / "manifests"

    if not isinstance(departments, dict):
        raise TypeError("departments must be an object")
    is_v3 = isinstance(summary, dict) and any(
        key in summary for key in ("schemaVersion", "kind")
    )
    if is_v3:
        _validate_summary_v3(summary, semester)
        for department, payload in departments.items():
            _validate_department_v3(payload, semester, department)
        data_model_version = DATA_MODEL_VERSION_V3
    else:
        _validate_legacy_payloads(summary, departments)
        data_model_version = DATA_MODEL_VERSION_V2

    if is_v3:
        if current_snapshot is None:
            normalised_snapshot = None
            effective_generated_at = generated_at or EMPTY_GENERATED_AT
        else:
            normalised_snapshot = _normalise_snapshot(
                _mapping(current_snapshot, "currentSnapshot"),
                "currentSnapshot",
            )
            effective_generated_at = generated_at or normalised_snapshot["observedAt"]
            if (
                not isinstance(effective_generated_at, str)
                or not effective_generated_at
            ):
                raise ValueError(
                    "generated_at is required when currentSnapshot.observedAt is unavailable"
                )
    else:
        raw_current_snapshot = _mapping(current_snapshot, "currentSnapshot")
        # The pre-v3 service can publish an empty semester with null snapshot
        # metadata. Preserve that compatibility shape, but keep its fallback
        # deterministic now that wall-clock generation is no longer allowed.
        normalised_snapshot = {
            "id": raw_current_snapshot.get("id"),
            "observedAt": raw_current_snapshot.get("observedAt"),
            "overallFill": raw_current_snapshot.get("overallFill"),
        }
        effective_generated_at = generated_at or normalised_snapshot["observedAt"]
        if effective_generated_at is None:
            effective_generated_at = EMPTY_GENERATED_AT
        if not isinstance(effective_generated_at, str) or not effective_generated_at:
            raise ValueError("generated_at must be a non-empty string")

    summary_payload = _canonical_bytes(summary)
    summary_hash = _sha256(summary_payload)
    department_payloads = {
        name: (_canonical_bytes(payload))
        for name, payload in sorted(departments.items())
    }
    department_hashes = {
        name: _sha256(payload) for name, payload in department_payloads.items()
    }
    identity = {
        "manifestVersion": MANIFEST_VERSION,
        "dataModelVersion": data_model_version,
        "summarySchemaVersion": (summary.get("schemaVersion") if is_v3 else None),
        "departmentSchemaVersion": (DEPARTMENT_SCHEMA_VERSION if is_v3 else None),
        "semester": semester,
        "currentSnapshot": normalised_snapshot,
        "summary": summary_hash,
        "departments": department_hashes,
    }
    build_id = _sha256(_canonical_bytes(identity))[:24]
    current_ref = f"manifests/{build_id}.json"

    prior_pointer = _read_json(pointer_path) if pointer_path.exists() else None
    if prior_pointer and prior_pointer.get("current") == current_ref:
        return PublicationResult(
            status="unchanged",
            pointer_path=pointer_path,
            manifest_path=semester_root / current_ref,
            build_id=build_id,
            blobs_written=0,
        )

    if hook:
        hook("blobs", "before")
    blobs_written = int(
        _write_once(blobs_root / f"{summary_hash}.json", summary_payload)
    )
    for name, payload in department_payloads.items():
        blobs_written += int(
            _write_once(blobs_root / f"{department_hashes[name]}.json", payload)
        )
    if hook:
        hook("blobs", "after")

    manifest = {
        "manifestVersion": MANIFEST_VERSION,
        "dataModelVersion": data_model_version,
        "buildId": build_id,
        "semester": semester,
        "generatedAt": effective_generated_at,
        "currentSnapshot": normalised_snapshot,
        "summary": {
            **({"schemaVersion": SUMMARY_SCHEMA_VERSION} if is_v3 else {}),
            "url": f"../../blobs/{summary_hash}.json",
            "sha256": summary_hash,
            "bytes": len(summary_payload),
        },
        "departments": {
            name: {
                **({"schemaVersion": DEPARTMENT_SCHEMA_VERSION} if is_v3 else {}),
                "url": f"../../blobs/{department_hashes[name]}.json",
                "sha256": department_hashes[name],
                "bytes": len(department_payloads[name]),
            }
            for name in department_payloads
        },
    }
    manifest_path = manifests_root / f"{build_id}.json"
    if hook:
        hook("manifest", "before")
    _write_once(manifest_path, _canonical_bytes(manifest))
    if hook:
        hook("manifest", "after")

    pointer = {
        "manifestVersion": MANIFEST_VERSION,
        "current": current_ref,
        "previous": prior_pointer.get("current") if prior_pointer else None,
    }
    if hook:
        hook("pointer", "before")
    _replace_json(pointer_path, pointer)
    if hook:
        hook("pointer", "after")
    return PublicationResult(
        status="published",
        pointer_path=pointer_path,
        manifest_path=manifest_path,
        build_id=build_id,
        blobs_written=blobs_written,
    )


def rollback_semester_pointer(
    output_root: Path,
    *,
    semester_slug: str,
) -> PublicationResult:
    """Atomically swap the stable pointer to its declared previous manifest."""
    pointer_path = output_root.resolve() / "data" / semester_slug / "manifest.json"
    pointer = _read_json(pointer_path)
    previous = pointer.get("previous")
    current = pointer.get("current")
    if not isinstance(previous, str) or not previous:
        raise ValueError("semester pointer has no previous manifest")
    manifest_path = pointer_path.parent / previous
    manifest = _read_json(manifest_path)
    _replace_json(
        pointer_path,
        {
            "manifestVersion": MANIFEST_VERSION,
            "current": previous,
            "previous": current,
        },
    )
    return PublicationResult(
        status="rolled_back",
        pointer_path=pointer_path,
        manifest_path=manifest_path,
        build_id=str(manifest["buildId"]),
        blobs_written=0,
    )
