"""Tests for the website data pipeline helper functions."""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.website.data import (
    _history_indices_for_website,
    _history_indices_in_milestone_window,
    _minify_keys,
    get_semester_data,
)


class TestMinifyKeys:
    def test_minifies_dict_keys(self):
        from registrarmonitor.website.config import KEY_MAP

        # Use a key we know is in KEY_MAP
        original = {"semester": "Spring 2024"}
        result = _minify_keys(original)
        expected_key = KEY_MAP.get("semester", "semester")
        assert expected_key in result

    def test_minifies_nested_dicts(self):
        result = _minify_keys({"courses": {"CS 101": {"semester": "test"}}})
        assert isinstance(result, dict)


class TestHistoryIndicesInMilestoneWindow:
    def test_returns_none_without_milestones(self):
        result = _history_indices_in_milestone_window(
            [{"timestamp": "2024-01-15T10:00:00"}], []
        )
        assert result is None

    def test_returns_indices_within_window(self):
        snapshots = [
            {"timestamp": "2024-01-15T10:00:00"},
            {"timestamp": "2024-01-15T14:00:00"},
        ]
        milestones = [{"time": "2024-01-15T12:00:00"}]
        result = _history_indices_in_milestone_window(
            snapshots, milestones, buffer_hours=3
        )
        assert result == {0, 1}


def test_history_indices_for_website_rejects_terminal_snapshot_outside_window():
    snapshots = [
        {"timestamp": "2024-01-15T11:00:00"},
        {"timestamp": "2024-01-15T12:00:00"},
        {"timestamp": "2024-01-16T12:00:00"},
    ]
    milestones = [{"time": "2024-01-15T12:00:00"}]

    result = _history_indices_for_website(
        snapshots,
        milestones,
        buffer_hours=1,
    )

    assert result == {0, 1}


def test_history_indices_for_website_keeps_a_24_hour_milestone_buffer():
    snapshots = [
        {"timestamp": "2024-01-14T11:59:59"},
        {"timestamp": "2024-01-14T12:00:00"},
        {"timestamp": "2024-01-15T12:00:00"},
        {"timestamp": "2024-01-16T12:00:00"},
    ]
    milestones = [{"time": "2024-01-15T12:00:00"}]

    result = _history_indices_for_website(snapshots, milestones)

    assert result == {1, 2, 3}


class TestGetSemesterData:
    def test_empty_database_returns_minimal_data(self, tmp_path):
        db = DatabaseManager(db_path=str(tmp_path / "empty.db"), semester="Spring 2024")

        result = get_semester_data("Spring 2024", minify=False, database=db)

        assert result["semester"] == "Spring 2024"
        assert result["snapshots"] == []
        assert result["courses"] == {}

    def test_stored_snapshot_is_exposed_with_full_and_minified_keys(self, tmp_path):
        db = DatabaseManager(db_path=str(tmp_path / "data.db"), semester="Spring 2024")
        section = Section("10L", "L", 25, 30, 25 / 30, "Dr. Smith")
        db.store_enrollment_snapshot(
            EnrollmentSnapshot(
                timestamp="2024-01-15 10:00:00",
                semester="Spring 2024",
                overall_fill=25 / 30,
                courses={
                    "CS 101": Course("CS 101", "CS", {"10L": section}, 25 / 30, "Intro")
                },
            )
        )

        full = get_semester_data("Spring 2024", minify=False, database=db)
        minified = get_semester_data("Spring 2024", minify=True, database=db)

        assert list(full["courses"]) == ["CS 101"]
        assert len(full["snapshots"]) == 1
        assert full["semester"] == "Spring 2024"
        assert list(minified["cr"]) == ["CS 101"]
        assert minified["sem"] == "Spring 2024"


def _store(db, timestamp, courses):
    built = {}
    for code, (title, sections) in courses.items():
        built[code] = Course(
            code,
            code.split()[0],
            {
                section_id: Section(
                    section_id,
                    section_id[-1],
                    enrolled,
                    capacity,
                    enrolled / capacity,
                    instructor,
                )
                for section_id, (enrolled, capacity, instructor) in sections.items()
            },
            0.0,
            title,
        )
    db.store_enrollment_snapshot(
        EnrollmentSnapshot(timestamp, "Spring 2024", 0.5, built)
    )


@pytest.fixture
def timeline_db(tmp_path):
    """Four observations; only the middle two fall inside the milestone window."""
    db = DatabaseManager(db_path=str(tmp_path / "timeline.db"), semester="Spring 2024")
    _store(
        db,
        "2024-03-01 00:00:00",
        {
            "CS 101": ("Intro", {"10L": (10, 30, "Smith, John")}),
            "MATH 1": ("Calc", {"1L": (5, 20, "TBA")}),
        },
    )
    # Reformatting the instructor is not a change; the new section is.
    _store(
        db,
        "2024-03-05 12:00:00",
        {
            "CS 101": (
                "Intro",
                {"10L": (20, 30, "John Smith"), "11L": (1, 10, "Lee, Ann")},
            ),
            "BIO 1": ("Cells", {"1L": (2, 10, "TBA")}),
        },
    )
    _store(
        db,
        "2024-03-05 13:00:00",
        {
            "CS 101": (
                "Intro",
                {"10L": (20, 40, "Ann Lee"), "11L": (1, 10, "Lee, Ann")},
            ),
            "BIO 1": ("Cells", {"1L": (2, 10, "TBA")}),
        },
    )
    _store(
        db,
        "2024-03-20 00:00:00",
        {
            "CS 101": (
                "Intro",
                {"10L": (25, 40, "Ann Lee"), "11L": (1, 10, "Lee, Ann")},
            ),
            "BIO 1": ("Cells", {"1L": (2, 10, "TBA")}),
        },
    )
    return db


MILESTONE = [{"time": "2024-03-05T12:00:00", "label": "Open", "color": "#000"}]


def _event_summary(course):
    return [
        (event["eventType"], event.get("sectionCode"), event["snapshotTimestamp"])
        for event in course.get("events", [])
    ]


class TestGetSemesterDataWindowing:
    def test_current_semester_lists_every_snapshot_but_windows_histories(
        self, timeline_db
    ):
        with patch(
            "registrarmonitor.website.data.get_milestones", return_value=MILESTONE
        ):
            data = get_semester_data(
                "Spring 2024", minify=False, database=timeline_db, archive_window=False
            )

        assert [s["timestamp"] for s in data["snapshots"]] == [
            "2024-03-01 00:00:00",
            "2024-03-05 12:00:00",
            "2024-03-05 13:00:00",
            "2024-03-20 00:00:00",
        ]
        assert data["lastReportTime"] == "2024-03-20 00:00:00"
        assert list(data["courses"]) == ["CS 101", "BIO 1"]
        cs = data["courses"]["CS 101"]
        assert [p["snapshotIdx"] for p in cs["sections"]["10L"]["history"]] == [1, 2]
        assert [p["snapshotIdx"] for p in cs["sections"]["11L"]["history"]] == [1, 2]
        assert [p["snapshotIdx"] for p in cs["averageHistory"]] == [1, 2]
        assert _event_summary(cs) == [
            ("section_added", "11L", "2024-03-05 12:00:00"),
            ("capacity_changed", "10L", "2024-03-05 13:00:00"),
            ("instructor_changed", "10L", "2024-03-05 13:00:00"),
        ]
        assert _event_summary(data["courses"]["BIO 1"]) == [
            ("course_added", None, "2024-03-05 12:00:00")
        ]

    def test_archived_semester_drops_snapshots_and_events_outside_the_window(
        self, timeline_db
    ):
        with patch(
            "registrarmonitor.website.data.get_milestones", return_value=MILESTONE
        ):
            data = get_semester_data(
                "Spring 2024", minify=False, database=timeline_db, archive_window=True
            )

        assert [s["timestamp"] for s in data["snapshots"]] == [
            "2024-03-05 12:00:00",
            "2024-03-05 13:00:00",
        ]
        assert data["lastReportTime"] == "2024-03-05 13:00:00"
        cs = data["courses"]["CS 101"]
        assert cs["sections"]["10L"]["currentEnrollment"] == 20
        assert [p["snapshotIdx"] for p in cs["sections"]["10L"]["history"]] == [0, 1]
        assert _event_summary(cs) == [
            ("capacity_changed", "10L", "2024-03-05 13:00:00"),
            ("instructor_changed", "10L", "2024-03-05 13:00:00"),
        ]
        assert "events" not in data["courses"]["BIO 1"]

    def test_without_milestones_every_snapshot_contributes_history(self, timeline_db):
        with patch("registrarmonitor.website.data.get_milestones", return_value=[]):
            data = get_semester_data(
                "Spring 2024", minify=False, database=timeline_db, archive_window=True
            )

        assert len(data["snapshots"]) == 4
        history = data["courses"]["CS 101"]["sections"]["10L"]["history"]
        assert [p["snapshotIdx"] for p in history] == [0, 1, 2, 3]
        assert "MATH 1" not in data["courses"]


def test_course_without_sections_has_zero_average_fill(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "bare.db"), semester="Spring 2024")
    db.store_enrollment_snapshot(
        EnrollmentSnapshot(
            "2024-03-01 00:00:00",
            "Spring 2024",
            0.0,
            {"CS 101": Course("CS 101", "CS", {}, 0.0, "Intro")},
        )
    )

    data = get_semester_data("Spring 2024", minify=False, database=db)

    assert data["courses"]["CS 101"]["sections"] == {}
    assert data["courses"]["CS 101"]["averageFill"] == 0.0
