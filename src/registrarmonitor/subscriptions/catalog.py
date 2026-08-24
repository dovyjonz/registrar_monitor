"""Search and validate subscription targets against the latest snapshot."""

import re

from ..models import Course, EnrollmentSnapshot
from .models import SubscriptionTarget


def normalize_search(value: str) -> str:
    return " ".join(value.upper().split())


def _compact(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize_search(value))


class SubscriptionCatalog:
    """A read-only view of one semester's latest stored enrollment state."""

    def __init__(self, snapshot: EnrollmentSnapshot) -> None:
        self.snapshot = snapshot

    def search(self, query: str, *, limit: int = 8) -> list[Course]:
        normalized = normalize_search(query)
        compact = _compact(query)
        if not compact:
            return []
        exact = [
            course
            for course in self.snapshot.courses.values()
            if _compact(course.course_code) == compact
        ]
        if exact:
            return sorted(exact, key=lambda course: course.course_code)[:limit]
        matches = [
            course
            for course in self.snapshot.courses.values()
            if compact in _compact(course.course_code)
            or normalized in normalize_search(course.course_title or "")
        ]
        return sorted(matches, key=lambda course: course.course_code)[:limit]

    def resolve(self, target: SubscriptionTarget) -> bool:
        if target.semester != self.snapshot.semester:
            return False
        course = self.snapshot.courses.get(target.course_code)
        if course is None:
            return False
        return target.is_course or target.section_code in course.sections

    def exact_target(self, query: str) -> SubscriptionTarget | None:
        """Resolve an exact course or course/section expression."""
        compact = _compact(query)
        if not compact:
            return None
        semester = self.snapshot.semester
        for course in self.snapshot.courses.values():
            course_compact = _compact(course.course_code)
            if compact == course_compact:
                return SubscriptionTarget(semester, course.course_code)
            for section in course.sections.values():
                if compact == course_compact + _compact(section.section_id):
                    return SubscriptionTarget(
                        semester,
                        course.course_code,
                        section.section_id,
                    )
        return None

    def course(self, course_code: str) -> Course | None:
        return self.snapshot.courses.get(course_code)
