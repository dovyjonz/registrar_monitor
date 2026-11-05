"""Data access layer for querying enrollment data from the database."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any

from registrarmonitor.config import get_timezone
from registrarmonitor.data.checkpointed_state import CheckpointedStateStore
from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.data.instructor_normalization import instructor_identity

from .config import KEY_MAP, get_milestones

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
    """Keep only real observations inside the buffered milestone window."""
    return _history_indices_in_milestone_window(
        snapshots,
        milestones,
        buffer_hours=buffer_hours,
    )


# A run is ``(first, last, enrollment, capacity)``: a section was present at every
# history position in ``first..last`` with constant enrollment and capacity.
_Run = tuple[int, int, int, int]
_AVERAGE_FILL_TOLERANCE = 1e-9


def _compact_section_history(
    runs: Sequence[_Run], snapshot_indices: Sequence[int]
) -> list[dict[str, Any]]:
    """
    Keep endpoints plus every enrollment/capacity change and its previous point.

    ``runs`` are a section's chronological runs and ``snapshot_indices`` maps a
    history position to its snapshot index.
    """
    if not runs:
        return []

    keep = {
        runs[0][0]: (runs[0][2], runs[0][3]),
        runs[-1][1]: (runs[-1][2], runs[-1][3]),
    }
    for previous, run in pairwise(runs):
        if previous[2:] != run[2:]:
            keep[previous[1]] = (previous[2], previous[3])
            keep[run[0]] = (run[2], run[3])

    return [
        {
            "snapshotIdx": snapshot_indices[position],
            "fill": enrollment / capacity,
            "enrollment": enrollment,
            "capacity": capacity,
        }
        for position, (enrollment, capacity) in sorted(keep.items())
    ]


def _compact_average_history(
    section_runs: Sequence[Sequence[_Run]],
    snapshot_indices: Sequence[int],
) -> list[dict[str, Any]]:
    """
    Build the course-level average fill history from its sections' runs.

    The average only changes where some section starts, stops, or changes a run,
    so it is evaluated once per segment between those boundaries. Keeps endpoints
    plus points where the average changes and the point before each change.
    """
    boundaries = sorted(
        {run[0] for runs in section_runs for run in runs}
        | {run[1] + 1 for runs in section_runs for run in runs}
    )
    cursors = [0] * len(section_runs)
    segments: list[tuple[int, int, float]] = []
    for start, stop in pairwise(boundaries):
        fills: list[float] = []
        for index, runs in enumerate(section_runs):
            cursor = cursors[index]
            while cursor < len(runs) and runs[cursor][1] < start:
                cursor += 1
            cursors[index] = cursor
            if cursor < len(runs) and runs[cursor][0] <= start:
                fills.append(runs[cursor][2] / runs[cursor][3])
        if fills:
            segments.append((start, stop - 1, sum(fills) / len(fills)))

    if not segments:
        return []

    keep = {segments[0][0]: segments[0][2], segments[-1][1]: segments[-1][2]}
    for previous, segment in pairwise(segments):
        if abs(segment[2] - previous[2]) > _AVERAGE_FILL_TOLERANCE:
            keep[previous[1]] = previous[2]
            keep[segment[0]] = segment[2]

    return [
        {"snapshotIdx": snapshot_indices[position], "fill": fill}
        for position, fill in sorted(keep.items())
    ]


def _instructor_changed(old: str | None, new: str | None) -> bool:
    """Compare raw strings first so identical names are never tokenized."""
    return old != new and instructor_identity(old) != instructor_identity(new)


_SectionState = tuple[str, int, int, str]


def _append_change_events(
    events_by_course: dict[str, list[dict[str, Any]]],
    timestamp: str,
    *,
    courses: dict[str, Any],
    sections: dict[tuple[str, str], _SectionState],
    changed_courses: dict[str, bool],
    changed_sections: dict[tuple[str, str], _SectionState | None],
) -> None:
    """
    Append structural events for what changed since the previous observation.

    ``changed_courses`` maps each touched course to whether it was present before
    and ``changed_sections`` maps each touched section to its previous state, so
    only touched entries are compared.

    Tracked events: course_added/removed, section_added/removed,
    capacity_changed, instructor_changed.
    """
    for course_code in sorted(changed_courses):
        if course_code in courses and not changed_courses[course_code]:
            events_by_course.setdefault(course_code, []).append(
                {"eventType": "course_added", "snapshotTimestamp": timestamp}
            )
    for course_code in sorted(changed_courses):
        if course_code not in courses and changed_courses[course_code]:
            events_by_course.setdefault(course_code, []).append(
                {"eventType": "course_removed", "snapshotTimestamp": timestamp}
            )

    section_codes_by_course: dict[str, list[str]] = {}
    for course_code, section_code in changed_sections:
        section_codes_by_course.setdefault(course_code, []).append(section_code)

    for course_code in sorted(section_codes_by_course):
        present = course_code in courses
        if not (present and changed_courses.get(course_code, present)):
            continue
        added: list[str] = []
        removed: list[str] = []
        shared: list[str] = []
        for section_code in section_codes_by_course[course_code]:
            old = changed_sections[(course_code, section_code)]
            new = sections.get((course_code, section_code))
            if old is None and new is not None:
                added.append(section_code)
            elif old is not None and new is None:
                removed.append(section_code)
            elif old is not None and new is not None:
                shared.append(section_code)

        events: list[dict[str, Any]] = []
        for section_code in sorted(added):
            events.append(
                {
                    "eventType": "section_added",
                    "sectionCode": section_code,
                    "snapshotTimestamp": timestamp,
                }
            )
        for section_code in sorted(removed):
            events.append(
                {
                    "eventType": "section_removed",
                    "sectionCode": section_code,
                    "snapshotTimestamp": timestamp,
                }
            )
        for section_code in sorted(shared):
            old = changed_sections[(course_code, section_code)]
            new = sections[(course_code, section_code)]
            if old is None:
                continue
            if old[2] != new[2]:
                events.append(
                    {
                        "eventType": "capacity_changed",
                        "sectionCode": section_code,
                        "oldValue": str(old[2]),
                        "newValue": str(new[2]),
                        "snapshotTimestamp": timestamp,
                    }
                )
            if _instructor_changed(old[3], new[3]):
                events.append(
                    {
                        "eventType": "instructor_changed",
                        "sectionCode": section_code,
                        "oldValue": old[3] or "TBA",
                        "newValue": new[3] or "TBA",
                        "snapshotTimestamp": timestamp,
                    }
                )
        if events:
            events_by_course.setdefault(course_code, []).extend(events)


def _checkpointed_semester_data(
    semester: str,
    db: DatabaseManager,
    *,
    minify: bool,
    archive_window: bool,
) -> dict[str, Any]:
    """
    Build the production payload by folding stored change events over one state.

    No full snapshot is reconstructed. The initial checkpoint state is mutated by
    each observation's events, and only the sections and courses an interval
    touched are compared, so the cost follows the number of changes rather than
    snapshots times sections. Sections always belong to a present course because
    the store writes them from a snapshot's courses.
    """
    store = CheckpointedStateStore(db.db_path, initialize=False)
    with db.get_connection() as connection:
        metadata: list[dict[str, Any]] = [
            {
                "id": int(row[0]),
                "timestamp": str(row[1]),
                "overallFill": float(row[2]),
            }
            for row in connection.execute(
                """
                SELECT snapshot_id, observed_at, overall_fill
                FROM state_snapshot ORDER BY sequence_no
                """
            )
        ]
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

    stored_snapshot_ids = [item["id"] for item in metadata]
    milestones = get_milestones(semester)
    selected_indices: set[int] | None = None
    if archive_window and metadata:
        selected_indices = _history_indices_for_website(
            metadata,
            milestones,
            buffer_hours=WEBSITE_HISTORY_BUFFER_HOURS,
        )
        if selected_indices is not None:
            metadata = [
                value
                for index, value in enumerate(metadata)
                if index in selected_indices
            ]

    data: dict[str, Any] = {
        "semester": semester,
        "lastReportTime": None,
        "snapshots": metadata,
        "courses": {},
    }
    if not metadata:
        print(f"No snapshots found for semester: {semester}")
        return _minify_keys(data) if minify else data

    history_indices = _history_indices_for_website(
        metadata,
        milestones,
        buffer_hours=WEBSITE_HISTORY_BUFFER_HOURS,
    )
    selected_global_indices = (
        list(range(len(metadata)))
        if selected_indices is None
        else sorted(selected_indices)
    )
    local_index_by_global = {
        global_index: local_index
        for local_index, global_index in enumerate(selected_global_indices)
    }
    # Snapshot indices that contribute history points, in order; a history
    # position is an offset into this list.
    history_snapshot_indices = (
        list(range(len(metadata)))
        if history_indices is None
        else sorted(history_indices)
    )
    history_position = {
        snapshot_index: position
        for position, snapshot_index in enumerate(history_snapshot_indices)
    }

    replay = store.read_state_replay()
    courses = replay.courses
    sections = replay.sections
    events_by_course: dict[str, list[dict[str, Any]]] = {}
    # Touched since the previous observation (for events) or history position.
    changed_courses: dict[str, bool] = {}
    changed_sections: dict[tuple[str, str], _SectionState | None] = {}
    changed_since_history: set[tuple[str, str]] = set()
    open_runs: dict[tuple[str, str], tuple[int, int, int]] = {}
    runs: dict[tuple[str, str], list[_Run]] = {}

    for global_index in range(selected_global_indices[-1] + 1):
        snapshot_id = stored_snapshot_ids[global_index]
        if global_index > 0:
            for row in replay.course_events.get(snapshot_id, ()):
                code = row["course_code"]
                changed_courses.setdefault(code, code in courses)
                if row["event_kind"] == "REMOVE":
                    courses.pop(code, None)
                else:
                    courses[code] = (row["new_title"] or "", row["new_department"])
            for row in replay.section_events.get(snapshot_id, ()):
                key = (row["course_code"], row["section_code"])
                changed_sections.setdefault(key, sections.get(key))
                changed_since_history.add(key)
                if row["event_kind"] == "REMOVE":
                    sections.pop(key, None)
                else:
                    sections[key] = (
                        row["new_section_type"],
                        row["new_enrollment_count"],
                        row["new_capacity_count"],
                        row["new_instructor"],
                    )

        snapshot_index = local_index_by_global.get(global_index)
        if snapshot_index is None:
            continue

        position = history_position.get(snapshot_index)
        if position == 0:
            for key, state in sections.items():
                open_runs[key] = (0, state[1], state[2])
        elif position is not None:
            for key in changed_since_history:
                state = sections.get(key)
                opened = open_runs.get(key)
                if opened is not None and (
                    state is None or (state[1], state[2]) != opened[1:]
                ):
                    runs.setdefault(key, []).append(
                        (opened[0], position - 1, opened[1], opened[2])
                    )
                    del open_runs[key]
                    opened = None
                if opened is None and state is not None:
                    open_runs[key] = (position, state[1], state[2])
        if position is not None:
            changed_since_history.clear()

        if snapshot_index > 0:
            _append_change_events(
                events_by_course,
                metadata[snapshot_index]["timestamp"],
                courses=courses,
                sections=sections,
                changed_courses=changed_courses,
                changed_sections=changed_sections,
            )
        changed_courses.clear()
        changed_sections.clear()

    last_position = len(history_snapshot_indices) - 1
    for key, (first, enrollment, capacity) in open_runs.items():
        runs.setdefault(key, []).append((first, last_position, enrollment, capacity))

    data["lastReportTime"] = metadata[-1]["timestamp"]
    sections_by_course: dict[str, list[tuple[str, _SectionState]]] = {}
    for (course_code, section_code), state in sections.items():
        if course_code in courses:
            sections_by_course.setdefault(course_code, []).append((section_code, state))
    for course_code, (title, department) in courses.items():
        course_data = {
            "department": department or (course_code.split()[0] if course_code else ""),
            "title": title or "",
            "averageFill": 0.0,
            "sections": {},
        }
        for section_code, state in sections_by_course.get(course_code, ()):
            section_type, enrollment, capacity, instructor = state
            course_data["sections"][section_code] = {
                "type": section_type or "",
                "instructor": instructor or "",
                "currentEnrollment": enrollment,
                "currentCapacity": capacity,
                "currentFill": enrollment / capacity,
                "sectionId": section_ids[(course_code, section_code)],
                "history": _compact_section_history(
                    runs.get((course_code, section_code), ()),
                    history_snapshot_indices,
                ),
            }
        if course_code in events_by_course:
            course_data["events"] = events_by_course[course_code]
        data["courses"][course_code] = course_data

    for course_code, course_data in data["courses"].items():
        course_sections = course_data["sections"]
        total_fill = sum(section["currentFill"] for section in course_sections.values())
        course_data["averageFill"] = total_fill / len(course_sections)
        sections_by_type: dict[str, list[float]] = {}
        for section in course_sections.values():
            sections_by_type.setdefault(section.get("type", ""), []).append(
                section["currentFill"]
            )
        course_data["isFilled"] = any(
            fills and all(fill >= 1.0 for fill in fills)
            for fills in sections_by_type.values()
        )
        course_data["averageHistory"] = _compact_average_history(
            [runs.get((course_code, code), ()) for code in course_sections],
            history_snapshot_indices,
        )

    return _minify_keys(data) if minify else data


def get_semester_data(
    semester: str,
    *,
    minify: bool = True,
    database: DatabaseManager | None = None,
    archive_window: bool = False,
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
    return _checkpointed_semester_data(
        semester,
        db,
        minify=minify,
        archive_window=archive_window,
    )
