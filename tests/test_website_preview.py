import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from registrarmonitor.website.preview import (
    PREVIEW_STATE_BUDGET_BYTES,
    _registrar_timestamp,
    build_course_preview_state,
    build_semester_preview_state,
    canonical_bytes,
)


def test_noop_poll_timestamp_does_not_change_course_hash():
    course = {
        "code": "ANT 140",
        "title": "Introduction",
        "sections": {
            "1": {
                "type": "Lecture",
                "currentEnrollment": 10,
                "currentCapacity": 20,
            }
        },
        "averageHistory": [{"timestampIdx": 0, "fill": 0.5}],
        "sectionHistory": {
            "1": [{"timestampIdx": 0, "enrollment": 10, "capacity": 20}]
        },
        "events": [],
    }

    first = build_course_preview_state(
        semester="Fall 2026",
        course=course,
        timestamps=["2026-08-05T09:00:00+05:00"],
        milestones=[],
    )
    second = build_course_preview_state(
        semester="Fall 2026",
        course=course,
        timestamps=["2026-08-05T09:00:00+05:00"],
        milestones=[],
    )

    assert first["hash"] == second["hash"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{8}", first["hash"])


def test_naive_course_observations_are_published_in_the_registrar_timezone():
    state = build_course_preview_state(
        semester="Fall 2026",
        course={
            "code": "ANT 140",
            "sections": {},
            "averageHistory": [{"timestampIdx": 0, "fill": 0.5}],
            "sectionHistory": {},
            "events": [],
        },
        timestamps=["2026-08-13 10:55:00"],
        milestones=[
            {"time": "2026-08-13T11:00:00+05:00", "label": "Y3", "priority": "2"}
        ],
    )

    assert state["timestamps"] == ["2026-08-13T10:55:00+05:00"]
    assert state["lastChanged"] == "2026-08-13T10:55:00+05:00"


def test_priority_transition_changes_course_hash_without_course_change():
    course = {
        "code": "ANT 140",
        "title": "Introduction",
        "sections": {},
        "averageHistory": [],
        "sectionHistory": {},
        "events": [],
    }
    milestones = [
        {"time": "2026-08-05T09:00:00+05:00", "priority": "1", "label": "Y4+"},
        {"time": "2026-08-05T13:00:00+05:00", "priority": "2", "label": "Y3"},
    ]

    first = build_course_preview_state(
        semester="Fall 2026",
        course=course,
        timestamps=[],
        milestones=milestones,
        published_at="2026-08-05T10:00:00+05:00",
    )
    second = build_course_preview_state(
        semester="Fall 2026",
        course=course,
        timestamps=[],
        milestones=milestones,
        published_at="2026-08-05T14:00:00+05:00",
    )

    assert first["priority"]["compact"] == "P1 · Y4+"
    assert first["priority"]["current"]["label"] == "Y4+"
    assert second["priority"]["compact"] == "P2 · Y3"
    assert second["priority"]["current"]["label"] == "Y3"
    assert first["hash"] != second["hash"]


def test_priority_state_is_cumulative_and_uses_deadline_as_next_item():
    state = build_course_preview_state(
        semester="Fall 2026",
        course={
            "code": "ANT 140",
            "sections": {},
            "averageHistory": [],
            "sectionHistory": {},
            "events": [],
        },
        timestamps=[],
        milestones=[
            {"time": "2026-08-05T09:00:00+05:00", "priority": "1", "label": "Y4+"},
            {"time": "2026-08-05T13:00:00+05:00", "priority": "2", "label": "Y3"},
            {"time": "2026-08-06T09:00:00+05:00", "label": "Drop"},
        ],
        published_at="2026-08-05T14:00:00+05:00",
    )

    assert state["priority"]["eligible"] == ["Y4+", "Y3"]
    assert state["priority"]["next"]["label"] == "Drop"
    assert state["priority"]["next"].get("priority") is None


def test_course_state_carries_existing_milestones_into_the_content_hash():
    course = {
        "code": "ANT 140",
        "sections": {},
        "averageHistory": [],
        "sectionHistory": {},
        "events": [],
    }
    first = build_course_preview_state(
        semester="Summer 2026",
        course=course,
        timestamps=[],
        milestones=[
            {
                "time": "2026-05-12T10:00:00+05:00",
                "label": "Y4+",
                "color": "#FF1744",
                "priority": "1",
            }
        ],
        published_at="2026-05-13T10:00:00+05:00",
    )
    second = build_course_preview_state(
        semester="Summer 2026",
        course=course,
        timestamps=[],
        milestones=[],
        published_at="2026-05-13T10:00:00+05:00",
    )

    assert first["milestones"][0]["label"] == "Y4+"
    assert first["hash"] != second["hash"]


def test_semester_state_sums_raw_open_seats_and_can_be_archived():
    summary = {
        "semester": "Spring 2026",
        "currentSnapshot": {"observedAt": "2026-01-05T09:00:00+05:00"},
        "courses": {
            "ANT 140": {"sectionCount": 2, "fullSectionCount": 1},
        },
    }
    departments = {
        "ANT": {
            "courses": {
                "ANT 140": {
                    "sections": {
                        "1": {
                            "type": "Lecture",
                            "currentEnrollment": 10,
                            "currentCapacity": 20,
                        },
                        "2": {
                            "type": "Lab",
                            "currentEnrollment": 12,
                            "currentCapacity": 15,
                        },
                    }
                }
            }
        }
    }

    state = build_semester_preview_state(
        summary=summary,
        departments=departments,
        milestones=[],
        archived=True,
    )

    assert state["openSeats"] == 13
    assert state["status"] == "archived"
    assert state["archived"] is True
    assert "footer" not in state


def test_course_state_keeps_only_referenced_timestamps_without_touching_input():
    course = {
        "code": "ANT 140",
        "sections": {},
        "averageHistory": [{"timestampIdx": 1, "fill": 0.5}],
        "sectionHistory": {"1": [{"timestampIdx": 3, "enrollment": 10}]},
        "events": [{"timestampIdx": 3, "eventType": "change"}],
    }
    timestamps = [
        "2026-08-01T09:00:00+05:00",
        "2026-08-02T09:00:00+05:00",
        "2026-08-03T09:00:00+05:00",
        "2026-08-04T09:00:00+05:00",
    ]

    state = build_course_preview_state(
        semester="Fall 2026", course=course, timestamps=timestamps, milestones=[]
    )

    assert state["timestamps"] == [timestamps[1], timestamps[3]]
    assert state["course"]["averageHistory"][0]["timestampIdx"] == 0
    assert state["course"]["sectionHistory"]["1"][0]["timestampIdx"] == 1
    assert state["course"]["events"][0]["timestampIdx"] == 1
    assert course["averageHistory"][0]["timestampIdx"] == 1
    assert course["events"][0]["timestampIdx"] == 3


def _oversized_course(*, sections: int = 24, points: int = 900) -> tuple[dict, list]:
    start = datetime(2026, 8, 1, tzinfo=timezone(timedelta(hours=5)))
    timestamps = [
        (start + timedelta(minutes=15 * i)).isoformat() for i in range(points)
    ]
    codes = [f"{n:02d}" for n in range(1, sections + 1)]
    course = {
        "code": "BIG 101",
        "title": "Large",
        "department": "BIG",
        "sections": {
            code: {
                "type": "Lecture",
                "instructor": "Staff",
                "currentEnrollment": 10,
                "currentCapacity": 20,
            }
            for code in codes
        },
        "averageHistory": [
            {"timestampIdx": i, "fill": round(0.1 + i / (points * 2), 4)}
            for i in range(points)
        ],
        "sectionHistory": {
            code: [
                {
                    "timestampIdx": i,
                    "fill": round((i + n) / (points + sections), 4),
                    "enrollment": (i + n) % 40,
                    # One capacity change halfway through.
                    "capacity": 20 if i < points // 2 else 25,
                }
                for i in range(points)
            ]
            for n, code in enumerate(codes)
        },
        "events": [
            {
                "eventType": kind,
                "sectionCode": codes[i % sections],
                "oldValue": "20",
                "newValue": "25",
                "timestampIdx": i,
            }
            for i in range(points)
            for kind in ("capacity_changed",)
        ]
        + [
            {"eventType": "section_added", "sectionCode": codes[0], "timestampIdx": 0},
            {
                "eventType": "section_removed",
                "sectionCode": codes[0],
                "timestampIdx": points - 1,
            },
        ],
    }
    return course, timestamps


def _unreduced(course, timestamps):
    return {"course": course, "timestamps": timestamps}


def _build_oversized():
    course, timestamps = _oversized_course()
    state = build_course_preview_state(
        semester="Fall 2026", course=course, timestamps=timestamps, milestones=[]
    )
    return course, timestamps, state


def _validate_like_worker(state: dict, *, route_hash: str, slug: str) -> None:
    """Mirror workers/site/src/model.ts validateState plus the fields card.ts reads."""
    assert state["schemaVersion"] == 1
    assert state["kind"] == "course"
    assert state["hash"] == route_hash
    assert state["slug"] == slug
    assert state["semesterSlug"] == "fall-2026"
    assert isinstance(state["semester"], str)
    assert re.fullmatch(r"[A-Za-z0-9_-]{8}", state["hash"])
    course = state["course"]
    count = len(state["timestamps"])
    for points in [course["averageHistory"], *course["sectionHistory"].values()]:
        for point in points:
            assert 0 <= point["timestampIdx"] < count
    for points in course["sectionHistory"].values():
        for point in points:
            assert isinstance(point["enrollment"], int)
            assert isinstance(point["capacity"], int)
    for event in course["events"]:
        assert 0 <= event["timestampIdx"] < count
    # The dashboard fallback validates every section and both history containers.
    assert course["sections"]
    assert isinstance(course["events"], list)


def test_oversized_course_state_is_reduced_below_the_worker_cap():
    course, timestamps, state = _build_oversized()
    assert len(canonical_bytes(_unreduced(course, timestamps))) > (
        PREVIEW_STATE_BUDGET_BYTES
    )  # the fixture is genuinely oversized

    payload = canonical_bytes(state)
    assert len(payload) <= PREVIEW_STATE_BUDGET_BYTES
    assert PREVIEW_STATE_BUDGET_BYTES < _worker_max_state_bytes()
    _validate_like_worker(state, route_hash=state["hash"], slug="big-101")


def test_reduction_keeps_ends_capacity_changes_and_section_lifecycle():
    course, timestamps, state = _build_oversized()
    reduced = state["course"]
    stamps = state["timestamps"]
    assert set(reduced["sections"]) == set(course["sections"])
    for code, original in course["sectionHistory"].items():
        kept = reduced["sectionHistory"][code]
        if not kept:
            continue
        indexes = [point["timestampIdx"] for point in kept]
        assert indexes == sorted(set(indexes))
        assert stamps[indexes[0]] == _registrar_timestamp(
            timestamps[original[0]["timestampIdx"]]
        )
        assert stamps[indexes[-1]] == _registrar_timestamp(
            timestamps[original[-1]["timestampIdx"]]
        )
        capacities = {point["capacity"] for point in kept}
        assert capacities == {20, 25}
    kinds = {event["eventType"] for event in reduced["events"]}
    assert {"section_added", "section_removed"} <= kinds


def test_reduction_is_deterministic_and_stable_when_rebuilt():
    course, timestamps, state = _build_oversized()
    again = build_course_preview_state(
        semester="Fall 2026", course=course, timestamps=timestamps, milestones=[]
    )
    assert again == state
    # Archived removals are rebuilt from a previously published state.
    rebuilt = build_course_preview_state(
        semester="Fall 2026",
        course=state["course"],
        timestamps=state["timestamps"],
        milestones=[],
        removed=True,
    )
    assert rebuilt["course"] == state["course"]
    assert rebuilt["lastChanged"] == state["lastChanged"]
    assert len(canonical_bytes(rebuilt)) <= PREVIEW_STATE_BUDGET_BYTES


def test_course_within_budget_is_published_unreduced():
    course, timestamps = _oversized_course(sections=2, points=20)
    state = build_course_preview_state(
        semester="Fall 2026", course=course, timestamps=timestamps, milestones=[]
    )
    assert state["course"]["sectionHistory"] == {
        code: [{**p, "timestampIdx": p["timestampIdx"]} for p in points]
        for code, points in course["sectionHistory"].items()
    }
    assert state["course"]["events"] == course["events"]


def test_course_that_cannot_fit_even_without_history_is_rejected():
    course, timestamps = _oversized_course(sections=2, points=4)
    course["title"] = "x" * PREVIEW_STATE_BUDGET_BYTES
    with pytest.raises(ValueError, match="budget"):
        build_course_preview_state(
            semester="Fall 2026", course=course, timestamps=timestamps, milestones=[]
        )


def _worker_max_state_bytes() -> int:
    source = (
        Path(__file__).resolve().parents[1] / "workers/site/src/handler.ts"
    ).read_text(encoding="utf-8")
    match = re.search(r"MAX_STATE_BYTES = ([\d_]+);", source)
    assert match, "handler.ts no longer declares MAX_STATE_BYTES"
    return int(match.group(1).replace("_", ""))
