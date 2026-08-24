"""Compact, untrusted Telegram deep-link and callback payloads."""

import base64
import json
import re

from .models import SubscriptionTarget

MAX_TARGET_PAYLOAD = 58
TARGET_PAYLOAD_VERSION = 1


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
