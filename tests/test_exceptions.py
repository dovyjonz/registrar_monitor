"""Tests for custom exception classes."""

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.core.exceptions import (
    ConfigurationError,
    DataValidationError,
    FileProcessingError,
    NotificationError,
    RegistrarMonitorError,
    ReportGenerationError,
)


class TestExceptionHierarchy:
    """Verify each exception can be instantiated and inherits correctly."""

    @pytest.mark.parametrize(
        "exc_class",
        [
            RegistrarMonitorError,
            FileProcessingError,
            DataValidationError,
            ConfigurationError,
            NotificationError,
            ReportGenerationError,
        ],
    )
    def test_inherits_from_base(self, exc_class):
        """All exceptions should inherit from RegistrarMonitorError."""
        assert issubclass(exc_class, RegistrarMonitorError)

    @pytest.mark.parametrize(
        "exc_class",
        [
            RegistrarMonitorError,
            FileProcessingError,
            DataValidationError,
            ConfigurationError,
            NotificationError,
            ReportGenerationError,
        ],
    )
    def test_can_instantiate_with_message(self, exc_class):
        """Each exception should carry its message."""
        err = exc_class("test message")
        assert str(err) == "test message"

    @pytest.mark.parametrize(
        "exc_class",
        [
            FileProcessingError,
            DataValidationError,
            ConfigurationError,
            NotificationError,
            ReportGenerationError,
        ],
    )
    def test_is_also_python_exception(self, exc_class):
        """All custom exceptions should also be standard Exceptions."""
        assert issubclass(exc_class, Exception)
