from pathlib import Path
import re
from typing import Any, Optional, Tuple, List, Union


def get_section_type(section: Any) -> str:
    """Extract the section type from a section code."""
    if not section:
        return ""
    s_type = "".join(c for c in str(section) if not c.isdigit())

    # Normalize lab variants (e.g., 'Lb', 'lb') to the canonical 'B' code
    if s_type.lower() == "lb":
        return "B"

    return s_type.upper()


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
