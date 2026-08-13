"""Template loading and HTML assembly for website generation."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

from .config import (
    BASE_URL,
    PREVIEW_BASE_URL,
    get_configured_semesters,
    semester_to_slug,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
# Output is assets/website/public. Assets are in assets/website/public/assets.
# We need to find manifest relative to this file.
REPO_ROOT = Path(__file__).parent.parent.parent.parent
ASSETS_DIR = REPO_ROOT / "assets" / "website" / "public" / "assets"
MANIFEST_PATH = ASSETS_DIR / ".vite" / "manifest.json"
ASTANA_TIMEZONE = ZoneInfo("Asia/Almaty")

# Initialize Jinja2 environment
env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _format_astana_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    timestamp = datetime.fromisoformat(value.replace(" ", "T"))
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(ASTANA_TIMEZONE)
    return f"{timestamp.day} {timestamp.strftime('%b')}, {timestamp.strftime('%H:%M')} Astana time"


def _get_asset_info(
    entry: str = "src/main.js", manifest_path: Path | None = None
) -> tuple[str | None, str | None]:
    """
    Get JS and CSS filenames from Vite manifest.

    Returns:
        Tuple of (js_filename, css_filename)
    """
    manifest_path = manifest_path or MANIFEST_PATH
    if not manifest_path.exists():
        print(f"Warning: Manifest not found at {manifest_path}")
        return None, None

    try:
        manifest = json.loads(manifest_path.read_text())
        info = manifest.get(entry)
        if not info:
            return None, None

        js_file = info.get("file")
        css_files = info.get("css", [])
        css_file = css_files[0] if css_files else None

        return js_file, css_file
    except Exception as e:
        print(f"Error reading manifest: {e}")
        return None, None


def _build_nav_html(current_semester: str, semesters: list[str] | None = None) -> str:
    """Build semester navigation HTML."""
    nav_items = []
    for sem in semesters if semesters is not None else get_configured_semesters():
        url = f"/semesters/{semester_to_slug(sem)}/"
        active = (
            ' class="semester-nav-link active" aria-current="page"'
            if sem == current_semester
            else ' class="semester-nav-link"'
        )
        nav_items.append(f'<a href="{url}"{active}>{sem}</a>')
    return "\n            ".join(nav_items)


def build_semester_page(
    data: dict[str, Any],
    milestones: list[dict[str, str]],
    semester: str,
    *,
    minify_assets: bool = False,
    manifest_path: Path | None = None,
    semesters: list[str] | None = None,
    preview_state: dict[str, Any] | None = None,
    course_state: dict[str, Any] | None = None,
) -> str:
    """
    Build HTML for a single semester page using Jinja2 templates.
    """
    # Get asset filenames
    js_file, css_file = _get_asset_info(manifest_path=manifest_path)

    # Build navigation
    nav_html = _build_nav_html(semester, semesters)

    # Format last updated text
    last_report_time = data.get("lrt")
    if last_report_time:
        dt = datetime.fromisoformat(last_report_time)
        last_updated = f"Last updated {dt.strftime('%Y-%m-%d %H:%M')}"
    else:
        last_updated = "Last updated N/A"

    # The deployed frontend starts from the stable semester manifest pointer.
    manifest_filename = f"/data/{semester_to_slug(semester)}/manifest.json"

    sem_slug = semester_to_slug(semester)
    canonical_url = f"{BASE_URL}/semesters/{sem_slug}/"
    if course_state:
        course_slug = course_state["slug"]
        canonical_url = f"{BASE_URL}/courses/{sem_slug}/{course_slug}/"
        archived = bool(course_state.get("archived"))
        og_url = (
            canonical_url if archived else f"{canonical_url}?v={course_state['hash']}"
        )
        title = f"{course_state['code']}: {semester} Enrollment Monitor"
        course_title = course_state.get("title", "")
        availability = course_state["availability"]["sentence"]
        priority_state = course_state.get("priority") or {}
        current = priority_state.get("current") or {}
        next_milestone = priority_state.get("next") or {}
        current_copy = (
            f"Open now: Priority {current.get('priority')}, {current['label']}"
            if current.get("priority") and current.get("label")
            else None
        )
        next_time = _format_astana_timestamp(next_milestone.get("time"))
        next_prefix = (
            f"Priority {next_milestone['priority']}, "
            if next_milestone.get("priority")
            else ""
        )
        next_copy = (
            f"Next: {next_prefix}{next_milestone['label']} on {next_time}"
            if next_milestone.get("label") and next_time
            else None
        )
        description_parts = [
            value.rstrip(".")
            for value in (course_title, availability, current_copy, next_copy, semester)
            if value
        ]
        description = ". ".join(description_parts) + "."
        image_url = (
            f"{PREVIEW_BASE_URL}/preview/course/{sem_slug}/{course_slug}/"
            f"{course_state['hash']}.png"
        )
        image_alt = f"{course_state['code']} enrollment preview for {semester}"
        state = course_state
    else:
        state = preview_state or {}
        title = f"{semester}: Enrollment Monitor"
        if state.get("archived"):
            description = (
                f"Registration closed. Historical data for {state.get('courseCount', 0)} "
                f"courses and {state.get('sectionCount', 0)} sections. "
                f"{state.get('fullSectionCount', 0)} sections were full at the final update."
            )
        else:
            updated = _format_astana_timestamp(state.get("updated"))
            description = (
                f"{state.get('courseCount', len(data.get('cr', {})))} courses, "
                f"{state.get('sectionCount', 0)} sections, "
                f"{state.get('fullSectionCount', 0)} full sections; "
                f"{state.get('openSeats', 0)} seats open"
                f"{', updated ' + updated if updated else ''}."
            )
        og_url = (
            canonical_url
            if state.get("archived")
            else f"{canonical_url}?v={state['hash']}"
            if state.get("hash")
            else canonical_url
        )
        image_url = (
            f"{PREVIEW_BASE_URL}/preview/semester/{sem_slug}/{state['hash']}.png"
            if state.get("hash")
            else f"{BASE_URL}/previews/root.png"
        )
        image_alt = f"Enrollment Monitor overview for {semester}"

    # Render template
    template = env.get_template("semester.html.jinja")
    return template.render(
        page_title=title,
        page_description=description,
        nav_html=nav_html,
        last_updated=last_updated,
        manifest_filename=manifest_filename,
        js_file=js_file,
        css_file=css_file,
        asset_base_url="/assets/",
        canonical_url=canonical_url,
        og_url=og_url,
        image_url=image_url,
        image_alt=image_alt,
        initial_course=course_state.get("code") if course_state else None,
        preview_state_url=(
            f"/data/previews/course/{course_state['hash']}.json"
            if course_state
            else None
        ),
        preview_hash=(course_state.get("hash") if course_state else state.get("hash")),
        archived=bool(
            course_state.get("archived") if course_state else state.get("archived")
        ),
    )


def build_prototype_page(
    *,
    semester: str,
    index_json: str = "prototype-data/index.json",
) -> str:
    """Build the local-only dashboard prototype shell."""
    js_file, css_file = _get_asset_info("src/prototype.js")
    template = env.get_template("prototype.html.jinja")
    return template.render(
        title=f"Enrollment Monitor Prototype - {semester}",
        semester=semester,
        index_json=index_json,
        js_file=js_file,
        css_file=css_file,
        asset_base_url="assets/",
        canonical_url=None,
        indexing="noindex",
    )


def build_redirect_index(latest_semester: str) -> str:
    """
    Build index.html that redirects to the latest semester.
    """
    latest_url = f"/semesters/{semester_to_slug(latest_semester)}/"
    canonical_url = f"{BASE_URL}/"
    description = "See historical and frequently updated undergraduate course data."
    image_url = f"{BASE_URL}/previews/root.png"
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="robots" content="noindex, nofollow">
    <meta http-equiv="refresh" content="0; url={latest_url}">
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='8' fill='%23ff9100'/></svg>">
    <title>Enrollment Monitor</title>
    <meta name="description" content="{description}">
    <link rel="canonical" href="{canonical_url}">
    <meta property="og:type" content="website">
    <meta property="og:title" content="Enrollment Monitor">
    <meta property="og:description" content="{description}">
    <meta property="og:url" content="{canonical_url}">
    <meta property="og:image" content="{image_url}">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta property="og:image:alt" content="Enrollment Monitor">
    <meta name="twitter:card" content="summary_large_image">
</head>
<body>
    <p>Redirecting to <a href="{latest_url}">{latest_semester}</a>...</p>
</body>
</html>
'''
