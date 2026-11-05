"""Static manifest publication and rollback behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from registrarmonitor.website.static_manifest import (
    _compact_timestamped_points,
    build_frontend_payloads_v3,
    publish_semester,
)


def test_timestamped_compaction_preserves_terminal_duplicate_state():
    points = [
        ("2026-06-01T00:00:00+00:00", {"fill": 0.5}),
        ("2026-06-02T00:00:00+00:00", {"fill": 1.0}),
        ("2026-06-03T00:00:00+00:00", {"fill": 1.0}),
        ("2026-06-04T00:00:00+00:00", {"fill": 1.0}),
    ]

    assert _compact_timestamped_points(points) == [
        points[0],
        points[1],
        points[3],
    ]


def _publish(
    root: Path,
    *,
    enrollment: int = 10,
    hook=None,
):
    timestamp = f"2026-05-01T10:{enrollment:02}:00+00:00"
    data = {
        "sem": "Summer 2025",
        "lrt": timestamp,
        "sn": [
            {"id": enrollment, "timestamp": timestamp, "overallFill": enrollment / 20}
        ],
        "cr": {
            "CSCI 101": {
                "d": "CSCI",
                "ti": "Computer Science",
                "af": enrollment / 20,
                "if": False,
                "ah": [{"i": 0, "f": enrollment / 20}],
                "ev": [],
                "s": {
                    "1L": {
                        "t": "L",
                        "in": "Instructor",
                        "ce": enrollment,
                        "cc": 20,
                        "cf": enrollment / 20,
                        "h": [
                            {
                                "i": 0,
                                "e": enrollment,
                                "c": 20,
                                "f": enrollment / 20,
                            }
                        ],
                    }
                },
            }
        },
    }
    summary, departments = build_frontend_payloads_v3(
        data=data,
        milestones=[],
        semester="Summer 2025",
    )
    return publish_semester(
        root,
        semester_slug="summer-2025",
        semester="Summer 2025",
        generated_at=timestamp,
        current_snapshot=summary["currentSnapshot"],
        summary=summary,
        departments=departments,
        hook=hook,
    )


def _v3_data(*, csci_enrollment: int = 5) -> dict:
    timestamps = [
        "2026-06-01T00:00:00+00:00",
        "2026-06-02T00:00:00+00:00",
        "2026-06-03T00:00:00+00:00",
    ]
    return {
        "semester": "Summer 2026",
        "lastReportTime": timestamps[-1],
        "snapshots": [
            {"id": 1, "timestamp": timestamps[0], "overallFill": 0.40},
            {"id": 2, "timestamp": timestamps[1], "overallFill": 0.45},
            {"id": 3, "timestamp": timestamps[2], "overallFill": 0.50},
        ],
        "courses": {
            "CSCI 101": {
                "department": "CSCI",
                "title": "Computer Science",
                "averageFill": csci_enrollment / 10,
                "isFilled": False,
                "sections": {
                    "1L": {
                        "sectionId": 11,
                        "type": "L",
                        "instructor": "A. Instructor",
                        "currentEnrollment": csci_enrollment,
                        "currentCapacity": 10,
                        "currentFill": csci_enrollment / 10,
                        "history": [
                            {
                                "snapshotIdx": 2,
                                "fill": csci_enrollment / 10,
                                "enrollment": csci_enrollment,
                                "capacity": 10,
                            },
                            {
                                "snapshotIdx": 1,
                                "fill": 0.5,
                                "enrollment": 5,
                                "capacity": 10,
                            },
                            {
                                "snapshotIdx": 0,
                                "fill": 0.5,
                                "enrollment": 5,
                                "capacity": 10,
                            },
                        ],
                    }
                },
                "averageHistory": [
                    {"snapshotIdx": 2, "fill": csci_enrollment / 10},
                    {"snapshotIdx": 1, "fill": 0.5},
                    {"snapshotIdx": 0, "fill": 0.5},
                ],
                "events": [
                    {
                        "eventType": "capacity_changed",
                        "sectionCode": "1L",
                        "oldValue": "8",
                        "newValue": "10",
                        "snapshotTimestamp": timestamps[2],
                    }
                ],
            },
            "MATH 101": {
                "department": "MATH",
                "title": "Calculus",
                "averageFill": 0.4,
                "isFilled": False,
                "sections": {
                    "1L": {
                        "sectionId": 21,
                        "type": "L",
                        "instructor": "B. Instructor",
                        "currentEnrollment": 4,
                        "currentCapacity": 10,
                        "currentFill": 0.4,
                        "history": [
                            {
                                "snapshotIdx": 2,
                                "fill": 0.4,
                                "enrollment": 4,
                                "capacity": 10,
                            },
                            {
                                "snapshotIdx": 1,
                                "fill": 0.4,
                                "enrollment": 4,
                                "capacity": 10,
                            },
                            {
                                "snapshotIdx": 0,
                                "fill": 0.3,
                                "enrollment": 3,
                                "capacity": 10,
                            },
                        ],
                    }
                },
                "averageHistory": [
                    {"snapshotIdx": 2, "fill": 0.4},
                    {"snapshotIdx": 1, "fill": 0.4},
                    {"snapshotIdx": 0, "fill": 0.3},
                ],
                "events": [],
            },
        },
    }


def _build_v3(
    data: dict,
    *,
    milestones: list[dict[str, str]] | None = None,
) -> tuple[dict, dict[str, dict]]:
    if milestones is None:
        milestones = [
            {"time": "2026-06-03T00:00:00+00:00", "label": "Close"},
            {"time": "2026-06-01T00:00:00+00:00", "label": "Open"},
        ]
    return build_frontend_payloads_v3(
        data=data,
        milestones=milestones,
        semester="Summer 2026",
    )


def _publish_v3(root: Path, data: dict, *, hook=None):
    summary, departments = _build_v3(data)
    return publish_semester(
        root,
        semester_slug="summer-2026",
        semester="Summer 2026",
        current_snapshot=summary["currentSnapshot"],
        summary=summary,
        departments=departments,
        hook=hook,
    )


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def test_v3_builder_emits_small_summary_and_local_department_histories() -> None:
    data = _v3_data()
    sections = data["courses"]["CSCI 101"]["sections"]
    sections["2L"] = {**sections["1L"], "instructor": "A. Instructor"}
    data["courses"]["CSCI 101"]["events"].append(
        {
            "eventType": "instructor_changed",
            "sectionCode": "1L",
            "oldValue": "Former Teacher",
            "newValue": "A. Instructor",
            "snapshotIdx": 2,
        }
    )
    summary, departments = _build_v3(data)
    assert summary["courses"]["CSCI 101"]["title"] == "Computer Science"
    assert summary["courses"]["CSCI 101"]["instructors"] == ["A. Instructor"]

    assert summary["schemaVersion"] == 1
    assert summary["kind"] == "semester-summary"
    assert summary["snapshotCount"] == 3
    assert summary["currentSnapshot"]["id"] == 3
    assert set(summary) == {
        "schemaVersion",
        "kind",
        "semester",
        "lastReportTime",
        "snapshotCount",
        "currentSnapshot",
        "milestones",
        "courses",
    }
    assert all(
        not {"history", "averageHistory", "sectionHistory", "events", "sn"}
        & set(course)
        for course in summary["courses"].values()
    )

    assert departments["CSCI"]["timestamps"] == [
        "2026-06-01T00:00:00+00:00",
        "2026-06-03T00:00:00+00:00",
    ]
    assert departments["MATH"]["timestamps"] == [
        "2026-06-01T00:00:00+00:00",
        "2026-06-02T00:00:00+00:00",
        "2026-06-03T00:00:00+00:00",
    ]
    assert all(
        point["timestampIdx"] < len(payload["timestamps"])
        for payload in departments.values()
        for course in payload["courses"].values()
        for point in course["averageHistory"]
    )
    assert all(
        point["timestampIdx"] < len(payload["timestamps"])
        for payload in departments.values()
        for course in payload["courses"].values()
        for points in course["sectionHistory"].values()
        for point in points
    )
    assert departments["CSCI"]["courses"]["CSCI 101"]["events"][0]["timestampIdx"] == 1


@pytest.mark.parametrize(
    ("phase", "boundary"),
    [("blobs", "after"), ("manifest", "after"), ("pointer", "before")],
)
def test_v3_interrupted_publication_retries_without_immutable_conflict(
    tmp_path: Path,
    phase: str,
    boundary: str,
) -> None:
    def interrupt(current_phase: str, current_boundary: str) -> None:
        if (current_phase, current_boundary) == (phase, boundary):
            raise RuntimeError("injected publication interruption")

    with pytest.raises(RuntimeError):
        _publish_v3(tmp_path, _v3_data(), hook=interrupt)

    result = _publish_v3(tmp_path, _v3_data())
    pointer = json.loads(result.pointer_path.read_text())
    assert result.status in {"published", "unchanged"}
    assert pointer["current"] == f"manifests/{result.build_id}.json"


def test_v3_publication_keeps_unchanged_department_hashes_stable(
    tmp_path: Path,
) -> None:
    first = _publish_v3(tmp_path / "first", _v3_data(csci_enrollment=5))
    second = _publish_v3(tmp_path / "second", _v3_data(csci_enrollment=6))
    first_manifest = json.loads(first.manifest_path.read_text())
    second_manifest = json.loads(second.manifest_path.read_text())

    assert first_manifest["dataModelVersion"] == 3
    assert first_manifest["generatedAt"] == "2026-06-03T00:00:00+00:00"
    assert first_manifest["departments"]["CSCI"]["schemaVersion"] == 1
    assert first_manifest["summary"]["schemaVersion"] == 1
    assert (
        first_manifest["departments"]["CSCI"]["sha256"]
        != second_manifest["departments"]["CSCI"]["sha256"]
    )
    assert (
        first_manifest["departments"]["MATH"]["sha256"]
        == second_manifest["departments"]["MATH"]["sha256"]
    )


def test_publication_is_content_addressed_pointer_last_and_unchanged_is_noop(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, str]] = []
    first = _publish(
        tmp_path, hook=lambda phase, boundary: events.append((phase, boundary))
    )
    pointer_before = first.pointer_path.read_bytes()
    files_before = sorted(
        path.relative_to(tmp_path) for path in tmp_path.rglob("*.json")
    )

    repeated = _publish(tmp_path, hook=lambda *_: pytest.fail("no write expected"))

    assert repeated.status == "unchanged"
    assert repeated.blobs_written == 0
    assert first.pointer_path.read_bytes() == pointer_before
    assert (
        sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*.json"))
        == files_before
    )
    assert events.index(("blobs", "after")) < events.index(("manifest", "after"))
    assert events.index(("manifest", "after")) < events.index(("pointer", "before"))


def test_pointer_inlines_current_manifest_and_keeps_rollback_reference(
    tmp_path: Path,
) -> None:
    first = _publish(tmp_path, enrollment=10)
    second = _publish(tmp_path, enrollment=11)
    pointer = json.loads(second.pointer_path.read_text())

    assert pointer["current"] == f"manifests/{second.build_id}.json"
    assert pointer["previous"] == f"manifests/{first.build_id}.json"
    assert pointer["currentManifest"] == json.loads(second.manifest_path.read_text())
    assert len(second.pointer_path.read_bytes()) < 10_000


def test_legacy_pointer_without_inline_manifest_is_upgraded_in_place(
    tmp_path: Path,
) -> None:
    first = _publish(tmp_path, enrollment=10)
    second = _publish(tmp_path, enrollment=11)
    legacy = json.loads(second.pointer_path.read_text())
    del legacy["currentManifest"]
    second.pointer_path.write_text(json.dumps(legacy))

    upgraded = _publish(tmp_path, enrollment=11)
    pointer = json.loads(upgraded.pointer_path.read_text())

    assert upgraded.status == "published"
    assert upgraded.blobs_written == 0
    assert pointer["previous"] == f"manifests/{first.build_id}.json"
    assert pointer["currentManifest"] == json.loads(upgraded.manifest_path.read_text())
