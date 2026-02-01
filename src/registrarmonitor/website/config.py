"""Configuration constants for website generation."""

from pathlib import Path

# CLI argument to semester display name mapping
SEMESTER_MAP: dict[str, str] = {
    "spring2026": "Spring 2026",
    "fall2025": "Fall 2025",
    "summer2025": "Summer 2025",
}

# All semesters in display order (latest first)
ALL_SEMESTERS: list[str] = ["Spring 2026", "Fall 2025", "Summer 2025"]

# Latest semester (used for index.html redirect)
LATEST_SEMESTER: str = ALL_SEMESTERS[0]

# Default output directory
OUTPUT_DIR = Path(__file__).parent.parent.parent.parent / "assets" / "website" / "public"


def semester_to_filename(semester: str) -> str:
    """Convert semester display name to URL-friendly filename."""
    # "Spring 2026" -> "spring2026.html"
    return semester.lower().replace(" ", "") + ".html"


# Registration milestones for each semester
# Colors use warm gradient (red-orange) for 1st priority,
# cool gradient (cyan-blue) for 2nd priority, and magenta for 3rd priority
MILESTONES_MAP: dict[str, list[dict[str, str]]] = {
    "Spring 2026": [
        # First Priority - December 17 (warm: red-orange gradient)
        {"time": "2025-12-17T09:00:00", "label": "Y4+", "color": "#FF1744"},
        {"time": "2025-12-17T11:00:00", "label": "Y3", "color": "#FF5722"},
        {"time": "2025-12-17T13:00:00", "label": "Y2", "color": "#FF9100"},
        {"time": "2025-12-17T15:00:00", "label": "Y1", "color": "#FFC400"},
        # Second Priority - December 18 (cool: cyan-blue gradient)
        {"time": "2025-12-18T09:00:00", "label": "Y4+", "color": "#00E5FF"},
        {"time": "2025-12-18T11:00:00", "label": "Y3", "color": "#00B0FF"},
        {"time": "2025-12-18T13:00:00", "label": "Y2", "color": "#2979FF"},
        {"time": "2025-12-18T15:00:00", "label": "Y1", "color": "#651FFF"},
        # Third Priority - December 19 (distinct: magenta)
        {"time": "2025-12-19T09:00:00", "label": "ALL", "color": "#D500F9"},
    ],
    "Fall 2025": [
        # First Priority - August 6 (warm: red-orange gradient)
        {"time": "2025-08-06T09:00:00", "label": "Y4+", "color": "#FF1744"},
        {"time": "2025-08-06T11:00:00", "label": "Y3", "color": "#FF5722"},
        {"time": "2025-08-06T13:00:00", "label": "Y2", "color": "#FF9100"},
        # First Priority continues - August 13
        {"time": "2025-08-13T09:00:00", "label": "Y1", "color": "#FFC400"},
        # Second Priority - August 14 (cool: cyan-blue gradient)
        {"time": "2025-08-14T09:00:00", "label": "Y4+", "color": "#00E5FF"},
        {"time": "2025-08-14T11:00:00", "label": "Y3", "color": "#00B0FF"},
        {"time": "2025-08-14T13:00:00", "label": "Y2", "color": "#2979FF"},
        {"time": "2025-08-14T15:00:00", "label": "Y1", "color": "#651FFF"},
        # Third Priority - August 15 (distinct: magenta)
        {"time": "2025-08-15T09:00:00", "label": "ALL", "color": "#D500F9"},
    ],
    "Summer 2025": [
        # First Priority - May 12 (warm: red-orange gradient)
        {"time": "2025-05-12T10:00:00", "label": "Y4+", "color": "#FF1744"},
        {"time": "2025-05-12T11:00:00", "label": "Y3", "color": "#FF5722"},
        {"time": "2025-05-12T12:00:00", "label": "Y2", "color": "#FF9100"},
        {"time": "2025-05-12T13:00:00", "label": "Y1", "color": "#FFC400"},
        # Second Priority - May 13 (cool: cyan-blue gradient)
        {"time": "2025-05-13T10:00:00", "label": "Y4+", "color": "#00E5FF"},
        {"time": "2025-05-13T11:00:00", "label": "Y2/Y1", "color": "#2979FF"},
        # Third Priority - May 14 (distinct: magenta)
        {"time": "2025-05-14T10:00:00", "label": "ALL", "color": "#D500F9"},
    ],
}

# Key mapping for JSON minification (verbose -> short)
# Used to reduce generated file size by ~15-20%
KEY_MAP: dict[str, str] = {
    "snapshotIdx": "i",
    "enrollment": "e",
    "capacity": "c",
    "fill": "f",
    "currentEnrollment": "ce",
    "currentCapacity": "cc",
    "currentFill": "cf",
    "averageFill": "af",
    "history": "h",
    "sections": "s",
    "department": "d",
    "instructor": "in",
    "timestamp": "ts",
    "sectionId": "sid",
    "overallFill": "of",
    "lastReportTime": "lrt",
    "snapshots": "sn",
    "courses": "cr",
    "semester": "sem",
    "semesters": "sems",
    "activeSemester": "as",
    "semesterData": "sd",
    "milestonesData": "md",
    "isFilled": "if",
    "type": "t",
    "title": "ti",
}
