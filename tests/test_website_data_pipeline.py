"""Tests for the website data pipeline helper functions."""

import pytest

pytestmark = pytest.mark.unit


from typing import Any, cast

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.website.data import (
    _add_course_average_history,
    _compact_average_history,
    _compact_histories_for_website,
    _compact_section_history,
    _filter_snapshots_to_milestone_window,
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

    def test_handles_lists(self):
        result = _minify_keys([{"semester": "Spring"}, {"semester": "Fall"}])
        assert isinstance(result, list)
        assert len(result) == 2

    def test_handles_primitives(self):
        assert _minify_keys("string") == "string"
        assert _minify_keys(42) == 42
        assert _minify_keys(None) is None

    def test_passes_unknown_keys_through(self):
        result = _minify_keys({"customKey": "value"})
        assert "customKey" in result


class TestFilterSnapshotsToMilestoneWindow:
    def test_no_milestones_returns_all(self):
        snapshots = [{"timestamp": "2024-01-15T10:00:00"}]
        result, index_map = _filter_snapshots_to_milestone_window(snapshots, [])
        assert result == snapshots
        assert index_map == {0: 0}

    def test_no_snapshots_returns_empty(self):
        result, index_map = _filter_snapshots_to_milestone_window(
            [], [{"time": "2024-01-15T10:00:00"}]
        )
        assert result == []
        assert index_map == {}

    def test_filters_within_window(self):
        snapshots = [
            {"timestamp": "2024-01-15T05:00:00"},  # outside window
            {
                "timestamp": "2024-01-15T10:00:00"
            },  # inside (milestone is at 12:00, buffer 2hr = 10:00)
            {"timestamp": "2024-01-15T14:00:00"},  # inside
            {
                "timestamp": "2024-01-15T15:00:00"
            },  # outside (14:00 + 2hr buffer = 14:00)
        ]
        milestones = [{"time": "2024-01-15T12:00:00"}]
        result, index_map = _filter_snapshots_to_milestone_window(
            snapshots, milestones, buffer_hours=2
        )
        assert len(result) == 2
        assert 0 not in index_map
        assert 1 in index_map
        assert 2 in index_map
        assert 3 not in index_map

    def test_filters_naive_snapshots_with_aware_milestones(self):
        snapshots = [{"timestamp": "2026-08-05T09:00:00"}]
        milestones = [{"time": "2026-08-05T09:00:00+05:00"}]

        result, index_map = _filter_snapshots_to_milestone_window(snapshots, milestones)

        assert result == snapshots
        assert index_map == {0: 0}

    def test_fall_back_to_all_when_filter_empties(self):
        snapshots = [
            {"timestamp": "2024-01-15T05:00:00"},  # outside window
        ]
        milestones = [{"time": "2024-01-15T12:00:00"}]
        result, index_map = _filter_snapshots_to_milestone_window(
            snapshots, milestones, buffer_hours=2
        )
        # Returns originals with empty index map
        assert result == snapshots
        assert index_map == {}

    def test_handles_invalid_timestamps_gracefully(self):
        snapshots = [
            {"timestamp": "invalid"},
            {"timestamp": "2024-01-15T10:00:00"},
        ]
        milestones = [{"time": "2024-01-15T12:00:00"}]
        result, index_map = _filter_snapshots_to_milestone_window(
            snapshots, milestones, buffer_hours=10
        )
        assert len(result) == 1  # Invalid timestamp filtered out


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


class TestCompactSectionHistory:
    def test_short_history_unchanged(self):
        h = [{"enrollment": 25}, {"enrollment": 30}]
        assert _compact_section_history(h) == h

    def test_preserves_endpoints_and_changes(self):
        h = [
            {"enrollment": 25, "capacity": 30},
            {"enrollment": 25, "capacity": 30},
            {"enrollment": 30, "capacity": 30},
            {"enrollment": 30, "capacity": 30},
        ]
        result = _compact_section_history(h)
        # Keeps idx 0 (first), 1 (point before change), 2 (change point), 3 (last)
        assert len(result) == 4
        assert result[0]["enrollment"] == 25
        assert result[1]["enrollment"] == 25
        assert result[2]["enrollment"] == 30
        assert result[3]["enrollment"] == 30


class TestAddCourseAverageHistory:
    def test_computes_average_from_sections(self):
        course_data: dict[str, Any] = {
            "sections": {
                "10L": {
                    "history": [
                        {"snapshotIdx": 0, "fill": 0.8},
                        {"snapshotIdx": 1, "fill": 0.9},
                    ]
                },
                "11L": {
                    "history": [
                        {"snapshotIdx": 0, "fill": 0.6},
                        {"snapshotIdx": 1, "fill": 0.7},
                    ]
                },
            }
        }
        _add_course_average_history(course_data)
        avgs = course_data["averageHistory"]
        assert len(avgs) == 2
        assert avgs[0]["snapshotIdx"] == 0
        assert avgs[0]["fill"] == 0.7  # (0.8 + 0.6) / 2
        assert avgs[1]["fill"] == 0.8  # (0.9 + 0.7) / 2


class TestCompactAverageHistory:
    def test_short_history_unchanged(self):
        h = [{"fill": 0.5}, {"fill": 0.6}]
        assert _compact_average_history(h) == h

    def test_preserves_endpoints_and_changes(self):
        h = [
            {"fill": 0.5},
            {"fill": 0.5},
            {"fill": 0.7},
            {"fill": 0.7},
        ]
        result = _compact_average_history(h)
        assert len(result) == 4  # all kept: endpoints + point before/at change

    def test_removes_unchanged_middle_points(self):
        h = [{"fill": 0.5}, {"fill": 0.5}, {"fill": 0.5}, {"fill": 0.5}, {"fill": 0.8}]
        result = _compact_average_history(h)
        # Keeps idx 0 (first), 3 (point before change), 4 (change point, last)
        assert len(result) == 3


class TestCompactHistoriesForWebsite:
    def test_compacts_all_histories(self):
        data: dict[str, Any] = {
            "courses": {
                "CS 101": {
                    "sections": {
                        "10L": {
                            "history": [
                                {
                                    "snapshotIdx": 0,
                                    "fill": 0.5,
                                    "enrollment": 15,
                                    "capacity": 30,
                                },
                                {
                                    "snapshotIdx": 1,
                                    "fill": 0.5,
                                    "enrollment": 15,
                                    "capacity": 30,
                                },
                                {
                                    "snapshotIdx": 2,
                                    "fill": 0.8,
                                    "enrollment": 24,
                                    "capacity": 30,
                                },
                            ]
                        },
                    },
                    "averageHistory": [
                        {"snapshotIdx": 0, "fill": 0.5},
                        {"snapshotIdx": 1, "fill": 0.5},
                        {"snapshotIdx": 2, "fill": 0.8},
                    ],
                },
            }
        }
        _compact_histories_for_website(data)
        cs = cast(dict[str, Any], data["courses"]["CS 101"])
        assert len(cs["sections"]["10L"]["history"]) <= 3
        assert len(cs["averageHistory"]) <= 3


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
