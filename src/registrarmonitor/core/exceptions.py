"""
Custom exceptions for the Registrar Monitor application.
"""


class RegistrarMonitorError(Exception):
    """Base class for exceptions in this application."""


class FileProcessingError(RegistrarMonitorError):
    """Raised for errors related to file processing."""


class DataValidationError(RegistrarMonitorError):
    """Raised for errors during data validation."""


class ConfigurationError(RegistrarMonitorError):
    """Raised for configuration-related errors."""


class NotificationError(RegistrarMonitorError):
    """Raised for errors related to sending notifications."""


class ReportGenerationError(RegistrarMonitorError):
    """Raised for errors during report generation."""
