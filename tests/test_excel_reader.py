"""Tests for the Excel reader module (with mocked xlrd)."""

import pytest

pytestmark = pytest.mark.unit


from collections.abc import Mapping
from unittest.mock import MagicMock, patch

from registrarmonitor.data.excel_reader import ExcelReader


def _make_mock_sheet(
    nrows: int,
    ncols: int,
    cells: Mapping[tuple[int, int], str | float | int],
) -> MagicMock:
    """Build a mock sheet with given dimensions and cell values."""
    sheet = MagicMock()
    sheet.nrows = nrows
    sheet.ncols = ncols

    def cell_value(row: int, col: int) -> str | float | int:
        return cells.get((row, col), "")

    sheet.cell_value.side_effect = cell_value
    return sheet


def _mock_workbook(sheet: MagicMock):
    """Build a mock workbook that returns the given sheet at index 0."""
    wb = MagicMock()
    wb.sheet_by_index.return_value = sheet
    return wb


class TestReadExcelData:
    """Tests for ExcelReader.read_excel_data."""

    def test_basic_read(self):
        cells = {
            (0, 0): "Spring 2024",
            (1, 0): "01/15/2024 10:30:00 AM",
            (2, 0): "Course Abbr",
            (2, 1): "S/T",
            (2, 2): "Enr",
            (2, 3): "Cap",
            (2, 4): "Level",
            (3, 0): "CS 101",
            (3, 1): "10L",
            (3, 2): 25,
            (3, 3): 30,
            (3, 4): "UG",
        }
        sheet = _make_mock_sheet(4, 5, cells)
        wb = _mock_workbook(sheet)

        with patch(
            "registrarmonitor.data.excel_reader.xlrd.open_workbook", return_value=wb
        ):
            reader = ExcelReader()
            semester, timestamp, data = reader.read_excel_data("fake.xls")

        assert semester == "Spring 2024"
        assert timestamp == "2024-01-15 10:30:00"
        assert len(data) == 1
        assert data[0]["Course Abbr"] == "CS 101"
        assert data[0]["S/T"] == "10L"
        assert data[0]["Enr"] == 25
        assert data[0]["Cap"] == 30

    def test_empty_sheet_returns_empty_data(self):
        sheet = _make_mock_sheet(0, 0, {})
        wb = _mock_workbook(sheet)

        with patch(
            "registrarmonitor.data.excel_reader.xlrd.open_workbook", return_value=wb
        ):
            reader = ExcelReader()
            semester, timestamp, data = reader.read_excel_data("fake.xls")

        assert data == []

    def test_timestamp_iso_format(self):
        cells = {
            (0, 0): "Spring 2024",
            (1, 0): "2024-01-15 10:30:00",
        }
        sheet = _make_mock_sheet(2, 3, cells)
        wb = _mock_workbook(sheet)

        with patch(
            "registrarmonitor.data.excel_reader.xlrd.open_workbook", return_value=wb
        ):
            reader = ExcelReader()
            _, timestamp, _ = reader.read_excel_data("fake.xls")

        assert timestamp == "2024-01-15 10:30:00"

    def test_fill_calculation_round_half_even(self):
        cells = {
            (0, 0): "Spring 2024",
            (1, 0): "2024-01-15 10:30:00",
            (2, 0): "Course Abbr",
            (2, 1): "S/T",
            (2, 2): "Enr",
            (2, 3): "Cap",
            (3, 0): "CS 101",
            (3, 1): "10L",
            (3, 2): 10,
            (3, 3): 30,
        }
        sheet = _make_mock_sheet(4, 4, cells)
        wb = _mock_workbook(sheet)

        with patch(
            "registrarmonitor.data.excel_reader.xlrd.open_workbook", return_value=wb
        ):
            reader = ExcelReader()
            _, _, data = reader.read_excel_data("fake.xls")

        assert data[0]["Fill"] == 0.33  # 10/30 = 0.333... rounded half even = 0.33

    def test_faculty_to_instructor_conversion(self):
        cells = {
            (0, 0): "Spring 2024",
            (1, 0): "2024-01-15 10:30:00",
            (2, 0): "Course Abbr",
            (2, 1): "S/T",
            (2, 2): "Enr",
            (2, 3): "Cap",
            (2, 4): "Faculty",
            (3, 0): "CS 101",
            (3, 1): "10L",
            (3, 2): 25,
            (3, 3): 30,
            (3, 4): "Smith",
        }
        sheet = _make_mock_sheet(4, 5, cells)
        wb = _mock_workbook(sheet)

        with patch(
            "registrarmonitor.data.excel_reader.xlrd.open_workbook", return_value=wb
        ):
            reader = ExcelReader()
            _, _, data = reader.read_excel_data("fake.xls")

        assert "Faculty" not in data[0]
        assert data[0]["Instructor"] == "Smith"

    def test_faculty_removes_p_suffix(self):
        cells = {
            (0, 0): "Spring 2024",
            (1, 0): "2024-01-15 10:30:00",
            (2, 0): "Course Abbr",
            (2, 1): "S/T",
            (2, 2): "Enr",
            (2, 3): "Cap",
            (2, 4): "Faculty",
            (3, 0): "CS 101",
            (3, 1): "10L",
            (3, 2): 25,
            (3, 3): 30,
            (3, 4): "Smith (P)",
        }
        sheet = _make_mock_sheet(4, 5, cells)
        wb = _mock_workbook(sheet)

        with patch(
            "registrarmonitor.data.excel_reader.xlrd.open_workbook", return_value=wb
        ):
            reader = ExcelReader()
            _, _, data = reader.read_excel_data("fake.xls")

        assert data[0]["Instructor"] == "Smith"
