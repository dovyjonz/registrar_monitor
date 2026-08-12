"""Tests for the monitoring service module."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.services.monitoring_service import MonitoringService


@pytest.fixture
def mock_deps():
    """Mock all MonitoringService dependencies."""
    with (
        patch(
            "registrarmonitor.services.monitoring_service.DataDownloader"
        ) as mock_dl_cls,
        patch(
            "registrarmonitor.services.monitoring_service.ExcelReader"
        ) as mock_excel_cls,
        patch(
            "registrarmonitor.services.monitoring_service.SnapshotProcessor"
        ) as mock_proc_cls,
        patch(
            "registrarmonitor.services.monitoring_service.DatabaseManager"
        ) as mock_db_cls,
    ):
        mock_dl = mock_dl_cls.return_value
        mock_excel = mock_excel_cls.return_value
        mock_proc = mock_proc_cls.return_value
        mock_db = mock_db_cls.return_value

        yield {
            "downloader": mock_dl,
            "excel_reader": mock_excel,
            "processor": mock_proc,
            "db_manager": mock_db,
            "dl_cls": mock_dl_cls,
            "excel_cls": mock_excel_cls,
            "proc_cls": mock_proc_cls,
            "db_cls": mock_db_cls,
        }


@pytest.fixture
def sample_snapshot():
    sections = {"10L": Section("10L", "L", 25, 30, 0.83)}
    course = Course("CS 101", "CS", sections, 0.83)
    return EnrollmentSnapshot(
        timestamp="2024-01-15 10:00:00",
        semester="Spring 2024",
        overall_fill=0.75,
        courses={"CS 101": course},
    )


class TestMonitoringServiceInit:
    def test_initializes_components(self, mock_deps):
        service = MonitoringService(semester="Spring 2024")
        assert service.semester == "Spring 2024"
        mock_deps["dl_cls"].assert_called_once()
        mock_deps["excel_cls"].assert_called_once()
        mock_deps["proc_cls"].assert_called_once()
        mock_deps["db_cls"].assert_called_once_with(semester="Spring 2024")

    def test_initializes_with_default_semester(self, mock_deps):
        service = MonitoringService()
        assert service.semester is None
        mock_deps["db_cls"].assert_called_once_with(semester=None)


class TestDownloadAndProcessLatest:
    @pytest.mark.asyncio
    async def test_successful_download_and_process(self, mock_deps, sample_snapshot):
        mock_deps["downloader"].download = AsyncMock(return_value="/tmp/data.xls")
        mock_deps["excel_reader"].read_excel_data.return_value = (
            "Spring 2024",
            "2024-01-15 10:00:00",
            [{"Course Abbr": "CS 101", "S/T": "10L", "Enr": 25, "Cap": 30}],
        )
        mock_deps["processor"].process_data.return_value = sample_snapshot

        service = MonitoringService(semester="Spring 2024")
        success, snapshot, path = await service.download_and_process_latest()

        assert success is True
        assert snapshot is sample_snapshot
        assert path == "/tmp/data.xls"
        mock_deps["downloader"].download.assert_awaited_once()
        mock_deps["processor"].save_snapshot.assert_called_once_with(sample_snapshot)

    @pytest.mark.asyncio
    async def test_download_failure_returns_false(self, mock_deps):
        mock_deps["downloader"].download = AsyncMock(return_value=None)

        service = MonitoringService()
        success, snapshot, path = await service.download_and_process_latest()

        assert success is False
        assert snapshot is None
        assert path is None

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, mock_deps):
        mock_deps["downloader"].download = AsyncMock(
            side_effect=Exception("Network error")
        )

        service = MonitoringService()
        success, snapshot, path = await service.download_and_process_latest()

        assert success is False
        assert snapshot is None
        assert path is None


class TestProcessSpecificFile:
    def test_processes_existing_file(self, mock_deps, sample_snapshot, tmp_path):
        file_path = str(tmp_path / "data.xls")
        Path(file_path).write_bytes(b"")
        mock_deps["excel_reader"].read_excel_data.return_value = (
            "Spring 2024",
            "2024-01-15 10:00:00",
            [{"Course Abbr": "CS 101"}],
        )
        mock_deps["processor"].process_data.return_value = sample_snapshot

        service = MonitoringService()
        success, snapshot = service.process_specific_file(file_path)

        assert success is True
        assert snapshot is sample_snapshot

    def test_nonexistent_file_returns_false(self, mock_deps):
        service = MonitoringService()
        success, snapshot = service.process_specific_file("/nonexistent/file.xls")

    def test_rejects_mismatched_registrar_semester_before_saving(
        self, mock_deps, tmp_path
    ):
        file_path = tmp_path / "data.xls"
        file_path.write_bytes(b"")
        mock_deps["excel_reader"].read_excel_data.return_value = (
            "Summer 2026",
            "2026-08-12 10:00:00",
            [{"Course Abbr": "ANT 140"}],
        )

        service = MonitoringService(semester="Fall 2026")
        success, snapshot = service.process_specific_file(str(file_path))

        assert success is False
        assert snapshot is None
        mock_deps["processor"].save_snapshot.assert_not_called()

        assert success is False
        assert snapshot is None

    def test_exception_in_processing_returns_false(self, mock_deps, tmp_path):
        file_path = str(tmp_path / "data.xls")
        Path(file_path).write_bytes(b"")
        mock_deps["excel_reader"].read_excel_data.side_effect = Exception("Parse error")

        service = MonitoringService()
        success, snapshot = service.process_specific_file(file_path)

        assert success is False
        assert snapshot is None


class TestGetLatestSnapshot:
    def test_returns_snapshot_when_exists(self, mock_deps, sample_snapshot):
        mock_deps["db_manager"].get_latest_snapshot_id.return_value = 1
        mock_deps["db_manager"].get_snapshot_data.return_value = sample_snapshot

        service = MonitoringService()
        result = service.get_latest_snapshot()

        assert result is sample_snapshot

    def test_returns_none_when_no_snapshots(self, mock_deps):
        mock_deps["db_manager"].get_latest_snapshot_id.return_value = None

        service = MonitoringService()
        result = service.get_latest_snapshot()

        assert result is None


class TestGetSnapshotComparison:
    def test_returns_pair_when_two_snapshots(self, mock_deps, sample_snapshot):
        mock_deps["db_manager"].get_latest_snapshot_id.return_value = 2
        mock_deps["db_manager"].get_previous_snapshot_id.return_value = 1
        mock_deps["db_manager"].get_snapshot_data.side_effect = [
            sample_snapshot,
            None,
        ]

        service = MonitoringService()
        current, previous = service.get_snapshot_comparison()

        assert current is sample_snapshot
        assert previous is None
        mock_deps["db_manager"].get_previous_snapshot_id.assert_called_once_with(2)

    def test_returns_none_pair_when_no_snapshots(self, mock_deps):
        mock_deps["db_manager"].get_latest_snapshot_id.return_value = None

        service = MonitoringService()
        current, previous = service.get_snapshot_comparison()

        assert current is None
        assert previous is None


class TestCleanupOldData:
    def test_delegates_to_db_manager(self, mock_deps):
        mock_deps["db_manager"].cleanup_old_snapshots.return_value = 5

        service = MonitoringService()
        result = service.cleanup_old_data(keep_count=20)

        assert result == 5
        mock_deps["db_manager"].cleanup_old_snapshots.assert_called_once_with(20)

    def test_returns_zero_on_exception(self, mock_deps):
        mock_deps["db_manager"].cleanup_old_snapshots.side_effect = Exception(
            "DB error"
        )

        service = MonitoringService()
        result = service.cleanup_old_data()

        assert result == 0


class TestGetDatabaseStats:
    def test_returns_stats_dict(self, mock_deps):
        mock_deps["db_manager"].get_database_stats.return_value = {
            "snapshots": 10,
            "courses": 50,
            "sections": 200,
            "earliest_snapshot": "2024-01-01",
            "latest_snapshot": "2024-12-31",
        }

        service = MonitoringService()
        stats = service.get_database_stats()

        assert stats["snapshots"] == 10
        assert stats["courses"] == 50
        assert stats["sections"] == 200
        assert stats["earliest_snapshot"] == "2024-01-01"
        assert stats["latest_snapshot"] == "2024-12-31"

    def test_returns_empty_on_exception(self, mock_deps):
        mock_deps["db_manager"].get_database_stats.side_effect = Exception("DB error")

        service = MonitoringService()
        stats = service.get_database_stats()

        assert stats == {}


class TestGetCourseHistory:
    def test_delegates_to_db_manager(self, mock_deps):
        mock_deps["db_manager"].get_course_history.return_value = [
            {"timestamp": "2024-01-15", "section_code": "10L", "enrollment_count": 25}
        ]

        service = MonitoringService(semester="Spring 2024")
        history = service.get_course_history("CS 101")

        assert len(history) == 1
        mock_deps["db_manager"].get_course_history.assert_called_once_with(
            "CS 101", "Spring 2024"
        )

    def test_returns_empty_on_exception(self, mock_deps):
        mock_deps["db_manager"].get_course_history.side_effect = Exception("DB error")

        service = MonitoringService()
        history = service.get_course_history("CS 101")

        assert history == []
