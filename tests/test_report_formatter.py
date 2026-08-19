"""Tests for the report formatter module."""

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.data.snapshot_comparator import SnapshotComparator
from registrarmonitor.models import (
    Course,
    CourseChangeDetail,
    EnrollmentComparison,
    EnrollmentSnapshot,
    Section,
    SectionChangeDetail,
)
from registrarmonitor.reporting.report_formatter import (
    NEAR_THRESHOLD,
    ReportFormatter,
)


@pytest.fixture
def formatter() -> ReportFormatter:
    """Create a ReportFormatter instance."""
    return ReportFormatter()


class TestGetStatusEmoji:
    """Tests for _get_status_emoji method."""

    def test_full_section_red(self, formatter: ReportFormatter):
        """Full section (100%) should return red emoji."""
        assert formatter._get_status_emoji(1.0) == "🔴"

    def test_overcapacity_red(self, formatter: ReportFormatter):
        """Overcapacity section (>100%) should return red emoji."""
        assert formatter._get_status_emoji(1.15) == "🔴"

    def test_near_filled_orange(self, formatter: ReportFormatter):
        """Near filled section (>=75%) should return orange emoji."""
        assert formatter._get_status_emoji(0.80) == "🟠"
        assert formatter._get_status_emoji(NEAR_THRESHOLD) == "🟠"

    def test_open_section_green(self, formatter: ReportFormatter):
        """Open section (<75%) should return green emoji."""
        assert formatter._get_status_emoji(0.50) == "🟢"
        assert formatter._get_status_emoji(0.74) == "🟢"

    def test_course_filled_check(self, formatter: ReportFormatter):
        """Course with all sections of one type filled should be red."""
        sections = {
            "10L": Section("10L", "L", 30, 30, 1.0),
            "11L": Section("11L", "L", 30, 30, 1.0),
        }
        course = Course("CS 101", "CS", sections, 1.0)
        assert formatter._get_status_emoji(1.0, is_course=True, course=course) == "🔴"


class TestFormatChangesReport:
    """Tests for format_changes_report method."""

    def test_no_changes_report(self, formatter: ReportFormatter):
        """Report with no changes should indicate no changes."""
        comparison = EnrollmentComparison(
            previous_snapshot_timestamp="2024-01-15 09:00:00",
            current_snapshot_timestamp="2024-01-15 10:00:00",
        )
        previous = EnrollmentSnapshot("2024-01-15 09:00:00", "Spring 2024", 0.70, {})
        current = EnrollmentSnapshot("2024-01-15 10:00:00", "Spring 2024", 0.70, {})

        report = formatter.format_changes_report(comparison, current, previous)

        assert "No significant changes" in report

    def test_header_includes_timestamp(self, formatter: ReportFormatter):
        """Report header should include timestamp."""
        comparison = EnrollmentComparison(
            previous_snapshot_timestamp="2024-01-15 09:00:00",
            current_snapshot_timestamp="2024-01-15 10:00:00",
        )
        previous = EnrollmentSnapshot("2024-01-15 09:00:00", "Spring 2024", 0.70, {})
        current = EnrollmentSnapshot("2024-01-15 10:00:00", "Spring 2024", 0.75, {})

        report = formatter.format_changes_report(comparison, current, previous)

        assert "2024-01-15 10:00:00" in report
        assert "📅" in report

    def test_new_course_formatting(self, formatter: ReportFormatter):
        """New course should be formatted with sparkle emoji."""
        sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
        new_course = Course("CS 101", "CS", sections, 0.83)

        comparison = EnrollmentComparison(
            previous_snapshot_timestamp="2024-01-15 09:00:00",
            current_snapshot_timestamp="2024-01-15 10:00:00",
            new_courses=[new_course],
        )
        previous = EnrollmentSnapshot("2024-01-15 09:00:00", "Spring 2024", 0.70, {})
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.75, {"CS 101": new_course}
        )

        report = formatter.format_changes_report(comparison, current, previous)

        assert "+ CS 101 - COURSE ADDED" in report
        assert "CS 101" in report
        assert "ADDED" in report

    def test_removed_course_formatting(self, formatter: ReportFormatter):
        """Removed course should be formatted with X emoji."""
        sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
        old_course = Course("CS 101", "CS", sections, 0.83)

        comparison = EnrollmentComparison(
            previous_snapshot_timestamp="2024-01-15 09:00:00",
            current_snapshot_timestamp="2024-01-15 10:00:00",
            removed_courses=[old_course],
        )
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00", "Spring 2024", 0.75, {"CS 101": old_course}
        )
        current = EnrollmentSnapshot("2024-01-15 10:00:00", "Spring 2024", 0.70, {})

        report = formatter.format_changes_report(comparison, current, previous)

        assert "− CS 101 - COURSE REMOVED" in report
        assert "CS 101" in report
        assert "REMOVED" in report

    def test_modified_course_formatting(self, formatter: ReportFormatter):
        """Modified course should show change delta."""
        prev_sections = {"10L": Section("10L", "L", 20, 30, 0.67)}
        prev_course = Course("CS 101", "CS", prev_sections, 0.67)

        curr_sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
        curr_course = Course("CS 101", "CS", curr_sections, 0.83)

        mod_section = SectionChangeDetail(
            section_id="10L",
            previous_fill=0.67,
            current_fill=0.83,
            previous_enrollment=20,
            current_enrollment=25,
            previous_capacity=30,
            current_capacity=30,
            previous_instructor="Ada Lovelace",
            current_instructor="Grace Hopper",
        )
        course_change = CourseChangeDetail(
            course_code="CS 101",
            previous_average_fill=0.67,
            current_average_fill=0.83,
            modified_sections=[mod_section],
        )

        comparison = EnrollmentComparison(
            previous_snapshot_timestamp="2024-01-15 09:00:00",
            current_snapshot_timestamp="2024-01-15 10:00:00",
            changed_courses=[course_change],
        )
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00", "Spring 2024", 0.70, {"CS 101": prev_course}
        )
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.75, {"CS 101": curr_course}
        )

        report = formatter.format_changes_report(comparison, current, previous)

        assert "CS 101" in report
        assert "10L" in report
        # Should show enrollment delta
        assert "+5" in report or "(+5)" in report
        assert "instructor:" not in report

    def test_instructor_only_changes_are_not_reported(self, formatter: ReportFormatter):
        previous_section = Section("10L", "L", 20, 30, 0.67, "Ada Lovelace")
        current_section = Section("10L", "L", 20, 30, 0.67, "Grace Hopper")
        previous_course = Course("CS 101", "CS", {"10L": previous_section}, 0.67)
        current_course = Course("CS 101", "CS", {"10L": current_section}, 0.67)
        comparison = EnrollmentComparison(
            previous_snapshot_timestamp="2024-01-15 09:00:00",
            current_snapshot_timestamp="2024-01-15 10:00:00",
            changed_courses=[
                CourseChangeDetail(
                    course_code="CS 101",
                    modified_sections=[
                        SectionChangeDetail(
                            section_id="10L",
                            previous_fill=0.67,
                            current_fill=0.67,
                            previous_enrollment=20,
                            current_enrollment=20,
                            previous_capacity=30,
                            current_capacity=30,
                            previous_instructor="Ada Lovelace",
                            current_instructor="Grace Hopper",
                        )
                    ],
                )
            ],
        )

        report = formatter.format_changes_report(
            comparison,
            EnrollmentSnapshot(
                "2024-01-15 10:00:00", "Spring 2024", 0.67, {"CS 101": current_course}
            ),
            EnrollmentSnapshot(
                "2024-01-15 09:00:00", "Spring 2024", 0.67, {"CS 101": previous_course}
            ),
        )

        assert "CS 101" not in report
        assert "instructor:" not in report
        assert "No significant changes" in report

    def test_name_formatting_is_not_reported(self, formatter: ReportFormatter):
        previous_course = Course(
            "CS 101",
            "CS",
            {"10L": Section("10L", "L", 20, 30, 0.67, "Park, Chun Young")},
            0.67,
        )
        current_course = Course(
            "CS 101",
            "CS",
            {"10L": Section("10L", "L", 20, 30, 0.67, "Chun Young Park")},
            0.67,
        )
        previous = EnrollmentSnapshot(
            "2024-01-15 09:00:00", "Spring 2024", 0.67, {"CS 101": previous_course}
        )
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.67, {"CS 101": current_course}
        )

        comparison = SnapshotComparator().compare_snapshots(current, previous)
        report = formatter.format_changes_report(comparison, current, previous)

        assert comparison.changed_courses == []
        assert "No significant changes" in report
        assert "instructor:" not in report

    def test_courses_sorted_alphabetically(self, formatter: ReportFormatter):
        """Courses in report should be sorted alphabetically."""
        course_a = Course("AA 101", "AA", {}, 0.50)
        course_b = Course("BB 201", "BB", {}, 0.60)
        course_c = Course("CC 301", "CC", {}, 0.70)

        comparison = EnrollmentComparison(
            previous_snapshot_timestamp="2024-01-15 09:00:00",
            current_snapshot_timestamp="2024-01-15 10:00:00",
            new_courses=[course_c, course_a, course_b],  # Unsorted
        )
        previous = EnrollmentSnapshot("2024-01-15 09:00:00", "Spring 2024", 0.60, {})
        current = EnrollmentSnapshot(
            "2024-01-15 10:00:00",
            "Spring 2024",
            0.60,
            {"AA 101": course_a, "BB 201": course_b, "CC 301": course_c},
        )

        report = formatter.format_changes_report(comparison, current, previous)

        # Check AA comes before BB and BB before CC
        aa_pos = report.find("AA 101")
        bb_pos = report.find("BB 201")
        cc_pos = report.find("CC 301")
        assert aa_pos < bb_pos < cc_pos

    def test_header_uses_active_priority_once_and_omits_routine_semester_fill(
        self, formatter: ReportFormatter
    ):
        comparison = EnrollmentComparison(
            previous_snapshot_timestamp="2026-08-13 10:00:00",
            current_snapshot_timestamp="2026-08-13 12:00:00",
        )
        previous = EnrollmentSnapshot("2026-08-13 10:00:00", "Fall 2026", 0.70, {})
        current = EnrollmentSnapshot("2026-08-13 12:00:00", "Fall 2026", 0.75, {})

        report = formatter.format_changes_report(comparison, current, previous)

        assert report.count("P2 · Y3") == 1
        assert "📈" not in report
        assert "75%" not in report

    def test_new_course_is_one_compact_event_without_section_dump(
        self, formatter: ReportFormatter
    ):
        course = Course(
            "CS 101",
            "CS",
            {
                "10L": Section("10L", "L", 25, 30, 0.83),
                "11L": Section("11L", "L", 20, 30, 0.67),
            },
            0.75,
        )
        comparison = EnrollmentComparison(
            previous_snapshot_timestamp="2026-08-13 10:00:00",
            current_snapshot_timestamp="2026-08-13 12:00:00",
            new_courses=[course],
        )

        report = formatter.format_changes_report(
            comparison,
            EnrollmentSnapshot(
                "2026-08-13 12:00:00", "Fall 2026", 0.75, {"CS 101": course}
            ),
            EnrollmentSnapshot("2026-08-13 10:00:00", "Fall 2026", 0.70, {}),
        )

        assert "+ CS 101 - COURSE ADDED" in report
        assert "10L" not in report
        assert "11L" not in report

    def test_required_type_full_names_only_an_actual_limiting_type(
        self, formatter: ReportFormatter
    ):
        previous_course = Course(
            "CS 101",
            "CS",
            {
                "1L": Section("1L", "L", 10, 20, 0.5),
                "1B": Section("1B", "B", 9, 10, 0.9),
            },
            0.7,
        )
        current_course = Course(
            "CS 101",
            "CS",
            {
                "1L": Section("1L", "L", 11, 20, 0.55),
                "1B": Section("1B", "B", 10, 10, 1.0),
            },
            0.775,
        )
        comparison = SnapshotComparator().compare_snapshots(
            EnrollmentSnapshot(
                "2026-08-13 12:00:00",
                "Fall 2026",
                0.75,
                {"CS 101": current_course},
            ),
            EnrollmentSnapshot(
                "2026-08-13 10:00:00",
                "Fall 2026",
                0.70,
                {"CS 101": previous_course},
            ),
        )

        report = formatter.format_changes_report(
            comparison,
            EnrollmentSnapshot(
                "2026-08-13 12:00:00",
                "Fall 2026",
                0.75,
                {"CS 101": current_course},
            ),
            EnrollmentSnapshot(
                "2026-08-13 10:00:00",
                "Fall 2026",
                0.70,
                {"CS 101": previous_course},
            ),
        )

        assert "CS 101 - LAB FULL" in report
        assert "section type mismatch" not in report

    def test_uniformly_full_required_types_use_simple_100_percent_heading(
        self, formatter: ReportFormatter
    ):
        previous_course = Course(
            "CS 101",
            "CS",
            {
                "1L": Section("1L", "L", 19, 20, 0.95),
                "1B": Section("1B", "B", 9, 10, 0.9),
            },
            0.925,
        )
        current_course = Course(
            "CS 101",
            "CS",
            {
                "1L": Section("1L", "L", 20, 20, 1.0),
                "1B": Section("1B", "B", 10, 10, 1.0),
            },
            1.0,
        )
        current = EnrollmentSnapshot(
            "2026-08-13 12:00:00", "Fall 2026", 1.0, {"CS 101": current_course}
        )
        previous = EnrollmentSnapshot(
            "2026-08-13 10:00:00",
            "Fall 2026",
            0.925,
            {"CS 101": previous_course},
        )

        report = formatter.format_changes_report(
            SnapshotComparator().compare_snapshots(current, previous),
            current,
            previous,
        )

        assert "CS 101 - 100%" in report
        assert "LAB FULL" not in report
