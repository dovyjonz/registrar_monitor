"""
Shared pytest fixtures for registrarmonitor tests.

This module provides reusable test fixtures including:
- Model builders (Section, Course, EnrollmentSnapshot)
- Temporary database fixtures
- Mock configuration
- Sample DataFrame fixtures
"""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest

from registrarmonitor.models import (
    Course,
    EnrollmentComparison,
    EnrollmentSnapshot,
    Section,
)


# =============================================================================
# Model Factory Fixtures
# =============================================================================


@pytest.fixture
def sample_section() -> Section:
    """Create a sample section with typical values."""
    return Section(
        section_id="10L",
        section_type="L",
        enrollment=25,
        capacity=30,
        fill=0.83,
    )


@pytest.fixture
def full_section() -> Section:
    """Create a section that is at capacity."""
    return Section(
        section_id="20L",
        section_type="L",
        enrollment=30,
        capacity=30,
        fill=1.0,
    )


@pytest.fixture
def sample_course() -> Course:
    """Create a sample course with multiple sections."""
    sections = {
        "10L": Section(
            section_id="10L",
            section_type="L",
            enrollment=25,
            capacity=30,
            fill=0.83,
        ),
        "11L": Section(
            section_id="11L",
            section_type="L",
            enrollment=28,
            capacity=30,
            fill=0.93,
        ),
        "1R": Section(
            section_id="1R",
            section_type="R",
            enrollment=20,
            capacity=25,
            fill=0.80,
        ),
    }
    return Course(
        course_code="CS 101",
        department="CS",
        sections=sections,
        average_fill=0.85,
        course_title="Introduction to Computer Science",
    )


@pytest.fixture
def full_course() -> Course:
    """Create a course where all sections of one type are full."""
    sections = {
        "10L": Section(
            section_id="10L",
            section_type="L",
            enrollment=30,
            capacity=30,
            fill=1.0,
        ),
        "11L": Section(
            section_id="11L",
            section_type="L",
            enrollment=30,
            capacity=30,
            fill=1.0,
        ),
    }
    return Course(
        course_code="CS 102",
        department="CS",
        sections=sections,
        average_fill=1.0,
    )


@pytest.fixture
def sample_snapshot(sample_course: Course) -> EnrollmentSnapshot:
    """Create a sample enrollment snapshot."""
    return EnrollmentSnapshot(
        timestamp="2024-01-15 10:30:00",
        semester="Spring 2024",
        overall_fill=0.75,
        courses={sample_course.course_code: sample_course},
    )


@pytest.fixture
def previous_snapshot() -> EnrollmentSnapshot:
    """Create a previous snapshot for comparison testing."""
    sections = {
        "10L": Section(
            section_id="10L",
            section_type="L",
            enrollment=20,
            capacity=30,
            fill=0.67,
        ),
    }
    course = Course(
        course_code="CS 101",
        department="CS",
        sections=sections,
        average_fill=0.67,
    )
    return EnrollmentSnapshot(
        timestamp="2024-01-15 09:00:00",
        semester="Spring 2024",
        overall_fill=0.70,
        courses={"CS 101": course},
    )


@pytest.fixture
def current_snapshot() -> EnrollmentSnapshot:
    """Create a current snapshot for comparison testing."""
    sections = {
        "10L": Section(
            section_id="10L",
            section_type="L",
            enrollment=25,
            capacity=30,
            fill=0.83,
        ),
        "11L": Section(
            section_id="11L",
            section_type="L",
            enrollment=15,
            capacity=30,
            fill=0.50,
        ),
    }
    course = Course(
        course_code="CS 101",
        department="CS",
        sections=sections,
        average_fill=0.67,
    )
    return EnrollmentSnapshot(
        timestamp="2024-01-15 10:30:00",
        semester="Spring 2024",
        overall_fill=0.75,
        courses={"CS 101": course},
    )


# =============================================================================
# Database Fixtures
# =============================================================================


@pytest.fixture
def temp_db_path(tmp_path: Path) -> str:
    """Create a temporary database path."""
    return str(tmp_path / "test_enrollment.db")


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


# =============================================================================
# Configuration Fixtures
# =============================================================================


@pytest.fixture
def mock_config() -> dict[str, Any]:
    """Create a mock configuration dictionary."""
    return {
        "paths": {
            "data_dir": "/tmp/test_data",
            "output_dir": "/tmp/test_output",
        },
        "telegram": {
            "bot_token": "test_token",
            "chat_id": "test_chat_id",
            "dry_run": True,
        },
        "settings": {
            "semester": "Spring 2024",
        },
    }


@pytest.fixture
def patched_config(mock_config: dict[str, Any]):
    """Patch the get_config function to return mock config."""
    with patch("registrarmonitor.config.get_config", return_value=mock_config):
        yield mock_config


# =============================================================================
# DataFrame Fixtures
# =============================================================================


@pytest.fixture
def sample_enrollment_df() -> pd.DataFrame:
    """Create a sample enrollment DataFrame as would come from Excel."""
    data = {
        "Subject": ["CS", "CS", "MATH", "MATH"],
        "Cat#": ["101", "101", "201", "201"],
        "S/T": ["10L", "1R", "20L", "21L"],
        "Enr": [25, 20, 30, 28],
        "Cap": [30, 25, 30, 30],
        "Fill": [0.83, 0.80, 1.00, 0.93],
        "Instructor": ["Smith", "Jones", "Brown", "Brown"],
    }
    return pd.DataFrame(data)


@pytest.fixture
def empty_df() -> pd.DataFrame:
    """Create an empty DataFrame with expected columns."""
    return pd.DataFrame(
        columns=["Subject", "Cat#", "S/T", "Enr", "Cap", "Fill", "Instructor"]
    )


# =============================================================================
# Comparison Fixtures
# =============================================================================


@pytest.fixture
def sample_comparison(
    current_snapshot: EnrollmentSnapshot, previous_snapshot: EnrollmentSnapshot
) -> EnrollmentComparison:
    """Create a sample comparison between two snapshots."""
    from registrarmonitor.data.snapshot_comparator import SnapshotComparator

    comparator = SnapshotComparator()
    return comparator.compare_snapshots(current_snapshot, previous_snapshot)


@pytest.fixture
def empty_comparison() -> EnrollmentComparison:
    """Create an empty comparison (no changes)."""
    return EnrollmentComparison(
        previous_snapshot_timestamp="2024-01-15 09:00:00",
        current_snapshot_timestamp="2024-01-15 10:30:00",
    )
