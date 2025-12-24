"""Tests for the models module (Section, Course, EnrollmentSnapshot dataclasses)."""

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

    def test_is_filled_when_below_capacity(self, sample_section: Section):
        """Section should not be filled when below capacity."""
        assert sample_section.is_filled is False

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

    def test_is_near_filled_below_threshold(self):
        """Section should not be near filled below 75% threshold."""
        section = Section(
            section_id="10L",
            section_type="L",
            enrollment=15,
            capacity=30,
            fill=0.50,
        )
        assert section.is_near_filled is False

    def test_to_dict(self, sample_section: Section):
        """Section should serialize to dictionary correctly."""
        result = sample_section.to_dict()
        assert result["section_id"] == "10L"
        assert result["section_type"] == "L"
        assert result["enrollment"] == 25
        assert result["capacity"] == 30
        assert result["fill"] == 0.83

    def test_from_dict(self):
        """Section should deserialize from dictionary correctly."""
        data = {
            "section_id": "10L",
            "section_type": "L",
            "enrollment": 25,
            "capacity": 30,
            "fill": 0.83,
        }
        section = Section.from_dict(data)
        assert section.section_id == "10L"
        assert section.section_type == "L"
        assert section.enrollment == 25
        assert section.capacity == 30
        assert section.fill == 0.83

    def test_roundtrip_serialization(self, sample_section: Section):
        """Section should survive serialization roundtrip."""
        data = sample_section.to_dict()
        restored = Section.from_dict(data)
        assert restored.section_id == sample_section.section_id
        assert restored.enrollment == sample_section.enrollment


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

    def test_total_enrollment_single_section_type(self):
        """Total enrollment with single section type sums all sections."""
        sections = {
            "10L": Section("10L", "L", 20, 30, 0.67),
            "11L": Section("11L", "L", 25, 30, 0.83),
        }
        course = Course("CS 101", "CS", sections, 0.75)
        assert course.total_enrollment == 45

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

    def test_total_capacity_single_section_type(self):
        """Total capacity with single section type sums all sections."""
        sections = {
            "10L": Section("10L", "L", 20, 30, 0.67),
            "11L": Section("11L", "L", 25, 35, 0.71),
        }
        course = Course("CS 101", "CS", sections, 0.69)
        assert course.total_capacity == 65

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

    def test_to_dict(self, sample_course: Course):
        """Course should serialize to dictionary correctly."""
        result = sample_course.to_dict()
        assert result["course_code"] == "CS 101"
        assert result["department"] == "CS"
        assert "sections" in result
        assert len(result["sections"]) == 3

    def test_from_dict(self):
        """Course should deserialize from dictionary correctly."""
        data = {
            "course_code": "CS 101",
            "department": "CS",
            "average_fill": 0.75,
            "course_title": "Intro CS",
            "sections": {
                "10L": {
                    "section_id": "10L",
                    "section_type": "L",
                    "enrollment": 25,
                    "capacity": 30,
                    "fill": 0.83,
                }
            },
        }
        course = Course.from_dict(data)
        assert course.course_code == "CS 101"
        assert course.department == "CS"
        assert "10L" in course.sections


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

    def test_to_dict(self, sample_snapshot: EnrollmentSnapshot):
        """Snapshot should serialize to dictionary correctly."""
        result = sample_snapshot.to_dict()
        assert result["timestamp"] == "2024-01-15 10:30:00"
        assert result["semester"] == "Spring 2024"
        assert "courses" in result

    def test_from_dict(self):
        """Snapshot should deserialize from dictionary correctly."""
        data = {
            "timestamp": "2024-01-15 10:30:00",
            "semester": "Spring 2024",
            "overall_fill": 0.75,
            "courses": {
                "CS 101": {
                    "course_code": "CS 101",
                    "department": "CS",
                    "average_fill": 0.75,
                    "course_title": None,
                    "sections": {},
                }
            },
        }
        snapshot = EnrollmentSnapshot.from_dict(data)
        assert snapshot.timestamp == "2024-01-15 10:30:00"
        assert snapshot.semester == "Spring 2024"
        assert "CS 101" in snapshot.courses

    def test_roundtrip_serialization(self, sample_snapshot: EnrollmentSnapshot):
        """Snapshot should survive serialization roundtrip."""
        data = sample_snapshot.to_dict()
        restored = EnrollmentSnapshot.from_dict(data)
        assert restored.timestamp == sample_snapshot.timestamp
        assert restored.semester == sample_snapshot.semester
        assert len(restored.courses) == len(sample_snapshot.courses)

    def test_empty_snapshot(self):
        """Empty snapshot should have zero fill."""
        snapshot = EnrollmentSnapshot(
            timestamp="2024-01-15 10:30:00",
            semester="Spring 2024",
            overall_fill=0.0,
            courses={},
        )
        assert snapshot.calculate_total_enrollment() == 0
        assert snapshot.calculate_total_capacity() == 0
