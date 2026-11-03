"""
Shared pytest fixtures for registrarmonitor tests.

This module provides reusable test fixtures including:
- Model builders (Section, Course, EnrollmentSnapshot)
- Temporary database fixtures
- Mock configuration
"""

from typing import Any
from unittest.mock import patch

import pytest

from registrarmonitor.models import (
    Course,
    EnrollmentSnapshot,
    Section,
)

# =============================================================================
# Model Factory Fixtures
# =============================================================================


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


# =============================================================================
# Data Fixtures
# =============================================================================


# =============================================================================
# Comparison Fixtures
# =============================================================================


@pytest.fixture
def mock_decision_logger():
    """Prevent the scheduler from writing decision logs during tests."""
    with patch("registrarmonitor.automation.scheduler.TwoPhaseScheduler._log_decision"):
        yield


@pytest.fixture(autouse=True)
def _fresh_settings(tmp_path, monkeypatch):
    """Isolate tests from a developer's .env and from cached settings."""
    import registrarmonitor.config as config_mod

    monkeypatch.setattr(config_mod, "ENV_PATH", tmp_path / "missing.env")
    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()
    yield
    config_mod.get_settings.cache_clear()
    config_mod.get_config.cache_clear()
