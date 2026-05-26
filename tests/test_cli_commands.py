"""Tests for CLI command implementations."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    db_patch = patch("registrarmonitor.cli.commands.DatabaseManager")
    pop_patch = patch("registrarmonitor.cli.commands.populate_instructors")
    detect_patch.start()
    ms_cls = ms_patch.start()
    db_cls = db_patch.start()
    pop_cls = pop_patch.start()
    return ms_cls, db_cls, pop_cls, [detect_patch, ms_patch, db_patch, pop_patch]


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

        ms_cls, db_cls, pop_cls, patches = _mock_services()

        monitoring = ms_cls.return_value
        monitoring.download_and_process_latest = AsyncMock(
            return_value=(True, sample_snapshot, "/tmp/downloaded.xls")
        )
        monitoring.get_snapshot_comparison.return_value = (sample_snapshot, None)

        db_before = MagicMock()
        db_before.get_latest_snapshot_id.return_value = None
        db_cls.return_value = db_before

        # Set up create_for_semester to return a mock with get_latest_snapshot_id returning 1
        db_after = MagicMock()
        db_after.get_latest_snapshot_id.return_value = 1
        db_cls.create_for_semester.return_value = db_after

        try:
            cmd = PollCommand()
            result = await cmd.run_with_result()

            assert result.success is True
            assert result.semester == "Spring 2024"
            assert result.snapshot_id_after == 1
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_run_with_result_download_failure(self):
        from registrarmonitor.cli.commands import PollCommand

        ms_cls, db_cls, pop_cls, patches = _mock_services()

        monitoring = ms_cls.return_value
        monitoring.download_and_process_latest = AsyncMock(
            return_value=(False, None, None)
        )

        db_before = MagicMock()
        db_before.get_latest_snapshot_id.return_value = None
        db_cls.return_value = db_before

        try:
            cmd = PollCommand()
            result = await cmd.run_with_result()

            assert result.success is False
        finally:
            for p in patches:
                p.stop()

    @pytest.mark.asyncio
    async def test_run_with_result_file_success(self, sample_snapshot, tmp_path):
        from registrarmonitor.cli.commands import PollCommand

        file_path = str(tmp_path / "data.xls")
        Path(file_path).write_bytes(b"")

        ms_cls, db_cls, pop_cls, patches = _mock_services()

        monitoring = ms_cls.return_value
        monitoring.process_specific_file.return_value = (True, sample_snapshot)
        monitoring.get_snapshot_comparison.return_value = (None, None)

        db_before = MagicMock()
        db_before.get_latest_snapshot_id.return_value = None
        db_cls.return_value = db_before
        db_after = MagicMock()
        db_after.get_latest_snapshot_id.return_value = 1
        db_cls.create_for_semester.return_value = db_after

        try:
            cmd = PollCommand()
            result = await cmd.run_with_result(file_path=file_path)

            assert result.success is True
        finally:
            for p in patches:
                p.stop()

    def test_calculate_change_score_no_change(self):
        from registrarmonitor.cli.commands import PollCommand

        cmd = PollCommand()
        score = cmd._calculate_change_score("Spring 2024", changed=False)
        assert score == 0.0


# ── ReportCommand tests ───────────────────────────────────────────


class TestReportCommand:
    @pytest.mark.asyncio
    async def test_standard_mode_success(self, current_snapshot, previous_snapshot):
        from registrarmonitor.cli.commands import ReportCommand

        with (
            patch(
                "registrarmonitor.cli.commands.detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.cli.commands.MonitoringService") as ms_cls,
            patch("registrarmonitor.cli.commands.ReportingService") as rs_cls,
        ):
            ms_cls.return_value.get_snapshot_comparison.return_value = (
                current_snapshot,
                previous_snapshot,
            )
            rs_cls.return_value.generate_and_send_reports = AsyncMock(
                return_value=(True, ["/tmp/report.txt"])
            )

            cmd = ReportCommand(no_telegram=True)
            result = await cmd.run()

        assert result is True

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

    @pytest.mark.asyncio
    async def test_poll_failure_aborts(self):
        from registrarmonitor.cli.commands import RunCommand

        with patch("registrarmonitor.cli.commands.PollCommand") as poll_cls:
            poll_cls.return_value.run_with_result = AsyncMock(
                return_value=type("R", (), {"success": False})()
            )

            cmd = RunCommand()
            result = await cmd.run()

        assert result is False

    @pytest.mark.asyncio
    async def test_deploy_with_generation(self):
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
            ws_instance = ws_cls.return_value
            ws_instance.generate.return_value = True
            ws_instance.deploy.return_value = True
            ws_instance.last_generation_skipped = False

            cmd = RunCommand(deploy=True)
            result = await cmd.run()

        assert result is True
        ws_instance.deploy.assert_called_once()


# ── DatabaseCommands tests ────────────────────────────────────────


class TestDatabaseCommands:
    @pytest.mark.asyncio
    async def test_stats_success(self):
        from registrarmonitor.cli.commands import DatabaseCommands

        with (
            patch(
                "registrarmonitor.cli.commands.detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.cli.commands.MonitoringService") as ms_cls,
        ):
            ms_cls.return_value.get_database_stats.return_value = {
                "snapshots": 10,
                "courses": 50,
                "sections": 200,
                "earliest_snapshot": "2024-01-01",
                "latest_snapshot": "2024-12-31",
            }
            cmd = DatabaseCommands()
            result = await cmd.stats()

        assert result is True

    @pytest.mark.asyncio
    async def test_stats_empty_returns_false(self):
        from registrarmonitor.cli.commands import DatabaseCommands

        with (
            patch(
                "registrarmonitor.cli.commands.detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.cli.commands.MonitoringService") as ms_cls,
        ):
            ms_cls.return_value.get_database_stats.return_value = {}
            cmd = DatabaseCommands()
            result = await cmd.stats()

        assert result is False

    @pytest.mark.asyncio
    async def test_cleanup_success(self):
        from registrarmonitor.cli.commands import DatabaseCommands

        with (
            patch(
                "registrarmonitor.cli.commands.detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.cli.commands.MonitoringService") as ms_cls,
        ):
            ms_cls.return_value.cleanup_old_data.return_value = 5
            cmd = DatabaseCommands()
            result = await cmd.cleanup(keep_count=20)

        assert result is True
        ms_cls.return_value.cleanup_old_data.assert_called_once_with(20)

    def test_migrate_success(self):
        from registrarmonitor.cli.commands import DatabaseCommands

        with patch("registrarmonitor.cli.commands.JSONMigrator") as mig_cls:
            mig_cls.return_value.migrate_all.return_value = {"Spring 2024": 3}
            cmd = DatabaseCommands()
            result = cmd.migrate()

        assert result is True

    def test_migrate_empty(self):
        from registrarmonitor.cli.commands import DatabaseCommands

        with patch("registrarmonitor.cli.commands.JSONMigrator") as mig_cls:
            mig_cls.return_value.migrate_all.return_value = {}
            cmd = DatabaseCommands()
            result = cmd.migrate()

        assert result is True

    @pytest.mark.asyncio
    async def test_dedupe_dry_run(self):
        from registrarmonitor.cli.commands import DatabaseCommands

        with (
            patch(
                "registrarmonitor.cli.commands.detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.cli.commands.DatabaseManager") as db_cls,
        ):
            db_cls.return_value.dedupe_instructor_changes.return_value = 3
            cmd = DatabaseCommands()
            result = await cmd.dedupe_instructor_changes(dry_run=True)

        assert result is True
        db_cls.return_value.dedupe_instructor_changes.assert_called_once_with(
            dry_run=True
        )


# ── StatusCommand tests ───────────────────────────────────────────


class TestStatusCommand:
    @pytest.mark.asyncio
    async def test_found_courses_returns_true(self, sample_snapshot):
        from registrarmonitor.cli.commands import StatusCommand

        with (
            patch(
                "registrarmonitor.cli.commands.detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.cli.commands.MonitoringService") as ms_cls,
        ):
            ms_cls.return_value.get_latest_snapshot.return_value = sample_snapshot
            cmd = StatusCommand()
            result = await cmd.run(courses=["CS 101"])

        assert result is True

    @pytest.mark.asyncio
    async def test_no_snapshot_returns_false(self):
        from registrarmonitor.cli.commands import StatusCommand

        with (
            patch(
                "registrarmonitor.cli.commands.detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.cli.commands.MonitoringService") as ms_cls,
        ):
            ms_cls.return_value.get_latest_snapshot.return_value = None
            cmd = StatusCommand()
            result = await cmd.run(courses=["CS 101"])

        assert result is False

    @pytest.mark.asyncio
    async def test_no_matching_course_returns_false(self, sample_snapshot):
        from registrarmonitor.cli.commands import StatusCommand

        with (
            patch(
                "registrarmonitor.cli.commands.detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.cli.commands.MonitoringService") as ms_cls,
        ):
            ms_cls.return_value.get_latest_snapshot.return_value = sample_snapshot
            cmd = StatusCommand()
            result = await cmd.run(courses=["PHYS 999"])

        assert result is False


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
        ws_cls.return_value.deploy.assert_called_once_with(
            project_name="registrar-monitor", branch=None
        )

    def test_generation_failure_returns_false(self):
        from registrarmonitor.cli.commands import DeployCommand

        with patch("registrarmonitor.cli.commands.WebsiteService") as ws_cls:
            ws_cls.return_value.generate.return_value = False

            cmd = DeployCommand()
            result = cmd.run(deploy=False)

        assert result is False

    def test_refuses_deploy_when_generation_skipped(self):
        from registrarmonitor.cli.commands import DeployCommand

        with patch("registrarmonitor.cli.commands.WebsiteService") as ws_cls:
            ws_cls.return_value.generate.return_value = True
            ws_cls.return_value.last_generation_skipped = True

            cmd = DeployCommand()
            result = cmd.run(deploy=True)

        assert result is False
