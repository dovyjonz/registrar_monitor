from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from fpdf import FPDF

from ..config import get_config
from ..utils import (
    analyze_section_pattern,
    calculate_effective_rows,
    format_course_code,
)
from ..validation import validate_directory_exists

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
        course_sections: Optional[List[Dict[str, Any]]] = None,
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
            pattern = analyze_section_pattern(course_sections)
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

    def __init__(self, config_dict: Optional[Dict[str, Any]] = None):
        if config_dict is None:
            config_dict = get_config()

        output_dir = config_dict["directories"]["pdf_output"]
        self.output_dir = output_dir
        validate_directory_exists(output_dir, create_if_missing=True)

        # Store configuration for use in methods
        self.config = config_dict

    def generate_enrollment_report(
        self, current_snapshot, output_path: str, previous_snapshot=None
    ) -> str:
        """Generate enrollment report from snapshot data.

        Args:
            current_snapshot: Current enrollment snapshot
            output_path: Full path where to save the PDF
            previous_snapshot: Optional previous snapshot for comparison

        Returns:
            str: Path to the generated PDF file
        """
        import pandas as pd

        # Convert snapshot data to DataFrame format expected by generate_pdf
        data = []
        filled_courses = {}

        for course_code, course in current_snapshot.courses.items():
            for section_id, section in course.sections.items():
                data.append(
                    {
                        "Course Abbr": course_code,
                        "S/T": section_id,
                        "Enr": section.enrollment,
                        "Cap": section.capacity,
                        "Fill": section.fill,
                        "Level": "UG",  # Assuming UG level for all
                    }
                )

            # Check if course is filled (all sections at or near capacity)
            filled_courses[course_code] = (
                course.is_filled
                if hasattr(course, "is_filled")
                else course.average_fill >= 1.0
            )

        # Create DataFrame
        df = pd.DataFrame(data)
        filtered_df: pd.DataFrame = df[(df["Level"] == "UG") & (df["Cap"] > 0)]  # type: ignore[assignment]

        if filtered_df.empty:
            # Create empty report
            pdf = EnrollmentPDF(
                semester=current_snapshot.semester,
                timestamp=current_snapshot.timestamp,
                overall_fill=0.0,
            )
            pdf.output(output_path)
            return output_path

        # Create pivot table
        pivot: pd.DataFrame = (
            filtered_df.groupby("Course Abbr")["Fill"].mean().to_frame()
        )
        pivot.columns = pd.Index(["Fill"])

        # Use existing generate_pdf method
        return self._generate_pdf_to_path(
            pivot=pivot,
            filtered_df=filtered_df,
            filled_courses=filled_courses,
            semester=current_snapshot.semester,
            timestamp=current_snapshot.timestamp,
            output_path=output_path,
        )

    def _generate_pdf_to_path(
        self,
        pivot: pd.DataFrame,
        filtered_df: pd.DataFrame,
        filled_courses: Dict[str, bool],
        semester: str,
        timestamp: str,
        output_path: str,
    ) -> str:
        """Generate PDF and save to specific path."""
        # Calculate overall fill as total enrollment divided by total capacity
        total_enrollment = filtered_df["Enr"].sum()
        total_capacity = filtered_df["Cap"].sum()
        overall_fill = (
            (total_enrollment / total_capacity).round(2) if total_capacity > 0 else 0.0
        )

        # Create PDF
        pdf = EnrollmentPDF(
            semester=semester,
            timestamp=timestamp,
            overall_fill=overall_fill,
        )

        # Group courses by department
        dept_groups = self._group_courses_by_department(pivot)

        # Calculate rows per column
        usable_height = PAGE_HEIGHT - 2 * MARGIN - FOOTER_HEIGHT
        rows_per_column = int(usable_height / ROW_HEIGHT)

        # Check if we should use legacy department rows calculation
        pdf_settings = self.config.get("pdf_settings", {})
        use_legacy_dept_rows = pdf_settings.get("use_legacy_dept_rows", False)

        # Distribute courses across columns
        columns_data, _ = self._distribute_courses_to_columns(
            dept_groups, filtered_df, rows_per_column, use_legacy_dept_rows
        )

        # Create the PDF layout
        self._create_pdf_layout(columns_data, filtered_df, filled_courses, pdf)

        # Save PDF to specified path
        pdf.output(output_path)
        return output_path

    def _group_courses_by_department(self, pivot: pd.DataFrame) -> List[List[Tuple]]:
        """Group courses by department while maintaining overall order."""
        dept_groups = []
        current_dept = None
        current_group: list[tuple] = []

        data_items = [(index, row) for index, row in pivot.iterrows()]

        for index, row in data_items:
            dept = str(index).split()[0] if " " in str(index) else str(index)
            if dept != current_dept:
                if current_group:
                    dept_groups.append(current_group)
                current_group = [(index, row)]
                current_dept = dept
            else:
                current_group.append((index, row))

        if current_group:
            dept_groups.append(current_group)

        return dept_groups

    def _distribute_courses_to_columns(
        self,
        dept_groups: List[List[Tuple]],
        filtered_df: pd.DataFrame,
        rows_per_column: int,
        use_legacy_dept_rows: bool = False,
    ) -> Tuple[List[List[Tuple]], Set[str]]:
        """Distribute course data across columns for display.

        Args:
            use_legacy_dept_rows: If True, uses simple calculation (len + 0.5).
                                If False, uses sophisticated calculate_effective_rows.

        Example difference:
            For a department with 3 courses:
            - Legacy: 3 + 0.5 = 3.5 rows
            - New: Considers section patterns, spacing, etc. (may be 4.2 rows)
        """
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
                dept_rows = calculate_effective_rows(dept_group, filtered_df.to_dict())
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
        columns_data: List[List[Tuple]],
        filtered_df: pd.DataFrame,
        filled_courses: Dict[str, bool],
        pdf: EnrollmentPDF,
    ) -> None:
        """Layout the course data in columns on the PDF."""
        for col, column_items in enumerate(columns_data):
            x_pos = MARGIN + col * (COLUMN_WIDTH + PERCENT_WIDTH + SPACING)
            y_pos = MARGIN
            pdf.set_xy(x_pos, y_pos)
            current_dept = None

            for index, row in column_items:
                dept = str(index).split()[0] if " " in str(index) else str(index)

                # Department spacing
                if current_dept is not None and dept != current_dept:
                    pdf.set_xy(x_pos, pdf.get_y() + ROW_HEIGHT / 2)
                current_dept = dept

                fill_value = row["Fill"]
                current_x = pdf.get_x()
                current_y = pdf.get_y()

                # Write course and percentage
                formatted_code = format_course_code(str(index))
                force_red = filled_courses.get(index, False)
                course_df = filtered_df[filtered_df["Course Abbr"] == index]
                course_sections: list[dict[str, Any]] = course_df.to_dict("records")  # type: ignore

                pdf.cell_with_color(
                    COLUMN_WIDTH,
                    ROW_HEIGHT,
                    formatted_code,
                    fill_value,
                    force_red=force_red,
                    course_sections=course_sections,
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
