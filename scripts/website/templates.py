"""Template loading and HTML assembly for website generation."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ALL_SEMESTERS, LATEST_SEMESTER, semester_to_filename

TEMPLATES_DIR = Path(__file__).parent / "templates"


def load_template(name: str) -> str:
    """Load a template file from the templates directory."""
    template_path = TEMPLATES_DIR / name
    return template_path.read_text()


def _minify_css(css: str) -> str:
    """Basic CSS minification: remove comments and excess whitespace."""
    # Remove comments
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Remove excess whitespace
    css = re.sub(r"\s+", " ", css)
    # Remove space around special chars
    css = re.sub(r"\s*([{}:;,>+~])\s*", r"\1", css)
    # Remove trailing semicolons before closing braces
    css = re.sub(r";}", "}", css)
    return css.strip()


def _indent_content(content: str, indent: str = "        ") -> str:
    """Indent each line of content with the specified prefix."""
    lines = content.split("\n")
    return "\n".join(indent + line if line.strip() else line for line in lines)


def _build_nav_html(current_semester: str) -> str:
    """Build semester navigation HTML."""
    nav_items = []
    for sem in ALL_SEMESTERS:
        filename = semester_to_filename(sem)
        active = " active" if sem == current_semester else ""
        aria_current = ' aria-current="page"' if sem == current_semester else ""
        nav_items.append(
            f'<a href="{filename}" class="semester-nav-link{active}"{aria_current}>{sem}</a>'
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
    Build HTML for a single semester page with navigation.

    Args:
        data: Semester data dictionary (with minified keys)
        milestones: List of milestone dictionaries
        semester: Display name of this semester
        minify_assets: Whether to minify CSS/JS

    Returns:
        Complete HTML string
    """
    # Load templates
    html_template = load_template("single.html")
    css = load_template("styles.css")
    js = load_template("app.js")

    if minify_assets:
        css = _minify_css(css)

    # Format data as JSON
    json_data = json.dumps(data, indent=None, separators=(",", ":"))
    milestones_json = json.dumps(milestones, indent=None, separators=(",", ":"))

    # Format last updated text
    last_report_time = data.get("lrt")  # Using minified key
    if last_report_time:
        dt = datetime.fromisoformat(last_report_time)
        last_updated = f"Last updated {dt.strftime('%Y-%m-%d %H:%M')}"
    else:
        last_updated = "Last updated N/A"

    # Build navigation
    nav_html = _build_nav_html(semester)

    # Replace placeholders
    html = html_template.replace("__TITLE__", f"Enrollment Monitor - {semester}")
    html = html.replace("__CSS__", _indent_content(css))
    html = html.replace("__DATA__", json_data)
    html = html.replace("__MILESTONES__", milestones_json)
    html = html.replace("__JS__", _indent_content(js))
    html = html.replace("__LAST_UPDATED__", last_updated)
    html = html.replace("__NAV__", nav_html)

    return html


def build_redirect_index() -> str:
    """
    Build index.html that redirects to the latest semester.

    Returns:
        HTML redirect page
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


# Keep legacy functions for backward compatibility
def build_single_html(
    data: dict[str, Any],
    milestones: list[dict[str, str]],
    *,
    minify_assets: bool = False,
) -> str:
    """Legacy: Build HTML for single semester mode without navigation."""
    semester = data.get("sem", "")
    return build_semester_page(data, milestones, semester, minify_assets=minify_assets)


def build_combined_html(
    combined_data: dict[str, Any],
    *,
    minify_assets: bool = False,
) -> str:
    """
    Build HTML for combined multi-semester mode (deprecated).

    Use build_semester_page for new multi-page architecture.
    """
    # Load templates
    html_template = load_template("combined.html")
    css = load_template("styles.css")
    js = load_template("app.js")

    if minify_assets:
        css = _minify_css(css)

    # Format data as JSON
    json_data = json.dumps(combined_data, indent=None, separators=(",", ":"))

    # Replace placeholders
    html = html_template.replace("__TITLE__", "Enrollment Monitor - All Semesters")
    html = html.replace("__CSS__", _indent_content(css))
    html = html.replace("__DATA__", json_data)
    html = html.replace("__JS__", _indent_content(js))

    return html
