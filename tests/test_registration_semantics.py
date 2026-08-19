from registrarmonitor.registration import (
    derive_priority_state,
    format_priority_compact,
    format_priority_full,
)


def test_priority_vocabulary_has_one_compact_and_full_form():
    assert format_priority_compact("1", "Y4+") == "P1 · Y4+"
    assert format_priority_full("1", "Y4+") == "Priority 1 — Year 4+"
    assert format_priority_full("3", "ALL") == "Priority 3 — All students"


def test_priority_state_hides_internal_gates_and_keeps_latest_gate_primary():
    state = derive_priority_state(
        [
            {"time": "2026-08-05T09:00:00+05:00", "priority": "1", "label": "Y4+"},
            {"time": "2026-08-05T10:00:00+05:00", "priority": "1", "label": "_Cap+"},
            {"time": "2026-08-05T11:00:00+05:00", "priority": "1", "label": "Y3"},
            {"time": "2026-08-06T09:00:00+05:00", "priority": "2", "label": "Y2"},
        ],
        at="2026-08-05T12:00:00+05:00",
    )

    assert state is not None
    assert state["compact"] == "P1 · Y3"
    assert state["full"] == "Priority 1 — Year 3"
    assert state["eligible"] == ["Y4+", "Y3"]
    assert state["next"]["compact"] == "P2 · Y2"
    assert "_Cap+" not in str(state)


def test_priority_state_keeps_future_gate_separate_before_registration_opens():
    state = derive_priority_state(
        [
            {"time": "2026-08-05T09:00:00+05:00", "priority": "1", "label": "Y4+"},
            {"time": "2026-08-05T11:00:00+05:00", "priority": "1", "label": "Y3"},
        ],
        at="2026-08-05T08:00:00+05:00",
    )

    assert state == {
        "compact": None,
        "full": None,
        "current": None,
        "eligible": [],
        "next": {
            "label": "Y4+",
            "time": "2026-08-05T09:00:00+05:00",
            "priority": "1",
            "compact": "P1 · Y4+",
            "full": "Priority 1 — Year 4+",
        },
    }
