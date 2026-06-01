"""Data access layer for querying enrollment data from the database."""

from datetime import datetime, timedelta
import sqlite3
from typing import Any

from registrarmonitor.data.database_manager import DatabaseManager

from .config import ALL_SEMESTERS, KEY_MAP, MILESTONES_MAP


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
                milestone_times.append(datetime.fromisoformat(time_str))
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
            ts = datetime.fromisoformat(ts_str)

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
                milestone_times.append(datetime.fromisoformat(time_str))
        except (ValueError, TypeError):
            continue

    if not milestone_times:
        return None

    window_start = min(milestone_times) - timedelta(hours=buffer_hours)
    window_end = max(milestone_times) + timedelta(hours=buffer_hours)
    keep: set[int] = set()
    for idx, snapshot in enumerate(snapshots):
        try:
            ts = datetime.fromisoformat(snapshot.get("timestamp", ""))
        except (ValueError, TypeError):
            continue
        if window_start <= ts <= window_end:
            keep.add(idx)

    return keep


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
            # Pre-fetch all enrollment data grouped by snapshot.
            def _get_snapshot_state(
                snapshot_id: int,
            ) -> dict[str, dict[str, dict[str, Any]]]:
                """Get state of all courses/sections for a snapshot."""
                cursor.execute(
                    """
                    SELECT c.course_code, s.section_code,
                           ed.enrollment_count, ed.capacity_count
                    FROM enrollment_data ed
                    JOIN sections s ON ed.section_id = s.section_id
                    JOIN courses c ON s.course_id = c.course_id
                    WHERE ed.snapshot_id = ?
                    """,
                    (snapshot_id,),
                )
                state: dict[str, dict[str, dict[str, Any]]] = {}
                for row in cursor.fetchall():
                    course_code, section_code, enrollment, capacity = row
                    if course_code not in state:
                        state[course_code] = {}
                    state[course_code][section_code] = {
                        "enrollment": enrollment,
                        "capacity": capacity,
                    }
                return state

            prev_state = _get_snapshot_state(snapshots[0][0])

            for i in range(1, len(snapshots)):
                snapshot_id = snapshots[i][0]
                snapshot_ts = snapshots[i][1]
                curr_state = _get_snapshot_state(snapshot_id)

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


def get_semester_data(semester: str, *, minify: bool = True) -> dict[str, Any]:
    """
    Query the database for all course, section, and enrollment data.

    Args:
        semester: Semester name (e.g., "Spring 2026")
        minify: Whether to minify JSON keys for smaller output

    Returns:
        Dictionary with all data needed for the website.
    """
    db = DatabaseManager(semester=semester)

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

    # Apply milestone-based filtering to trim histories outside registration window.
    # The snapshots array stays intact so counts and snapshotIdx references remain stable.
    milestones = MILESTONES_MAP.get(semester, [])
    if milestones and data["snapshots"]:
        keep_indices = _history_indices_in_milestone_window(
            data["snapshots"], milestones, buffer_hours=1
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


def get_combined_data(*, minify: bool = True) -> dict[str, Any]:
    """
    Get data for all semesters combined into a single structure.

    Args:
        minify: Whether to minify JSON keys for smaller output

    Returns:
        Combined data structure with all semesters accessible via toggle.
    """
    combined: dict[str, Any] = {
        "semesters": ALL_SEMESTERS,
        "activeSemester": ALL_SEMESTERS[0],
        "semesterData": {},
        "milestonesData": {},
    }

    for semester in ALL_SEMESTERS:
        print(f"  Loading {semester}...")
        # Get data with minify=False since we'll minify the whole structure at the end
        data = get_semester_data(semester, minify=False)
        combined["semesterData"][semester] = data
        combined["milestonesData"][semester] = MILESTONES_MAP.get(semester, [])

    if minify:
        return _minify_keys(combined)
    return combined
