"""Tests for the validation module."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.core.exceptions import FileProcessingError
from registrarmonitor.validation import (
    validate_excel_file,
)


class TestValidateExcelFile:
    """Tests for validate_excel_file function."""

    def test_valid_xls_extension(self, tmp_path: Path):
        """XLS file should pass validation."""
        test_file = tmp_path / "test.xls"
        test_file.write_bytes(b"fake excel content")
        # Should not raise
        validate_excel_file(str(test_file))

    def test_valid_xlsx_extension(self, tmp_path: Path):
        """XLSX file should pass validation."""
        test_file = tmp_path / "test.xlsx"
        test_file.write_bytes(b"fake excel content")
        # Should not raise
        validate_excel_file(str(test_file))

    def test_invalid_extension(self, tmp_path: Path):
        """Non-Excel file should raise FileProcessingError."""
        test_file = tmp_path / "test.csv"
        test_file.write_text("a,b,c")
        with pytest.raises(FileProcessingError):
            validate_excel_file(str(test_file))

    def test_missing_file(self):
        """Missing file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            validate_excel_file("/nonexistent/file.xlsx")
