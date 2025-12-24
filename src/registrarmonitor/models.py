from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Section:
    section_id: str
    section_type: str
    enrollment: int
    capacity: int
    fill: float

    @property
    def is_filled(self) -> bool:
        return self.fill >= 1.0

    @property
    def is_near_filled(self) -> bool:
        return 0.75 <= self.fill < 1.0

    def to_dict(self) -> dict:
        """Convert Section to dictionary for serialization."""
        return {
            "section_id": self.section_id,
            "section_type": self.section_type,
            "enrollment": self.enrollment,
            "capacity": self.capacity,
            "fill": self.fill,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Section":
        """Create Section from dictionary."""
        return cls(
            section_id=data.get("section_id", ""),
            section_type=data["section_type"],
            enrollment=data["enrollment"],
            capacity=data["capacity"],
            fill=data["fill"],
        )


@dataclass
class Course:
    course_code: str
    department: str
    sections: dict[str, Section] = field(default_factory=dict)
    average_fill: float = 0.0
    course_title: Optional[str] = None

    @property
    def is_filled(self) -> bool:
        """Check if all sections of at least one type are filled."""
        if not self.sections:
            return False

        # Group sections by type
        sections_by_type: dict[str, list[Section]] = {}
        for section in self.sections.values():
            if section.section_type not in sections_by_type:
                sections_by_type[section.section_type] = []
            sections_by_type[section.section_type].append(section)

        # Check if any section type has all sections filled
        return any(
            sections and all(section.is_filled for section in sections)
            for sections in sections_by_type.values()
        )

    @property
    def is_near_filled(self) -> bool:
        """Check if course is near capacity but not filled."""
        return not self.is_filled and self.average_fill >= 0.75

    @property
    def total_enrollment(self) -> int:
        """Get total enrollment for the course.

        When a course has multiple section types (e.g., Lectures, Recitations, Labs),
        students must enroll in one section of each type. Therefore, the actual
        enrollment is limited by the section type with the minimum total enrollment.
        """
        if not self.sections:
            return 0

        # Group sections by type
        sections_by_type: dict[str, list[Section]] = {}
        for section in self.sections.values():
            if section.section_type not in sections_by_type:
                sections_by_type[section.section_type] = []
            sections_by_type[section.section_type].append(section)

        # Calculate total enrollment for each type
        enrollment_by_type = {
            section_type: sum(s.enrollment for s in sections)
            for section_type, sections in sections_by_type.items()
        }

        # Return minimum enrollment across all types
        return min(enrollment_by_type.values()) if enrollment_by_type else 0

    @property
    def total_capacity(self) -> int:
        """Get total capacity for the course.

        When a course has multiple section types (e.g., Lectures, Recitations, Labs),
        students must enroll in one section of each type. Therefore, the actual
        capacity is limited by the section type with the minimum total capacity.
        """
        if not self.sections:
            return 0

        # Group sections by type
        sections_by_type: dict[str, list[Section]] = {}
        for section in self.sections.values():
            if section.section_type not in sections_by_type:
                sections_by_type[section.section_type] = []
            sections_by_type[section.section_type].append(section)

        # Calculate total capacity for each type
        capacity_by_type = {
            section_type: sum(s.capacity for s in sections)
            for section_type, sections in sections_by_type.items()
        }

        # Return minimum capacity across all types
        return min(capacity_by_type.values()) if capacity_by_type else 0

    def to_dict(self) -> dict:
        """Convert Course to dictionary for serialization."""
        return {
            "course_code": self.course_code,
            "department": self.department,
            "sections": {
                sid: section.to_dict() for sid, section in self.sections.items()
            },
            "average_fill": self.average_fill,
            "course_title": self.course_title,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Course":
        """Create Course from dictionary."""
        # Handle both old and new JSON formats
        course_code = data.get("course_code", "")
        course = cls(
            course_code=course_code,
            department=data["department"],
            average_fill=data["average_fill"],
            course_title=data.get("course_title"),
        )
        # Convert sections
        for sid, section_data in data["sections"].items():
            # Add section_id if not present (old format compatibility)
            if "section_id" not in section_data:
                section_data["section_id"] = sid
            course.sections[sid] = Section.from_dict(section_data)
        return course


@dataclass
class EnrollmentSnapshot:
    timestamp: str
    semester: str
    overall_fill: float
    courses: dict[str, Course] = field(default_factory=dict)

    def calculate_overall_fill(self) -> float:
        """Calculate overall system fill rate."""
        if not self.courses:
            return 0.0

        total_enrollment = sum(
            course.total_enrollment for course in self.courses.values()
        )
        total_capacity = sum(course.total_capacity for course in self.courses.values())

        return total_enrollment / total_capacity if total_capacity > 0 else 0.0

    def calculate_total_enrollment(self) -> int:
        """Get total enrollment across all courses."""
        return sum(course.total_enrollment for course in self.courses.values())

    def calculate_total_capacity(self) -> int:
        """Get total capacity across all courses."""
        return sum(course.total_capacity for course in self.courses.values())

    def to_dict(self) -> dict:
        """Convert EnrollmentSnapshot to dictionary for serialization."""
        return {
            "timestamp": self.timestamp,
            "semester": self.semester,
            "overall_fill": self.overall_fill,
            "courses": {
                code: course.to_dict() for code, course in self.courses.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EnrollmentSnapshot":
        """Create EnrollmentSnapshot from dictionary."""
        snapshot = cls(
            timestamp=data["timestamp"],
            semester=data["semester"],
            overall_fill=data["overall_fill"],
        )
        # Convert courses
        for code, course_data in data["courses"].items():
            snapshot.courses[code] = Course.from_dict(course_data)
        return snapshot


@dataclass
class SectionChangeDetail:
    section_id: str
    previous_fill: Optional[float] = None
    current_fill: Optional[float] = None
    previous_enrollment: Optional[int] = None
    current_enrollment: Optional[int] = None
    previous_capacity: Optional[int] = None
    current_capacity: Optional[int] = None


@dataclass
class CourseChangeDetail:
    course_code: str
    previous_average_fill: Optional[float] = None
    current_average_fill: Optional[float] = None
    added_sections: list[Section] = field(default_factory=list)
    removed_sections: list[Section] = field(default_factory=list)
    modified_sections: list[SectionChangeDetail] = field(default_factory=list)


@dataclass
class EnrollmentComparison:
    previous_snapshot_timestamp: str
    current_snapshot_timestamp: str
    new_courses: list[Course] = field(default_factory=list)
    removed_courses: list[Course] = field(default_factory=list)
    changed_courses: list[CourseChangeDetail] = field(default_factory=list)
