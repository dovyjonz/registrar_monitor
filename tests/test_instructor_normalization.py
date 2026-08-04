"""Tests for the instructor normalization module."""

import pytest

pytestmark = pytest.mark.unit


from registrarmonitor.data.instructor_normalization import (
    aggregate_instructors_by_section,
    instructor_identity,
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

    def test_removes_html_markup_and_preserves_name_separators(self):
        assert normalize_instructors(["<b>Smith</b><br/> <span>Jones</span>"]) == (
            "Smith, Jones"
        )

    def test_keeps_multiple_distinct_names(self):
        assert (
            normalize_instructors(["Smith", "Jones", "Brown"]) == "Smith, Jones, Brown"
        )


class TestInstructorIdentity:
    """Tests for comparison-safe instructor identities."""

    @pytest.mark.parametrize(
        ("first", "formatted"),
        [
            ("Park, Chun Young", "Chun Young Park"),
            ("Arif, Syed Muhammad Umair", "Syed Muhammad Umair Arif"),
            ("Petrikin Jr, Ian Albert", "Ian Albert Petrikin Jr"),
            (
                "Mun, Ellina, Elkamhawy, Ahmed",
                "Ellina Mun, Ahmed Elkamhawy",
            ),
        ],
    )
    def test_ignores_registrar_name_formatting(self, first, formatted):
        assert instructor_identity(first) == instructor_identity(formatted)

    def test_ignores_html_and_name_order(self):
        assert instructor_identity("Akarca, Halit") == instructor_identity(
            "<span>Halit</span> Akarca"
        )

    def test_ignores_typographic_name_formatting(self):
        assert instructor_identity("Dr. José O'Connor-Smith") == instructor_identity(
            "Jose O Connor Smith"
        )

    def test_ignores_placeholder_and_suffix_formatting(self):
        assert instructor_identity("TBA") == instructor_identity("TBA TBA")
        assert instructor_identity("Petrikin, Jr, Ian Albert") == instructor_identity(
            "Ian Albert Petrikin Jr"
        )

    def test_ignores_order_of_multiple_instructors(self):
        assert instructor_identity("Ada Lovelace, Grace Hopper") == instructor_identity(
            "Grace Hopper, Ada Lovelace"
        )

    def test_preserves_multi_instructor_boundaries(self):
        assert instructor_identity("John Smith, Jane Doe") != instructor_identity(
            "John Doe, Jane Smith"
        )

    def test_preserves_genuine_name_changes(self):
        assert instructor_identity("Ada Lovelace") != instructor_identity(
            "Grace Hopper"
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
