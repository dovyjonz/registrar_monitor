"""Tests for repeatable performance benchmark calculations and fixtures."""

from __future__ import annotations

import argparse
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "benchmark_performance.py"
SPEC = spec_from_file_location("benchmark_performance", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
benchmark = module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


@pytest.mark.parametrize(
    ("samples", "expected_median", "expected_p95"),
    [
        ([3, 1, 2], 2, 3),
        ([4, 1, 3, 2], 2.5, 4),
        ([7], 7, 7),
        ([5, 5, 5, 5], 5, 5),
    ],
)
def test_summary_statistics(samples, expected_median, expected_p95):
    result = benchmark.summarize(samples)
    assert result["samples"] == samples
    assert result["median"] == expected_median
    assert result["p95"] == expected_p95


def test_summary_rejects_empty_samples():
    with pytest.raises(ValueError, match="must not be empty"):
        benchmark.summarize([])


def test_synthetic_database_is_deterministic_and_non_secret(tmp_path):
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    dimensions = {"courses": 3, "sections": 5, "snapshots": 4}
    benchmark.create_synthetic_database(first, seed=7, **dimensions)
    benchmark.create_synthetic_database(second, seed=7, **dimensions)

    first_metadata = benchmark.database_metadata(first)
    second_metadata = benchmark.database_metadata(second)
    assert first_metadata["counts"] == {
        "snapshots": 4,
        "courses": 3,
        "sections": 5,
        "enrollment_data": 20,
    }
    assert first_metadata["sha256"] == second_metadata["sha256"]
    assert first_metadata["allocation"]["table_bytes"] > 0
    assert first_metadata["allocation"]["index_bytes"] > 0
    assert b"Synthetic Instructor" in first.read_bytes()


def test_database_benchmark_does_not_mutate_source(tmp_path):
    database = tmp_path / "source.db"
    benchmark.create_synthetic_database(
        database, seed=9, courses=3, sections=4, snapshots=3
    )
    before = benchmark.sha256_file(database)
    result = benchmark.benchmark_database(database, 1, 1)
    assert benchmark.sha256_file(database) == before
    assert set(result) == {"cold", "warm", "poll_effects"}
    assert result["poll_effects"]["unchanged"]["rows_added"]["snapshots"] == 0
    assert result["poll_effects"]["changed"]["rows_added"]["snapshots"] == 1
    assert result["cold"]["latest_snapshot_read"]["samples"]


def test_changed_snapshot_mutation_handles_zero_enrollment():
    from registrarmonitor.models import Course, EnrollmentSnapshot, Section

    section = Section("1L", "L", 0, 10, 0.0)
    snapshot = EnrollmentSnapshot(
        "2026-01-01T00:00:00+00:00",
        "Summer 2026",
        0.0,
        {"TEST 100": Course("TEST 100", "TEST", {"1L": section})},
    )

    benchmark._change_first_section(snapshot)

    assert section.enrollment == 1
    assert section.fill == 0.1


def test_website_adapter_generates_expected_artifacts(tmp_path):
    database = tmp_path / "source.db"
    output = tmp_path / "site"
    benchmark.create_synthetic_database(
        database, seed=3, courses=3, sections=4, snapshots=3
    )
    artifacts = benchmark.generate_website_once(database, output)
    assert artifacts["courses"] == 3
    assert artifacts["snapshots"] == 3
    assert artifacts["query_count"] > 0
    assert artifacts["slowest_sql"]
    assert artifacts["file_count"] == 3
    assert (output / "summer2026.html").is_file()
    payload = json.loads((output / "summer2026.json").read_text())
    assert payload["semester"] == "Summer 2026"


def test_result_schema_and_markdown_share_values(tmp_path, monkeypatch):
    database = tmp_path / "source.db"
    output = tmp_path / "result.json"
    markdown = tmp_path / "result.md"
    benchmark.create_synthetic_database(
        database, seed=11, courses=3, sections=4, snapshots=3
    )
    monkeypatch.setattr(benchmark, "repository_revision", lambda: "revision")
    args = argparse.Namespace(
        database=database,
        synthetic=False,
        mode="database",
        cold_iterations=1,
        warm_iterations=1,
        seed=11,
        output=output,
        markdown=markdown,
    )
    result = benchmark.run(args)
    saved = json.loads(output.read_text())
    report = markdown.read_text()
    assert saved["format"] == 1
    assert saved["source"] == {
        "classification": "runtime_copy",
        "semester": "Summer 2026",
    }
    assert saved["database"]["sha256"] in report
    assert str(saved["database"]["bytes"]) in report.replace(",", "")
    assert result["definitions"]["p95"] == "nearest-rank percentile"
    assert str(database.resolve()) not in output.read_text()


def test_invalid_iterations_are_rejected(tmp_path):
    args = argparse.Namespace(
        database=None,
        synthetic=True,
        mode="database",
        cold_iterations=0,
        warm_iterations=1,
        seed=1,
        output=tmp_path / "result.json",
        markdown=None,
    )
    with pytest.raises(ValueError, match="positive"):
        benchmark.run(args)


def test_browser_benchmark_uses_server_assigned_port(tmp_path, monkeypatch):
    output = tmp_path / "site"
    output.mkdir()
    server = MagicMock()
    server.server_address = ("127.0.0.1", 54321)
    thread = MagicMock()
    completed = SimpleNamespace(
        returncode=0,
        stdout=json.dumps(
            {
                "browser_version": "test",
                "cold": {},
                "warm": {},
            }
        ),
        stderr="",
    )
    run = MagicMock(return_value=completed)

    monkeypatch.setattr(benchmark, "_copy_frontend_assets", lambda output: None)
    monkeypatch.setattr(benchmark, "ThreadingHTTPServer", lambda *args: server)
    monkeypatch.setattr(benchmark, "Thread", lambda **kwargs: thread)
    monkeypatch.setattr(benchmark.subprocess, "run", run)
    monkeypatch.setattr(
        benchmark,
        "file_inventory",
        lambda output: {"file_count": 0, "total_bytes": 0, "largest_files": []},
    )

    benchmark.benchmark_browser(output, 1, 1)

    assert run.call_args.args[0][2] == "http://127.0.0.1:54321/summer2026.html"
    server.shutdown.assert_called_once_with()
    server.server_close.assert_called_once_with()
    thread.join.assert_called_once_with(timeout=5)


def test_deployment_loads_project_dotenv(tmp_path, monkeypatch):
    dotenv_loader = MagicMock()
    monkeypatch.setattr(benchmark, "load_dotenv", dotenv_loader)
    monkeypatch.setattr(
        benchmark,
        "file_inventory",
        lambda output: {"file_count": 0, "total_bytes": 0},
    )
    monkeypatch.setattr(
        benchmark.subprocess,
        "run",
        MagicMock(return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
    )

    benchmark.benchmark_deployment(
        tmp_path,
        project="registrar-monitor",
        branch=benchmark.PREVIEW_BRANCH,
    )

    dotenv_loader.assert_called_once_with(benchmark.ROOT / ".env")


def test_deploy_preview_always_uses_fixed_preview_branch(tmp_path, monkeypatch):
    database = tmp_path / "source.db"
    benchmark.create_synthetic_database(
        database,
        seed=11,
        courses=3,
        sections=4,
        snapshots=3,
    )
    deployment = MagicMock(return_value={"status": "deployed"})
    monkeypatch.setattr(
        benchmark,
        "benchmark_browser",
        MagicMock(
            return_value={
                "browser_version": "test",
                "cold": {},
                "warm": {},
                "served_files": {"file_count": 0, "total_bytes": 0},
            }
        ),
    )
    monkeypatch.setattr(
        benchmark,
        "benchmark_website",
        MagicMock(return_value={"cold": {}, "warm": {}, "artifacts": {}}),
    )
    monkeypatch.setattr(benchmark, "benchmark_deployment", deployment)
    monkeypatch.setattr(benchmark, "repository_revision", lambda: "revision")
    args = argparse.Namespace(
        database=database,
        synthetic=False,
        mode="browser",
        cold_iterations=1,
        warm_iterations=1,
        seed=11,
        output=tmp_path / "result.json",
        markdown=None,
        deploy_preview=True,
        pages_project="registrar-monitor",
    )

    benchmark.run(args)

    assert deployment.call_args.kwargs["branch"] == benchmark.PREVIEW_BRANCH


def test_pages_branch_override_is_not_accepted():
    with pytest.raises(SystemExit):
        benchmark.parse_args(["--synthetic", "--pages-branch", "main"])
