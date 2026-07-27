"""Tests for course share page generation and URL slug helpers."""

import pytest

pytestmark = pytest.mark.unit


from unittest.mock import patch

from registrarmonitor.website.config import (
    course_to_slug,
    semester_to_slug,
    slug_to_course_code,
)


class TestSemesterToSlug:
    def test_fall_2026(self):
        assert semester_to_slug("Fall 2026") == "fall-2026"

    def test_summer_2026(self):
        assert semester_to_slug("Summer 2026") == "summer-2026"

    def test_spring_2026(self):
        assert semester_to_slug("Spring 2026") == "spring-2026"

    def test_fall_2025(self):
        assert semester_to_slug("Fall 2025") == "fall-2025"


class TestCourseToSlug:
    def test_basic_code(self):
        assert course_to_slug("CSCI 101") == "csci-101"

    def test_code_with_letter(self):
        assert course_to_slug("MATH 201A") == "math-201a"

    def test_long_code(self):
        assert course_to_slug("ENGR 3020") == "engr-3020"

    def test_slash_in_code(self):
        """Slashes are removed to avoid creating nested directory paths."""
        assert course_to_slug("ANT 214/SOC 214") == "ant-214soc-214"


class TestSlugToCourseCode:
    def test_basic_slug(self):
        assert slug_to_course_code("csci-101") == "CSCI 101"

    def test_slug_with_letter(self):
        assert slug_to_course_code("math-201a") == "MATH 201a"

    def test_single_part(self):
        assert slug_to_course_code("cs") == "CS"


class TestBaseUrl:
    def test_uses_configured_value(self):
        with patch(
            "registrarmonitor.website.config._load_settings",
            return_value={
                "website": {
                    "base_url": "https://custom.example.com",
                    "pages_project_name": "test",
                }
            },
        ):
            from registrarmonitor.website.config import _get_base_url

            assert _get_base_url() == "https://custom.example.com"

    def test_strips_trailing_slash(self):
        with patch(
            "registrarmonitor.website.config._load_settings",
            return_value={
                "website": {
                    "base_url": "https://custom.example.com/",
                    "pages_project_name": "test",
                }
            },
        ):
            from registrarmonitor.website.config import _get_base_url

            assert _get_base_url() == "https://custom.example.com"

    def test_falls_back_to_pages_project(self):
        with patch(
            "registrarmonitor.website.config._load_settings",
            return_value={
                "website": {"base_url": "", "pages_project_name": "my-project"}
            },
        ):
            from registrarmonitor.website.config import _get_base_url

            assert _get_base_url() == "https://my-project.pages.dev"


class TestIndexing:
    def test_defaults_to_noindex(self):
        with patch(
            "registrarmonitor.website.config._load_settings",
            return_value={"website": {"indexing": "noindex"}},
        ):
            from registrarmonitor.website.config import _get_indexing

            assert _get_indexing() == "noindex"

    def test_reads_from_settings(self):
        with patch(
            "registrarmonitor.website.config._load_settings",
            return_value={"website": {"indexing": ""}},
        ):
            from registrarmonitor.website.config import _get_indexing

            assert _get_indexing() == ""
