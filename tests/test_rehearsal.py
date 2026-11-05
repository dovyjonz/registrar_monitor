"""Tests for the scratch-copy rehearsal command (downloader replaced)."""

import logging
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from registrarmonitor.cli.rehearsal import (
    RehearseCommand,
    fingerprint,
    scratch_settings,
)
from registrarmonitor.config import get_settings
from registrarmonitor.data.checkpointed_state import CheckpointedStateStore
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.services import MonitoringService

pytestmark = pytest.mark.integration


def _snapshot(stamp: str, enrolled: int) -> EnrollmentSnapshot:
    return EnrollmentSnapshot(
        timestamp=stamp,
        semester="Fall 2026",
        overall_fill=enrolled / 10,
        courses={
            "CS 101": Course(
                "CS 101",
                "CS",
                {"1L": Section("1L", "L", enrolled, 10, enrolled / 10)},
                enrolled / 10,
            )
        },
    )


@pytest.fixture
def live(tmp_path, monkeypatch) -> Path:
    data = tmp_path / "live"
    data.mkdir()
    monkeypatch.setenv("DIRECTORIES__DATA_STORAGE", str(data))
    monkeypatch.setenv("DIRECTORIES__TEXT_REPORTS", str(tmp_path / "live-changes"))
    monkeypatch.setenv("DIRECTORIES__TELEGRAM_BOT_STATE", str(data / "bot.db"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "live-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    get_settings.cache_clear()
    store = CheckpointedStateStore(data / "enrollment_fall_2026.db")
    store.write_snapshot(_snapshot("2026-08-01 08:00:00", 1))
    store.write_snapshot(_snapshot("2026-08-02 08:00:00", 2))
    return data / "enrollment_fall_2026.db"


def _fake_download(touch_live: Path | None = None, seen: list | None = None):
    async def download_and_process_latest(self):
        settings = get_settings()
        if seen is not None:
            seen.append(
                (
                    settings.directories.data_storage,
                    settings.telegram_bot_token,
                    settings.telegram_chat_id,
                    os.environ.get("HEARTBEAT_URL"),
                )
            )
        snapshot = _snapshot("2026-08-09 08:00:00", 9)
        self.db_manager.store_enrollment_snapshot(snapshot)
        if touch_live is not None:
            with sqlite3.connect(touch_live) as connection:
                connection.execute("CREATE TABLE intruder (x)")
        return True, snapshot, "download.xls"

    return patch.object(
        MonitoringService, "download_and_process_latest", download_and_process_latest
    )


def _snapshot_count(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0]


@pytest.mark.asyncio
async def test_rehearsal_runs_on_a_copy_and_leaves_live_data_alone(live, capsys):
    before = fingerprint(get_settings())
    seen: list = []
    with _fake_download(seen=seen):
        ok = await RehearseCommand().run(semester="Fall 2026")

    assert ok is True
    # Poll ran against the scratch directory with credentials cleared.
    scratch_data, token, chat, heartbeat = seen[0]
    assert Path(scratch_data) != live.parent
    assert (token, chat, heartbeat) == (None, None, "")
    # The live database and environment are exactly as before.
    assert fingerprint(get_settings()) == before
    assert _snapshot_count(live) == 2
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "live-token"
    assert get_settings().telegram_chat_id is not None
    assert not (live.parent / "bot.db").exists()
    assert not list(Path(get_settings().directories.text_reports).glob("*"))
    out = capsys.readouterr().out
    assert "Download and parse: ok" in out
    assert "Parsed: 1 courses, 1 sections" in out
    assert "stored as" in out
    assert "bytes (generated locally, nothing sent)" in out
    assert "Live database: unchanged" in out


@pytest.mark.asyncio
async def test_rehearsal_fails_when_live_database_changes(live, capsys):
    with _fake_download(touch_live=live):
        ok = await RehearseCommand().run(semester="Fall 2026")

    assert ok is False
    assert "Live database: CHANGED" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_rehearsal_reports_a_failed_download(live, capsys):
    async def failed(self):
        return False, None, None

    with patch.object(MonitoringService, "download_and_process_latest", failed):
        ok = await RehearseCommand().run(semester="Fall 2026")

    assert ok is False
    out = capsys.readouterr().out
    assert "Download and parse: FAILED" in out
    assert "Live database: unchanged" in out


@pytest.mark.asyncio
async def test_unknown_semester_is_rejected(live, caplog):
    caplog.set_level(logging.INFO)
    assert await RehearseCommand().run(semester="Spring 2027") is False
    assert "no database" in caplog.text


def test_scratch_settings_restores_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "live-token")
    monkeypatch.delenv("HEARTBEAT_URL", raising=False)
    get_settings.cache_clear()
    with scratch_settings(tmp_path / "scratch") as settings:
        assert settings.telegram_bot_token is None
        assert settings.directories.logs == str(tmp_path / "scratch" / "logs")
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "live-token"
    assert "HEARTBEAT_URL" not in os.environ
    assert get_settings().telegram_bot_token is not None
