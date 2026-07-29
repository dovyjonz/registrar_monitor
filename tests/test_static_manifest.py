"""Static manifest publication and rollback behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from registrarmonitor.website.static_manifest import (
    build_legacy_frontend_payloads,
    publish_semester,
    rollback_semester_pointer,
)


def _publish(
    root: Path,
    *,
    enrollment: int = 10,
    hook=None,
):
    return publish_semester(
        root,
        semester_slug="summer-2025",
        semester="Summer 2025",
        generated_at=f"2026-05-01T10:{enrollment:02}:00+00:00",
        current_snapshot={
            "id": enrollment,
            "observedAt": f"2026-05-01T10:{enrollment:02}:00+00:00",
            "overallFill": enrollment / 20,
        },
        summary={
            "courseRows": [
                {
                    "code": "CSCI 101",
                    "department": "CSCI",
                    "enrollmentTotal": enrollment,
                }
            ]
        },
        departments={
            "CSCI": {
                "courses": {
                    "CSCI 101": {"sections": {"1L": {"enrollment": enrollment}}}
                },
                "timestamps": ["2026-05-01T10:00:00+00:00"],
            }
        },
        hook=hook,
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


@pytest.mark.parametrize(
    ("phase", "boundary", "pointer_exists"),
    [
        ("blobs", "before", False),
        ("blobs", "after", False),
        ("manifest", "before", False),
        ("manifest", "after", False),
        ("pointer", "before", False),
        ("pointer", "after", True),
    ],
)
def test_interrupted_publication_is_deterministically_repeatable(
    tmp_path: Path,
    phase: str,
    boundary: str,
    pointer_exists: bool,
) -> None:
    def interrupt(current_phase: str, current_boundary: str) -> None:
        if (current_phase, current_boundary) == (phase, boundary):
            raise RuntimeError("injected publication interruption")

    with pytest.raises(RuntimeError):
        _publish(tmp_path, hook=interrupt)
    pointer = tmp_path / "data" / "summer-2025" / "manifest.json"
    assert pointer.exists() is pointer_exists

    result = _publish(tmp_path)
    assert result.status in {"published", "unchanged"}
    assert json.loads(pointer.read_text())["current"] == (
        f"manifests/{result.build_id}.json"
    )


def test_pointer_rollback_swaps_current_and_previous(tmp_path: Path) -> None:
    first = _publish(tmp_path, enrollment=10)
    second = _publish(tmp_path, enrollment=12)

    rolled_back = rollback_semester_pointer(
        tmp_path,
        semester_slug="summer-2025",
    )

    pointer = json.loads(rolled_back.pointer_path.read_text())
    assert rolled_back.status == "rolled_back"
    assert rolled_back.build_id == first.build_id
    assert pointer == {
        "manifestVersion": 1,
        "current": f"manifests/{first.build_id}.json",
        "previous": f"manifests/{second.build_id}.json",
    }


def test_legacy_frontend_payloads_keep_current_state_and_lazy_load_histories() -> None:
    data = {
        "sem": "Summer 2026",
        "lrt": "2026-06-01T12:00:00+00:00",
        "sn": [{"id": 1, "ts": "2026-06-01T12:00:00+00:00"}],
        "cr": {
            "CSCI 101": {
                "d": "CSCI",
                "ti": "Intro",
                "af": 0.5,
                "ah": [{"i": 0, "f": 0.5}],
                "ev": [{"et": "course_added"}],
                "s": {
                    "1L": {
                        "t": "L",
                        "ce": 10,
                        "cc": 20,
                        "cf": 0.5,
                        "h": [{"i": 0, "e": 10, "c": 20, "f": 0.5}],
                    }
                },
            }
        },
    }

    summary, departments = build_legacy_frontend_payloads(
        data=data,
        milestones=[{"time": "2026-06-01T12:00:00+00:00"}],
        semester="Summer 2026",
    )

    summary_course = summary["data"]["cr"]["CSCI 101"]
    assert summary_course["s"]["1L"]["ce"] == 10
    assert "ah" not in summary_course
    assert "ev" not in summary_course
    assert "h" not in summary_course["s"]["1L"]
    assert departments["CSCI"]["courses"]["CSCI 101"] == data["cr"]["CSCI 101"]
