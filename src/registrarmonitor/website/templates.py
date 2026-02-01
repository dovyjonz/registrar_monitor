"""Template loading and HTML assembly for website generation."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from .config import ALL_SEMESTERS, LATEST_SEMESTER, semester_to_filename

TEMPLATES_DIR = Path(__file__).parent / "templates"
# Output is assets/website/public. Assets are in assets/website/public/assets.
# We need to find manifest relative to this file.
REPO_ROOT = Path(__file__).parent.parent.parent.parent
ASSETS_DIR = REPO_ROOT / "assets" / "website" / "public" / "assets"
MANIFEST_PATH = ASSETS_DIR / ".vite" / "manifest.json"

# Initialize Jinja2 environment
env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _get_asset_info(entry: str = "src/main.js") -> tuple[str | None, str | None]:
    """
    Get JS and CSS filenames from Vite manifest.

    Returns:
        Tuple of (js_filename, css_filename)
    """
    if not MANIFEST_PATH.exists():
        print(f"Warning: Manifest not found at {MANIFEST_PATH}")
        return None, None

    try:
        manifest = json.loads(MANIFEST_PATH.read_text())
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


def _build_nav_html(current_semester: str) -> str:
    """Build semester navigation HTML."""
    nav_items = []
    for sem in ALL_SEMESTERS:
        filename = semester_to_filename(sem)
        active = ' class="semester-nav-link active" aria-current="page"' if sem == current_semester else ' class="semester-nav-link"'
        nav_items.append(
            f'<a href="{filename}"{active}>{sem}</a>'
        )
    return "\n            ".join(nav_items)


def build_semester_page(
    data: dict[str, Any],
    milestones: list[dict[str, str]],
    semester: str,
    *,
    minify_assets: bool = False,
) -> str:
    """
    Build HTML for a single semester page using Jinja2 templates.
    """
    # Get asset filenames
    js_file, css_file = _get_asset_info()

    # Build navigation
    nav_html = _build_nav_html(semester)

    # Format last updated text
    last_report_time = data.get("lrt")
    if last_report_time:
        dt = datetime.fromisoformat(last_report_time)
        last_updated = f"Last updated {dt.strftime('%Y-%m-%d %H:%M')}"
    else:
        last_updated = "Last updated N/A"

    # Render template
    template = env.get_template("semester.html.jinja")
    return template.render(
        title=f"Enrollment Monitor - {semester}",
        nav_html=nav_html,
        last_updated=last_updated,
        data=data,
        milestones=milestones,
        js_file=js_file,
        css_file=css_file,
        asset_base_url="assets/"
    )


def build_redirect_index() -> str:
    """
    Build index.html that redirects to the latest semester.
    """
    latest_file = semester_to_filename(LATEST_SEMESTER)
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="0; url={latest_file}">
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='8' fill='%23ff9100'/></svg>">
    <title>Redirecting...</title>
</head>
<body>
    <p>Redirecting to <a href="{latest_file}">{LATEST_SEMESTER}</a>...</p>
</body>
</html>
'''
