"""Formats enrollment data into human-readable reports."""

from ..availability import calculate_availability
from ..models import (
    Course,
    CourseChangeDetail,
    EnrollmentComparison,
    EnrollmentSnapshot,
)
from ..registration import derive_priority_state, get_priority_milestones
from ..utils import get_section_sort_key

# Status thresholds
NEAR_THRESHOLD = 0.75  # 75%


class ReportFormatter:
    """Formats enrollment data into human-readable reports."""

    @staticmethod
    def has_reportable_section_change(section_change) -> bool:
        """Return whether a modified section has an enrollment-side change."""
        return (
            section_change.current_enrollment != section_change.previous_enrollment
            or section_change.current_capacity != section_change.previous_capacity
        )

    @classmethod
    def has_reportable_course_change(cls, course_change: CourseChangeDetail) -> bool:
        """Ignore instructor-only changes in text reports."""
        return bool(
            course_change.added_sections
            or course_change.removed_sections
            or any(
                cls.has_reportable_section_change(section_change)
                for section_change in course_change.modified_sections
            )
        )

    def has_reportable_changes(self, comparison: EnrollmentComparison) -> bool:
        """Return whether a comparison warrants an enrollment report."""
        return bool(
            comparison.new_courses
            or comparison.removed_courses
            or any(
                self.has_reportable_course_change(course_change)
                for course_change in comparison.changed_courses
            )
        )

    def _get_status_emoji(
        self, fill: float, is_course: bool = False, course: Course | None = None
    ) -> str:
        """Get status emoji based on fill percentage.

        For courses, check if any section type is completely filled.
        """
        if is_course and course and course.is_filled:
            return "🔴"
        if fill >= 1.0:
            return "🔴"
        if fill >= NEAR_THRESHOLD:
            return "🟠"
        return "🟢"

    def _modified_section_sort_key(self, sec_mod, current_course_obj) -> tuple:
        """Sort modified sections using shared sort logic."""
        section_type = None
        if current_course_obj and sec_mod.section_id in current_course_obj.sections:
            section_type = current_course_obj.sections[sec_mod.section_id].section_type

        return get_section_sort_key(sec_mod.section_id, section_type)

    @staticmethod
    def _course_availability(course: Course) -> dict:
        return calculate_availability(
            {
                code: {
                    "type": section.section_type,
                    "currentEnrollment": section.enrollment,
                    "currentCapacity": section.capacity,
                }
                for code, section in course.sections.items()
            }
        )

    def _course_heading(self, course: Course) -> str:
        availability = self._course_availability(course)
        if availability["status"] == "required-type-full":
            limiting_types = availability["limitingTypes"]
            all_types = availability["types"]
            state = (
                availability["compact"]
                if len(limiting_types) < len(all_types)
                else "100%"
            )
            return f"{course.course_code} - {state}"
        return course.course_code

    def format_changes_report(
        self,
        comparison: EnrollmentComparison,
        current: EnrollmentSnapshot,
        previous: EnrollmentSnapshot,
    ) -> str:
        """Format changes from EnrollmentComparison into a compact, emoji-based report."""

        report_lines = []

        timestamp = comparison.current_snapshot_timestamp
        priority = derive_priority_state(
            get_priority_milestones(current.semester),
            at=timestamp,
        )
        priority_copy = (
            f" · {priority['compact']}" if priority and priority.get("compact") else ""
        )
        report_lines.append(f"📅 {timestamp}{priority_copy}")
        report_lines.append("")

        # Pre-compute lookups for O(1) access
        new_course_codes = {c.course_code for c in comparison.new_courses}
        removed_course_codes = {c.course_code for c in comparison.removed_courses}
        changed_courses_dict = {
            cc.course_code: cc
            for cc in comparison.changed_courses
            if self.has_reportable_course_change(cc)
        }

        all_course_codes: set[str] = (
            new_course_codes | removed_course_codes | set(changed_courses_dict.keys())
        )

        if not all_course_codes:
            report_lines.append("No significant changes detected.")
            return "\n".join(report_lines)

        # Avoid redundant list conversion if sorting a set
        sorted_course_codes = sorted(all_course_codes)

        for course_code in sorted_course_codes:
            current_course = current.courses.get(course_code)
            prev_course = previous.courses.get(course_code)

            is_new_course = course_code in new_course_codes
            is_removed_course = course_code in removed_course_codes
            course_change_detail = changed_courses_dict.get(course_code)

            # Format course header line
            if is_new_course and current_course:
                heading = self._course_heading(current_course)
                report_lines.append(f"+ {heading} - COURSE ADDED")

            elif is_removed_course and prev_course:
                report_lines.append(f"− {course_code} - COURSE REMOVED")

            elif course_change_detail and current_course and prev_course:
                report_lines.append(self._course_heading(current_course))

                # Format sections with changes
                section_lines = []

                # Added sections
                for section in sorted(
                    course_change_detail.added_sections,
                    key=lambda s: get_section_sort_key(s.section_id, s.section_type),
                ):
                    section_lines.append(
                        f"  + {section.section_id:<4} {section.enrollment:>3}/{section.capacity} - SECTION ADDED"
                    )

                # Removed sections
                for section in sorted(
                    course_change_detail.removed_sections,
                    key=lambda s: get_section_sort_key(s.section_id, s.section_type),
                ):
                    section_lines.append(
                        f"  − {section.section_id:<4}                 - SECTION REMOVED"
                    )

                # Modified sections
                reportable_modified_sections = [
                    section_change
                    for section_change in course_change_detail.modified_sections
                    if self.has_reportable_section_change(section_change)
                ]
                for sec_mod in sorted(
                    reportable_modified_sections,
                    key=lambda sm: self._modified_section_sort_key(sm, current_course),
                ):
                    curr_sec = current_course.sections.get(sec_mod.section_id)
                    if curr_sec:
                        sec_emoji = self._get_status_emoji(curr_sec.fill)
                        enrollment_delta = (sec_mod.current_enrollment or 0) - (
                            sec_mod.previous_enrollment or 0
                        )
                        delta_text = (
                            f"+{enrollment_delta}"
                            if enrollment_delta > 0
                            else f"−{abs(enrollment_delta)}"
                            if enrollment_delta < 0
                            else "0"
                        )
                        section_lines.append(
                            f"  {sec_emoji} {sec_mod.section_id:<4}: "
                            f"{sec_mod.current_enrollment:>3}/{sec_mod.current_capacity} "
                            f"({delta_text})"
                        )

                if section_lines:
                    report_lines.extend(section_lines)

            report_lines.append("")

        return "\n".join(report_lines).rstrip()
