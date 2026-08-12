from registrarmonitor.website.preview import (
    build_course_preview_state,
    build_semester_preview_state,
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


def test_unrelated_department_timestamp_does_not_change_course_hash():
    course = {
        "code": "ANT 140",
        "title": "Introduction",
        "sections": {},
        "averageHistory": [{"timestampIdx": 0, "fill": 0.5}],
        "sectionHistory": {},
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
        timestamps=[
            "2026-08-05T09:00:00+05:00",
            "2026-08-05T10:00:00+05:00",
        ],
        milestones=[],
    )

    assert second["timestamps"] == ["2026-08-05T09:00:00+05:00"]
    assert first["hash"] == second["hash"]


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

    assert first["priority"]["label"] == "PRIORITY 1"
    assert second["priority"]["label"] == "PRIORITY 2"
    assert first["hash"] != second["hash"]


def test_priority_state_accepts_naive_milestones_with_aware_observation():
    course = {
        "code": "ANT 140",
        "sections": {},
        "averageHistory": [],
        "sectionHistory": {},
        "events": [],
    }

    state = build_course_preview_state(
        semester="Fall 2026",
        course=course,
        timestamps=[],
        milestones=[{"time": "2026-08-05T09:00:00", "priority": "1", "label": "Y4+"}],
        published_at="2026-08-05T10:00:00+05:00",
    )

    assert state["priority"]["label"] == "PRIORITY 1"


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
