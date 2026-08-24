"""Filter enrollment comparisons to a user's effective subscriptions."""

from collections import defaultdict

from ..models import CourseChangeDetail, EnrollmentComparison
from ..reporting.report_formatter import ReportFormatter
from .models import SubscriptionTarget


def filter_comparison(
    comparison: EnrollmentComparison,
    targets: list[SubscriptionTarget],
) -> EnrollmentComparison:
    """Return only reportable changes covered by course or section watches."""
    watched_courses = {target.course_code for target in targets if target.is_course}
    watched_sections: dict[str, set[str]] = defaultdict(set)
    for target in targets:
        if not target.is_course:
            watched_sections[target.course_code].add(target.section_code)

    filtered = EnrollmentComparison(
        previous_snapshot_timestamp=comparison.previous_snapshot_timestamp,
        current_snapshot_timestamp=comparison.current_snapshot_timestamp,
    )
    for course in comparison.new_courses:
        if course.course_code in watched_courses or watched_sections[
            course.course_code
        ].intersection(course.sections):
            filtered.new_courses.append(course)
    for course in comparison.removed_courses:
        if course.course_code in watched_courses or watched_sections[
            course.course_code
        ].intersection(course.sections):
            filtered.removed_courses.append(course)

    for change in comparison.changed_courses:
        if change.course_code in watched_courses:
            filtered.changed_courses.append(change)
            continue
        section_ids = watched_sections[change.course_code]
        if not section_ids:
            continue
        scoped = CourseChangeDetail(
            course_code=change.course_code,
            previous_average_fill=change.previous_average_fill,
            current_average_fill=change.current_average_fill,
            added_sections=[
                section
                for section in change.added_sections
                if section.section_id in section_ids
            ],
            removed_sections=[
                section
                for section in change.removed_sections
                if section.section_id in section_ids
            ],
            modified_sections=[
                section
                for section in change.modified_sections
                if section.section_id in section_ids
            ],
        )
        if ReportFormatter.has_reportable_course_change(scoped):
            filtered.changed_courses.append(scoped)
    return filtered
