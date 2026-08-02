"""Create a tiny generated-site fixture for browser and crawl CI jobs."""

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.website.config import ALL_SEMESTERS


def main() -> None:
    test_section = Section("001", "Lecture", 12, 20, 0.6, "Test Instructor")
    test_course = Course(
        "TEST 101",
        "TEST",
        {"001": test_section},
        0.6,
        "Generated-site smoke fixture",
    )
    math_sections = {
        "1L": Section("1L", "Lecture", 12, 20, 0.6, "Test Instructor"),
        "1R": Section("1R", "Recitation", 8, 20, 0.4, "Test Instructor"),
    }
    math_course = Course(
        "MATH 161",
        "MATH",
        math_sections,
        0.5,
        "Generated historical-comparison fixture",
    )
    for semester in ALL_SEMESTERS:
        snapshot = EnrollmentSnapshot(
            timestamp="2026-07-29 00:00:00",
            semester=semester,
            overall_fill=0.6,
            courses={"TEST 101": test_course, "MATH 161": math_course},
        )
        DatabaseManager(semester=semester).store_enrollment_snapshot(snapshot)


if __name__ == "__main__":
    main()
