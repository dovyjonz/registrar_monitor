"""Tests for CLI command implementations."""

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.models import Course, EnrollmentSnapshot, Section

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def sample_course():
    sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
    return Course("CS 101", "CS", sections, 0.83)


@pytest.fixture
def sample_snapshot(sample_course):
    return EnrollmentSnapshot(
        timestamp="2024-01-15 10:00:00",
        semester="Spring 2024",
        overall_fill=0.75,
        courses={"CS 101": sample_course},
    )


def _mock_services():
    """Apply common patches for PollCommand test context."""
    detect_patch = patch(
        "registrarmonitor.cli.commands.detect_active_semester",
        new_callable=AsyncMock,
        return_value="Spring 2024",
    )
    ms_patch = patch("registrarmonitor.cli.commands.MonitoringService")
    detect_mock = detect_patch.start()
    ms_cls = ms_patch.start()
    detect_patch.new = detect_mock
    return ms_cls, [detect_patch, ms_patch]


# ── PollCommand tests ─────────────────────────────────────────────


class TestPollCommand:
    @pytest.mark.asyncio
    async def test_run_delegates_to_run_with_result(self):
        from registrarmonitor.cli.commands import PollCommand

        with patch.object(
            PollCommand, "run_with_result", new_callable=AsyncMock
        ) as mock_rwr:
            mock_rwr.return_value = type("R", (), {"success": True})()
            cmd = PollCommand()
            result = await cmd.run(file_path="test.xls")

        assert result is True
        mock_rwr.assert_awaited_once_with(file_path="test.xls")

    @pytest.mark.asyncio
    async def test_run_with_result_download_success(self, sample_snapshot):
        from registrarmonitor.cli.commands import PollCommand

        ms_cls, patches = _mock_services()

        monitoring = ms_cls.return_value
        monitoring.download_and_process_latest = AsyncMock(
            return_value=(True, sample_snapshot, "/tmp/downloaded.xls")
        )
        monitoring.db_manager.get_latest_snapshot_id.return_value = None
        monitoring.db_for.return_value.get_latest_snapshot_id.return_value = 1
        monitoring.db_for.return_value.get_previous_snapshot_id.return_value = None
        monitoring.db_for.return_value.get_snapshot_data.return_value = sample_snapshot

        try:
            result = await PollCommand().run_with_result()

            assert result.success is True
            assert result.semester == "Spring 2024"
            assert result.snapshot_id_after == 1
            # One reconstruction of the stored snapshot is handed on to the caller.
            assert result.latest_snapshot is sample_snapshot
            assert result.db_manager is monitoring.db_for.return_value
            monitoring.db_for.return_value.get_snapshot_data.assert_called_once_with(1)
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_run_with_result_download_failure(self):
        from registrarmonitor.cli.commands import PollCommand

        ms_cls, patches = _mock_services()

        monitoring = ms_cls.return_value
        monitoring.download_and_process_latest = AsyncMock(
            return_value=(False, None, None)
        )
        monitoring.db_manager.get_latest_snapshot_id.return_value = None

        try:
            result = await PollCommand().run_with_result()

            assert result.success is False
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_run_with_result_marks_new_semester_with_reused_id_as_changed(
        self, sample_course, tmp_path
    ):
        from registrarmonitor.cli.commands import PollCommand
        from registrarmonitor.services.monitoring_service import ProcessedFile

        file_path = str(tmp_path / "fall-data.xls")
        Path(file_path).write_bytes(b"")
        new_semester_snapshot = EnrollmentSnapshot(
            timestamp="2024-08-15 10:00:00",
            semester="Fall 2024",
            overall_fill=0.75,
            courses={"CS 101": sample_course},
        )
        ms_cls, patches = _mock_services()

        monitoring = ms_cls.return_value
        monitoring.process_specific_file = AsyncMock(
            return_value=ProcessedFile(True, new_semester_snapshot, "digest")
        )
        monitoring.db_manager.get_latest_snapshot_id.return_value = 1
        target = monitoring.db_for.return_value
        target.get_latest_snapshot_id.return_value = 1
        target.get_previous_snapshot_id.return_value = None
        target.get_snapshot_data.return_value = new_semester_snapshot

        try:
            result = await PollCommand().run_with_result(file_path=file_path)

            assert result.success is True
            assert result.semester == "Fall 2024"
            assert result.snapshot_id_before == 1
            assert result.snapshot_id_after == 1
            assert result.changed is True
            assert result.change_score == 1.0
            assert result.rows_digest == "digest"
        finally:
            for patcher in patches:
                patcher.stop()

    @pytest.mark.asyncio
    async def test_unchanged_rows_skip_comparison_and_reads(self, tmp_path):
        from registrarmonitor.cli.commands import PollCommand
        from registrarmonitor.services.monitoring_service import ProcessedFile

        file_path = str(tmp_path / "data.xls")
        Path(file_path).write_bytes(b"")
        ms_cls, patches = _mock_services()
        monitoring = ms_cls.return_value
        monitoring.process_specific_file = AsyncMock(
            return_value=ProcessedFile(True, None, "digest", unchanged=True)
        )
        monitoring.db_manager.get_latest_snapshot_id.return_value = 4

        try:
            result = await PollCommand().run_with_result(
                file_path=file_path, previous_digest="digest"
            )

            assert result.success is True
            assert result.unchanged is True
            assert result.changed is False
            assert result.change_score == 0.0
            monitoring.process_specific_file.assert_awaited_once_with(
                file_path, "digest"
            )
            monitoring.db_for.assert_not_called()
            monitoring.db_manager.get_snapshot_data.assert_not_called()
        finally:
            for patcher in patches:
                patcher.stop()

    @pytest.mark.asyncio
    async def test_known_semester_skips_detection_and_poll_logs_resources(
        self, sample_snapshot, tmp_path, caplog
    ):
        import logging

        from registrarmonitor.cli.commands import PollCommand
        from registrarmonitor.services.monitoring_service import ProcessedFile

        file_path = str(tmp_path / "data.xls")
        Path(file_path).write_bytes(b"")
        ms_cls, patches = _mock_services()
        detect = patches[0].new  # the patched detect_active_semester
        monitoring = ms_cls.return_value
        monitoring.process_specific_file = AsyncMock(
            return_value=ProcessedFile(True, sample_snapshot, "digest")
        )
        monitoring.db_manager.get_latest_snapshot_id.return_value = 1
        monitoring.db_for.return_value.get_latest_snapshot_id.return_value = 1

        try:
            with caplog.at_level(logging.INFO):
                await PollCommand().run_with_result(
                    file_path=file_path, semester="Spring 2024"
                )

            detect.assert_not_called()
            ms_cls.assert_called_once_with(semester="Spring 2024")
            summary = [r.message for r in caplog.records if "Poll " in r.message]
            assert len(summary) == 1
            assert "duration=" in summary[0]
            assert "peak_rss=" in summary[0]
        finally:
            for patcher in patches:
                patcher.stop()


# ── ReportCommand tests ───────────────────────────────────────────


class TestReportCommand:
    @pytest.mark.asyncio
    async def test_no_snapshots_returns_false(self):
        from registrarmonitor.cli.commands import ReportCommand

        with (
            patch(
                "registrarmonitor.cli.commands.detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.cli.commands.MonitoringService") as ms_cls,
            patch("registrarmonitor.cli.commands.ReportingService"),
        ):
            ms_cls.return_value.get_snapshot_comparison.return_value = (None, None)

            cmd = ReportCommand()
            result = await cmd.run()

        assert result is False

    @pytest.mark.asyncio
    async def test_stateful_mode(self):
        from registrarmonitor.cli.commands import ReportCommand

        with (
            patch(
                "registrarmonitor.cli.commands.detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.cli.commands.MonitoringService"),
            patch("registrarmonitor.cli.commands.ReportingService") as rs_cls,
        ):
            rs_cls.return_value.run_stateful_report_cycle = AsyncMock(return_value=True)

            cmd = ReportCommand(stateful=True, no_telegram=True)
            result = await cmd.run()

        assert result is True
        rs_cls.return_value.run_stateful_report_cycle.assert_awaited_once_with(
            send_telegram=False, debug_mode=False
        )


# ── RunCommand tests ──────────────────────────────────────────────


class TestRunCommand:
    @pytest.mark.asyncio
    async def test_full_workflow_success(self):
        from registrarmonitor.cli.commands import RunCommand

        with (
            patch("registrarmonitor.cli.commands.PollCommand") as poll_cls,
            patch("registrarmonitor.cli.commands.ReportCommand") as report_cls,
            patch("registrarmonitor.cli.commands.WebsiteService") as ws_cls,
        ):
            poll_cls.return_value.run_with_result = AsyncMock(
                return_value=type("R", (), {"success": True, "change_score": 5.0})()
            )
            report_cls.return_value.run = AsyncMock(return_value=True)
            ws_cls.return_value.generate.return_value = True

            cmd = RunCommand()
            result = await cmd.run()

        assert result is True


# ── DeployCommand tests ───────────────────────────────────────────


class TestDeployCommand:
    def test_generate_only(self):
        from registrarmonitor.cli.commands import DeployCommand

        with patch("registrarmonitor.cli.commands.WebsiteService") as ws_cls:
            ws_cls.return_value.generate.return_value = True
            ws_cls.return_value.last_generation_skipped = False

            cmd = DeployCommand()
            result = cmd.run(deploy=False)

        assert result is True

    def test_generate_and_deploy(self):
        from registrarmonitor.cli.commands import DeployCommand

        with patch("registrarmonitor.cli.commands.WebsiteService") as ws_cls:
            ws_cls.return_value.generate.return_value = True
            ws_cls.return_value.deploy.return_value = True
            ws_cls.return_value.last_generation_skipped = False

            cmd = DeployCommand()
            result = cmd.run(deploy=True)

        assert result is True
        ws_cls.return_value.deploy.assert_called_once_with(dry_run=False)

    def test_refuses_deploy_when_generation_skipped(self):
        from registrarmonitor.cli.commands import DeployCommand

        with patch("registrarmonitor.cli.commands.WebsiteService") as ws_cls:
            ws_cls.return_value.generate.return_value = True
            ws_cls.return_value.last_generation_skipped = True

            cmd = DeployCommand()
            result = cmd.run(deploy=True)

        assert result is False

    def test_dry_run_generates_then_validates_without_upload(self):
        from registrarmonitor.cli.commands import DeployCommand

        with patch("registrarmonitor.cli.commands.WebsiteService") as ws_cls:
            ws_cls.return_value.generate.return_value = True
            ws_cls.return_value.deploy.return_value = True
            ws_cls.return_value.last_generation_skipped = True

            result = DeployCommand().run(dry_run=True)

        assert result is True
        ws_cls.return_value.generate.assert_called_once()
        ws_cls.return_value.deploy.assert_called_once_with(dry_run=True)

    @pytest.mark.parametrize("deployed", [True, False])
    def test_upload_only_skips_generation_and_returns_deploy_result(self, deployed):
        from registrarmonitor.cli.commands import DeployCommand

        with patch("registrarmonitor.cli.commands.WebsiteService") as ws_cls:
            ws_cls.return_value.deploy.return_value = deployed

            result = DeployCommand().run(upload_only=True)

        assert result is deployed
        ws_cls.return_value.generate.assert_not_called()
        ws_cls.return_value.deploy.assert_called_once_with(dry_run=False)

    @pytest.mark.parametrize(
        "options",
        [{"semester": "Fall 2026"}, {"force": True}, {"output_dir": Path("out")}],
    )
    def test_upload_only_rejects_generation_options(self, options):
        from registrarmonitor.cli.commands import DeployCommand

        with patch("registrarmonitor.cli.commands.WebsiteService") as ws_cls:
            result = DeployCommand().run(upload_only=True, **options)

        assert result is False
        ws_cls.return_value.deploy.assert_not_called()


# ── BaselineCommand tests ─────────────────────────────────────────


class TestBaselineCommand:
    @pytest.fixture
    def live(self, tmp_path, monkeypatch):
        from registrarmonitor.config import get_settings
        from registrarmonitor.data.checkpointed_state import CheckpointedStateStore

        data = tmp_path / "data"
        data.mkdir()
        monkeypatch.setenv("DIRECTORIES__DATA_STORAGE", str(data))
        get_settings.cache_clear()
        store = CheckpointedStateStore(data / "enrollment_fall_2026.db")
        for stamp, enrolled in (
            ("2026-08-01 08:00:00", 1),
            ("2026-08-02 08:00:00", 2),
            ("2026-08-03 08:00:00", 3),
        ):
            store.write_snapshot(
                EnrollmentSnapshot(
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
            )
        return data / "enrollment_fall_2026.db"

    @pytest.mark.asyncio
    async def test_marks_latest_snapshot_reported_once(self, live, capsys):
        from registrarmonitor.cli.commands import BaselineCommand
        from registrarmonitor.data.database_manager import DatabaseManager

        db = DatabaseManager(semester="Fall 2026")
        latest = db.get_latest_snapshot_id()
        db.add_reporting_log(
            snapshot_id=db.get_first_snapshot_id() or 0, changes_were_found=True
        )

        assert await BaselineCommand().run(semester="Fall 2026") is True
        assert f"-> {latest}" in capsys.readouterr().out
        assert db.get_last_reported_snapshot_id() == latest

        with sqlite3.connect(live) as connection:
            rows_before = connection.execute(
                "SELECT count(*) FROM reporting_log_v2"
            ).fetchone()
        assert await BaselineCommand().run(semester="Fall 2026") is True
        assert "already the baseline" in capsys.readouterr().out
        with sqlite3.connect(live) as connection:
            assert (
                connection.execute("SELECT count(*) FROM reporting_log_v2").fetchone()
                == rows_before
            )

    @pytest.mark.asyncio
    async def test_baselined_snapshot_produces_no_report(self, live):
        from registrarmonitor.cli.commands import BaselineCommand
        from registrarmonitor.services import ReportingService

        assert await BaselineCommand().run(semester="Fall 2026") is True

        service = ReportingService(semester="Fall 2026")
        assert await service.run_stateful_report_cycle(send_telegram=False) is False

    @pytest.mark.asyncio
    async def test_unknown_semester_creates_nothing(self, live, capsys):
        from registrarmonitor.cli.commands import BaselineCommand

        assert await BaselineCommand().run(semester="Spring 2027") is False
        assert "no database" in capsys.readouterr().out
        assert not (live.parent / "enrollment_spring_2027.db").exists()
