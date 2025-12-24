"""Tests for the report formatter module."""

import pytest

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
    SIGNIFICANT_CHANGE_THRESHOLD,
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


class TestFormatChangeDelta:
    """Tests for _format_change_delta method."""

    def test_significant_increase(self, formatter: ReportFormatter):
        """Significant increase should include triangle indicator."""
        result = formatter._format_change_delta(0.20)  # 20% increase
        assert "🔺" in result
        assert "+" in result

    def test_significant_decrease(self, formatter: ReportFormatter):
        """Significant decrease should include triangle indicator."""
        result = formatter._format_change_delta(-0.20)  # 20% decrease
        assert "🔺" in result
        assert "-" in result

    def test_minor_change(self, formatter: ReportFormatter):
        """Minor change should not include triangle indicator."""
        result = formatter._format_change_delta(0.05)  # 5% change
        assert "🔺" not in result
        assert "+" in result

    def test_threshold_boundary(self, formatter: ReportFormatter):
        """Change at threshold should not include indicator."""
        result = formatter._format_change_delta(SIGNIFICANT_CHANGE_THRESHOLD)
        assert "🔺" not in result


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

        assert "✨" in report
        assert "CS 101" in report
        assert "NEW" in report

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

        assert "❌" in report
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
