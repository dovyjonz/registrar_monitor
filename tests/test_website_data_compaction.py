"""Tests."""

import pytest

pytestmark = pytest.mark.unit


from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.website.data import (
    _add_course_average_history,
    _build_course_events,
    _compact_average_history,
    _compact_histories_for_website,
    _compact_section_history,
)


def test_section_history_compaction_preserves_endpoints_changes_and_previous_points():
    history = [
        {"snapshotIdx": 0, "fill": 0.1, "enrollment": 1, "capacity": 10},
        {"snapshotIdx": 1, "fill": 0.1, "enrollment": 1, "capacity": 10},
        {"snapshotIdx": 2, "fill": 0.2, "enrollment": 2, "capacity": 10},
        {"snapshotIdx": 3, "fill": 0.2, "enrollment": 2, "capacity": 10},
        {"snapshotIdx": 4, "fill": 0.2, "enrollment": 2, "capacity": 12},
        {"snapshotIdx": 5, "fill": 0.2, "enrollment": 2, "capacity": 12},
    ]

    compacted = _compact_section_history(history)

    assert [point["snapshotIdx"] for point in compacted] == [0, 1, 2, 3, 4, 5]


def test_section_history_compaction_removes_unchanged_middle_points():
    history = [
        {"snapshotIdx": 0, "fill": 0.1, "enrollment": 1, "capacity": 10},
        {"snapshotIdx": 1, "fill": 0.1, "enrollment": 1, "capacity": 10},
        {"snapshotIdx": 2, "fill": 0.1, "enrollment": 1, "capacity": 10},
        {"snapshotIdx": 3, "fill": 0.1, "enrollment": 1, "capacity": 10},
    ]

    compacted = _compact_section_history(history)

    assert [point["snapshotIdx"] for point in compacted] == [0, 3]


def test_course_average_history_uses_compacted_section_points():
    course = {
        "sections": {
            "10L": {
                "history": [
                    {"snapshotIdx": 0, "fill": 0.5},
                    {"snapshotIdx": 2, "fill": 0.7},
                ],
            },
            "11L": {
                "history": [
                    {"snapshotIdx": 0, "fill": 0.25},
                    {"snapshotIdx": 2, "fill": 0.75},
                ],
            },
        }
    }

    _add_course_average_history(course)

    assert course["averageHistory"] == [
        {"snapshotIdx": 0, "fill": 0.375},
        {"snapshotIdx": 2, "fill": 0.725},
    ]


def test_compact_histories_builds_course_average_before_section_compaction():
    data = {
        "courses": {
            "CSC 100": {
                "sections": {
                    "10L": {
                        "history": [
                            {
                                "snapshotIdx": 0,
                                "fill": 0.5,
                                "enrollment": 5,
                                "capacity": 10,
                            },
                            {
                                "snapshotIdx": 1,
                                "fill": 0.7,
                                "enrollment": 7,
                                "capacity": 10,
                            },
                            {
                                "snapshotIdx": 2,
                                "fill": 0.7,
                                "enrollment": 7,
                                "capacity": 10,
                            },
                        ],
                    },
                    "11L": {
                        "history": [
                            {
                                "snapshotIdx": 0,
                                "fill": 0.25,
                                "enrollment": 5,
                                "capacity": 20,
                            },
                            {
                                "snapshotIdx": 1,
                                "fill": 0.25,
                                "enrollment": 5,
                                "capacity": 20,
                            },
                            {
                                "snapshotIdx": 2,
                                "fill": 0.25,
                                "enrollment": 5,
                                "capacity": 20,
                            },
                        ],
                    },
                },
            },
        },
    }

    _compact_histories_for_website(data)

    course = data["courses"]["CSC 100"]
    assert course["sections"]["11L"]["history"] == [
        {"snapshotIdx": 0, "fill": 0.25, "enrollment": 5, "capacity": 20},
        {"snapshotIdx": 2, "fill": 0.25, "enrollment": 5, "capacity": 20},
    ]
    assert course["averageHistory"] == [
        {"snapshotIdx": 0, "fill": 0.375},
        {"snapshotIdx": 1, "fill": 0.475},
        {"snapshotIdx": 2, "fill": 0.475},
    ]


def test_average_history_compaction_preserves_previous_point_before_change():
    """When average fill changes, keep the point before the change as anchor for stepped interpolation."""
    history = [
        {"snapshotIdx": 0, "fill": 0.0},
        {"snapshotIdx": 1, "fill": 0.0},
        {"snapshotIdx": 2, "fill": 0.0},
        {"snapshotIdx": 3, "fill": 0.4},
        {"snapshotIdx": 4, "fill": 0.4},
    ]

    compacted = _compact_average_history(history)

    # idx 0 endpoint, idx 2 is before-change anchor, idx 3 is change, idx 4 endpoint
    assert [point["snapshotIdx"] for point in compacted] == [0, 2, 3, 4]


def test_average_history_compaction_removes_unchanged_middle_points():
    history = [
        {"snapshotIdx": 0, "fill": 0.5},
        {"snapshotIdx": 1, "fill": 0.5},
        {"snapshotIdx": 2, "fill": 0.5},
        {"snapshotIdx": 3, "fill": 0.5},
    ]

    assert _compact_average_history(history) == [
        {"snapshotIdx": 0, "fill": 0.5},
        {"snapshotIdx": 3, "fill": 0.5},
    ]


def test_compact_histories_compacts_average_after_full_history_calculation():
    data = {
        "courses": {
            "CSC 100": {
                "sections": {
                    "10L": {
                        "history": [
                            {
                                "snapshotIdx": 0,
                                "fill": 0.5,
                                "enrollment": 5,
                                "capacity": 10,
                            },
                            {
                                "snapshotIdx": 1,
                                "fill": 0.7,
                                "enrollment": 7,
                                "capacity": 10,
                            },
                            {
                                "snapshotIdx": 2,
                                "fill": 0.7,
                                "enrollment": 7,
                                "capacity": 10,
                            },
                        ],
                    },
                    "11L": {
                        "history": [
                            {
                                "snapshotIdx": 0,
                                "fill": 0.25,
                                "enrollment": 5,
                                "capacity": 20,
                            },
                            {
                                "snapshotIdx": 1,
                                "fill": 0.25,
                                "enrollment": 5,
                                "capacity": 20,
                            },
                            {
                                "snapshotIdx": 2,
                                "fill": 0.25,
                                "enrollment": 5,
                                "capacity": 20,
                            },
                        ],
                    },
                },
            },
        },
    }

    _compact_histories_for_website(data)

    assert data["courses"]["CSC 100"]["averageHistory"] == [
        {"snapshotIdx": 0, "fill": 0.375},
        {"snapshotIdx": 1, "fill": 0.475},
        {"snapshotIdx": 2, "fill": 0.475},
    ]


def test_instructor_events_dedupe_consecutive_duplicates_and_preserve_toggles(
    tmp_path,
):
    db = DatabaseManager(db_path=str(tmp_path / "events.db"), semester="Test 2024")
    course_id = db.insert_course("BUS 101", "Business", "BUS")
    section_id = db.insert_section(course_id, "10L", "L", "B")

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT INTO instructor_changes
            (section_id, old_instructor, new_instructor, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    section_id,
                    "Park, Chun Young",
                    "Chun Young Park",
                    "2024-01-15T09:00:00",
                ),
                (
                    section_id,
                    "Ted Parent, Parent, Ted",
                    "Ted Parent",
                    "2024-01-15T09:30:00",
                ),
                (section_id, "A", "B", "2024-01-15T10:00:00"),
                (section_id, "A", "B", "2024-01-15T10:01:00"),
                (section_id, "B", "A", "2024-01-15T10:02:00"),
                (section_id, "A", "B", "2024-01-15T10:03:00"),
            ],
        )
        conn.commit()

    events = _build_course_events("Test 2024", db)["BUS 101"]

    assert [
        (event["oldValue"], event["newValue"])
        for event in events
        if event["eventType"] == "instructor_changed"
    ] == [("A", "B"), ("B", "A"), ("A", "B")]


def test_snapshot_instructor_transition_is_recorded_once_and_emitted(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "events.db"), semester="Test 2024")

    def snapshot(timestamp: str, instructor: str, enrollment: int):
        section = Section(
            "10L",
            "L",
            enrollment,
            20,
            enrollment / 20,
            instructor,
        )
        course = Course(
            "BUS 101",
            "BUS",
            {"10L": section},
            enrollment / 20,
            "Business",
        )
        return EnrollmentSnapshot(
            timestamp=timestamp,
            semester="Test 2024",
            overall_fill=enrollment / 20,
            courses={"BUS 101": course},
        )

    db.store_enrollment_snapshot(snapshot("2024-01-15T10:00:00", "Alice", 10))
    db.store_enrollment_snapshot(snapshot("2024-01-15T10:15:00", "Bob", 11))
    db.store_enrollment_snapshot(snapshot("2024-01-15T10:30:00", "Bob", 12))

    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT old_instructor, new_instructor, timestamp
            FROM instructor_changes
            ORDER BY change_id
            """
        ).fetchall()

    assert [tuple(row) for row in rows] == [("Alice", "Bob", "2024-01-15T10:15:00")]

    instructor_events = [
        event
        for event in _build_course_events("Test 2024", db)["BUS 101"]
        if event["eventType"] == "instructor_changed"
    ]
    assert instructor_events == [
        {
            "eventType": "instructor_changed",
            "sectionCode": "10L",
            "oldValue": "Alice",
            "newValue": "Bob",
            "snapshotTimestamp": "2024-01-15T10:15:00",
        }
    ]


def test_snapshot_formatting_only_instructor_change_is_not_an_event(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "events.db"), semester="Test 2024")

    def snapshot(timestamp: str, instructor: str, enrollment: int):
        section = Section(
            "10L",
            "L",
            enrollment,
            20,
            enrollment / 20,
            instructor,
        )
        course = Course(
            "BUS 101",
            "BUS",
            {"10L": section},
            enrollment / 20,
            "Business",
        )
        return EnrollmentSnapshot(
            timestamp=timestamp,
            semester="Test 2024",
            overall_fill=enrollment / 20,
            courses={"BUS 101": course},
        )

    db.store_enrollment_snapshot(snapshot("2024-01-15T10:00:00", "Mun, Ellina", 10))
    db.store_enrollment_snapshot(snapshot("2024-01-15T10:15:00", "Ellina Mun", 11))

    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT old_instructor, new_instructor FROM instructor_changes"
        ).fetchall()

    assert rows == []
    assert [
        event
        for event in _build_course_events("Test 2024", db).get("BUS 101", [])
        if event["eventType"] == "instructor_changed"
    ] == []


def test_removed_section_clears_stale_instructor_before_reappearing(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "events.db"), semester="Test 2024")

    def snapshot(timestamp: str, instructor: str | None):
        courses = {}
        overall_fill = 0.0
        if instructor is not None:
            section = Section("10L", "L", 10, 20, 0.5, instructor)
            courses["BUS 101"] = Course(
                "BUS 101",
                "BUS",
                {"10L": section},
                0.5,
                "Business",
            )
            overall_fill = 0.5
        return EnrollmentSnapshot(
            timestamp=timestamp,
            semester="Test 2024",
            overall_fill=overall_fill,
            courses=courses,
        )

    db.store_enrollment_snapshot(snapshot("2024-01-15T10:00:00", "Alice"))
    db.store_enrollment_snapshot(snapshot("2024-01-15T10:15:00", None))
    db.store_enrollment_snapshot(snapshot("2024-01-15T10:30:00", "Bob"))

    with db.get_connection() as conn:
        instructor = conn.execute(
            "SELECT instructor FROM sections WHERE section_code = '10L'"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT old_instructor, new_instructor, timestamp
            FROM instructor_changes
            ORDER BY change_id
            """
        ).fetchall()

    assert instructor == "Bob"
    assert [tuple(row) for row in rows] == [
        ("Alice", "", "2024-01-15T10:15:00"),
        ("", "Bob", "2024-01-15T10:30:00"),
    ]
