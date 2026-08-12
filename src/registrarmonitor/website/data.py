"""Data access layer for querying enrollment data from the database."""

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from registrarmonitor.config import get_timezone
from registrarmonitor.data.checkpointed_state import CheckpointedStateStore
from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.data.instructor_normalization import instructor_identity

from .config import KEY_MAP, course_to_slug, get_milestones

WEBSITE_HISTORY_BUFFER_HOURS = 24


def _parse_registrar_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp into the configured registrar timezone."""
    parsed = datetime.fromisoformat(value)
    timezone = get_timezone()
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def _minify_keys(obj: Any) -> Any:
    """Recursively replace verbose keys with short versions for smaller JSON output."""
    if isinstance(obj, dict):
        return {KEY_MAP.get(k, k): _minify_keys(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_minify_keys(item) for item in obj]
    return obj


def _filter_snapshots_to_milestone_window(
    snapshots: list[dict[str, Any]],
    milestones: list[dict[str, str]],
    buffer_hours: int = 2,
) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """
    Filter snapshots to only include those within the registration window.

    Args:
        snapshots: List of snapshot dictionaries with 'timestamp' field
        milestones: List of milestone dictionaries with 'time' field
        buffer_hours: Hours to include before first and after last milestone

    Returns:
        Tuple of (filtered_snapshots, old_idx_to_new_idx_map)
    """
    if not milestones or not snapshots:
        # No filtering if no milestones - return identity mapping
        return snapshots, {i: i for i in range(len(snapshots))}

    # Parse milestone timestamps
    milestone_times = []
    for m in milestones:
        try:
            # Handle both ISO format and other formats
            time_str = m.get("time", "")
            if time_str:
                milestone_times.append(_parse_registrar_timestamp(time_str))
        except (ValueError, TypeError):
            continue

    if not milestone_times:
        return snapshots, {i: i for i in range(len(snapshots))}

    # Calculate window bounds
    window_start = min(milestone_times) - timedelta(hours=buffer_hours)
    window_end = max(milestone_times) + timedelta(hours=buffer_hours)

    # Filter snapshots to window
    filtered: list[dict[str, Any]] = []
    index_map: dict[int, int] = {}  # old_idx -> new_idx

    for old_idx, snapshot in enumerate(snapshots):
        try:
            ts_str = snapshot.get("timestamp", "")
            if not ts_str:
                continue
            ts = _parse_registrar_timestamp(ts_str)

            if window_start <= ts <= window_end:
                index_map[old_idx] = len(filtered)
                filtered.append(snapshot)
        except (ValueError, TypeError):
            continue

    # If filtering removed everything, snapshots are all outside the
    # registration window (pre-registration polling).  Return the
    # originals so the course list still renders, but with an empty
    # history (the chart will show "no data yet").
    if not filtered:
        # Keep the snapshot list so we can still display course metadata,
        # but clear the index map so no history points are emitted.
        return snapshots, {}

    return filtered, index_map


def _history_indices_in_milestone_window(
    snapshots: list[dict[str, Any]],
    milestones: list[dict[str, str]],
    buffer_hours: int = 1,
) -> set[int] | None:
    """Return snapshot indices inside the milestone window, or None if unfiltered."""
    if not milestones or not snapshots:
        return None

    milestone_times = []
    for m in milestones:
        try:
            time_str = m.get("time", "")
            if time_str:
                milestone_times.append(_parse_registrar_timestamp(time_str))
        except (ValueError, TypeError):
            continue

    if not milestone_times:
        return None

    window_start = min(milestone_times) - timedelta(hours=buffer_hours)
    window_end = max(milestone_times) + timedelta(hours=buffer_hours)
    keep: set[int] = set()
    for idx, snapshot in enumerate(snapshots):
        try:
            ts = _parse_registrar_timestamp(snapshot.get("timestamp", ""))
        except (ValueError, TypeError):
            continue
        if window_start <= ts <= window_end:
            keep.add(idx)

    return keep


def _history_indices_for_website(
    snapshots: list[dict[str, Any]],
    milestones: list[dict[str, str]],
    buffer_hours: int = WEBSITE_HISTORY_BUFFER_HOURS,
) -> set[int] | None:
    """Keep the buffered milestone window and latest snapshot as a chart anchor."""
    keep_indices = _history_indices_in_milestone_window(
        snapshots,
        milestones,
        buffer_hours=buffer_hours,
    )
    if keep_indices is not None and snapshots:
        keep_indices.add(len(snapshots) - 1)
    return keep_indices


def _compact_section_history(
    history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Keep endpoints plus every enrollment/capacity change and its previous point.
    """
    if len(history) <= 2:
        return history

    keep = {0, len(history) - 1}
    prev = history[0]
    for idx in range(1, len(history)):
        point = history[idx]
        if point.get("enrollment") != prev.get("enrollment") or point.get(
            "capacity"
        ) != prev.get("capacity"):
            keep.add(idx - 1)
            keep.add(idx)
        prev = point

    return [history[idx] for idx in sorted(keep)]


def _add_course_average_history(course_data: dict[str, Any]) -> None:
    """Build course-level average fill history from section histories."""
    fills_by_snapshot: dict[int, list[float]] = {}
    for section in course_data["sections"].values():
        for point in section["history"]:
            fills_by_snapshot.setdefault(point["snapshotIdx"], []).append(point["fill"])

    average_history = []
    for snapshot_idx in sorted(fills_by_snapshot):
        fills = fills_by_snapshot[snapshot_idx]
        average_history.append(
            {
                "snapshotIdx": snapshot_idx,
                "fill": sum(fills) / len(fills),
            }
        )
    course_data["averageHistory"] = average_history


def _compact_average_history(
    history: list[dict[str, Any]], *, tolerance: float = 1e-9
) -> list[dict[str, Any]]:
    """Keep endpoints plus points where average fill changes."""
    if len(history) <= 2:
        return history

    keep = {0, len(history) - 1}
    prev_fill = history[0].get("fill", 0.0)
    for idx in range(1, len(history)):
        fill = history[idx].get("fill", 0.0)
        if abs(fill - prev_fill) > tolerance:
            keep.add(idx - 1)
            keep.add(idx)
        prev_fill = fill

    return [history[idx] for idx in sorted(keep)]


def _compact_histories_for_website(data: dict[str, Any]) -> None:
    """Add course average histories, then compact per-section histories."""
    for course_data in data["courses"].values():
        _add_course_average_history(course_data)
        course_data["averageHistory"] = _compact_average_history(
            course_data["averageHistory"]
        )
        for section_data in course_data["sections"].values():
            section_data["history"] = _compact_section_history(section_data["history"])


def _get_course_totals(course_data: dict[str, Any]) -> tuple[int, int]:
    """Return course-level enrollment/capacity using the domain course semantics."""
    sections = course_data.get("sections", {})
    if not sections:
        return 0, 0

    enrollment_by_type: dict[str, int] = {}
    capacity_by_type: dict[str, int] = {}
    for section in sections.values():
        section_type = section.get("type", "")
        enrollment_by_type[section_type] = enrollment_by_type.get(
            section_type, 0
        ) + int(section.get("currentEnrollment") or 0)
        capacity_by_type[section_type] = capacity_by_type.get(section_type, 0) + int(
            section.get("currentCapacity") or 0
        )

    enrollment = min(enrollment_by_type.values()) if enrollment_by_type else 0
    capacity = min(capacity_by_type.values()) if capacity_by_type else 0
    return enrollment, capacity


def _get_course_status(course_data: dict[str, Any]) -> str:
    """Return prototype course status derived from current fill state."""
    if course_data.get("isFilled") or course_data.get("averageFill", 0) >= 1.0:
        return "full"
    if course_data.get("averageFill", 0) >= 0.75:
        return "near"
    return "open"


def _sort_sections_for_prototype(
    sections: dict[str, dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    """Sort sections by instructional type, then natural section code."""
    type_priority = {"L": 0, "S": 1, "R": 1, "D": 1, "B": 2, "Lb": 2}
    return sorted(
        sections.items(),
        key=lambda item: (
            type_priority.get(item[1].get("type", ""), 3),
            item[0],
        ),
    )


def _format_prototype_event(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize a course event for the prototype UI."""
    event_type = event.get("eventType", "")
    section_code = event.get("sectionCode", "")
    old_value = event.get("oldValue")
    new_value = event.get("newValue")

    labels = {
        "course_added": "Course Added",
        "course_removed": "Course Removed",
        "section_added": "Section Added",
        "section_removed": "Section Removed",
        "capacity_changed": "Capacity Change",
        "instructor_changed": "Instructor Change",
    }
    if event_type == "capacity_changed":
        description = f"{section_code} {old_value} -> {new_value}".strip()
    elif event_type == "instructor_changed":
        description = f"{section_code} {old_value} -> {new_value}".strip()
    elif section_code:
        description = section_code
    else:
        description = labels.get(event_type, event_type.replace("_", " ").title())

    return {
        "type": event_type,
        "label": labels.get(event_type, event_type.replace("_", " ").title()),
        "description": description,
        "sectionCode": section_code,
        "oldValue": old_value,
        "newValue": new_value,
        "timestamp": event.get("snapshotTimestamp"),
    }


def _get_last_activity_at(
    course_data: dict[str, Any], fallback: str | None
) -> str | None:
    """Return the latest event timestamp for a course, or the semester fallback."""
    event_times = [
        event.get("snapshotTimestamp")
        for event in course_data.get("events", [])
        if event.get("snapshotTimestamp")
    ]
    return max(event_times) if event_times else fallback


def _build_prototype_course_row(
    code: str,
    course_data: dict[str, Any],
    *,
    last_report_time: str | None,
    detail_base_url: str,
) -> dict[str, Any]:
    """Build the lightweight course row used by the local prototype index."""
    enrollment, capacity = _get_course_totals(course_data)
    section_count = len(course_data.get("sections", {}))
    status = _get_course_status(course_data)
    events = sorted(
        [_format_prototype_event(event) for event in course_data.get("events", [])],
        key=lambda event: event.get("timestamp") or "",
        reverse=True,
    )

    return {
        "code": code,
        "title": course_data.get("title", ""),
        "department": course_data.get("department")
        or (code.split()[0] if code else ""),
        "enrollmentTotal": enrollment,
        "capacityTotal": capacity,
        "fill": course_data.get("averageFill", 0),
        "status": status,
        "isFilled": bool(course_data.get("isFilled")),
        "sectionCount": section_count,
        "lastUpdated": last_report_time,
        "lastActivityAt": _get_last_activity_at(course_data, last_report_time),
        "recentEvents": events[:3],
        "detailUrl": f"{detail_base_url}/{course_to_slug(code)}.json",
    }


def _build_prototype_summary(
    data: dict[str, Any],
    course_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build summary stats for the local prototype index payload."""
    full_sections = 0
    near_full_sections = 0
    enrollment_total = 0
    capacity_total = 0

    for course_data in data.get("courses", {}).values():
        enrollment, capacity = _get_course_totals(course_data)
        enrollment_total += enrollment
        capacity_total += capacity
        for section in course_data.get("sections", {}).values():
            fill = section.get("currentFill", 0)
            if fill >= 1.0:
                full_sections += 1
            elif fill >= 0.75:
                near_full_sections += 1

    return {
        "semester": data.get("semester"),
        "lastReportTime": data.get("lastReportTime"),
        "courses": len(course_rows),
        "sections": sum(row["sectionCount"] for row in course_rows),
        "fullSections": full_sections,
        "nearFullSections": near_full_sections,
        "snapshots": len(data.get("snapshots", [])),
        "enrollmentTotal": enrollment_total,
        "capacityTotal": capacity_total,
        "overallFill": (enrollment_total / capacity_total if capacity_total > 0 else 0),
    }


def _compact_course_snapshots_for_detail(
    course_data: dict[str, Any],
    snapshots: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Keep only snapshots referenced by a course detail payload and remap indices.

    The normal website payload keeps global snapshot indices.  Prototype detail
    files are lazy-loaded per course, so remapping keeps each detail payload
    small without requiring frontend lookups into the initial index payload.
    """
    snapshot_indices: set[int] = set()
    for point in course_data.get("averageHistory", []):
        if isinstance(point.get("snapshotIdx"), int):
            snapshot_indices.add(point["snapshotIdx"])
    for section in course_data.get("sections", {}).values():
        for point in section.get("history", []):
            if isinstance(point.get("snapshotIdx"), int):
                snapshot_indices.add(point["snapshotIdx"])

    kept_indices = [
        idx for idx in sorted(snapshot_indices) if 0 <= idx < len(snapshots)
    ]
    index_map = {old_idx: new_idx for new_idx, old_idx in enumerate(kept_indices)}

    def remap_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        remapped = []
        for point in history:
            old_idx = point.get("snapshotIdx")
            if old_idx not in index_map:
                continue
            next_point = dict(point)
            next_point["snapshotIdx"] = index_map[old_idx]
            remapped.append(next_point)
        return remapped

    detail_course = dict(course_data)
    detail_course["averageHistory"] = remap_history(
        course_data.get("averageHistory", [])
    )
    detail_course["sections"] = {}
    for section_code, section in course_data.get("sections", {}).items():
        detail_section = dict(section)
        detail_section["history"] = remap_history(section.get("history", []))
        detail_course["sections"][section_code] = detail_section

    detail_snapshots = [snapshots[idx] for idx in kept_indices]
    return detail_course, detail_snapshots


def build_prototype_payloads(
    data: dict[str, Any],
    *,
    detail_base_url: str = "prototype-data",
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """
    Split full semester website data into a lightweight index and course details.

    Returns:
        Tuple of (index_payload, detail_payloads_by_slug).
    """
    courses = data.get("courses", {})
    last_report_time = data.get("lastReportTime")
    course_rows = [
        _build_prototype_course_row(
            code,
            course_data,
            last_report_time=last_report_time,
            detail_base_url=detail_base_url,
        )
        for code, course_data in sorted(courses.items())
    ]
    recent_events = sorted(
        [
            {**event, "courseCode": row["code"], "courseTitle": row["title"]}
            for row in course_rows
            for event in row["recentEvents"]
        ],
        key=lambda event: event.get("timestamp") or "",
        reverse=True,
    )[:8]

    index_payload = {
        "semester": data.get("semester"),
        "summary": _build_prototype_summary(data, course_rows),
        "courseRows": course_rows,
        "recentEvents": recent_events,
    }

    detail_payloads: dict[str, dict[str, Any]] = {}
    snapshots = data.get("snapshots", [])
    for code, course_data in sorted(courses.items()):
        detail_course, detail_snapshots = _compact_course_snapshots_for_detail(
            course_data,
            snapshots,
        )
        row = next(row for row in course_rows if row["code"] == code)
        sections = [
            {
                "code": section_code,
                "type": section.get("type", ""),
                "instructor": section.get("instructor", ""),
                "enrollment": section.get("currentEnrollment", 0),
                "capacity": section.get("currentCapacity", 0),
                "fill": section.get("currentFill", 0),
            }
            for section_code, section in _sort_sections_for_prototype(
                course_data.get("sections", {})
            )
        ]
        detail_payloads[course_to_slug(code)] = {
            "semester": data.get("semester"),
            "course": {
                **row,
                "sections": sections,
                "averageHistory": detail_course.get("averageHistory", []),
                "rawSections": detail_course.get("sections", {}),
                "events": [
                    _format_prototype_event(event)
                    for event in course_data.get("events", [])
                ],
            },
            "snapshots": detail_snapshots,
        }

    return index_payload, detail_payloads


def get_prototype_payloads(
    semester: str,
    *,
    detail_base_url: str = "prototype-data",
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Query and split semester data for the local dashboard prototype."""
    data = get_semester_data(semester, minify=False)
    return build_prototype_payloads(data, detail_base_url=detail_base_url)


def _build_course_events(
    semester: str, db: DatabaseManager
) -> dict[str, list[dict[str, Any]]]:
    """
    Build a dictionary of structural events for each course by diffing
    consecutive snapshots in the database.

    Tracked events:
      - course_added / course_removed
      - section_added / section_removed
      - capacity_changed
      - instructor_changed

    Returns:
        Dict mapping course_code -> list of event dicts.
    """
    events_by_course: dict[str, list[dict[str, Any]]] = {}

    with db.get_connection() as conn:
        cursor = conn.cursor()

        # Get all snapshots ordered chronologically
        cursor.execute(
            "SELECT snapshot_id, timestamp FROM snapshots ORDER BY timestamp ASC",
        )
        snapshots = cursor.fetchall()

        if len(snapshots) >= 2:
            # Fetch every state in one chronological query. Keeping the
            # snapshot ordering separate preserves non-monotonic legacy IDs
            # without issuing one query per snapshot.
            state_by_snapshot: dict[int, dict[str, dict[str, dict[str, Any]]]] = {
                int(snapshot_id): {} for snapshot_id, _ in snapshots
            }
            cursor.execute(
                """
                SELECT ed.snapshot_id, c.course_code, s.section_code,
                       ed.enrollment_count, ed.capacity_count
                FROM enrollment_data ed
                JOIN sections s ON ed.section_id = s.section_id
                JOIN courses c ON s.course_id = c.course_id
                JOIN snapshots snap ON snap.snapshot_id = ed.snapshot_id
                ORDER BY snap.timestamp, c.course_code, s.section_code
                """
            )
            for row in cursor.fetchall():
                snapshot_id, course_code, section_code, enrollment, capacity = row
                state_by_snapshot[int(snapshot_id)].setdefault(course_code, {})[
                    section_code
                ] = {
                    "enrollment": enrollment,
                    "capacity": capacity,
                }

            prev_state = state_by_snapshot[int(snapshots[0][0])]

            for i in range(1, len(snapshots)):
                snapshot_id = snapshots[i][0]
                snapshot_ts = snapshots[i][1]
                curr_state = state_by_snapshot[int(snapshot_id)]

                prev_courses = set(prev_state.keys())
                curr_courses = set(curr_state.keys())

                # Course added
                for cc in curr_courses - prev_courses:
                    events_by_course.setdefault(cc, []).append(
                        {
                            "eventType": "course_added",
                            "snapshotTimestamp": snapshot_ts,
                        }
                    )

                # Course removed
                for cc in prev_courses - curr_courses:
                    events_by_course.setdefault(cc, []).append(
                        {
                            "eventType": "course_removed",
                            "snapshotTimestamp": snapshot_ts,
                        }
                    )

                # Diff sections for courses present in both
                for cc in curr_courses & prev_courses:
                    prev_sections = set(prev_state[cc].keys())
                    curr_sections = set(curr_state[cc].keys())

                    # Section added
                    for sc in curr_sections - prev_sections:
                        events_by_course.setdefault(cc, []).append(
                            {
                                "eventType": "section_added",
                                "sectionCode": sc,
                                "snapshotTimestamp": snapshot_ts,
                            }
                        )

                    # Section removed
                    for sc in prev_sections - curr_sections:
                        events_by_course.setdefault(cc, []).append(
                            {
                                "eventType": "section_removed",
                                "sectionCode": sc,
                                "snapshotTimestamp": snapshot_ts,
                            }
                        )

                    # Diff shared sections for capacity changes.
                    for sc in curr_sections & prev_sections:
                        prev_sec = prev_state[cc][sc]
                        curr_sec = curr_state[cc][sc]

                        if prev_sec["capacity"] != curr_sec["capacity"]:
                            events_by_course.setdefault(cc, []).append(
                                {
                                    "eventType": "capacity_changed",
                                    "sectionCode": sc,
                                    "oldValue": str(prev_sec["capacity"]),
                                    "newValue": str(curr_sec["capacity"]),
                                    "snapshotTimestamp": snapshot_ts,
                                }
                            )

                prev_state = curr_state

        # Query historical instructor changes from the dedicated instructor_changes table
        try:
            cursor.execute(
                """
                SELECT ic.section_id, c.course_code, s.section_code,
                       ic.old_instructor, ic.new_instructor, ic.timestamp
                FROM instructor_changes ic
                JOIN sections s ON ic.section_id = s.section_id
                JOIN courses c ON s.course_id = c.course_id
                ORDER BY ic.section_id ASC, ic.timestamp ASC, ic.change_id ASC
                """
            )
            last_transition_by_section: dict[int, tuple[str, str]] = {}
            for row in cursor.fetchall():
                section_id, cc, sc, old_val, new_val, ts = row
                if instructor_identity(old_val) == instructor_identity(new_val):
                    continue
                transition = (old_val or "", new_val or "")
                if last_transition_by_section.get(section_id) == transition:
                    continue
                last_transition_by_section[section_id] = transition
                events_by_course.setdefault(cc, []).append(
                    {
                        "eventType": "instructor_changed",
                        "sectionCode": sc,
                        "oldValue": old_val or "TBA",
                        "newValue": new_val or "TBA",
                        "snapshotTimestamp": ts,
                    }
                )
        except sqlite3.OperationalError:
            # Handle cases where the table doesn't exist yet (e.g., in transition on old DBs)
            pass

    return events_by_course


def _checkpointed_semester_data(
    semester: str,
    db: DatabaseManager,
    *,
    minify: bool,
) -> dict[str, Any]:
    """Build the production payload from v2 state without legacy tables."""
    reconstructed = list(
        CheckpointedStateStore(
            db.db_path, initialize=False
        ).iter_reconstructed_snapshots()
    )
    data: dict[str, Any] = {
        "semester": semester,
        "lastReportTime": None,
        "snapshots": [],
        "courses": {},
    }
    if not reconstructed:
        print(f"No snapshots found for semester: {semester}")
        return _minify_keys(data) if minify else data

    with db.get_connection() as connection:
        section_ids = {
            (str(row[0]), str(row[1])): int(row[2])
            for row in connection.execute(
                """
                SELECT c.course_code, s.section_code, s.section_id
                FROM section_catalog s
                JOIN course_catalog c ON c.course_id = s.course_id
                """
            )
        }

    for snapshot_id, snapshot in reconstructed:
        data["snapshots"].append(
            {
                "id": snapshot_id,
                "timestamp": snapshot.timestamp,
                "overallFill": snapshot.overall_fill,
            }
        )
    data["lastReportTime"] = reconstructed[-1][1].timestamp

    latest = reconstructed[-1][1]
    for course_code, course in latest.courses.items():
        course_data = {
            "department": course.department
            or (course_code.split()[0] if course_code else ""),
            "title": course.course_title or "",
            "averageFill": 0.0,
            "sections": {},
        }
        for section_code, section in course.sections.items():
            course_data["sections"][section_code] = {
                "type": section.section_type or "",
                "instructor": section.instructor or "",
                "currentEnrollment": section.enrollment,
                "currentCapacity": section.capacity,
                "currentFill": section.fill,
                "sectionId": section_ids[(course_code, section_code)],
                "history": [],
            }
        data["courses"][course_code] = course_data

    def append_event(course_code: str, event: dict[str, Any]) -> None:
        data["courses"][course_code].setdefault("events", []).append(event)

    for snapshot_index, (_, snapshot) in enumerate(reconstructed):
        for course_code, course_data in data["courses"].items():
            course = snapshot.courses.get(course_code)
            if course is None:
                continue
            for section_code, section_data in course_data["sections"].items():
                section = course.sections.get(section_code)
                if section is None:
                    continue
                section_data["history"].append(
                    {
                        "snapshotIdx": snapshot_index,
                        "fill": section.fill,
                        "enrollment": section.enrollment,
                        "capacity": section.capacity,
                    }
                )

    for index in range(1, len(reconstructed)):
        previous = reconstructed[index - 1][1]
        current = reconstructed[index][1]
        timestamp = current.timestamp
        previous_courses = set(previous.courses)
        current_courses = set(current.courses)
        for course_code in sorted(current_courses - previous_courses):
            if course_code in data["courses"]:
                append_event(
                    course_code,
                    {"eventType": "course_added", "snapshotTimestamp": timestamp},
                )
        for course_code in sorted(previous_courses - current_courses):
            if course_code in data["courses"]:
                append_event(
                    course_code,
                    {"eventType": "course_removed", "snapshotTimestamp": timestamp},
                )
        for course_code in sorted(previous_courses & current_courses):
            if course_code not in data["courses"]:
                continue
            previous_sections = previous.courses[course_code].sections
            current_sections = current.courses[course_code].sections
            for section_code in sorted(set(current_sections) - set(previous_sections)):
                append_event(
                    course_code,
                    {
                        "eventType": "section_added",
                        "sectionCode": section_code,
                        "snapshotTimestamp": timestamp,
                    },
                )
            for section_code in sorted(set(previous_sections) - set(current_sections)):
                append_event(
                    course_code,
                    {
                        "eventType": "section_removed",
                        "sectionCode": section_code,
                        "snapshotTimestamp": timestamp,
                    },
                )
            for section_code in sorted(set(previous_sections) & set(current_sections)):
                old = previous_sections[section_code]
                new = current_sections[section_code]
                if old.capacity != new.capacity:
                    append_event(
                        course_code,
                        {
                            "eventType": "capacity_changed",
                            "sectionCode": section_code,
                            "oldValue": str(old.capacity),
                            "newValue": str(new.capacity),
                            "snapshotTimestamp": timestamp,
                        },
                    )
                if instructor_identity(old.instructor) != instructor_identity(
                    new.instructor
                ):
                    append_event(
                        course_code,
                        {
                            "eventType": "instructor_changed",
                            "sectionCode": section_code,
                            "oldValue": old.instructor or "TBA",
                            "newValue": new.instructor or "TBA",
                            "snapshotTimestamp": timestamp,
                        },
                    )

    milestones = get_milestones(semester)
    if milestones:
        keep_indices = _history_indices_for_website(
            data["snapshots"],
            milestones,
            buffer_hours=WEBSITE_HISTORY_BUFFER_HOURS,
        )
        if keep_indices is not None:
            for course_data in data["courses"].values():
                for section_data in course_data["sections"].values():
                    section_data["history"] = [
                        entry
                        for entry in section_data["history"]
                        if entry["snapshotIdx"] in keep_indices
                    ]

    for course_data in data["courses"].values():
        sections = course_data["sections"]
        total_fill = sum(section["currentFill"] for section in sections.values())
        course_data["averageFill"] = total_fill / len(sections)
        sections_by_type: dict[str, list[float]] = {}
        for section in sections.values():
            sections_by_type.setdefault(section.get("type", ""), []).append(
                section["currentFill"]
            )
        course_data["isFilled"] = any(
            fills and all(fill >= 1.0 for fill in fills)
            for fills in sections_by_type.values()
        )

    _compact_histories_for_website(data)
    return _minify_keys(data) if minify else data


def get_semester_data(
    semester: str,
    *,
    minify: bool = True,
    database: DatabaseManager | None = None,
) -> dict[str, Any]:
    """
    Query the database for all course, section, and enrollment data.

    Args:
        semester: Semester name (e.g., "Spring 2026")
        minify: Whether to minify JSON keys for smaller output

    Returns:
        Dictionary with all data needed for the website.
    """
    db = database or DatabaseManager(semester=semester)
    if db.storage_mode in {"v2", "finalized"}:
        return _checkpointed_semester_data(semester, db, minify=minify)

    data: dict[str, Any] = {
        "semester": semester,
        "lastReportTime": None,
        "snapshots": [],
        "courses": {},
    }

    with db.get_connection() as conn:
        cursor = conn.cursor()

        # Get all snapshots for this semester (ordered by timestamp)
        cursor.execute(
            """
            SELECT snapshot_id, timestamp, overall_fill
            FROM snapshots
            ORDER BY timestamp ASC
        """
        )

        snapshots = cursor.fetchall()
        snapshot_id_to_idx: dict[int, int] = {}

        for idx, (snapshot_id, timestamp, overall_fill) in enumerate(snapshots):
            data["snapshots"].append(
                {
                    "id": snapshot_id,
                    "timestamp": timestamp,
                    "overallFill": overall_fill,
                }
            )
            snapshot_id_to_idx[snapshot_id] = idx

        # Set last report time to the latest snapshot
        if snapshots:
            data["lastReportTime"] = snapshots[-1][1]

        if not snapshots:
            print(f"No snapshots found for semester: {semester}")
            if minify:
                return _minify_keys(data)
            return data

        # Get latest snapshot ID
        latest_snapshot_id = snapshots[-1][0]

        # Get all courses
        cursor.execute("""
            SELECT course_id, course_code, course_title, department
            FROM courses
            ORDER BY course_code
        """)
        courses = cursor.fetchall()

        course_id_to_code: dict[int, str] = {}
        for course_id, course_code, course_title, department in courses:
            course_id_to_code[course_id] = course_code
            data["courses"][course_code] = {
                "department": department
                or (course_code.split()[0] if course_code else ""),
                "title": course_title or "",
                "averageFill": 0.0,
                "sections": {},
            }

        # Get all sections with their latest enrollment data
        cursor.execute(
            """
            SELECT
                s.section_id,
                s.course_id,
                s.section_code,
                s.section_type,
                s.instructor,
                ed.enrollment_count,
                ed.capacity_count,
                ed.fill_percentage
            FROM sections s
            JOIN enrollment_data ed ON s.section_id = ed.section_id
            WHERE ed.snapshot_id = ?
        """,
            (latest_snapshot_id,),
        )

        section_id_to_info: dict[int, tuple[str, str]] = {}

        for row in cursor.fetchall():
            (
                section_id,
                course_id,
                section_code,
                section_type,
                instructor,
                enrollment,
                capacity,
                fill,
            ) = row
            course_code = course_id_to_code.get(course_id)

            if not course_code or course_code not in data["courses"]:
                continue

            section_id_to_info[section_id] = (course_code, section_code)

            data["courses"][course_code]["sections"][section_code] = {
                "type": section_type or "",
                "instructor": instructor or "",
                "currentEnrollment": enrollment,
                "currentCapacity": capacity,
                "currentFill": fill,
                "sectionId": section_id,
                "history": [],
            }

        # Get enrollment history for all sections
        cursor.execute("""
            SELECT
                ed.section_id,
                ed.snapshot_id,
                ed.fill_percentage,
                ed.enrollment_count,
                ed.capacity_count
            FROM enrollment_data ed
            ORDER BY ed.snapshot_id ASC
        """)

        for (
            section_id,
            snapshot_id,
            fill_percentage,
            enrollment_count,
            capacity_count,
        ) in cursor.fetchall():
            if section_id not in section_id_to_info:
                continue
            if snapshot_id not in snapshot_id_to_idx:
                continue

            course_code, section_code = section_id_to_info[section_id]

            if course_code not in data["courses"]:
                continue
            if section_code not in data["courses"][course_code]["sections"]:
                continue

            data["courses"][course_code]["sections"][section_code]["history"].append(
                {
                    "snapshotIdx": snapshot_id_to_idx[snapshot_id],
                    "fill": fill_percentage,
                    "enrollment": enrollment_count,
                    "capacity": capacity_count,
                }
            )

    # Apply milestone-based filtering with a 24-hour buffer on both sides.
    # The snapshots array stays intact so counts and snapshotIdx references remain stable.
    milestones = get_milestones(semester)
    if milestones and data["snapshots"]:
        keep_indices = _history_indices_for_website(
            data["snapshots"],
            milestones,
            buffer_hours=WEBSITE_HISTORY_BUFFER_HOURS,
        )
        if keep_indices is not None:
            for course_data in data["courses"].values():
                for section_data in course_data["sections"].values():
                    section_data["history"] = [
                        entry
                        for entry in section_data["history"]
                        if entry["snapshotIdx"] in keep_indices
                    ]

    # Calculate average fill and isFilled for each course
    for course_data in data["courses"].values():
        sections = course_data["sections"]
        if sections:
            total_fill = sum(s["currentFill"] for s in sections.values())
            course_data["averageFill"] = total_fill / len(sections)

            # Compute isFilled: True when all sections of at least one type are >= 100%
            sections_by_type: dict[str, list[float]] = {}
            for section in sections.values():
                sec_type = section.get("type", "")
                if sec_type not in sections_by_type:
                    sections_by_type[sec_type] = []
                sections_by_type[sec_type].append(section["currentFill"])

            course_data["isFilled"] = any(
                fills and all(f >= 1.0 for f in fills)
                for fills in sections_by_type.values()
            )

    _compact_histories_for_website(data)

    # Build and attach course events
    try:
        course_events = _build_course_events(semester, db)
        for course_code, events in course_events.items():
            if course_code in data["courses"]:
                data["courses"][course_code]["events"] = events
    except Exception as e:
        print(f"Warning: Failed to build course events: {e}")

    # Remove courses with no sections
    data["courses"] = {
        code: course for code, course in data["courses"].items() if course["sections"]
    }

    if minify:
        return _minify_keys(data)
    return data
