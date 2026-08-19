"""Shared public registration-priority semantics."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _expand_cohort(label: str) -> str:
    if label == "ALL":
        return "All students"
    if label.startswith("Y") and len(label) > 1:
        return f"Year {label[1:]}"
    return label


def format_priority_compact(priority: object, label: object) -> str:
    """Return the sole compact priority form used on public surfaces."""
    return f"P{priority} · {label}"


def format_priority_full(priority: object, label: object) -> str:
    """Return the sole expanded, accessible priority form."""
    return f"Priority {priority} — {_expand_cohort(str(label))}"


def get_priority_milestones(semester: str) -> list[dict[str, str]]:
    """Load configured registration gates without presentation metadata."""
    from .config import get_config, get_timezone

    config = get_config()
    timezone = get_timezone(config)
    semester_config = config.get("semesters", {}).get(semester, {})
    priorities = semester_config.get("priorities", {})
    milestones = []
    for priority in sorted(priorities, key=int):
        for time_text, label in priorities[priority]:
            value = datetime.fromisoformat(time_text)
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone)
            milestones.append(
                {
                    "time": value.isoformat(),
                    "label": label,
                    "priority": str(priority),
                }
            )
    return milestones


def derive_priority_state(
    milestones: list[dict[str, str]], *, at: str | None
) -> dict[str, Any] | None:
    """Derive the latest visible registration gate and cumulative eligibility."""
    if not at:
        return None
    now = datetime.fromisoformat(at)

    def milestone_time(item: dict[str, str]) -> datetime:
        value = datetime.fromisoformat(item["time"])
        if value.tzinfo is None and now.tzinfo is not None:
            return value.replace(tzinfo=now.tzinfo)
        if value.tzinfo is not None and now.tzinfo is None:
            return value.replace(tzinfo=None)
        return value

    visible = [
        item
        for item in milestones
        if item.get("label") and not item["label"].startswith("_")
    ]
    ordered = sorted(visible, key=milestone_time)
    priority_gates = [item for item in ordered if item.get("priority")]
    reached = [item for item in priority_gates if milestone_time(item) <= now]
    upcoming = [item for item in priority_gates if milestone_time(item) > now]
    if not reached and not upcoming:
        return None

    current = reached[-1] if reached else None

    def public_gate(item: dict[str, str] | None) -> dict[str, str] | None:
        if item is None:
            return None
        gate = {key: item[key] for key in ("label", "time", "priority") if key in item}
        if item.get("priority"):
            gate["compact"] = format_priority_compact(item["priority"], item["label"])
            gate["full"] = format_priority_full(item["priority"], item["label"])
        return gate

    next_item = next((item for item in ordered if milestone_time(item) > now), None)
    return {
        "compact": (
            format_priority_compact(current["priority"], current["label"])
            if current
            else None
        ),
        "full": (
            format_priority_full(current["priority"], current["label"])
            if current
            else None
        ),
        "current": public_gate(current),
        "eligible": list(dict.fromkeys(item["label"] for item in reached)),
        "next": public_gate(next_item),
    }
