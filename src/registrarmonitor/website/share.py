"""Resolve state-addressed share URLs from generated course routes."""

import re
from pathlib import Path

from .config import BASE_URL, OUTPUT_DIR, course_to_slug, semester_to_slug

_PREVIEW_HASH_PATTERN = re.compile(r'data-preview-hash="([A-Za-z0-9_-]{8})"')


def published_course_share_url(
    semester: str,
    course_code: str,
    *,
    output_dir: Path = OUTPUT_DIR,
) -> str | None:
    """Return the exact versioned URL published for a live course route."""
    semester_slug = semester_to_slug(semester)
    course_slug = course_to_slug(course_code)
    route = output_dir / "courses" / semester_slug / course_slug / "index.html"
    try:
        html = route.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _PREVIEW_HASH_PATTERN.search(html)
    if match is None or 'data-page-archived="true"' in html:
        return None
    return f"{BASE_URL}/courses/{semester_slug}/{course_slug}/?v={match.group(1)}"
