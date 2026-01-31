import json
import os
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
    """Processes data into EnrollmentSnapshot objects."""

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
            row for row in data
            if row.get("Level") == "UG" and row.get("Cap", 0) > 0
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
                (Decimal(total_enrollment) / Decimal(total_capacity))
                .quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
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

    def save_snapshot(self, snapshot: EnrollmentSnapshot) -> str:
        """Save enrollment snapshot to a file."""
        # Save to JSON file first
        data: dict[str, Any] = {
            "timestamp": snapshot.timestamp,
            "semester": snapshot.semester,
            "overall_fill": snapshot.overall_fill,
            "courses": {},
        }

        for course_code, course in snapshot.courses.items():
            # Type the course data dictionary
            course_data: dict[str, Any] = {
                "department": course.department,
                "average_fill": course.average_fill,
                "course_title": course.course_title,
                "sections": {},
            }
            data["courses"][course_code] = course_data

            for section_id, section in course.sections.items():
                # Type the section data dictionary
                section_data: dict[str, Any] = {
                    "section_type": section.section_type,
                    "enrollment": section.enrollment,
                    "capacity": section.capacity,
                    "fill": section.fill,
                }
                course_data["sections"][section_id] = section_data

        safe_semester = snapshot.semester.replace(" ", "_").lower()
        filename = f"{safe_semester}_{snapshot.timestamp.replace(':', '-').replace(' ', '_')}.json"
        filepath = os.path.join(self.data_dir, filename)

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)

        # Also store in database
        try:
            # Create or get database manager for this semester
            if self.db_manager is None or self._current_semester != snapshot.semester:
                self.db_manager = DatabaseManager.create_for_semester(snapshot.semester)
                self._current_semester = snapshot.semester

            self.db_manager.store_enrollment_snapshot(snapshot)
            print("✅ Stored snapshot in database")
        except Exception as e:
            print(f"⚠️  Failed to store snapshot in database: {e}")
            # Continue with file-based storage as fallback

        return filepath

    def _deserialize_snapshot_from_dict(self, data: dict) -> EnrollmentSnapshot:
        """Deserialize snapshot data from dictionary to EnrollmentSnapshot object.

        Args:
            data: Dictionary containing snapshot data

        Returns:
            EnrollmentSnapshot object
        """
        snapshot = EnrollmentSnapshot(
            timestamp=data["timestamp"],
            semester=data["semester"],
            overall_fill=data["overall_fill"],
        )

        for course_code, course_data in data["courses"].items():
            course = Course(
                course_code=course_code,
                department=course_data["department"],
                average_fill=course_data["average_fill"],
                course_title=course_data.get("course_title", "").strip()
                if course_data.get("course_title")
                else None,
            )

            for section_id, section_data in course_data["sections"].items():
                section = Section(
                    section_id=section_id,
                    section_type=section_data["section_type"],
                    enrollment=section_data["enrollment"],
                    capacity=section_data["capacity"],
                    fill=section_data["fill"],
                )
                course.sections[section_id] = section

            snapshot.courses[course_code] = course

        return snapshot

    def load_latest_snapshot(
        self, semester: str, current_timestamp: Optional[str] = None
    ) -> Optional[EnrollmentSnapshot]:
        """Load the most recent enrollment snapshot for a given semester."""
        safe_semester = semester.replace(" ", "_").lower()
        files = [
            f
            for f in os.listdir(self.data_dir)
            if f.startswith(safe_semester) and f.endswith(".json")
        ]

        if not files:
            return None

        if current_timestamp:
            safe_current_timestamp = current_timestamp.replace(":", "-").replace(
                " ", "_"
            )
            current_file_name = f"{safe_semester}_{safe_current_timestamp}.json"
            files = [f for f in files if f != current_file_name]

        if not files:
            return None

        files.sort(reverse=True)
        latest_file = os.path.join(self.data_dir, files[0])

        with open(latest_file, "r") as f:
            data = json.load(f)

        return self._deserialize_snapshot_from_dict(data)

    def get_latest_snapshot(self) -> Optional[EnrollmentSnapshot]:
        """Get the most recent snapshot across all semesters."""
        if not os.path.exists(self.data_dir):
            return None

        json_files = [
            f
            for f in os.listdir(self.data_dir)
            if f.endswith(".json") and not f.startswith(".")
        ]

        if not json_files:
            return None

        # Sort by modification time to get the most recent
        json_files.sort(
            key=lambda f: os.path.getmtime(os.path.join(self.data_dir, f)), reverse=True
        )
        latest_file = os.path.join(self.data_dir, json_files[0])

        try:
            with open(latest_file, "r") as f:
                data = json.load(f)

            return self._deserialize_snapshot_from_dict(data)

        except Exception as e:
            print(f"Error loading latest snapshot from {latest_file}: {e}")
            return None
