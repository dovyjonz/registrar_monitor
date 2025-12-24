"""Tests for the validation module."""

from pathlib import Path

import pytest

from registrarmonitor.core.exceptions import FileProcessingError
from registrarmonitor.validation import (
    validate_directory_exists,
    validate_excel_file,
    validate_file_exists,
    validate_multiple_files,
)


class TestValidateFileExists:
    """Tests for validate_file_exists function."""

    def test_existing_file(self, tmp_path: Path):
        """Existing file should not raise."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        # Should not raise
        validate_file_exists(str(test_file))

    def test_missing_file(self):
        """Missing file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            validate_file_exists("/nonexistent/path/file.txt")

    def test_custom_file_type_in_error(self):
        """Error message should include custom file type."""
        with pytest.raises(FileNotFoundError) as exc_info:
            validate_file_exists("/nonexistent/file.txt", "Configuration file")
        assert "Configuration file" in str(exc_info.value)


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


class TestValidateMultipleFiles:
    """Tests for validate_multiple_files function."""

    def test_all_files_exist(self, tmp_path: Path):
        """All existing files should not raise."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("content1")
        file2.write_text("content2")
        # Should not raise
        validate_multiple_files([str(file1), str(file2)])

    def test_some_files_missing(self, tmp_path: Path):
        """Missing file in list should raise FileNotFoundError."""
        file1 = tmp_path / "file1.txt"
        file1.write_text("content")
        with pytest.raises(FileNotFoundError):
            validate_multiple_files([str(file1), "/nonexistent/file.txt"])

    def test_empty_list(self):
        """Empty list should not raise."""
        validate_multiple_files([])


class TestValidateDirectoryExists:
    """Tests for validate_directory_exists function."""

    def test_existing_directory(self, tmp_path: Path):
        """Existing directory should return Path object."""
        result = validate_directory_exists(str(tmp_path))
        assert result == tmp_path

    def test_missing_directory_no_create(self):
        """Missing directory should raise when create_if_missing is False."""
        with pytest.raises(FileProcessingError):
            validate_directory_exists("/nonexistent/directory", create_if_missing=False)

    def test_missing_directory_with_create(self, tmp_path: Path):
        """Missing directory should be created when create_if_missing is True."""
        new_dir = tmp_path / "new_subdir"
        result = validate_directory_exists(str(new_dir), create_if_missing=True)
        assert new_dir.exists()
        assert result == new_dir

    def test_file_path_raises(self, tmp_path: Path):
        """File path (not directory) should raise FileProcessingError."""
        test_file = tmp_path / "file.txt"
        test_file.write_text("content")
        with pytest.raises(FileProcessingError):
            validate_directory_exists(str(test_file))

    def test_nested_directory_creation(self, tmp_path: Path):
        """Nested directories should be created recursively."""
        nested_dir = tmp_path / "a" / "b" / "c"
        result = validate_directory_exists(str(nested_dir), create_if_missing=True)
        assert nested_dir.exists()
        assert result == nested_dir
