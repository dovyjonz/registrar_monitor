"""Tests for the utils module (formatting, sorting, analysis functions)."""

from registrarmonitor.utils import (
    analyze_section_pattern,
    construct_output_path,
    format_course_code,
    generate_safe_filename_components,
    get_section_sort_key,
    get_section_type,
    get_sort_priority,
)


class TestFormatCourseCode:
    """Tests for format_course_code function."""

    def test_standard_course_code(self):
        """Standard course code should be formatted with proper spacing."""
        result = format_course_code("CS 101", width=8)
        assert len(result) >= 8
        assert "CS" in result
        assert "101" in result

    def test_long_course_code(self):
        """Long course codes should be handled gracefully."""
        result = format_course_code("MATH 101A", width=8)
        assert "MATH" in result
        assert "101" in result

    def test_empty_code(self):
        """Empty code should return spaces."""
        result = format_course_code("", width=8)
        assert result == " " * 8

    def test_single_word_code(self):
        """Single word code should be left-justified."""
        result = format_course_code("CS101", width=8)
        assert len(result) == 8


class TestGetSectionType:
    """Tests for get_section_type function."""

    def test_lecture_section(self):
        """Lecture sections should return 'L'."""
        assert get_section_type("10L") == "L"
        assert get_section_type("1L") == "L"

    def test_recitation_section(self):
        """Recitation sections should return 'R'."""
        assert get_section_type("5R") == "R"

    def test_lab_section(self):
        """Lab sections should return 'B'."""
        assert get_section_type("3Lb") == "B"
        assert get_section_type("2B") == "B"

    def test_seminar_section(self):
        """Seminar sections should return 'S'."""
        assert get_section_type("1S") == "S"

    def test_discussion_section(self):
        """Discussion sections should return 'D'."""
        assert get_section_type("4D") == "D"

    def test_empty_section(self):
        """Empty section should return empty string."""
        assert get_section_type("") == ""
        assert get_section_type(None) == ""

    def test_numeric_only(self):
        """Numeric only should return empty string."""
        assert get_section_type("123") == ""


class TestGetSortPriority:
    """Tests for get_sort_priority function."""

    def test_lecture_highest_priority(self):
        """Lectures should have highest priority (0)."""
        assert get_sort_priority("L") == 0

    def test_seminar_discussion_recitation_priority(self):
        """S, D, R should have priority 1."""
        assert get_sort_priority("S") == 1
        assert get_sort_priority("D") == 1
        assert get_sort_priority("R") == 1

    def test_lab_priority(self):
        """Labs should have priority 2."""
        assert get_sort_priority("B") == 2
        assert get_sort_priority("Lb") == 2

    def test_other_priority(self):
        """Other types should have priority 3."""
        assert get_sort_priority("X") == 3
        assert get_sort_priority("P") == 3


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


class TestAnalyzeSectionPattern:
    """Tests for analyze_section_pattern function."""

    def test_empty_sections(self):
        """Empty sections should return empty string."""
        assert analyze_section_pattern([]) == ""

    def test_single_fill_value(self):
        """Single fill value should return empty string."""
        sections = [{"S/T": "10L", "Fill": 0.80}]
        assert analyze_section_pattern(sections) == ""

    def test_multiple_section_types(self):
        """Multiple section types should produce summary."""
        sections = [
            {"S/T": "10L", "Fill": 0.80},
            {"S/T": "11L", "Fill": 0.85},
            {"S/T": "1R", "Fill": 0.70},
        ]
        result = analyze_section_pattern(sections)
        assert result != ""
        # Should contain type indicators
        assert "L" in result or "R" in result


class TestGenerateSafeFilenameComponents:
    """Tests for generate_safe_filename_components function."""

    def test_basic_conversion(self):
        """Basic semester and timestamp should be converted safely."""
        semester, timestamp = generate_safe_filename_components(
            "Spring 2024", "2024-01-15 10:30:00"
        )
        assert semester == "spring_2024"
        assert ":" not in timestamp
        assert " " not in timestamp

    def test_special_characters(self):
        """Special characters should be handled."""
        semester, timestamp = generate_safe_filename_components(
            "Fall 2024", "2024-09-01 14:00:00"
        )
        assert semester == "fall_2024"
        assert timestamp == "2024-09-01_14-00-00"


class TestConstructOutputPath:
    """Tests for construct_output_path function."""

    def test_pdf_extension(self):
        """PDF path should be constructed correctly."""
        path = construct_output_path(
            "/tmp/output", "Spring 2024", "2024-01-15 10:30:00", ".pdf"
        )
        assert path.endswith(".pdf")
        assert "spring_2024" in path
        assert "/tmp/output/" in path

    def test_txt_extension(self):
        """TXT path should be constructed correctly."""
        path = construct_output_path(
            "/tmp/output", "Fall 2024", "2024-09-01 14:00:00", ".txt"
        )
        assert path.endswith(".txt")
        assert "fall_2024" in path
