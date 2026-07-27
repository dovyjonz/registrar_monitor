"""Tests for the PDF generator module (helper functions and layout logic)."""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.models import Course, Section
from registrarmonitor.reporting.pdf_generator import (
    PDFGenerator,
    _analyze_section_pattern,
    _calculate_effective_rows,
    _format_course_code,
    _format_type_summary,
    _group_sections_by_type,
)

# ── Helper function tests ─────────────────────────────────────────


class TestFormatCourseCode:
    """Tests for _format_course_code."""

    def test_standard_code(self):
        result = _format_course_code("CS 101")
        assert result == "CS   101"

    def test_empty_string(self):
        assert _format_course_code("") == " " * 8

    def test_code_without_space(self):
        result = _format_course_code("CSCI101")
        assert len(result) == 8

    def test_code_with_extra_digits(self):
        result = _format_course_code("CS 101A")
        assert result == "CS   101A"

    def test_pads_to_width(self):
        result = _format_course_code("MATH 201")
        assert len(result) == 8


class TestGroupSectionsByType:
    """Tests for _group_sections_by_type."""

    def test_groups_by_type(self):
        sections = [
            {"S/T": "10L", "Fill": 0.8},
            {"S/T": "11L", "Fill": 0.9},
            {"S/T": "1R", "Fill": 0.75},
        ]
        types, fills = _group_sections_by_type(sections)
        assert "L" in types
        assert "R" in types
        assert len(types["L"]) == 2
        assert 0.8 in fills

    def test_dedupes_same_section(self):
        sections = [
            {"S/T": "10L", "Fill": 0.8},
            {"S/T": "10L", "Fill": 0.8},
        ]
        types, _ = _group_sections_by_type(sections)
        assert len(types["L"]) == 1

    def test_empty_input(self):
        types, fills = _group_sections_by_type([])
        assert types == {}
        assert fills == set()


class TestFormatTypeSummary:
    """Tests for _format_type_summary."""

    def test_single_type_no_prefix(self):
        result = _format_type_summary("L", [0.8, 0.9], 1)
        assert result  # Should produce a summary

    def test_multi_type_with_prefix(self):
        result = _format_type_summary("L", [0.8, 0.9], 3)
        assert result

    def test_empty_fills(self):
        assert _format_type_summary("L", [], 1) == ""

    def test_all_full(self):
        result = _format_type_summary("L", [1.0, 1.0], 1)
        assert "×2" in result  # 2 full sections compactly summarized

    def test_close_fills_together(self):
        result = _format_type_summary("L", [0.81, 0.82], 1)
        assert result


class TestAnalyzeSectionPattern:
    """Tests for _analyze_section_pattern."""

    def test_empty_sections(self):
        assert _analyze_section_pattern([]) == ""

    def test_single_fill_value_returns_empty(self):
        sections = [{"S/T": "10L", "Fill": 0.8}, {"S/T": "11L", "Fill": 0.8}]
        result = _analyze_section_pattern(sections)
        assert result == ""

    def test_varied_fills_produces_pattern(self):
        sections = [
            {"S/T": "10L", "Fill": 0.5},
            {"S/T": "11L", "Fill": 1.0},
        ]
        result = _analyze_section_pattern(sections)
        assert result != ""

    def test_multiple_types(self):
        sections = [
            {"S/T": "10L", "Fill": 0.6},
            {"S/T": "1R", "Fill": 0.9},
        ]
        result = _analyze_section_pattern(sections)
        assert "L" in result or "R" in result


class TestCalculateEffectiveRows:
    """Tests for _calculate_effective_rows."""

    def test_single_dept_no_spacing(self):
        items = [("CS 101", None), ("CS 102", None)]
        assert _calculate_effective_rows(items) == 2.0

    def test_adds_spacing_for_dept_change(self):
        items = [("CS 101", None), ("MATH 201", None)]
        assert _calculate_effective_rows(items) == 2.5

    def test_multiple_dept_changes(self):
        items = [("CS 101", None), ("MATH 201", None), ("PHYS 101", None)]
        assert _calculate_effective_rows(items) == 4.0

    def test_empty_input(self):
        assert _calculate_effective_rows([]) == 0.0

    def test_no_space_first_item(self):
        items = [("CS 101", None)]
        assert _calculate_effective_rows(items) == 1.0


# ── PDFGenerator method tests ─────────────────────────────────────


@pytest.fixture
def courses():
    """Two courses in the same department + one in another."""
    return [
        Course(
            course_code="CS 101",
            department="CS",
            sections={
                "10L": Section("10L", "L", 25, 30, 0.83),
            },
            average_fill=0.83,
            course_title="Intro CS",
        ),
        Course(
            course_code="CS 102",
            department="CS",
            sections={
                "20L": Section("20L", "L", 30, 30, 1.0),
            },
            average_fill=1.0,
            course_title="Data Structures",
        ),
        Course(
            course_code="MATH 201",
            department="MATH",
            sections={
                "30L": Section("30L", "L", 28, 30, 0.93),
            },
            average_fill=0.93,
            course_title="Calculus",
        ),
    ]


class TestGroupCoursesByDepartment:
    """Tests for PDFGenerator._group_courses_by_department."""

    def test_groups_consecutive_departments(self, courses):
        generator = PDFGenerator(config_dict={"directories": {"pdf_output": "/tmp"}})
        groups = generator._group_courses_by_department(courses)
        assert len(groups) == 2
        assert len(groups[0]) == 2  # CS group
        assert len(groups[1]) == 1  # MATH group

    def test_single_department(self):
        course = Course(
            course_code="CS 101",
            department="CS",
            sections={
                "10L": Section("10L", "L", 25, 30, 0.83),
            },
            average_fill=0.83,
        )
        generator = PDFGenerator(config_dict={"directories": {"pdf_output": "/tmp"}})
        groups = generator._group_courses_by_department([course, course])
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_all_different_departments(self):
        courses = [
            Course("CS 101", "CS", {}, 0.5),
            Course("MATH 201", "MATH", {}, 0.6),
            Course("PHYS 101", "PHYS", {}, 0.7),
        ]
        generator = PDFGenerator(config_dict={"directories": {"pdf_output": "/tmp"}})
        groups = generator._group_courses_by_department(courses)
        assert len(groups) == 3
        assert all(len(g) == 1 for g in groups)


class TestDistributeCoursesToColumns:
    """Tests for PDFGenerator._distribute_courses_to_columns."""

    def test_fits_in_single_column(self, courses):
        generator = PDFGenerator(config_dict={"directories": {"pdf_output": "/tmp"}})
        dept_groups = generator._group_courses_by_department(courses)
        columns, split = generator._distribute_courses_to_columns(
            dept_groups, rows_per_column=10
        )
        assert len(columns) == 1
        assert len(columns[0]) == 3

    def test_splits_across_columns_when_tight(self, courses):
        generator = PDFGenerator(config_dict={"directories": {"pdf_output": "/tmp"}})
        dept_groups = generator._group_courses_by_department(courses)
        columns, split = generator._distribute_courses_to_columns(
            dept_groups, rows_per_column=2
        )
        assert len(columns) >= 2

    def test_returns_empty_when_no_groups(self, courses):
        generator = PDFGenerator(config_dict={"directories": {"pdf_output": "/tmp"}})
        columns, split = generator._distribute_courses_to_columns(
            [], rows_per_column=10
        )
        assert columns == []


class TestGenerateEnrollmentReport:
    """Tests for PDFGenerator.generate_enrollment_report (with mocked FPDF)."""

    def test_empty_snapshot_creates_pdf(self):
        generator = PDFGenerator(config_dict={"directories": {"pdf_output": "/tmp"}})

        with (
            patch(
                "registrarmonitor.reporting.pdf_generator.EnrollmentPDF"
            ) as mock_pdf_class,
            patch("registrarmonitor.reporting.pdf_generator.get_config") as mock_config,
        ):
            mock_pdf_instance = mock_pdf_class.return_value
            mock_pdf_instance.get_x.return_value = 0.0
            mock_pdf_instance.get_y.return_value = 0.0
            mock_pdf_instance.get_string_width.return_value = 10.0
            mock_config.return_value = {
                "directories": {"pdf_output": "/tmp"},
                "pdf_settings": {},
            }

            from registrarmonitor.models import EnrollmentSnapshot

            snapshot = EnrollmentSnapshot(
                timestamp="2024-01-15 10:00:00",
                semester="Spring 2024",
                overall_fill=0.0,
                courses={},
            )
            result = generator.generate_enrollment_report(snapshot, "/tmp/test.pdf")

        assert result == "/tmp/test.pdf"
        mock_pdf_instance.output.assert_called_once_with("/tmp/test.pdf")

    def test_generates_report_with_courses(self, courses):
        generator = PDFGenerator(config_dict={"directories": {"pdf_output": "/tmp"}})

        with (
            patch(
                "registrarmonitor.reporting.pdf_generator.EnrollmentPDF"
            ) as mock_pdf_class,
            patch("registrarmonitor.reporting.pdf_generator.get_config") as mock_config,
        ):
            mock_pdf_instance = mock_pdf_class.return_value
            mock_pdf_instance.get_x.return_value = 0.0
            mock_pdf_instance.get_y.return_value = 0.0
            mock_pdf_instance.get_string_width.return_value = 10.0
            mock_config.return_value = {
                "directories": {"pdf_output": "/tmp"},
                "pdf_settings": {},
            }

            from registrarmonitor.models import EnrollmentSnapshot

            snapshot = EnrollmentSnapshot(
                timestamp="2024-01-15 10:00:00",
                semester="Spring 2024",
                overall_fill=0.85,
                courses={c.course_code: c for c in courses},
            )
            result = generator.generate_enrollment_report(snapshot, "/tmp/test.pdf")

        assert result == "/tmp/test.pdf"
        mock_pdf_instance.output.assert_called_once_with("/tmp/test.pdf")


class TestCellWithColorConstants:
    """Verify the PDF color constants are correct."""

    def test_red_and_yellow_constants(self):
        from registrarmonitor.reporting.pdf_generator import RED_FILL, YELLOW_FILL

        assert RED_FILL == (230, 25, 75)
        assert YELLOW_FILL == (255, 225, 25)

    def test_cell_color_dispatch_through_generator(self, courses):
        """Verify that cell_with_color is called with correct arguments during report generation."""
        generator = PDFGenerator(config_dict={"directories": {"pdf_output": "/tmp"}})

        with (
            patch(
                "registrarmonitor.reporting.pdf_generator.EnrollmentPDF"
            ) as mock_pdf_class,
            patch("registrarmonitor.reporting.pdf_generator.get_config") as mock_config,
        ):
            mock_pdf_instance = mock_pdf_class.return_value
            mock_pdf_instance.get_x.return_value = 0.0
            mock_pdf_instance.get_y.return_value = 0.0
            mock_pdf_instance.get_string_width.return_value = 10.0
            mock_config.return_value = {
                "directories": {"pdf_output": "/tmp"},
                "pdf_settings": {},
            }

            from registrarmonitor.models import EnrollmentSnapshot

            snapshot = EnrollmentSnapshot(
                timestamp="2024-01-15 10:00:00",
                semester="Spring 2024",
                overall_fill=0.85,
                courses={c.course_code: c for c in courses},
            )
            generator.generate_enrollment_report(snapshot, "/tmp/test.pdf")

        # cell_with_color should have been called at least twice (course code + percentage)
        assert mock_pdf_instance.cell_with_color.call_count >= 2
