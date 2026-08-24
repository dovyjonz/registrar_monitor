"""Tests for the top-level monitor CLI parser."""

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.main import create_parser


@pytest.mark.parametrize(
    ("argv", "command"),
    [
        (["poll"], "poll"),
        (["poll", "--file", "data.xlsx"], "poll"),
        (["status", "CSCI 101"], "status"),
        (["report"], "report"),
        (["report", "--no-telegram"], "report"),
        (["report", "--stateful"], "report"),
        (["run"], "run"),
        (["run", "--no-telegram"], "run"),
        (["run", "--deploy"], "run"),
        (["schedule"], "schedule"),
        (["schedule", "--no-telegram"], "schedule"),
        (["bot"], "bot"),
        (["health-monitor"], "health-monitor"),
        (["deploy"], "deploy"),
        (["deploy", "--semester", "fall2025"], "deploy"),
        (["deploy", "--no-minify"], "deploy"),
        (["deploy", "--output-dir", "output/site"], "deploy"),
        (["doctor"], "doctor"),
        (["doctor", "--json"], "doctor"),
        (["doctor", "--output", "output/doctor.json"], "doctor"),
        (["db", "stats"], "db"),
        (
            [
                "db",
                "initialize",
                "--semester",
                "Fall 2026",
                "--report",
                "output/fall-2026-init.json",
            ],
            "db",
        ),
        (["db", "cleanup", "--keep", "100"], "db"),
        (["db", "dedupe-instructor-changes", "--dry-run"], "db"),
        (
            [
                "db",
                "rehearse",
                "--semester",
                "Summer 2025",
                "--target-version",
                "2",
                "--metadata-mode",
                "legacy-preserving",
                "--database",
                "data/source.db",
                "--report",
                "output/rehearsal.json",
                "--evidence-dir",
                "output/recovery",
            ],
            "db",
        ),
        (
            [
                "db",
                "migrate",
                "--semester",
                "Summer 2025",
                "--target-version",
                "2",
                "--metadata-mode",
                "legacy-preserving",
                "--report",
                "output/migration.json",
                "--dry-run",
                "--candidate",
                "output/candidate.db",
                "--database",
                "data/source.db",
            ],
            "db",
        ),
        (
            [
                "db",
                "rollback-manifest",
                "--semester",
                "Summer 2025",
                "--output-dir",
                "assets/website/public",
                "--report",
                "output/rollback.json",
            ],
            "db",
        ),
        (
            [
                "db",
                "mode",
                "--semester",
                "Summer 2025",
                "--target-mode",
                "shadow",
                "--report",
                "output/mode.json",
                "--database",
                "data/source.db",
            ],
            "db",
        ),
    ],
)
def test_canonical_commands_parse(argv, command):
    parser = create_parser()

    args = parser.parse_args(argv)

    assert args.command == command


@pytest.mark.parametrize("alias", ["fetch", "sync", "website"])
def test_removed_aliases_fail(alias):
    parser = create_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([alias])

    assert isinstance(exc_info.value, SystemExit)
    assert exc_info.value.code == 2


def test_global_debug_before_command_parses():
    parser = create_parser()

    args = parser.parse_args(["--debug", "poll"])

    assert args.debug is True
    assert args.command == "poll"


def test_migrate_requires_exactly_one_execution_mode():
    parser = create_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                "db",
                "migrate",
                "--semester",
                "Summer 2025",
                "--target-version",
                "2",
                "--metadata-mode",
                "legacy-preserving",
                "--report",
                "migration.json",
            ]
        )

    assert exc_info.value.code == 2


def test_migrate_rejects_dry_run_and_apply_together():
    parser = create_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                "db",
                "migrate",
                "--semester",
                "Summer 2025",
                "--target-version",
                "2",
                "--metadata-mode",
                "legacy-preserving",
                "--report",
                "migration.json",
                "--dry-run",
                "--apply",
                "--candidate",
                "candidate.db",
            ]
        )

    assert exc_info.value.code == 2


def test_migrate_apply_accepts_explicit_authorization():
    parser = create_parser()

    args = parser.parse_args(
        [
            "db",
            "migrate",
            "--semester",
            "Summer 2025",
            "--target-version",
            "2",
            "--metadata-mode",
            "legacy-preserving",
            "--report",
            "migration.json",
            "--apply",
            "--authorize",
        ]
    )

    assert args.apply is True
    assert args.authorize is True


def test_migrate_apply_is_not_authorized_by_default():
    parser = create_parser()

    args = parser.parse_args(
        [
            "db",
            "migrate",
            "--semester",
            "Summer 2025",
            "--target-version",
            "2",
            "--metadata-mode",
            "legacy-preserving",
            "--report",
            "migration.json",
            "--apply",
        ]
    )

    assert args.apply is True
    assert args.authorize is False


@pytest.mark.parametrize(
    "argv",
    [
        ["poll", "--debug"],
        ["report", "--debug"],
        ["run", "--debug"],
        ["schedule", "--debug"],
        ["deploy", "--debug"],
        ["deploy", "--minify"],
        ["db", "stats", "--debug"],
    ],
)
def test_post_command_debug_fails(argv):
    parser = create_parser()

    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(argv)

    assert isinstance(exc_info.value, SystemExit)
    assert exc_info.value.code == 2
