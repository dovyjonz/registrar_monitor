from types import SimpleNamespace
from unittest.mock import patch

import pytest

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
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
                latest_snapshot_has_courses=lambda label: label == semester,
            ),
        )

    assert [entry.label for entry in catalog] == ["Summer 2026"]


def test_matching_snapshot_is_publishable_but_mismatch_is_hidden():
    def database(semester):
        embedded = "Fall 2026" if semester == "Fall 2026" else "Spring 2026"
        return SimpleNamespace(
            latest_snapshot_has_courses=lambda label: label == embedded,
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


def test_latest_snapshot_has_courses_reads_the_stored_latest_snapshot(tmp_path):
    db = DatabaseManager(db_path=str(tmp_path / "fall.db"), semester="Fall 2026")
    assert db.latest_snapshot_has_courses("Fall 2026") is False

    section = Section("10L", "L", 5, 10, 0.5, "Dr. Smith")
    db.store_enrollment_snapshot(
        EnrollmentSnapshot(
            "2026-11-01 10:00:00",
            "Fall 2026",
            0.5,
            {"CS 101": Course("CS 101", "CS", {"10L": section}, 0.5, "Intro")},
        )
    )
    assert db.latest_snapshot_has_courses("Fall 2026") is True
    assert db.latest_snapshot_has_courses("Spring 2026") is False

    db.store_enrollment_snapshot(
        EnrollmentSnapshot("2026-11-01 11:00:00", "Fall 2026", 0.0, {})
    )
    assert db.latest_snapshot_has_courses("Fall 2026") is False
