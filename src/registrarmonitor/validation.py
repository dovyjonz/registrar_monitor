"""
Common validation utilities for RegistrarPDF application.

This module provides shared validation functions used across multiple scripts
to eliminate code duplication.
"""

from pathlib import Path
from typing import List

from .core.exceptions import FileProcessingError


def validate_file_exists(file_path: str, file_type: str = "File") -> None:
    """
    Validate that a file exists.

    Args:
        file_path: Path to the file to validate
        file_type: Description of the file type for error messages

    Raises:
        FileNotFoundError: If the file doesn't exist
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"{file_type} '{file_path}' does not exist.")


def validate_excel_file(file_path: str) -> None:
    """
    Validate that a file exists and is an Excel file.

    Args:
        file_path: Path to the Excel file to validate

    Raises:
        FileNotFoundError: If the file doesn't exist
        FileProcessingError: If the file isn't an Excel file
    """
    # First check if file exists
    validate_file_exists(file_path, "Input file")

    # Then check if it's an Excel file
    path = Path(file_path)
    if path.suffix.lower() not in [".xls", ".xlsx"]:
        raise FileProcessingError("Input file must be an Excel file (.xls or .xlsx)")


def validate_multiple_files(file_paths: List[str], file_type: str = "File") -> None:
    """
    Validate that multiple files exist.

    Args:
        file_paths: List of file paths to validate
        file_type: Description of the file type for error messages

    Raises:
        FileNotFoundError: If any file doesn't exist
    """
    for file_path in file_paths:
        validate_file_exists(file_path, file_type)


def validate_directory_exists(dir_path: str, create_if_missing: bool = False) -> Path:
    """
    Validate that a directory exists, optionally creating it.

    Args:
        dir_path: Path to the directory to validate
        create_if_missing: Whether to create the directory if it doesn't exist

    Returns:
        Path object for the directory

    Raises:
        FileProcessingError: If the directory doesn't exist and create_if_missing is False
        FileProcessingError: If the path is not a directory
    """
    path = Path(dir_path)

    if not path.exists():
        if create_if_missing:
            path.mkdir(parents=True, exist_ok=True)
        else:
            raise FileProcessingError(f"Directory '{dir_path}' does not exist.")

    if not path.is_dir():
        raise FileProcessingError(f"'{dir_path}' is not a directory.")

    return path
