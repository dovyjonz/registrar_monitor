"""Render plain enrollment reports as safe Telegram MarkdownV2 chunks."""

import re
from collections.abc import Callable

from telegram import MessageEntity
from telegram.helpers import escape_markdown

TELEGRAM_MESSAGE_LIMIT = 4000
CourseLink = Callable[[str], str | None]
# "+ CODE - COURSE ADDED", "CODE - 100%", or a bare "CODE".
HEADING_PATTERN = re.compile(r"^(?P<prefix>[+] )?(?P<code>.+?)(?P<rest> - .*)?$")


def _escape_text(value: str) -> str:
    return escape_markdown(value, version=2)


def _escape_pre(value: str) -> str:
    return escape_markdown(value, version=2, entity_type=MessageEntity.PRE)


def _render_heading(line: str, course_link: CourseLink | None) -> str:
    """Render a course heading, linking the course code when a URL is available."""
    match = HEADING_PATTERN.fullmatch(line)
    url = course_link(match.group("code")) if match and course_link else None
    if match is None or url is None:
        return f"*{_escape_text(line)}*"
    prefix = _escape_text(match.group("prefix") or "")
    rest = _escape_text(match.group("rest") or "")
    link_url = escape_markdown(url, version=2, entity_type=MessageEntity.TEXT_LINK)
    return f"*{prefix}[{_escape_text(match.group('code'))}]({link_url}){rest}*"


def _render_course_block(block: str, course_link: CourseLink | None = None) -> str:
    lines = block.splitlines()
    heading = _render_heading(lines[0], course_link)
    if len(lines) == 1:
        return heading
    rows = _escape_pre("\n".join(lines[1:]))
    return f"{heading}\n```\n{rows}\n```"


def render_report_chunks(
    content: str,
    *,
    max_length: int = TELEGRAM_MESSAGE_LIMIT,
    course_link: CourseLink | None = None,
) -> list[str]:
    """Return MarkdownV2 messages split only between complete course blocks.

    ``course_link`` maps a heading's course code to a URL, or None to leave the
    heading unlinked.
    """
    blocks = [block.strip("\n") for block in content.strip().split("\n\n") if block]
    if not blocks:
        return []

    rendered = [_escape_text(blocks[0])]
    rendered.extend(_render_course_block(block, course_link) for block in blocks[1:])

    chunks: list[str] = []
    current = rendered[0]
    for block in rendered[1:]:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= max_length:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(block) > max_length:
            raise ValueError("A single course report exceeds Telegram's message limit")
        current = block
    if current:
        chunks.append(current)
    return chunks
