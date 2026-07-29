"""Configuration constants for website generation."""

from pathlib import Path
from typing import Any


def _get_base_url() -> str:
    """Get the base URL for canonical links and sharing.

    Reads from settings.toml [website].base_url. Falls back to
    https://{pages_project_name}.pages.dev.
    """
    cfg = _load_settings()
    website = cfg.get("website", {})
    base = website.get("base_url", "").strip().rstrip("/")
    if base:
        return base
    project = website.get("pages_project_name", "registrar-monitor")
    return f"https://{project}.pages.dev"


def _get_indexing() -> str:
    """Get the indexing directive from settings."""
    cfg = _load_settings()
    return cfg.get("website", {}).get("indexing", "noindex")


def semester_to_slug(semester: str) -> str:
    """Convert semester display name to URL slug.

    "Summer 2026" -> "summer-2026"
    """
    return semester.lower().replace(" ", "-")


def course_to_slug(course_code: str) -> str:
    """Convert course code to URL-friendly slug.

    "CSCI 101" -> "csci-101"
    "MATH 201A" -> "math-201a"
    "ANT 214/SOC 214" -> "ant-214soc-214"
    """
    import re

    slug = course_code.lower().replace(" ", "-")
    # Remove filesystem-unsafe characters (/, \, :, *, ?, ", <, >, |)
    slug = re.sub(r'[/\\:*?"<>|]', "", slug)
    return slug


def slug_to_course_code(slug: str) -> str:
    """Convert URL slug back to course code.

    "csci-101" -> "CSCI 101"
    """
    parts = slug.split("-", 1)
    if len(parts) == 2:
        return f"{parts[0].upper()} {parts[1]}"
    return slug.upper()


# CLI argument to semester display name mapping
SEMESTER_MAP: dict[str, str] = {
    "fall2026": "Fall 2026",
    "summer2026": "Summer 2026",
    "spring2026": "Spring 2026",
    "fall2025": "Fall 2025",
    "summer2025": "Summer 2025",
}

# All semesters in display order (latest first)
ALL_SEMESTERS: list[str] = [
    "Fall 2026",
    "Summer 2026",
    "Spring 2026",
    "Fall 2025",
    "Summer 2025",
]

# Latest semester (used for index.html redirect)
LATEST_SEMESTER: str = ALL_SEMESTERS[0]

# Default output directory
OUTPUT_DIR = (
    Path(__file__).parent.parent.parent.parent / "assets" / "website" / "public"
)


def semester_to_filename(semester: str) -> str:
    """Convert semester display name to URL-friendly filename."""
    # "Spring 2026" -> "spring2026.html"
    return semester.lower().replace(" ", "") + ".html"


# ---------------------------------------------------------------------------
# Color palettes for auto-assignment
# ---------------------------------------------------------------------------
# Milestones on the same calendar day form one "priority group".
# Groups are colored in this order:
_PRIORITY_PALETTES: list[list[str]] = [
    # Group 1 — warm (reds / oranges)
    ["#FF1744", "#FF5722", "#FF9100", "#FFC400"],
    # Group 2 — cool (cyan / blue)
    ["#00E5FF", "#00B0FF", "#2979FF", "#651FFF"],
    # Group 3 — magenta
    ["#D500F9", "#E040FB", "#EA80FC", "#CE93D8"],
    # Group 4+ — fallback neutral
    ["#78909C", "#546E7A", "#455A64", "#37474F"],
]

# Deadline color (neutral grey/pink)
_DEADLINE_COLORS = ["#78909C", "#546E7A", "#E040FB"]


def _load_settings() -> dict[str, Any]:
    """Load and return the parsed settings.toml via the Config singleton."""
    try:
        from registrarmonitor.config import get_config

        return get_config()
    except Exception:
        # Fallback: read directly with tomllib (stdlib Python 3.11+)
        import tomllib

        settings_path = Path(__file__).parent.parent.parent.parent / "settings.toml"
        with open(settings_path, "rb") as f:
            return tomllib.load(f)


# Settings-dependent constants (must come after _load_settings)
BASE_URL: str = _get_base_url()
INDEXING: str = _get_indexing()


def _assign_deadline_colors(
    deadlines: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Assign neutral colors to deadline entries."""
    result = [dict(d) for d in deadlines]
    for i, d in enumerate(result):
        d["color"] = _DEADLINE_COLORS[min(i, len(_DEADLINE_COLORS) - 1)]
    return result


def get_milestones(semester: str) -> list[dict[str, str]]:
    """
    Load milestones + deadlines for a semester from settings.toml.

    Returns a list of dicts with keys: time, label, color.
    Colors are auto-assigned based on explicit priority groups in TOML.
    """
    cfg = _load_settings()
    from registrarmonitor.config import get_timezone

    registrar_timezone = get_timezone(cfg)
    sem_data = cfg.get("semesters", {}).get(semester, {})

    colored_milestones = []

    # Parse priorities and apply colors
    priorities = sem_data.get("priorities", {})
    for p_level in sorted(priorities.keys(), key=int):
        # p_level is "1", "2", etc.
        palette_idx = max(0, int(p_level) - 1)
        palette = _PRIORITY_PALETTES[min(palette_idx, len(_PRIORITY_PALETTES) - 1)]

        for i, m_data in enumerate(priorities[p_level]):
            # m_data is [time, label] (and optional color if we wanted, but we don't need it now)
            time_str = _serialize_registrar_time(m_data[0], registrar_timezone)
            label_str = m_data[1]

            # Skip hidden milestones (labels starting with '_')
            if label_str.startswith("_"):
                continue

            color = palette[min(i, len(palette) - 1)]

            colored_milestones.append(
                {"time": time_str, "label": label_str, "color": color}
            )

    # Parse deadlines and apply neutral colors
    raw_deadlines = [
        {
            "time": _serialize_registrar_time(d[0], registrar_timezone),
            "label": d[1],
        }
        for d in sem_data.get("deadlines", [])
    ]
    colored_deadlines = _assign_deadline_colors(raw_deadlines)

    return colored_milestones + colored_deadlines


def _serialize_registrar_time(time_str: str, timezone) -> str:
    """Serialize configured registrar times with an explicit UTC offset."""
    from datetime import datetime

    value = datetime.fromisoformat(time_str)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone)
    return value.isoformat()


# Build the MILESTONES_MAP for backward compatibility
# (consumed by website_service.py and data.py)
MILESTONES_MAP: dict[str, list[dict[str, str]]] = {
    sem: get_milestones(sem) for sem in ALL_SEMESTERS
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
    "averageHistory": "ah",
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
    # Event/changelog keys
    "events": "ev",
    "eventType": "et",
    "sectionCode": "sc",
    "oldValue": "ov",
    "newValue": "nv",
    "snapshotTimestamp": "st",
}
