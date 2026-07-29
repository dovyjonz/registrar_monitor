"""Create a tiny generated-site fixture for browser and crawl CI jobs."""

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.website.config import ALL_SEMESTERS


def main() -> None:
    section = Section("001", "Lecture", 12, 20, 0.6, "Test Instructor")
    course = Course(
        "TEST 101",
        "TEST",
        {"001": section},
        0.6,
        "Generated-site smoke fixture",
    )
    for semester in ALL_SEMESTERS:
        snapshot = EnrollmentSnapshot(
            timestamp="2026-07-29 00:00:00",
            semester=semester,
            overall_fill=0.6,
            courses={"TEST 101": course},
        )
        DatabaseManager(semester=semester).store_enrollment_snapshot(snapshot)


if __name__ == "__main__":
    main()
