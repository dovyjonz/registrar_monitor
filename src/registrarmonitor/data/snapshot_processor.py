import itertools
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Optional, List, Dict

from ..config import get_config
from ..models import (
    Course,
    EnrollmentSnapshot,
    Section,
)
from ..utils import get_section_type
from ..validation import validate_directory_exists
from .database_manager import DatabaseManager


class SnapshotProcessor:
    """Processes data into EnrollmentSnapshot objects and stores them in the database."""

    def __init__(self, data_dir: Optional[str] = None):
        if data_dir is None:
            config = get_config()
            data_dir = config["directories"]["data_storage"]
        self.data_dir = data_dir
        validate_directory_exists(data_dir, create_if_missing=True)

        # Database manager will be created per semester
        self.db_manager: Optional[DatabaseManager] = None
        self._current_semester: Optional[str] = None

    def process_data(
        self, data: List[Dict[str, Any]], semester: str, timestamp: str
    ) -> EnrollmentSnapshot:
        """Process data list into EnrollmentSnapshot model."""
        if not data:
            return EnrollmentSnapshot(
                timestamp=timestamp, semester=semester, overall_fill=0.0
            )

        # Check for required keys in the first row (assuming uniform data)
        first_row = data[0]
        if "Level" not in first_row or "Cap" not in first_row:
            return EnrollmentSnapshot(
                timestamp=timestamp, semester=semester, overall_fill=0.0
            )

        # Filter: UG level and Cap > 0
        filtered_data = [
            row for row in data if row.get("Level") == "UG" and row.get("Cap", 0) > 0
        ]

        if not filtered_data:
            return EnrollmentSnapshot(
                timestamp=timestamp, semester=semester, overall_fill=0.0
            )

        total_enrollment = sum(row.get("Enr", 0) for row in filtered_data)
        total_capacity = sum(row.get("Cap", 0) for row in filtered_data)

        overall_fill = 0.0
        if total_capacity > 0:
            overall_fill = float(
                (Decimal(total_enrollment) / Decimal(total_capacity)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                )
            )

        snapshot = EnrollmentSnapshot(
            timestamp=timestamp,
            semester=semester,
            overall_fill=overall_fill,
        )

        # Sort by Course Abbr for groupby (itertools requirement)
        filtered_data.sort(key=lambda x: str(x.get("Course Abbr", "")))

        # Use groupby for efficient single-pass iteration
        for course_code_val, group in itertools.groupby(
            filtered_data, key=lambda x: str(x.get("Course Abbr", ""))
        ):
            course_rows = list(group)
            course_code = str(course_code_val)
            dept = course_code.split()[0] if " " in course_code else course_code

            # Extract course title from the first row of this course
            course_title = None
            first_course_row = course_rows[0]
            if "Course Title" in first_course_row:
                course_title = str(first_course_row["Course Title"]).strip()

            fills = [row.get("Fill", 0.0) for row in course_rows]
            course_avg_fill = 0.0
            if fills:
                # Use Decimal for precise mean calculation and rounding
                # Convert floats to string first to avoid precision artifacts
                avg = sum(Decimal(str(f)) for f in fills) / Decimal(len(fills))
                course_avg_fill = float(
                    avg.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
                )

            course = Course(
                course_code=course_code,
                department=dept,
                average_fill=course_avg_fill,
                course_title=course_title,
            )

            for section_row in course_rows:
                section_id = str(section_row.get("S/T", ""))
                section = Section(
                    section_id=section_id,
                    section_type=get_section_type(section_id),
                    enrollment=int(section_row.get("Enr", 0)),
                    capacity=int(section_row.get("Cap", 0)),
                    fill=float(section_row.get("Fill", 0.0)),
                )
                course.sections[section_id] = section

            snapshot.courses[course_code] = course

        return snapshot

    def save_snapshot(self, snapshot: EnrollmentSnapshot) -> None:
        """Save enrollment snapshot to the database.

        Args:
            snapshot: The enrollment snapshot to persist.
        """
        # Create or get database manager for this semester
        if self.db_manager is None or self._current_semester != snapshot.semester:
            self.db_manager = DatabaseManager.create_for_semester(snapshot.semester)
            self._current_semester = snapshot.semester

        self.db_manager.store_enrollment_snapshot(snapshot)
        print("✅ Stored snapshot in database")
