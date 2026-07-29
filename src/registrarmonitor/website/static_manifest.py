"""Immutable static-data publication with an atomic semester pointer."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PublicationHook = Callable[[str, str], None]


@dataclass(frozen=True)
class PublicationResult:
    status: str
    pointer_path: Path
    manifest_path: Path
    build_id: str
    blobs_written: int


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_once(path: Path, payload: bytes) -> bool:
    """Write immutable content, rejecting a hash-address collision."""
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"immutable artifact conflicts with existing file: {path}")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    return True


def _replace_json(path: Path, value: Any) -> None:
    payload = _canonical_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected an object in {path}")
    return value


def build_legacy_frontend_payloads(
    *,
    data: dict[str, Any],
    milestones: list[dict[str, str]],
    semester: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Split the deployed frontend model into current-state and lazy history blobs."""
    summary_courses: dict[str, Any] = {}
    departments: dict[str, dict[str, Any]] = {}

    for code, course_value in sorted(data.get("cr", {}).items()):
        course = dict(course_value)
        department = str(course.get("d") or code.split()[0])
        departments.setdefault(
            department,
            {"semester": semester, "department": department, "courses": {}},
        )["courses"][code] = course_value

        course.pop("ah", None)
        course.pop("ev", None)
        course["s"] = {
            section_code: {key: value for key, value in section.items() if key != "h"}
            for section_code, section in course.get("s", {}).items()
        }
        summary_courses[code] = course

    summary_data = dict(data)
    summary_data["cr"] = summary_courses
    return (
        {"data": summary_data, "milestones": milestones, "semester": semester},
        departments,
    )


def publish_semester(
    output_root: Path,
    *,
    semester_slug: str,
    semester: str,
    current_snapshot: dict[str, Any],
    summary: dict[str, Any],
    departments: dict[str, dict[str, Any]],
    generated_at: str | None = None,
    hook: PublicationHook | None = None,
) -> PublicationResult:
    """Publish blobs and an immutable manifest, replacing the pointer last."""
    root = output_root.resolve()
    semester_root = root / "data" / semester_slug
    pointer_path = semester_root / "manifest.json"
    blobs_root = root / "data" / "blobs"
    manifests_root = semester_root / "manifests"

    summary_payload = _canonical_bytes(summary)
    summary_hash = _sha256(summary_payload)
    department_payloads = {
        name: (_canonical_bytes(payload))
        for name, payload in sorted(departments.items())
    }
    department_hashes = {
        name: _sha256(payload) for name, payload in department_payloads.items()
    }
    identity = {
        "dataModelVersion": 2,
        "semester": semester,
        "currentSnapshot": current_snapshot,
        "summary": summary_hash,
        "departments": department_hashes,
    }
    build_id = _sha256(_canonical_bytes(identity))[:24]
    current_ref = f"manifests/{build_id}.json"

    prior_pointer = _read_json(pointer_path) if pointer_path.exists() else None
    if prior_pointer and prior_pointer.get("current") == current_ref:
        return PublicationResult(
            status="unchanged",
            pointer_path=pointer_path,
            manifest_path=semester_root / current_ref,
            build_id=build_id,
            blobs_written=0,
        )

    if hook:
        hook("blobs", "before")
    blobs_written = int(
        _write_once(blobs_root / f"{summary_hash}.json", summary_payload)
    )
    for name, payload in department_payloads.items():
        blobs_written += int(
            _write_once(blobs_root / f"{department_hashes[name]}.json", payload)
        )
    if hook:
        hook("blobs", "after")

    manifest = {
        "manifestVersion": 1,
        "dataModelVersion": 2,
        "buildId": build_id,
        "semester": semester,
        "generatedAt": generated_at or datetime.now(UTC).isoformat(),
        "currentSnapshot": current_snapshot,
        "summary": {
            "url": f"../../blobs/{summary_hash}.json",
            "sha256": summary_hash,
            "bytes": len(summary_payload),
        },
        "departments": {
            name: {
                "url": f"../../blobs/{department_hashes[name]}.json",
                "sha256": department_hashes[name],
                "bytes": len(department_payloads[name]),
            }
            for name in department_payloads
        },
    }
    manifest_path = manifests_root / f"{build_id}.json"
    if hook:
        hook("manifest", "before")
    _write_once(manifest_path, _canonical_bytes(manifest))
    if hook:
        hook("manifest", "after")

    pointer = {
        "manifestVersion": 1,
        "current": current_ref,
        "previous": prior_pointer.get("current") if prior_pointer else None,
    }
    if hook:
        hook("pointer", "before")
    _replace_json(pointer_path, pointer)
    if hook:
        hook("pointer", "after")
    return PublicationResult(
        status="published",
        pointer_path=pointer_path,
        manifest_path=manifest_path,
        build_id=build_id,
        blobs_written=blobs_written,
    )


def rollback_semester_pointer(
    output_root: Path,
    *,
    semester_slug: str,
) -> PublicationResult:
    """Atomically swap the stable pointer to its declared previous manifest."""
    pointer_path = output_root.resolve() / "data" / semester_slug / "manifest.json"
    pointer = _read_json(pointer_path)
    previous = pointer.get("previous")
    current = pointer.get("current")
    if not isinstance(previous, str) or not previous:
        raise ValueError("semester pointer has no previous manifest")
    manifest_path = pointer_path.parent / previous
    manifest = _read_json(manifest_path)
    _replace_json(
        pointer_path,
        {
            "manifestVersion": 1,
            "current": previous,
            "previous": current,
        },
    )
    return PublicationResult(
        status="rolled_back",
        pointer_path=pointer_path,
        manifest_path=manifest_path,
        build_id=str(manifest["buildId"]),
        blobs_written=0,
    )
