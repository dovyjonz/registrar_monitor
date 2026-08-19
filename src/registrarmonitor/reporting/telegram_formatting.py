"""Render plain enrollment reports as safe Telegram MarkdownV2 chunks."""

from telegram import MessageEntity
from telegram.helpers import escape_markdown

TELEGRAM_MESSAGE_LIMIT = 4000


def _escape_text(value: str) -> str:
    return escape_markdown(value, version=2)


def _escape_pre(value: str) -> str:
    return escape_markdown(value, version=2, entity_type=MessageEntity.PRE)


def _render_course_block(block: str) -> str:
    lines = block.splitlines()
    heading = f"*{_escape_text(lines[0])}*"
    if len(lines) == 1:
        return heading
    rows = _escape_pre("\n".join(lines[1:]))
    return f"{heading}\n```\n{rows}\n```"


def render_report_chunks(
    content: str, *, max_length: int = TELEGRAM_MESSAGE_LIMIT
) -> list[str]:
    """Return MarkdownV2 messages split only between complete course blocks."""
    blocks = [block.strip("\n") for block in content.strip().split("\n\n") if block]
    if not blocks:
        return []

    rendered = [_escape_text(blocks[0])]
    rendered.extend(_render_course_block(block) for block in blocks[1:])

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
