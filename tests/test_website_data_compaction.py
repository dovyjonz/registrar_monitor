"""History compaction from per-section runs of constant enrollment and capacity."""

import pytest

pytestmark = pytest.mark.unit


from registrarmonitor.website.data import (
    _compact_average_history,
    _compact_section_history,
)

IDENTITY = list(range(12))


def indices(history):
    return [point["snapshotIdx"] for point in history]


def test_section_history_compaction_preserves_endpoints_changes_and_previous_points():
    runs = [(0, 1, 1, 10), (2, 3, 2, 10), (4, 5, 2, 12)]

    compacted = _compact_section_history(runs, IDENTITY)

    assert indices(compacted) == [0, 1, 2, 3, 4, 5]
    assert compacted[4] == {
        "snapshotIdx": 4,
        "fill": 2 / 12,
        "enrollment": 2,
        "capacity": 12,
    }


def test_section_history_keeps_only_endpoints_of_an_unchanged_run():
    assert indices(_compact_section_history([(0, 9, 1, 10)], IDENTITY)) == [0, 9]
    assert _compact_section_history([], IDENTITY) == []


def test_section_history_does_not_keep_unchanged_points_around_a_gap():
    runs = [(0, 2, 3, 10), (6, 8, 3, 10)]

    assert indices(_compact_section_history(runs, IDENTITY)) == [0, 8]


def test_section_history_keeps_the_points_either_side_of_a_changed_gap():
    runs = [(0, 2, 3, 10), (6, 8, 4, 10)]

    assert indices(_compact_section_history(runs, IDENTITY)) == [0, 2, 6, 8]


def test_history_positions_map_to_snapshot_indices():
    snapshot_indices = [0, 3, 4, 9]
    runs = [(0, 1, 1, 10), (2, 3, 2, 10)]

    assert indices(_compact_section_history(runs, snapshot_indices)) == [0, 3, 4, 9]


def test_course_average_history_is_built_before_section_compaction():
    section_runs = [
        [(0, 0, 5, 10), (1, 2, 7, 10)],
        [(0, 2, 5, 20)],
    ]

    assert _compact_average_history(section_runs, IDENTITY) == [
        {"snapshotIdx": 0, "fill": 0.375},
        {"snapshotIdx": 1, "fill": 0.475},
        {"snapshotIdx": 2, "fill": 0.475},
    ]
    # The unchanging section keeps only its endpoints.
    assert indices(_compact_section_history(section_runs[1], IDENTITY)) == [0, 2]


def test_average_history_compaction_preserves_previous_point_before_change():
    """When average fill changes, keep the point before the change as anchor."""
    section_runs = [[(0, 2, 0, 10), (3, 4, 4, 10)]]

    compacted = _compact_average_history(section_runs, IDENTITY)

    # idx 0 endpoint, idx 2 is before-change anchor, idx 3 is change, idx 4 endpoint
    assert indices(compacted) == [0, 2, 3, 4]


def test_average_history_only_counts_sections_present_at_each_point():
    section_runs = [
        [(0, 4, 5, 10)],
        [(2, 4, 10, 10)],
    ]

    assert _compact_average_history(section_runs, IDENTITY) == [
        {"snapshotIdx": 0, "fill": 0.5},
        {"snapshotIdx": 1, "fill": 0.5},
        {"snapshotIdx": 2, "fill": 0.75},
        {"snapshotIdx": 4, "fill": 0.75},
    ]


def test_average_history_without_runs_is_empty():
    assert _compact_average_history([[], []], IDENTITY) == []
    assert _compact_average_history([], IDENTITY) == []
