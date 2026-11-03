"""Tests for the models module (Section, Course, EnrollmentSnapshot dataclasses)."""

import pytest

pytestmark = pytest.mark.unit


from registrarmonitor.models import (
    Course,
    EnrollmentSnapshot,
    Section,
)


class TestSection:
    """Tests for the Section dataclass."""

    def test_is_filled_when_at_capacity(self, full_section: Section):
        """Section should be filled when enrollment equals capacity."""
        assert full_section.is_filled is True

    def test_is_filled_when_overcapacity(self):
        """Section should be filled when over capacity."""
        section = Section(
            section_id="10L",
            section_type="L",
            enrollment=35,
            capacity=30,
            fill=1.17,
        )
        assert section.is_filled is True

    def test_is_near_filled_above_threshold(self):
        """Section should be near filled above 75% threshold."""
        section = Section(
            section_id="10L",
            section_type="L",
            enrollment=24,
            capacity=30,
            fill=0.80,
        )
        assert section.is_near_filled is True


class TestCourse:
    """Tests for the Course dataclass."""

    def test_is_filled_when_all_sections_of_type_full(self, full_course: Course):
        """Course should be filled when all sections of one type are full."""
        assert full_course.is_filled is True

    def test_is_filled_when_sections_available(self, sample_course: Course):
        """Course should not be filled when sections have availability."""
        assert sample_course.is_filled is False

    def test_is_near_filled(self, sample_course: Course):
        """Course near filled check based on average fill."""
        assert sample_course.is_near_filled is True

    def test_total_enrollment_multiple_section_types(self):
        """Total enrollment with multiple types returns minimum type total."""
        sections = {
            "10L": Section("10L", "L", 20, 30, 0.67),
            "11L": Section("11L", "L", 25, 30, 0.83),
            "1R": Section("1R", "R", 15, 25, 0.60),
        }
        course = Course("CS 101", "CS", sections, 0.70)
        # Lectures: 45, Recitations: 15 -> min is 15
        assert course.total_enrollment == 15

    def test_total_capacity_multiple_section_types(self):
        """Total capacity with multiple types returns minimum type capacity."""
        sections = {
            "10L": Section("10L", "L", 20, 30, 0.67),
            "11L": Section("11L", "L", 25, 30, 0.83),
            "1R": Section("1R", "R", 15, 25, 0.60),
        }
        course = Course("CS 101", "CS", sections, 0.70)
        # Lectures: 60, Recitations: 25 -> min is 25
        assert course.total_capacity == 25

    def test_is_filled_mixed_types_one_satisfied(self):
        """Course should be filled if one type is fully satisfied, even if another is not."""
        sections = {
            "10L": Section("10L", "L", 30, 30, 1.0),  # Type L satisfied
            "1R": Section("1R", "R", 10, 20, 0.5),  # Type R NOT satisfied
        }
        course = Course("CS 103", "CS", sections)
        assert course.is_filled is True


class TestEnrollmentSnapshot:
    """Tests for the EnrollmentSnapshot dataclass."""

    def test_calculate_overall_fill(self, sample_snapshot: EnrollmentSnapshot):
        """Overall fill should be calculated from course enrollments."""
        # This recalculates, may differ from preset value
        sample_snapshot.calculate_overall_fill()
        assert 0 <= sample_snapshot.overall_fill <= 1.5

    def test_calculate_total_enrollment(self, sample_snapshot: EnrollmentSnapshot):
        """Total enrollment should sum all course enrollments."""
        total = sample_snapshot.calculate_total_enrollment()
        assert total > 0

    def test_calculate_total_capacity(self, sample_snapshot: EnrollmentSnapshot):
        """Total capacity should sum all course capacities."""
        total = sample_snapshot.calculate_total_capacity()
        assert total > 0
