from typing import Any

from fpdf import FPDF

from ..config import get_config
from ..models import Course, EnrollmentSnapshot
from ..utils import get_section_type, get_sort_priority
from ..validation import validate_directory_exists

# ── PDF-local utility helpers ──────────────────────────────────────


def _format_course_code(code: str, width: int = 8) -> str:
    """Format course code to have consistent width by adjusting spacing."""
    if not code:
        return " " * width

    parts = code.split()
    if len(parts) != 2:
        return code.ljust(width)

    dept, num = parts
    base_num = num[:3]
    extra_chars = num[3:] if len(num) > 3 else ""

    space_needed = width - len(dept) - len(base_num)
    return f"{dept}{' ' * space_needed}{base_num}{extra_chars}"


def _group_sections_by_type(
    course_sections: list[dict[str, Any]],
) -> tuple[dict[str, list[float]], set[float]]:
    """Group sections by type and collect their fill percentages."""
    section_types: dict[str, list[float]] = {}
    seen_sections: set[tuple[str, str]] = set()
    all_fills: set[float] = set()

    for section in course_sections:
        s_type = get_section_type(section["S/T"])
        section_num = str(section["S/T"])

        section_key = (s_type, section_num)
        if section_key in seen_sections:
            continue

        seen_sections.add(section_key)
        all_fills.add(section["Fill"])

        if s_type not in section_types:
            section_types[s_type] = []
        section_types[s_type].append(section["Fill"])

    return section_types, all_fills


def _format_type_summary(
    s_type: str, fills: list[float], num_section_types: int
) -> str:
    """Format a summary string for a single section type."""
    if not fills:
        return ""

    type_prefix = "" if num_section_types == 1 else s_type[0]

    num_full = sum(1 for f in fills if f >= 1)
    num_fill = len(fills)
    avg_fill = sum(fills) / num_fill
    min_fill = min(fills)
    max_fill = max(fills)

    if max_fill - min_fill < 0.05:
        fill_percent = int(avg_fill * 100) if num_section_types > 1 else ""
        count_suffix = f"×{num_fill}" if num_fill > 1 else ""
        return f"{type_prefix}{fill_percent}{count_suffix}"

    if num_full > 0:
        partial = num_fill - num_full
        if partial == 0:
            return f"{type_prefix}F×{num_full}"
        avg_non_full = sum(f for f in fills if f < 1) / partial
        count_suffix = f"×{partial}" if partial > 1 else ""
        return f"{type_prefix}{int(avg_non_full * 100)}{count_suffix}|{num_full}"

    return f"{type_prefix}{int(min_fill * 100)}-{int(max_fill * 100)}×{num_fill}"


def _analyze_section_pattern(course_sections: list[dict[str, Any]]) -> str:
    """Create a compact section fill analysis."""
    if not course_sections:
        return ""

    section_types, all_fills = _group_sections_by_type(course_sections)

    if len(all_fills) == 1:
        return ""

    sorted_types = sorted(
        section_types.items(), key=lambda x: (get_sort_priority(x[0]), x[0])
    )

    patterns = [
        _format_type_summary(s_type, fills, len(section_types))
        for s_type, fills in sorted_types
        if fills
    ]

    return " ".join(patterns)


def _calculate_effective_rows(data_items: list[tuple]) -> float:
    """Calculate effective number of rows needed, accounting for department spacing."""
    total_rows = 0.0
    current_dept = None

    for index, _ in data_items:
        total_rows += 1
        dept = str(index).split()[0] if " " in str(index) else str(index)
        if current_dept is not None and dept != current_dept:
            total_rows += 0.5
        current_dept = dept

    return total_rows


# PDF layout constants
COLUMN_WIDTH = 17
PERCENT_WIDTH = 8
SPACING = 4
ROW_HEIGHT = 3.5
MARGIN = 5
PAGE_HEIGHT = 297  # A4 height in mm
FOOTER_HEIGHT = 15

# Fill colors
RED_FILL = (230, 25, 75)
YELLOW_FILL = (255, 225, 25)


class EnrollmentPDF(FPDF):
    """Custom PDF class for enrollment reports."""

    def __init__(self, semester: str, timestamp: str, overall_fill: float):
        super().__init__(orientation="P", format="A4")
        self.semester = semester
        self.timestamp = timestamp
        self.overall_fill = overall_fill

        # Load font configuration
        config = get_config()
        pdf_settings = config.get("pdf_settings", {})
        self.font_name = pdf_settings.get("font_name", "JetBrains Mono")
        self.font_path = pdf_settings.get(
            "font_path", "/Users/spook/Library/Fonts/JetBrainsMono-Regular.ttf"
        )
        self.font_size_normal = pdf_settings.get("font_size_normal", 7)
        self.font_size_footer = pdf_settings.get("font_size_footer", 8)
        self.font_size_pattern = 2  # Small font for section patterns

        # Department rows calculation method
        self.use_legacy_dept_rows = pdf_settings.get("use_legacy_dept_rows", False)

        # Add JetBrains Mono font
        self.add_font(self.font_name, "", self.font_path, uni=True)
        self.add_page()
        self.set_font(self.font_name, "", self.font_size_normal)

    def cell_with_color(
        self,
        w: float,
        h: float,
        txt: str,
        fill_value: float,
        border: int = 1,
        force_red: bool = False,
        align: str = "L",
        course_sections: list[dict[str, Any]] | None = None,
    ):
        """Draw a cell with background color based on fill value."""
        # Store current position
        start_x = self.get_x()
        start_y = self.get_y()

        # Clip text if too long
        txt = str(txt)
        if self.get_string_width(txt) > w:
            while self.get_string_width(txt + "..") + 2 > w and len(txt) > 0:
                txt = txt[:-1]
            txt = txt + ".."

        # Draw main cell with color
        if force_red or fill_value >= 1.0:
            self.set_fill_color(*RED_FILL)
            self.cell(w, h, txt, bool(border), 0, align, True)
        elif fill_value >= 0.75:
            self.set_fill_color(*YELLOW_FILL)
            self.cell(w, h, txt, bool(border), 0, align, True)
        else:
            self.cell(w, h, txt, bool(border), 0, align)

        # Add section pattern summary if available and cell is yellow
        if course_sections and 0.75 <= fill_value < 1.0:
            pattern = _analyze_section_pattern(course_sections)
            if pattern:
                # Set smaller font for pattern
                self.set_font(self.font_name, "", self.font_size_pattern)

                # Split pattern into lines (one per section type)
                pattern_lines = pattern.split()

                if pattern_lines:
                    # Calculate position for pattern (after the percentage cell)
                    pattern_x = start_x + w + 7.3  # After percentage column
                    pattern_y = start_y

                    pattern_width = 15  # Maximum width for pattern text

                    # Draw each line of the pattern
                    line_height = h / len(pattern_lines)
                    for line in pattern_lines:
                        self.set_xy(pattern_x, pattern_y)
                        self.cell(pattern_width, line_height, line, 0, 2, "L")
                        pattern_y += line_height

                # Restore original font size
                self.set_font(self.font_name, "", self.font_size_normal)

                # Reset position to end of original cell
                self.set_xy(start_x + w, start_y)

    def footer(self):
        """Add footer with semester, timestamp, and attribution."""
        self.set_y(-FOOTER_HEIGHT)
        self.set_font(self.font_name, "", self.font_size_footer)

        # Add semester and timestamp on the left
        self.set_x(MARGIN)
        self.cell(
            self.w - 2 * MARGIN - 45,
            5,
            f"Semester: {self.semester}",
            0,
            0,
            "L",
        )
        self.cell(45, 5, f"Overall fill: {self.overall_fill:.0%}", 0, 1, "R")

        self.set_x(MARGIN)
        self.cell(
            self.w - 2 * MARGIN - 45,
            5,
            f"Generated: {self.timestamp}",
            0,
            0,
            "L",
        )
        self.cell(
            45,
            5,
            "Made by @spooktaken",
            0,
            0,
            "R",
        )


class PDFGenerator:
    """Generate PDF enrollment reports."""

    def __init__(self, config_dict: dict[str, Any] | None = None):
        if config_dict is None:
            config_dict = get_config()

        output_dir = config_dict.get("directories", {}).get("pdf_output", "assets/pdf")
        self.output_dir = output_dir
        validate_directory_exists(output_dir, create_if_missing=True)

        # Store configuration for use in methods
        self.config = config_dict

    def generate_enrollment_report(
        self,
        current_snapshot: EnrollmentSnapshot,
        output_path: str,
        previous_snapshot=None,
    ) -> str:
        """Generate enrollment report from snapshot data.

        Args:
            current_snapshot: Current enrollment snapshot
            output_path: Full path where to save the PDF
            previous_snapshot: Optional previous snapshot for comparison

        Returns:
            str: Path to the generated PDF file
        """
        # Sort courses by code for consistent output
        sorted_courses = sorted(
            current_snapshot.courses.values(), key=lambda c: c.course_code
        )

        if not sorted_courses:
            # Create empty report
            pdf = EnrollmentPDF(
                semester=current_snapshot.semester,
                timestamp=current_snapshot.timestamp,
                overall_fill=0.0,
            )
            pdf.output(output_path)
            return output_path

        # Generate PDF
        return self._generate_pdf_to_path(
            courses=sorted_courses,
            semester=current_snapshot.semester,
            timestamp=current_snapshot.timestamp,
            overall_fill=current_snapshot.overall_fill,
            output_path=output_path,
        )

    def _generate_pdf_to_path(
        self,
        courses: list[Course],
        semester: str,
        timestamp: str,
        overall_fill: float,
        output_path: str,
    ) -> str:
        """Generate PDF and save to specific path."""
        # Create PDF
        pdf = EnrollmentPDF(
            semester=semester,
            timestamp=timestamp,
            overall_fill=overall_fill,
        )

        # Group courses by department
        dept_groups = self._group_courses_by_department(courses)

        # Calculate rows per column
        usable_height = PAGE_HEIGHT - 2 * MARGIN - FOOTER_HEIGHT
        rows_per_column = int(usable_height / ROW_HEIGHT)

        # Check if we should use legacy department rows calculation
        pdf_settings = self.config.get("pdf_settings", {})
        use_legacy_dept_rows = pdf_settings.get("use_legacy_dept_rows", False)

        # Distribute courses across columns
        columns_data, _ = self._distribute_courses_to_columns(
            dept_groups, rows_per_column, use_legacy_dept_rows
        )

        # Create the PDF layout
        self._create_pdf_layout(columns_data, pdf)

        # Save PDF to specified path
        pdf.output(output_path)
        return output_path

    def _group_courses_by_department(self, courses: list[Course]) -> list[list[Course]]:
        """Group courses by department while maintaining overall order."""
        dept_groups = []
        current_dept = None
        current_group: list[Course] = []

        for course in courses:
            dept = course.department
            if dept != current_dept:
                if current_group:
                    dept_groups.append(current_group)
                current_group = [course]
                current_dept = dept
            else:
                current_group.append(course)

        if current_group:
            dept_groups.append(current_group)

        return dept_groups

    def _distribute_courses_to_columns(
        self,
        dept_groups: list[list[Course]],
        rows_per_column: int,
        use_legacy_dept_rows: bool = False,
    ) -> tuple[list[list[Course]], set[str]]:
        """Distribute course data across columns for display."""
        columns_data = []
        current_column = []
        current_row_count = 0.0
        split_depts: set[str] = set()

        for dept_group in dept_groups:
            if use_legacy_dept_rows:
                # Legacy method: simple count + spacing
                dept_rows = len(dept_group) + 0.5
            else:
                # New method: sophisticated calculation
                # Create dummy items (course_code, None) to match calculate_effective_rows expected input
                # which expects a list of tuples where first element has course code
                dummy_items = [(c.course_code, None) for c in dept_group]
                dept_rows = _calculate_effective_rows(dummy_items)

            remaining_space = rows_per_column - current_row_count

            # If department fits in current column, add it
            if dept_rows <= remaining_space:
                current_column.extend(dept_group)
                current_row_count += dept_rows
            # Otherwise start new column
            else:
                if current_column:
                    columns_data.append(current_column)
                current_column = list(dept_group)
                current_row_count = dept_rows

        # Add remaining items
        if current_column:
            columns_data.append(current_column)

        return columns_data, split_depts

    def _create_pdf_layout(
        self,
        columns_data: list[list[Course]],
        pdf: EnrollmentPDF,
    ) -> None:
        """Layout the course data in columns on the PDF."""
        for col, column_items in enumerate(columns_data):
            x_pos = MARGIN + col * (COLUMN_WIDTH + PERCENT_WIDTH + SPACING)
            y_pos = MARGIN
            pdf.set_xy(x_pos, y_pos)
            current_dept = None

            for course in column_items:
                dept = course.department

                # Department spacing
                if current_dept is not None and dept != current_dept:
                    pdf.set_xy(x_pos, pdf.get_y() + ROW_HEIGHT / 2)
                current_dept = dept

                fill_value = course.average_fill
                current_x = pdf.get_x()
                current_y = pdf.get_y()

                # Write course and percentage
                formatted_code = _format_course_code(course.course_code)
                force_red = course.is_filled

                # Convert sections to dicts compatible with utils.analyze_section_pattern
                # which expects keys "S/T" and "Fill"
                mapped_sections = []
                for s in course.sections.values():
                    d = s.to_dict()
                    d["S/T"] = d["section_id"]
                    d["Fill"] = d["fill"]
                    mapped_sections.append(d)

                pdf.cell_with_color(
                    COLUMN_WIDTH,
                    ROW_HEIGHT,
                    formatted_code,
                    fill_value,
                    force_red=force_red,
                    course_sections=mapped_sections,
                )

                pdf.set_xy(current_x + COLUMN_WIDTH, current_y)
                pdf.cell_with_color(
                    PERCENT_WIDTH,
                    ROW_HEIGHT,
                    f"{fill_value:.0%}",
                    fill_value,
                    force_red=force_red,
                    align="R",
                )

                pdf.set_xy(current_x, current_y + ROW_HEIGHT)
