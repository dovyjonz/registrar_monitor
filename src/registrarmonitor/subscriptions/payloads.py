"""Compact, untrusted Telegram deep-link and callback payloads."""

import base64
import json
import re
from dataclasses import dataclass

from .models import SubscriptionTarget

MAX_TARGET_PAYLOAD = 58
MAX_START_PAYLOAD = 64  # Telegram's limit for /start deep-link payloads
WATCH_PREFIX = "watch"
SEASON_KEYS = {"Fall": "fa", "Spring": "sp", "Summer": "su"}
WATCH_PAYLOAD_PATTERN = re.compile(
    r"^watch_(?P<semester>(?:fa|sp|su)\d{2})_(?P<course>[A-Z0-9]+)"
    r"(?:_(?P<section>[A-Z0-9]+))?$"
)
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


def compact_code(value: str) -> str:
    """Reduce a course or section code to uppercase ASCII letters and digits."""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def semester_key(semester: str) -> str:
    """Return the short deep-link key for a semester name, e.g. Fall 2026 -> fa26."""
    match = re.fullmatch(r"(Fall|Spring|Summer) (\d{4})", semester)
    if match is None:
        raise ValueError("Unsupported semester for a Telegram link")
    return f"{SEASON_KEYS[match.group(1)]}{match.group(2)[2:]}"


def encode_watch_payload(target: SubscriptionTarget) -> str:
    """Encode a readable ``watch_<semester>_<course>[_<section>]`` start payload.

    Codes are compacted to ``[A-Z0-9]`` so the payload stays inside Telegram's
    alphabet. The mapping back to a real course is resolved against the latest
    snapshot, so it is lossy by design. Raises ``ValueError`` when the target
    cannot be represented, or the payload would exceed Telegram's length limit.
    """
    parts = [
        WATCH_PREFIX,
        semester_key(target.semester),
        compact_code(target.course_code),
    ]
    if target.section_code:
        parts.append(compact_code(target.section_code))
    if not all(parts):
        raise ValueError("Subscription target cannot be encoded as a Telegram link")
    payload = "_".join(parts)
    if len(payload) > MAX_START_PAYLOAD:
        raise ValueError("Subscription target is too long for a Telegram link")
    return payload


@dataclass(frozen=True)
class WatchLink:
    """A parsed, still-unresolved watch deep link."""

    semester_key: str
    course: str
    section: str = ""


def decode_watch_payload(payload: str) -> WatchLink:
    """Structurally validate an untrusted ``/start`` payload."""
    match = (
        WATCH_PAYLOAD_PATTERN.fullmatch(payload)
        if len(payload) <= MAX_START_PAYLOAD
        else None
    )
    if match is None:
        raise ValueError("Invalid subscription link")
    return WatchLink(
        match.group("semester"), match.group("course"), match.group("section") or ""
    )


def watch_deep_link(bot_username: str, target: SubscriptionTarget) -> str:
    """Return the t.me link that opens the bot's watch confirmation."""
    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", bot_username) is None:
        raise ValueError("Invalid bot username")
    return f"https://t.me/{bot_username}?start={encode_watch_payload(target)}"


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
