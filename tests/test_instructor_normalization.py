"""Tests for the instructor normalization module."""
import pytest

pytestmark = pytest.mark.unit


from registrarmonitor.data.instructor_normalization import (
    aggregate_instructors_by_section,
    normalize_instructors,
)


class TestNormalizeInstructors:
    """Tests for normalize_instructors."""

    def test_empty_iterable(self):
        assert normalize_instructors([]) == ""

    def test_single_name(self):
        assert normalize_instructors(["Smith"]) == "Smith"

    def test_deupes_duplicates(self):
        assert normalize_instructors(["Smith", "Smith"]) == "Smith"

    def test_filters_tba(self):
        assert normalize_instructors(["TBA"]) == ""

    def test_filters_tba_mixed(self):
        assert normalize_instructors(["Smith", "TBA", "Jones"]) == "Smith, Jones"

    def test_tba_tba_value(self):
        assert normalize_instructors(["TBA TBA"]) == ""

    def test_tba1_tba1_value(self):
        assert normalize_instructors(["TBA1 TBA1"]) == ""

    def test_handles_comma_separated_input(self):
        assert normalize_instructors(["Smith, Jones"]) == "Smith, Jones"

    def test_handles_comma_separated_with_tba(self):
        assert normalize_instructors(["Smith, TBA, Jones"]) == "Smith, Jones"

    def test_skips_non_string_values(self):
        assert normalize_instructors([None, 123, "Smith"]) == "Smith"

    def test_strips_whitespace(self):
        assert normalize_instructors(["  Smith  ", "  Jones  "]) == "Smith, Jones"

    def test_keeps_multiple_distinct_names(self):
        assert (
            normalize_instructors(["Smith", "Jones", "Brown"]) == "Smith, Jones, Brown"
        )


class TestAggregateInstructorsBySection:
    """Tests for aggregate_instructors_by_section."""

    def test_simple_case(self):
        rows = [
            {"Course Abbr": "CS 101", "S/T": "10L", "Instructor": "Smith"},
            {"Course Abbr": "CS 101", "S/T": "11L", "Instructor": "Jones"},
        ]
        result = aggregate_instructors_by_section(rows)
        assert result[("CS 101", "10L")] == "Smith"
        assert result[("CS 101", "11L")] == "Jones"

    def test_aggregates_multiple_rows_for_same_section(self):
        rows = [
            {"Course Abbr": "CS 101", "S/T": "10L", "Instructor": "Smith"},
            {"Course Abbr": "CS 101", "S/T": "10L", "Instructor": "Jones"},
        ]
        result = aggregate_instructors_by_section(rows)
        assert result[("CS 101", "10L")] == "Smith, Jones"

    def test_skips_rows_without_course_code(self):
        rows = [{"Course Abbr": "", "S/T": "10L", "Instructor": "Smith"}]
        result = aggregate_instructors_by_section(rows)
        assert result == {}

    def test_skips_rows_without_section_code(self):
        rows = [{"Course Abbr": "CS 101", "S/T": "", "Instructor": "Smith"}]
        result = aggregate_instructors_by_section(rows)
        assert result == {}

    def test_handles_missing_keys(self):
        rows = [{"Instructor": "Smith"}]
        result = aggregate_instructors_by_section(rows)
        assert result == {}

    def test_strips_whitespace_from_keys(self):
        rows = [
            {"Course Abbr": "  CS 101  ", "S/T": "  10L  ", "Instructor": "Smith"},
        ]
        result = aggregate_instructors_by_section(rows)
        assert ("CS 101", "10L") in result
        assert result[("CS 101", "10L")] == "Smith"
