"""Compact, untrusted Telegram deep-link and callback payloads."""

import base64
import json
import re

from .models import SubscriptionTarget

MAX_TARGET_PAYLOAD = 58
TARGET_PAYLOAD_VERSION = 1
IMPORT_COMMAND_PATTERN = re.compile(r"^/import(?:@[A-Za-z0-9_]+)?$")
SEMESTER_PATTERN = re.compile(r"^(Fall|Spring|Summer) \d{4}$")


def encode_target(target: SubscriptionTarget) -> str:
    """Encode a target using Telegram's deep-link-safe alphabet."""
    raw = json.dumps(
        [
            TARGET_PAYLOAD_VERSION,
            target.semester,
            target.course_code,
            target.section_code,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()
    payload = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    if len(payload) > MAX_TARGET_PAYLOAD:
        raise ValueError("Subscription target is too long for a Telegram link")
    return payload


def decode_target(payload: str) -> SubscriptionTarget:
    """Decode and structurally validate an untrusted target payload."""
    if (
        not payload
        or len(payload) > MAX_TARGET_PAYLOAD
        or re.fullmatch(r"[A-Za-z0-9_-]+", payload) is None
    ):
        raise ValueError("Invalid subscription link")
    try:
        padding = "=" * (-len(payload) % 4)
        values = json.loads(base64.urlsafe_b64decode(payload + padding))
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Invalid subscription link") from error
    if (
        not isinstance(values, list)
        or len(values) != 4
        or values[0] != TARGET_PAYLOAD_VERSION
        or not all(isinstance(value, str) for value in values[1:])
        or not values[1]
        or not values[2]
    ):
        raise ValueError("Invalid subscription link")
    return SubscriptionTarget(*values[1:])


def encode_course_import(semester: str, course_codes: list[str]) -> str:
    """Render a portable Telegram command containing website course bookmarks."""
    normalized = sorted(
        dict.fromkeys(code.strip() for code in course_codes if code.strip())
    )
    if SEMESTER_PATTERN.fullmatch(semester) is None or not normalized:
        raise ValueError("Invalid course import")
    if any("\n" in code or len(code) > 80 for code in normalized):
        raise ValueError("Invalid imported courses")
    return "\n".join(["/import", semester, *normalized])


def decode_course_import(value: str) -> tuple[str, list[str]]:
    """Parse a portable website selection pasted into the bot."""
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if (
        len(lines) < 3
        or IMPORT_COMMAND_PATTERN.fullmatch(lines[0]) is None
        or SEMESTER_PATTERN.fullmatch(lines[1]) is None
        or any(len(code) > 80 for code in lines[2:])
    ):
        raise ValueError("Invalid course import")
    return lines[1], list(dict.fromkeys(lines[2:]))
