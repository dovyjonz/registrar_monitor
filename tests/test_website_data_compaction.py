from registrarmonitor.website.data import (
    _add_course_average_history,
    _compact_histories_for_website,
    _compact_section_history,
)


def test_section_history_compaction_preserves_endpoints_changes_and_previous_points():
    history = [
        {"snapshotIdx": 0, "fill": 0.1, "enrollment": 1, "capacity": 10},
        {"snapshotIdx": 1, "fill": 0.1, "enrollment": 1, "capacity": 10},
        {"snapshotIdx": 2, "fill": 0.2, "enrollment": 2, "capacity": 10},
        {"snapshotIdx": 3, "fill": 0.2, "enrollment": 2, "capacity": 10},
        {"snapshotIdx": 4, "fill": 0.2, "enrollment": 2, "capacity": 12},
        {"snapshotIdx": 5, "fill": 0.2, "enrollment": 2, "capacity": 12},
    ]

    compacted = _compact_section_history(history)

    assert [point["snapshotIdx"] for point in compacted] == [0, 1, 2, 3, 4, 5]


def test_section_history_compaction_removes_unchanged_middle_points():
    history = [
        {"snapshotIdx": 0, "fill": 0.1, "enrollment": 1, "capacity": 10},
        {"snapshotIdx": 1, "fill": 0.1, "enrollment": 1, "capacity": 10},
        {"snapshotIdx": 2, "fill": 0.1, "enrollment": 1, "capacity": 10},
        {"snapshotIdx": 3, "fill": 0.1, "enrollment": 1, "capacity": 10},
    ]

    compacted = _compact_section_history(history)

    assert [point["snapshotIdx"] for point in compacted] == [0, 3]


def test_course_average_history_uses_compacted_section_points():
    course = {
        "sections": {
            "10L": {
                "history": [
                    {"snapshotIdx": 0, "fill": 0.5},
                    {"snapshotIdx": 2, "fill": 0.7},
                ],
            },
            "11L": {
                "history": [
                    {"snapshotIdx": 0, "fill": 0.25},
                    {"snapshotIdx": 2, "fill": 0.75},
                ],
            },
        }
    }

    _add_course_average_history(course)

    assert course["averageHistory"] == [
        {"snapshotIdx": 0, "fill": 0.375},
        {"snapshotIdx": 2, "fill": 0.725},
    ]


def test_compact_histories_builds_course_average_before_section_compaction():
    data = {
        "courses": {
            "CSC 100": {
                "sections": {
                    "10L": {
                        "history": [
                            {
                                "snapshotIdx": 0,
                                "fill": 0.5,
                                "enrollment": 5,
                                "capacity": 10,
                            },
                            {
                                "snapshotIdx": 1,
                                "fill": 0.7,
                                "enrollment": 7,
                                "capacity": 10,
                            },
                            {
                                "snapshotIdx": 2,
                                "fill": 0.7,
                                "enrollment": 7,
                                "capacity": 10,
                            },
                        ],
                    },
                    "11L": {
                        "history": [
                            {
                                "snapshotIdx": 0,
                                "fill": 0.25,
                                "enrollment": 5,
                                "capacity": 20,
                            },
                            {
                                "snapshotIdx": 1,
                                "fill": 0.25,
                                "enrollment": 5,
                                "capacity": 20,
                            },
                            {
                                "snapshotIdx": 2,
                                "fill": 0.25,
                                "enrollment": 5,
                                "capacity": 20,
                            },
                        ],
                    },
                },
            },
        },
    }

    _compact_histories_for_website(data)

    course = data["courses"]["CSC 100"]
    assert course["sections"]["11L"]["history"] == [
        {"snapshotIdx": 0, "fill": 0.25, "enrollment": 5, "capacity": 20},
        {"snapshotIdx": 2, "fill": 0.25, "enrollment": 5, "capacity": 20},
    ]
    assert course["averageHistory"] == [
        {"snapshotIdx": 0, "fill": 0.375},
        {"snapshotIdx": 1, "fill": 0.475},
        {"snapshotIdx": 2, "fill": 0.475},
    ]
