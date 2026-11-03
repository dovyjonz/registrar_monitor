"""Tests for the PDF generator module (helper functions and layout logic)."""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.models import Course, Section
from registrarmonitor.reporting.pdf_generator import (
    PDFGenerator,
)

# ── Helper function tests ─────────────────────────────────────────


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


class TestGenerateEnrollmentReport:
    """Tests for PDFGenerator.generate_enrollment_report (with mocked FPDF)."""

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
