"""Exhaustive, disposable restart rehearsal for the production migration runner."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .migration import (
    LegacyReader,
    MetadataMode,
    MigrationInterrupted,
    MigrationRequest,
    run_migration,
    sha256_file,
)


@dataclass(frozen=True)
class RehearsalRequest:
    """Operator inputs for one exhaustive dry-run recovery rehearsal."""

    database: Path
    semester: str
    target_version: int
    metadata_mode: MetadataMode
    report_path: Path
    evidence_dir: Path
    raw_dir: Path | None = None
    workers: int = 1
    snapshot_stride: int = 1


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _completion_state(database: Path) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        digest = connection.execute(
            "SELECT state_digest FROM migration_phase "
            "WHERE target_version = 2 AND phase = 'complete'"
        ).fetchone()
        tables = (
            "state_snapshot",
            "course_change_event",
            "section_change_event",
            "state_checkpoint",
            "reporting_log_v2",
        )
        return {
            "complete_digest": str(digest[0]) if digest else None,
            "table_counts": {
                table: int(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
                for table in tables
            },
        }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Migration recovery rehearsal",
        "",
        f"- Status: `{report['status']}`",
        f"- Semester: `{report['semester']}`",
        f"- Scenarios: `{report['scenario_count']}`",
        f"- Passed: `{report['passed']}`",
        f"- Failed: `{report['failed']}`",
        f"- Source hash unchanged: `{report['source_hash_unchanged']}`",
        f"- Snapshot stride: `{report['snapshot_stride']}`",
        "",
        "| Phase | Boundary | Result |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| `{scenario['phase']}` | `{scenario['boundary']}` | `{scenario['status']}` |"
        for scenario in report["scenarios"]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text("\n".join(lines) + "\n")
    os.replace(temporary, path)


def _exercise_scenario(
    request: RehearsalRequest,
    source: Path,
    temporary_root: Path,
    baseline: dict[str, Any],
    phase: str,
    boundary: str,
) -> dict[str, Any]:
    """Interrupt and resume one disposable runner candidate."""
    slug = phase.replace(":", "-")
    candidate = temporary_root / f"{slug}-{boundary}.db"
    scenario_report = request.evidence_dir / f"{slug}-{boundary}.json"
    interrupted = False

    def inject(current_phase: str, current_boundary: str) -> None:
        if (current_phase, current_boundary) == (phase, boundary):
            raise MigrationInterrupted(f"rehearsal interruption at {phase}/{boundary}")

    error: str | None = None
    try:
        run_migration(
            MigrationRequest(
                database=source,
                semester=request.semester,
                target_version=request.target_version,
                metadata_mode=request.metadata_mode,
                report_path=scenario_report,
                dry_run=True,
                candidate_path=candidate,
                raw_dir=request.raw_dir,
            ),
            phase_hook=inject,
        )
    except MigrationInterrupted:
        interrupted = True

    final_state: dict[str, Any] | None = None
    if not interrupted:
        error = "configured interruption boundary was not reached"
    else:
        try:
            result = run_migration(
                MigrationRequest(
                    database=source,
                    semester=request.semester,
                    target_version=request.target_version,
                    metadata_mode=request.metadata_mode,
                    report_path=scenario_report,
                    dry_run=True,
                    candidate_path=candidate,
                    raw_dir=request.raw_dir,
                )
            )
            final_state = _completion_state(candidate)
            if result.status != "verified" or final_state != baseline:
                error = "resumed result differs from uninterrupted baseline"
        except Exception as exception:  # keep the full evidence ledger
            error = f"{type(exception).__name__}: {exception}"

    scenario = {
        "phase": phase,
        "boundary": boundary,
        "interrupted": interrupted,
        "status": "passed" if error is None else "failed",
        "error": error,
        "final_state": final_state,
        "baseline_state": baseline,
        "migration_report": str(scenario_report),
    }
    _atomic_json(
        scenario_report.with_name(f"{scenario_report.stem}-recovery.json"),
        scenario,
    )
    return scenario


def run_rehearsal(request: RehearsalRequest) -> dict[str, Any]:
    """Interrupt and resume the real runner at every transactional boundary."""
    if request.workers <= 0:
        raise ValueError("rehearsal workers must be positive")
    if request.snapshot_stride <= 0:
        raise ValueError("rehearsal snapshot stride must be positive")
    source = request.database.resolve()
    source_hash_before = sha256_file(source)
    snapshots, _ = LegacyReader(source, immutable=True).snapshots()
    snapshot_ordinals = (
        sorted(
            {
                1,
                len(snapshots),
                *range(
                    request.snapshot_stride,
                    len(snapshots) + 1,
                    request.snapshot_stride,
                ),
            }
        )
        if snapshots
        else []
    )
    phases = [
        "schema",
        "catalog",
        *(f"snapshots:{ordinal}" for ordinal in snapshot_ordinals),
        "reporting",
        "complete",
    ]
    request.evidence_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="registrar-migration-rehearsal-") as temp:
        temporary_root = Path(temp)
        baseline_candidate = temporary_root / "baseline.db"
        baseline_report = temporary_root / "baseline.json"
        run_migration(
            MigrationRequest(
                database=source,
                semester=request.semester,
                target_version=request.target_version,
                metadata_mode=request.metadata_mode,
                report_path=baseline_report,
                dry_run=True,
                candidate_path=baseline_candidate,
                raw_dir=request.raw_dir,
            )
        )
        baseline = _completion_state(baseline_candidate)

        ordered_boundaries = [
            (phase, boundary)
            for phase in phases
            for boundary in ("before_commit", "after_commit")
        ]
        try:
            executor = ProcessPoolExecutor(max_workers=request.workers)
        except PermissionError:
            # Restricted sandboxes may deny the semaphore sysconf used by the
            # process pool. Preserve functional testability with threads.
            executor = ThreadPoolExecutor(max_workers=request.workers)
        with executor:
            futures = [
                executor.submit(
                    _exercise_scenario,
                    request,
                    source,
                    temporary_root,
                    baseline,
                    phase,
                    boundary,
                )
                for phase, boundary in ordered_boundaries
            ]
            scenarios = [future.result() for future in futures]

    source_hash_after = sha256_file(source)
    failed = sum(scenario["status"] == "failed" for scenario in scenarios)
    report = {
        "format": 1,
        "status": "passed" if failed == 0 else "failed",
        "semester": request.semester,
        "metadata_mode": request.metadata_mode.value,
        "workers": request.workers,
        "snapshot_stride": request.snapshot_stride,
        "snapshot_phases_tested": snapshot_ordinals,
        "source": str(source),
        "source_sha256_before": source_hash_before,
        "source_sha256_after": source_hash_after,
        "source_hash_unchanged": source_hash_before == source_hash_after,
        "baseline_state": baseline,
        "scenario_count": len(scenarios),
        "passed": len(scenarios) - failed,
        "failed": failed,
        "scenarios": scenarios,
    }
    if not report["source_hash_unchanged"]:
        report["status"] = "failed"
        report["failed"] += 1
    _atomic_json(request.report_path, report)
    _write_markdown(request.report_path.with_suffix(".md"), report)
    return report
