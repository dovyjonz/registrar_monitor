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
