"""Formats enrollment data into human-readable reports."""

from typing import Optional, Set

from ..models import (
    Course,
    EnrollmentComparison,
    EnrollmentSnapshot,
)
from ..utils import get_section_sort_key

# Status thresholds
NEAR_THRESHOLD = 0.75  # 75%
SIGNIFICANT_CHANGE_THRESHOLD = 0.15  # 15%


class ReportFormatter:
    """Formats enrollment data into human-readable reports."""

    def _get_status_emoji(
        self, fill: float, is_course: bool = False, course: Optional[Course] = None
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

    def _format_change_delta(self, delta: float) -> str:
        """Format change delta with optional significant change indicator."""
        if abs(delta) > SIGNIFICANT_CHANGE_THRESHOLD:
            return f"🔺{delta:+.0%}"
        return f"{delta:+.0%}"

    def _modified_section_sort_key(self, sec_mod, current_course_obj) -> tuple:
        """Sort modified sections using shared sort logic."""
        section_type = None
        if current_course_obj and sec_mod.section_id in current_course_obj.sections:
            section_type = current_course_obj.sections[sec_mod.section_id].section_type

        return get_section_sort_key(sec_mod.section_id, section_type)

    def format_changes_report(
        self,
        comparison: EnrollmentComparison,
        current: EnrollmentSnapshot,
        previous: EnrollmentSnapshot,
    ) -> str:
        """Format changes from EnrollmentComparison into a compact, emoji-based report."""

        report_lines = []

        # Header with date/time and overall fill
        timestamp = comparison.current_snapshot_timestamp
        overall_fill_change = current.overall_fill - previous.overall_fill
        change_str = self._format_change_delta(overall_fill_change)
        report_lines.append(
            f"📅 {timestamp} | 📈 {current.overall_fill:.0%} ({change_str})"
        )
        report_lines.append("")

        # Collect all course codes that changed
        all_course_codes: Set[str] = set()
        all_course_codes.update(c.course_code for c in comparison.new_courses)
        all_course_codes.update(c.course_code for c in comparison.removed_courses)
        all_course_codes.update(cc.course_code for cc in comparison.changed_courses)

        if not all_course_codes:
            report_lines.append("No significant changes detected.")
            return "\n".join(report_lines)

        sorted_course_codes = sorted(list(all_course_codes))

        for course_code in sorted_course_codes:
            current_course = current.courses.get(course_code)
            prev_course = previous.courses.get(course_code)

            is_new_course = any(
                c.course_code == course_code for c in comparison.new_courses
            )
            is_removed_course = any(
                c.course_code == course_code for c in comparison.removed_courses
            )

            course_change_detail = next(
                (
                    cc
                    for cc in comparison.changed_courses
                    if cc.course_code == course_code
                ),
                None,
            )

            # Format course header line
            if is_new_course and current_course:
                emoji = self._get_status_emoji(
                    current_course.average_fill, is_course=True, course=current_course
                )
                report_lines.append(
                    f"✨ {course_code} {current_course.average_fill:.0%} (NEW)"
                )
                # Show all sections for new courses
                for sec in sorted(
                    current_course.sections.values(),
                    key=lambda s: get_section_sort_key(s.section_id, s.section_type),
                ):
                    sec_emoji = self._get_status_emoji(sec.fill)
                    report_lines.append(
                        f"  {sec_emoji} {sec.section_id:<4}: {sec.enrollment:>3}/{sec.capacity}"
                    )

            elif is_removed_course and prev_course:
                report_lines.append(
                    f"❌ {course_code} (REMOVED) was {prev_course.average_fill:.0%}"
                )

            elif course_change_detail and current_course and prev_course:
                # Skip if there are no actual section changes
                if not (
                    course_change_detail.added_sections
                    or course_change_detail.removed_sections
                    or course_change_detail.modified_sections
                ):
                    continue

                emoji = self._get_status_emoji(
                    current_course.average_fill, is_course=True, course=current_course
                )
                avg_fill_delta = current_course.average_fill - prev_course.average_fill
                change_str = self._format_change_delta(avg_fill_delta)
                report_lines.append(
                    f"{emoji} {course_code} {current_course.average_fill:.0%} ({change_str})"
                )

                # Format sections with changes
                section_lines = []

                # Added sections
                for section in sorted(
                    course_change_detail.added_sections,
                    key=lambda s: get_section_sort_key(s.section_id, s.section_type),
                ):
                    sec_emoji = self._get_status_emoji(section.fill)
                    section_lines.append(
                        f"  {sec_emoji} {section.section_id:<4}: {section.enrollment:>3}/{section.capacity} (NEW)"
                    )

                # Removed sections
                for section in sorted(
                    course_change_detail.removed_sections,
                    key=lambda s: get_section_sort_key(s.section_id, s.section_type),
                ):
                    section_lines.append(f"  ❌ {section.section_id:<4}: (REMOVED)")

                # Modified sections
                for sec_mod in sorted(
                    course_change_detail.modified_sections,
                    key=lambda sm: self._modified_section_sort_key(sm, current_course),
                ):
                    curr_sec = current_course.sections.get(sec_mod.section_id)
                    if curr_sec:
                        sec_emoji = self._get_status_emoji(curr_sec.fill)
                        enrollment_delta = (sec_mod.current_enrollment or 0) - (
                            sec_mod.previous_enrollment or 0
                        )
                        section_lines.append(
                            f"  {sec_emoji} {sec_mod.section_id:<4}: {sec_mod.current_enrollment:>3}/{sec_mod.current_capacity} ({enrollment_delta:+d})"
                        )

                if section_lines:
                    report_lines.extend(section_lines)

            report_lines.append("")

        return "\n".join(report_lines).rstrip()
