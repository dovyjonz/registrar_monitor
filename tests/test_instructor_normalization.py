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

    def test_deupes_duplicates(self):
        assert normalize_instructors(["Smith", "Smith"]) == "Smith"

    def test_filters_tba(self):
        assert normalize_instructors(["TBA"]) == ""

    def test_handles_comma_separated_input(self):
        assert normalize_instructors(["Smith, Jones"]) == "Smith, Jones"

    def test_removes_html_markup_and_preserves_name_separators(self):
        assert normalize_instructors(["<b>Smith</b><br/> <span>Jones</span>"]) == (
            "Smith, Jones"
        )


class TestInstructorIdentity:
    """Tests for comparison-safe instructor identities."""

    @pytest.mark.parametrize(
        ("first", "formatted"),
        [
            ("Park, Chun Young", "Chun Young Park"),
            ("Arif, Syed Muhammad Umair", "Syed Muhammad Umair Arif"),
            ("Petrikin Jr, Ian Albert", "Ian Albert Petrikin Jr"),
            ("Ted Parent, Parent, Ted", "Ted Parent"),
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

    def test_ignores_placeholder_and_suffix_formatting(self):
        assert instructor_identity("TBA") == instructor_identity("TBA TBA")
        assert instructor_identity("Petrikin, Jr, Ian Albert") == instructor_identity(
            "Ian Albert Petrikin Jr"
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

    def test_aggregates_duplicate_full_and_last_first_fragments_once(self):
        rows = [
            {"Course Abbr": "CS 101", "S/T": "10L", "Instructor": "Ted Parent"},
            {
                "Course Abbr": "CS 101",
                "S/T": "10L",
                "Instructor": "Parent, Ted",
            },
        ]

        result = aggregate_instructors_by_section(rows)

        assert result[("CS 101", "10L")] == "Ted Parent"
