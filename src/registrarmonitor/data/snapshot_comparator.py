from ..models import (
    CourseChangeDetail,
    EnrollmentComparison,
    EnrollmentSnapshot,
    SectionChangeDetail,
)


class SnapshotComparator:
    """Compares two EnrollmentSnapshot objects."""

    def compare_snapshots(
        self, current: EnrollmentSnapshot, previous: EnrollmentSnapshot
    ) -> EnrollmentComparison:
        """Compare two snapshots and identify structural changes."""
        comparison = EnrollmentComparison(
            previous_snapshot_timestamp=previous.timestamp,
            current_snapshot_timestamp=current.timestamp,
        )

        current_course_codes = set(current.courses.keys())
        previous_course_codes = set(previous.courses.keys())

        for course_code in current_course_codes - previous_course_codes:
            comparison.new_courses.append(current.courses[course_code])

        for course_code in previous_course_codes - current_course_codes:
            comparison.removed_courses.append(previous.courses[course_code])

        for course_code in current_course_codes.intersection(previous_course_codes):
            current_course = current.courses[course_code]
            prev_course = previous.courses[course_code]

            course_detail = CourseChangeDetail(
                course_code=course_code,
                previous_average_fill=prev_course.average_fill,
                current_average_fill=current_course.average_fill,
            )

            made_changes_to_course = False
            # Note: We intentionally don't check average_fill here because it's
            # a derived value. A course is only "changed" if it has actual
            # section changes (added, removed, or modified sections).

            current_section_ids = set(current_course.sections.keys())
            prev_section_ids = set(prev_course.sections.keys())

            for section_id in current_section_ids - prev_section_ids:
                course_detail.added_sections.append(current_course.sections[section_id])
                made_changes_to_course = True

            for section_id in prev_section_ids - current_section_ids:
                course_detail.removed_sections.append(prev_course.sections[section_id])
                made_changes_to_course = True

            for section_id in current_section_ids.intersection(prev_section_ids):
                current_section = current_course.sections[section_id]
                prev_section = prev_course.sections[section_id]

                if (
                    abs(current_section.fill - prev_section.fill) > 0.001
                    or current_section.enrollment != prev_section.enrollment
                    or current_section.capacity != prev_section.capacity
                ):
                    section_detail = SectionChangeDetail(
                        section_id=section_id,
                        previous_fill=prev_section.fill,
                        current_fill=current_section.fill,
                        previous_enrollment=prev_section.enrollment,
                        current_enrollment=current_section.enrollment,
                        previous_capacity=prev_section.capacity,
                        current_capacity=current_section.capacity,
                    )
                    course_detail.modified_sections.append(section_detail)
                    made_changes_to_course = True

            if made_changes_to_course:
                comparison.changed_courses.append(course_detail)
        return comparison
