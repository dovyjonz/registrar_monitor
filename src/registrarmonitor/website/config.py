"""Configuration helpers for website generation."""

import re
from pathlib import Path
from typing import Any

from registrarmonitor.registration import get_priority_milestones


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


def _get_preview_base_url() -> str:
    """Get the separately deployed Worker origin for social preview images."""
    website = _load_settings().get("website", {})
    base = website.get("preview_base_url", "").strip().rstrip("/")
    if not base:
        raise ValueError("website.preview_base_url must be configured")
    return base


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


_SEMESTER_PATTERN = re.compile(r"^(Fall|Spring|Summer) (\d{4})$")


def semester_sort_key(semester: str) -> tuple[int, int]:
    """Return the Fall-Spring-Summer academic-year position."""
    match = _SEMESTER_PATTERN.fullmatch(semester.strip())
    if match is None:
        raise ValueError(f"unrecognized semester label: {semester!r}")
    term, year_text = match.groups()
    year = int(year_text)
    if term == "Fall":
        return year, 0
    return year - 1, 1 if term == "Spring" else 2


def get_configured_semesters(*, newest_first: bool = True) -> list[str]:
    """Return canonical semesters with registrar sources from settings."""
    semesters = _load_settings().get("semesters", {})
    configured = [
        label
        for label, value in semesters.items()
        if isinstance(value, dict)
        and isinstance(value.get("registrar_url"), str)
        and value["registrar_url"].strip()
        and _SEMESTER_PATTERN.fullmatch(label)
    ]
    return sorted(configured, key=semester_sort_key, reverse=newest_first)


def semester_key_map() -> dict[str, str]:
    """Return CLI keys derived from configured canonical semester labels."""
    return {
        semester.replace(" ", "").lower(): semester
        for semester in get_configured_semesters()
    }


def get_registrar_url(semester: str) -> str:
    """Return the configured registrar source for one canonical semester."""
    value = _load_settings().get("semesters", {}).get(semester, {})
    url = value.get("registrar_url") if isinstance(value, dict) else None
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"no registrar source configured for {semester!r}")
    return url.strip()


def latest_configured_semester() -> str:
    """Return the newest canonical semester that has a registrar source."""
    semesters = get_configured_semesters()
    if not semesters:
        raise ValueError("no semesters with registrar sources are configured")
    return semesters[0]


# Default output directory
OUTPUT_DIR = (
    Path(__file__).parent.parent.parent.parent / "assets" / "website" / "public"
)


# ---------------------------------------------------------------------------
# Color palettes for auto-assignment
# ---------------------------------------------------------------------------
# Registration milestones are grouped by their explicit priority in settings.toml.
# Priority waves are colored in this order:
_PRIORITY_PALETTES: list[list[str]] = [
    # Group 1: warm (reds / oranges)
    ["#FF1744", "#FF5722", "#FF9100", "#FFC400"],
    # Group 2: cool (cyan / blue)
    ["#00E5FF", "#00B0FF", "#2979FF", "#651FFF"],
    # Group 3: magenta
    ["#D500F9", "#E040FB", "#EA80FC", "#CE93D8"],
    # Group 4+: fallback neutral
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
PREVIEW_BASE_URL: str = _get_preview_base_url()


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

    Returns a list of dicts with keys: time, label, color, and optional priority.
    Colors are auto-assigned based on explicit priority groups in TOML.
    """
    cfg = _load_settings()
    from registrarmonitor.config import get_timezone

    registrar_timezone = get_timezone(cfg)
    sem_data = cfg.get("semesters", {}).get(semester, {})

    colored_milestones = []

    # Apply presentation colors to shared registration milestones.
    priority_indices: dict[str, int] = {}
    for milestone in get_priority_milestones(semester):
        priority = milestone["priority"]
        palette_idx = max(0, int(priority) - 1)
        palette = _PRIORITY_PALETTES[min(palette_idx, len(_PRIORITY_PALETTES) - 1)]
        label = milestone["label"]
        if label.startswith("_"):
            continue
        index = priority_indices.get(priority, 0)
        priority_indices[priority] = index + 1
        colored_milestones.append(
            {
                **milestone,
                "color": palette[min(index, len(palette) - 1)],
            }
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
