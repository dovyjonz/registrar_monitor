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

    def test_comparison_order_is_independent_of_mapping_insertion_order(
        self, comparator: SnapshotComparator
    ):
        class CollidingSectionCode(str):
            __slots__ = ()

            def __hash__(self) -> int:
                return 1

        codes = [CollidingSectionCode(f"{number}S") for number in range(8)]

        def snapshot(timestamp: str, enrollment: int, reverse: bool):
            ordered_codes = reversed(codes) if reverse else codes
            sections: dict[str, Section] = {
                code: Section(code, "S", enrollment, 50, enrollment / 50)
                for code in ordered_codes
            }
            return EnrollmentSnapshot(
                timestamp,
                "Spring 2026",
                enrollment / 50,
                {"PHIL 210": Course("PHIL 210", "SSH", sections, enrollment / 50)},
            )

        expected = comparator.compare_snapshots(
            snapshot("2026-01-31 22:36:19", 49, False),
            snapshot("2026-01-16 14:06:05", 50, False),
        )
        reconstructed = comparator.compare_snapshots(
            snapshot("2026-01-31 22:36:19", 49, True),
            snapshot("2026-01-16 14:06:05", 50, True),
        )

        assert reconstructed == expected

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

    def test_removed_course_detected(self, comparator: SnapshotComparator):
        """Removed courses should be detected."""
        sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
        old_course = Course("CS 101", "CS", sections, 0.83)
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00", "Spring 2024", 0.75, {"CS 101": old_course}
        )

        current = EnrollmentSnapshot("2024-01-15 10:00:00", "Spring 2024", 0.70, {})

        comparison = comparator.compare_snapshots(current, previous)

        assert len(comparison.removed_courses) == 1
        assert comparison.removed_courses[0].course_code == "CS 101"
        assert len(comparison.new_courses) == 0

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

    def test_section_removed_from_course(self, comparator: SnapshotComparator):
        """Removed sections from existing course should be detected."""
        prev_sections = {
            "10L": Section("10L", "L", 25, 30, 0.83),
            "11L": Section("11L", "L", 20, 30, 0.67),
        }
        prev_course = Course("CS 101", "CS", prev_sections, 0.75)
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00", "Spring 2024", 0.75, {"CS 101": prev_course}
        )

        curr_sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
        curr_course = Course("CS 101", "CS", curr_sections, 0.83)
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.75, {"CS 101": curr_course}
        )

        comparison = comparator.compare_snapshots(current, previous)

        assert len(comparison.changed_courses) == 1
        course_change = comparison.changed_courses[0]
        assert len(course_change.removed_sections) == 1
        assert course_change.removed_sections[0].section_id == "11L"

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

    def test_capacity_change_detected(self, comparator: SnapshotComparator):
        """Capacity changes should be detected."""
        prev_sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
        prev_course = Course("CS 101", "CS", prev_sections, 0.83)
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00", "Spring 2024", 0.75, {"CS 101": prev_course}
        )

        curr_sections = {"10L": Section("10L", "L", 25, 35, 0.71)}
        curr_course = Course("CS 101", "CS", curr_sections, 0.71)
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.75, {"CS 101": curr_course}
        )

        comparison = comparator.compare_snapshots(current, previous)

        assert len(comparison.changed_courses) == 1
        course_change = comparison.changed_courses[0]
        assert len(course_change.modified_sections) == 1
        mod = course_change.modified_sections[0]
        assert mod.previous_capacity == 30
        assert mod.current_capacity == 35

    def test_multiple_courses_tracked(self, comparator: SnapshotComparator):
        """Multiple course changes should be tracked independently."""
        prev_courses = {
            "CS 101": Course(
                "CS 101", "CS", {"10L": Section("10L", "L", 20, 30, 0.67)}, 0.67
            ),
            "MATH 201": Course(
                "MATH 201", "MATH", {"20L": Section("20L", "L", 15, 30, 0.50)}, 0.50
            ),
        }
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00", "Spring 2024", 0.60, prev_courses
        )

        curr_courses = {
            "CS 101": Course(
                "CS 101", "CS", {"10L": Section("10L", "L", 25, 30, 0.83)}, 0.83
            ),
            "MATH 201": Course(
                "MATH 201", "MATH", {"20L": Section("20L", "L", 20, 30, 0.67)}, 0.67
            ),
        }
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.75, curr_courses
        )

        comparison = comparator.compare_snapshots(current, previous)

        assert len(comparison.changed_courses) == 2

    def test_timestamps_recorded(self, comparator: SnapshotComparator):
        """Comparison should record both snapshot timestamps."""
        previous = EnrollmentSnapshot("2024-01-15 09:00:00", "Spring 2024", 0.70, {})
        current = EnrollmentSnapshot("2024-01-15 10:00:00", "Spring 2024", 0.75, {})

        comparison = comparator.compare_snapshots(current, previous)

        assert comparison.previous_snapshot_timestamp == "2024-01-15 09:00:00"
        assert comparison.current_snapshot_timestamp == "2024-01-15 10:00:00"

    def test_small_fill_change_ignored(self, comparator: SnapshotComparator):
        """Very small fill changes (< 0.001) should not trigger change detection."""
        prev_sections = {"10L": Section("10L", "L", 25, 30, 0.8333)}
        prev_course = Course("CS 101", "CS", prev_sections, 0.8333)
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00", "Spring 2024", 0.75, {"CS 101": prev_course}
        )

        # Same enrollment/capacity, just tiny fill rounding difference
        curr_sections = {"10L": Section("10L", "L", 25, 30, 0.8334)}
        curr_course = Course("CS 101", "CS", curr_sections, 0.8334)
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.75, {"CS 101": curr_course}
        )

        comparison = comparator.compare_snapshots(current, previous)

        # No significant changes
        assert len(comparison.changed_courses) == 0

    def test_average_fill_only_change_ignored(self, comparator: SnapshotComparator):
        """Course with only average_fill difference (no section changes) should not be flagged."""
        sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
        prev_course = Course("CS 101", "CS", sections, 0.80)  # Different average_fill
        curr_course = Course("CS 101", "CS", sections, 0.85)  # Different average_fill

        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00", "Spring 2024", 0.75, {"CS 101": prev_course}
        )
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.75, {"CS 101": curr_course}
        )

        comparison = comparator.compare_snapshots(current, previous)

        # No changes because section enrollment/capacity unchanged
        assert len(comparison.changed_courses) == 0
