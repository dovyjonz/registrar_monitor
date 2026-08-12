from types import SimpleNamespace
from unittest.mock import patch

import pytest

from registrarmonitor.website.catalog import build_publication_catalog
from registrarmonitor.website.config import semester_sort_key


@pytest.mark.parametrize(
    ("semester", "expected"),
    [
        ("Fall 2025", (2025, 0)),
        ("Spring 2026", (2025, 1)),
        ("Summer 2026", (2025, 2)),
        ("Fall 2026", (2026, 0)),
    ],
)
def test_academic_year_sort_key(semester, expected):
    assert semester_sort_key(semester) == expected


def test_academic_order_crosses_fall_boundary():
    values = ["Fall 2026", "Spring 2026", "Fall 2025", "Summer 2026"]
    assert sorted(values, key=semester_sort_key) == [
        "Fall 2025",
        "Spring 2026",
        "Summer 2026",
        "Fall 2026",
    ]


def test_future_config_without_database_stays_hidden():
    with patch(
        "registrarmonitor.website.catalog.get_configured_semesters",
        return_value=["Fall 2026", "Summer 2026"],
    ):
        catalog = build_publication_catalog(
            existing_labels={"Summer 2026"},
            database_factory=lambda semester: SimpleNamespace(
                get_latest_snapshot_id=lambda: 1,
                get_snapshot_data=lambda _: SimpleNamespace(
                    semester=semester, courses={"ANT 140": object()}
                ),
            ),
        )

    assert [entry.label for entry in catalog] == ["Summer 2026"]


def test_matching_snapshot_is_publishable_but_mismatch_is_hidden():
    def database(semester):
        embedded = "Fall 2026" if semester == "Fall 2026" else "Spring 2026"
        return SimpleNamespace(
            get_latest_snapshot_id=lambda: 1,
            get_snapshot_data=lambda _: SimpleNamespace(
                semester=embedded, courses={"ANT 140": object()}
            ),
        )

    with (
        patch(
            "registrarmonitor.website.catalog.get_configured_semesters",
            return_value=["Fall 2026", "Summer 2026"],
        ),
        patch(
            "registrarmonitor.website.catalog.get_registrar_url",
            side_effect=lambda semester: f"https://example.test/{semester}",
        ),
    ):
        catalog = build_publication_catalog(
            existing_labels={"Fall 2026", "Summer 2026"},
            database_factory=database,
        )

    assert [entry.label for entry in catalog] == ["Fall 2026"]
