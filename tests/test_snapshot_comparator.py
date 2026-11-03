"""Tests for the snapshot comparator module."""

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.data.snapshot_comparator import SnapshotComparator
from registrarmonitor.models import (
    Course,
    EnrollmentSnapshot,
    Section,
)


@pytest.fixture
def comparator() -> SnapshotComparator:
    """Create a SnapshotComparator instance."""
    return SnapshotComparator()


class TestSnapshotComparator:
    """Tests for the SnapshotComparator class."""

    def test_ignores_instructor_markup_and_order_changes(
        self, comparator: SnapshotComparator
    ):
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00",
            "Spring 2024",
            0.50,
            {
                "CS 101": Course(
                    "CS 101",
                    "CS",
                    {"10L": Section("10L", "L", 15, 30, 0.50, "Akarca, Halit")},
                    0.50,
                )
            },
        )
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00",
            "Spring 2024",
            0.50,
            {
                "CS 101": Course(
                    "CS 101",
                    "CS",
                    {"10L": Section("10L", "L", 15, 30, 0.50, "<b>Halit</b> Akarca")},
                    0.50,
                )
            },
        )

        comparison = comparator.compare_snapshots(current, previous)

        assert comparison.changed_courses == []

    def test_detects_genuine_instructor_change(self, comparator: SnapshotComparator):
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00",
            "Spring 2024",
            0.50,
            {
                "CS 101": Course(
                    "CS 101",
                    "CS",
                    {"10L": Section("10L", "L", 15, 30, 0.50, "Ada Lovelace")},
                    0.50,
                )
            },
        )
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00",
            "Spring 2024",
            0.50,
            {
                "CS 101": Course(
                    "CS 101",
                    "CS",
                    {"10L": Section("10L", "L", 15, 30, 0.50, "Grace Hopper")},
                    0.50,
                )
            },
        )

        comparison = comparator.compare_snapshots(current, previous)

        assert len(comparison.changed_courses) == 1
        assert comparison.changed_courses[0].modified_sections[
            0
        ].previous_instructor == ("Ada Lovelace")
        assert comparison.changed_courses[0].modified_sections[
            0
        ].current_instructor == ("Grace Hopper")

    def test_no_changes(self, comparator: SnapshotComparator):
        """Identical snapshots should produce no changes."""
        sections = {
            "10L": Section("10L", "L", 25, 30, 0.83),
        }
        course = Course("CS 101", "CS", sections, 0.83)
        snapshot = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.75, {"CS 101": course}
        )

        comparison = comparator.compare_snapshots(snapshot, snapshot)

        assert len(comparison.new_courses) == 0
        assert len(comparison.removed_courses) == 0
        assert len(comparison.changed_courses) == 0

    def test_new_course_detected(self, comparator: SnapshotComparator):
        """New courses should be detected."""
        previous = EnrollmentSnapshot("2024-01-15 09:00:00", "Spring 2024", 0.70, {})

        sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
        new_course = Course("CS 101", "CS", sections, 0.83)
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.75, {"CS 101": new_course}
        )

        comparison = comparator.compare_snapshots(current, previous)

        assert len(comparison.new_courses) == 1
        assert comparison.new_courses[0].course_code == "CS 101"
        assert len(comparison.removed_courses) == 0

    def test_section_added_to_course(self, comparator: SnapshotComparator):
        """New sections added to existing course should be detected."""
        prev_sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
        prev_course = Course("CS 101", "CS", prev_sections, 0.83)
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00", "Spring 2024", 0.75, {"CS 101": prev_course}
        )

        curr_sections = {
            "10L": Section("10L", "L", 25, 30, 0.83),
            "11L": Section("11L", "L", 20, 30, 0.67),
        }
        curr_course = Course("CS 101", "CS", curr_sections, 0.75)
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.75, {"CS 101": curr_course}
        )

        comparison = comparator.compare_snapshots(current, previous)

        assert len(comparison.changed_courses) == 1
        course_change = comparison.changed_courses[0]
        assert len(course_change.added_sections) == 1
        assert course_change.added_sections[0].section_id == "11L"

    def test_enrollment_change_detected(self, comparator: SnapshotComparator):
        """Enrollment changes should be detected as modified sections."""
        prev_sections = {"10L": Section("10L", "L", 20, 30, 0.67)}
        prev_course = Course("CS 101", "CS", prev_sections, 0.67)
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00", "Spring 2024", 0.70, {"CS 101": prev_course}
        )

        curr_sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
        curr_course = Course("CS 101", "CS", curr_sections, 0.83)
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.75, {"CS 101": curr_course}
        )

        comparison = comparator.compare_snapshots(current, previous)

        assert len(comparison.changed_courses) == 1
        course_change = comparison.changed_courses[0]
        assert len(course_change.modified_sections) == 1
        mod = course_change.modified_sections[0]
        assert mod.section_id == "10L"
        assert mod.previous_enrollment == 20
        assert mod.current_enrollment == 25
