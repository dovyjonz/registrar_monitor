"""Tests for the top-level monitor CLI parser."""

import pytest

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
        (["deploy"], "deploy"),
        (["deploy", "--semester", "fall2025"], "deploy"),
        (["deploy", "--no-minify"], "deploy"),
        (["db", "stats"], "db"),
        (["db", "cleanup", "--keep", "100"], "db"),
        (["db", "migrate"], "db"),
        (["db", "dedupe-instructor-changes", "--dry-run"], "db"),
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
