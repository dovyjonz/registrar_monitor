from pathlib import Path
import re
from typing import Any, Optional, Tuple, List, Union


def format_course_code(code: str, width: int = 8) -> str:
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


def get_section_type(section: Any) -> str:
    """Extract the section type from a section code."""
    if not section:
        return ""
    s_type = "".join(c for c in str(section) if not c.isdigit())
    if s_type.endswith("Lb"):
        return "B"
    return s_type


def get_sort_priority(section_type: str) -> int:
    """
    Return a sort priority for a section type.

    Priority Order:
    0: Lecture (L)
    1: Seminar (S), Discussion (D), Recitation (R)
    2: Lab (B, Lb)
    3: Others
    """
    if section_type == "L":
        return 0
    elif section_type in ["S", "D", "R"]:
        return 1
    elif section_type in ["B", "Lb"]:
        return 2
    else:
        return 3


def _get_sort_priority(section_type: str) -> int:
    """Deprecated internal wrapper for get_sort_priority."""
    return get_sort_priority(section_type)


def get_section_sort_key(
    section_id: str, section_type: Optional[str] = None
) -> Tuple[int, List[Union[int, str]]]:
    """
    Get sorting key for a section.

    Args:
        section_id: The section ID string (e.g., "10L")
        section_type: Optional section type code (e.g., "L"). If None, inferred from ID.

    Returns:
        Tuple of (priority, natural_sort_key)
    """
    # 1. Determine Type Priority
    if section_type is None:
        section_type = get_section_type(section_id)

    priority = get_sort_priority(section_type)

    # 2. Natural Sort of ID
    # Split into numeric and non-numeric parts
    # e.g. "10L" -> ['', 10, 'L']
    natural_key = [int(c) if c.isdigit() else c for c in re.split(r"(\d+)", section_id)]

    return (priority, natural_key)


def _group_sections_by_type(
    course_sections: list[dict[str, Any]],
) -> tuple[dict[str, list[int]], set[int]]:
    """
    Group sections by type and collect their fill percentages.

    Returns:
        A tuple of (section_types dict, all_fills set)
    """
    section_types: dict[str, list[int]] = {}
    seen_sections: set[tuple[str, str]] = set()
    all_fills: set[int] = set()

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


def _format_type_summary(s_type: str, fills: list[int], num_section_types: int) -> str:
    """Format a summary string for a single section type."""
    if not fills:
        return ""

    type_prefix = "" if num_section_types == 1 else s_type[0]

    num_full = sum(1 for f in fills if f >= 1)
    num_fill = len(fills)
    avg_fill = sum(fills) / num_fill
    min_fill = min(fills)
    max_fill = max(fills)

    # Multiple identical sections
    if max_fill - min_fill < 0.05:
        fill_percent = int(avg_fill * 100) if num_section_types > 1 else ""
        count_suffix = f"×{num_fill}" if num_fill > 1 else ""
        return f"{type_prefix}{fill_percent}{count_suffix}"

    # Sections with full ones
    if num_full > 0:
        partial = num_fill - num_full
        if partial == 0:
            return f"{type_prefix}F×{num_full}"
        avg_non_full = sum(f for f in fills if f < 1) / partial
        count_suffix = f"×{partial}" if partial > 1 else ""
        return f"{type_prefix}{int(avg_non_full * 100)}{count_suffix}|{num_full}"

    # Default case: show range
    return f"{type_prefix}{int(min_fill * 100)}-{int(max_fill * 100)}×{num_fill}"


def analyze_section_pattern(course_sections: list[dict[str, Any]]) -> str:
    """
    Create a compact section fill analysis.
    Format examples:
    L80×3 R95×2  (3 lectures at 80%, 2 recitations at 95%)
    L75-90(2F)   (lectures ranging 75-90% with 2 full sections)
    L85P90       (lecture at 85%, practicum at 90%)
    """
    if not course_sections:
        return ""

    section_types, all_fills = _group_sections_by_type(course_sections)

    if len(all_fills) == 1:
        return ""

    sorted_types = sorted(
        section_types.items(), key=lambda x: (_get_sort_priority(x[0]), x[0])
    )

    patterns = [
        _format_type_summary(s_type, fills, len(section_types))
        for s_type, fills in sorted_types
        if fills
    ]

    return " ".join(patterns)


def calculate_effective_rows(data_items: list[tuple]) -> float:
    """Calculate effective number of rows needed, accounting for department spacing."""
    total_rows = 0.0
    current_dept = None

    for index, _ in data_items:
        # Add regular row
        total_rows += 1

        # Check for department change spacing
        dept = str(index).split()[0] if " " in str(index) else str(index)
        if current_dept is not None and dept != current_dept:
            total_rows += 0.5  # Add half-row for department spacing
        current_dept = dept

    return total_rows


def generate_safe_filename_components(semester: str, timestamp: str) -> tuple[str, str]:
    """
    Generate safe filename components from semester and timestamp.

    Args:
        semester: Semester name (e.g., "Spring 2024")
        timestamp: Timestamp string (e.g., "2024-01-15 10:30:00")

    Returns:
        Tuple of (safe_semester, safe_timestamp) strings suitable for filenames
    """
    safe_semester = semester.replace(" ", "_").lower()
    safe_timestamp = timestamp.replace(":", "-").replace(" ", "_")
    return safe_semester, safe_timestamp


def construct_output_path(
    output_dir: str, semester: str, timestamp: str, extension: str
) -> str:
    """
    Construct a full output path for a report file.

    Args:
        output_dir: Directory where the file will be saved
        semester: Semester name
        timestamp: Timestamp string
        extension: File extension (e.g., ".pdf", ".txt")

    Returns:
        Full path to the output file
    """
    safe_semester, safe_timestamp = generate_safe_filename_components(
        semester, timestamp
    )
    filename = f"{safe_semester}_{safe_timestamp}{extension}"
    return str(Path(output_dir) / filename)
