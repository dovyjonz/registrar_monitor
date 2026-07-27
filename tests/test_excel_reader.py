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

    def test_sheet_with_only_header_rows(self):
        cells = {
            (0, 0): "Spring 2024",
            (1, 0): "01/15/2024 10:30:00 AM",
        }
        sheet = _make_mock_sheet(2, 3, cells)
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

    def test_timestamp_iso_without_seconds(self):
        cells = {
            (0, 0): "Spring 2024",
            (1, 0): "2024-01-15 10:30",
        }
        sheet = _make_mock_sheet(2, 3, cells)
        wb = _mock_workbook(sheet)

        with patch(
            "registrarmonitor.data.excel_reader.xlrd.open_workbook", return_value=wb
        ):
            reader = ExcelReader()
            _, timestamp, _ = reader.read_excel_data("fake.xls")

        assert timestamp == "2024-01-15 10:30:00"

    def test_timestamp_slash_format_no_am_pm(self):
        cells = {
            (0, 0): "Spring 2024",
            (1, 0): "01/15/2024 10:30:00",
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

    def test_zero_cap_gives_zero_fill(self):
        cells = {
            (0, 0): "Spring 2024",
            (1, 0): "2024-01-15 10:30:00",
            (2, 0): "Course Abbr",
            (2, 1): "S/T",
            (2, 2): "Enr",
            (2, 3): "Cap",
            (3, 0): "CS 101",
            (3, 1): "10L",
            (3, 2): 0,
            (3, 3): 0,
        }
        sheet = _make_mock_sheet(4, 4, cells)
        wb = _mock_workbook(sheet)

        with patch(
            "registrarmonitor.data.excel_reader.xlrd.open_workbook", return_value=wb
        ):
            reader = ExcelReader()
            _, _, data = reader.read_excel_data("fake.xls")

        assert data[0]["Fill"] == 0.0

    def test_empty_enr_and_cap_default_to_zero(self):
        cells = {
            (0, 0): "Spring 2024",
            (1, 0): "2024-01-15 10:30:00",
            (2, 0): "Course Abbr",
            (2, 1): "S/T",
            (2, 2): "Enr",
            (2, 3): "Cap",
            (3, 0): "CS 101",
            (3, 1): "10L",
            (3, 2): "",
            (3, 3): "",
        }
        sheet = _make_mock_sheet(4, 4, cells)
        wb = _mock_workbook(sheet)

        with patch(
            "registrarmonitor.data.excel_reader.xlrd.open_workbook", return_value=wb
        ):
            reader = ExcelReader()
            _, _, data = reader.read_excel_data("fake.xls")

        assert data[0]["Enr"] == 0
        assert data[0]["Cap"] == 0
        assert data[0]["Fill"] == 0.0

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

    def test_empty_faculty_becomes_tba(self):
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
            (3, 4): "",
        }
        sheet = _make_mock_sheet(4, 5, cells)
        wb = _mock_workbook(sheet)

        with patch(
            "registrarmonitor.data.excel_reader.xlrd.open_workbook", return_value=wb
        ):
            reader = ExcelReader()
            _, _, data = reader.read_excel_data("fake.xls")

        assert data[0]["Instructor"] == "TBA"

    def test_invalid_enr_falls_back_to_zero(self):
        cells = {
            (0, 0): "Spring 2024",
            (1, 0): "2024-01-15 10:30:00",
            (2, 0): "Course Abbr",
            (2, 1): "S/T",
            (2, 2): "Enr",
            (2, 3): "Cap",
            (3, 0): "CS 101",
            (3, 1): "10L",
            (3, 2): "N/A",
            (3, 3): 30,
        }
        sheet = _make_mock_sheet(4, 4, cells)
        wb = _mock_workbook(sheet)

        with patch(
            "registrarmonitor.data.excel_reader.xlrd.open_workbook", return_value=wb
        ):
            reader = ExcelReader()
            _, _, data = reader.read_excel_data("fake.xls")

        assert data[0]["Enr"] == 0
        assert data[0]["Cap"] == 30


class TestGetTimestampFromFile:
    """Tests for ExcelReader.get_timestamp_from_file."""

    def test_uses_file_mtime(self):
        reader = ExcelReader()
        with patch(
            "registrarmonitor.data.excel_reader.os.path.getmtime",
            return_value=1705312230.0,
        ):
            result = reader.get_timestamp_from_file("fake.xls")
        # 2024-01-15 10:30:30 UTC -> local time depends on TZ
        # Just check it returns a well-formed ISO string
        assert len(result) == 19
        assert result[4] == "-"
        assert result[7] == "-"
        assert result[10] == " "
        assert result[13] == ":"
        assert result[16] == ":"

    def test_missing_file_falls_back_to_now(self):
        reader = ExcelReader()
        with patch(
            "registrarmonitor.data.excel_reader.os.path.getmtime",
            side_effect=FileNotFoundError,
        ):
            with patch(
                "registrarmonitor.data.excel_reader.datetime.datetime"
            ) as mock_dt:
                mock_dt.now.return_value.strftime.return_value = "2024-01-15 10:30:00"
                result = reader.get_timestamp_from_file("missing.xls")
        assert result == "2024-01-15 10:30:00"
