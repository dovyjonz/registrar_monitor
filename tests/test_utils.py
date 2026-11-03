"""Tests for the utils module (formatting, sorting, analysis functions)."""

import pytest

pytestmark = pytest.mark.unit


from registrarmonitor.utils import (
    get_section_sort_key,
)


class TestGetSectionSortKey:
    """Tests for get_section_sort_key function."""

    def test_lecture_before_recitation(self):
        """Lectures should sort before recitations."""
        lecture_key = get_section_sort_key("10L", "L")
        recitation_key = get_section_sort_key("5R", "R")
        assert lecture_key < recitation_key

    def test_natural_sorting_within_type(self):
        """Sections of same type should sort naturally."""
        key_2 = get_section_sort_key("2L", "L")
        key_10 = get_section_sort_key("10L", "L")
        assert key_2 < key_10

    def test_inferred_section_type(self):
        """Section type should be inferred if not provided."""
        key = get_section_sort_key("10L")
        assert key[0] == 0  # Lecture priority

    def test_mixed_alphanumeric(self):
        """Mixed alphanumeric IDs should sort correctly."""
        key_a = get_section_sort_key("1A", "L")
        key_b = get_section_sort_key("1B", "L")
        assert key_a < key_b
