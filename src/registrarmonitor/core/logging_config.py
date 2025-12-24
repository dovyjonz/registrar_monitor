"""
Centralized logging configuration for the registrar monitor application.

This module provides consistent logging setup across all components with
configurable levels, formatters, and handlers.
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

from ..config import get_config

# Global flag to prevent multiple logging setups
_logging_setup_done = False


class ColoredFormatter(logging.Formatter):
    """Custom formatter that adds colors to log levels for console output."""

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        # Add color to levelname
        if record.levelname in self.COLORS:
            record.levelname = (
                f"{self.COLORS[record.levelname]}{record.levelname}{self.RESET}"
            )

        return super().format(record)


def setup_logging(
    level: str = "INFO",
    log_dir: Optional[str] = None,
    enable_console: bool = True,
    enable_file: bool = True,
    max_file_size: int = 10_000_000,  # 10MB
    backup_count: int = 5,
    force_setup: bool = False,
) -> None:
    """
    Set up centralized logging configuration.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files (defaults to config or './logs')
        enable_console: Whether to enable console logging
        enable_file: Whether to enable file logging
        max_file_size: Maximum size for log files before rotation
        backup_count: Number of backup log files to keep
        force_setup: Force setup even if already done (useful for tests)
    """
    global _logging_setup_done

    # Prevent multiple setups unless forced
    if _logging_setup_done and not force_setup:
        return
    # Convert string level to logging constant
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Get log directory from config or use default
    if log_dir is None:
        try:
            config = get_config()
            log_dir = config.get("directories", {}).get("logs", "./logs")
        except Exception:
            log_dir = "./logs"

    # Ensure log directory exists
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    # Set root logger level
    root_logger.setLevel(numeric_level)

    # Create formatters
    console_formatter = ColoredFormatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    if enable_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

    # File handlers
    if enable_file:
        # Main application log (rotating)
        main_log_file = log_path / "registrar_monitor.log"
        file_handler = logging.handlers.RotatingFileHandler(
            main_log_file,
            maxBytes=max_file_size,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

        # Error log (for ERROR and CRITICAL only)
        error_log_file = log_path / "errors.log"
        error_handler = logging.handlers.RotatingFileHandler(
            error_log_file,
            maxBytes=max_file_size,
            backupCount=backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_formatter)
        root_logger.addHandler(error_handler)

    # Mark logging as set up
    _logging_setup_done = True

    # Log the logging setup
    logger = logging.getLogger(__name__)
    logger.info(
        f"Logging initialized - Level: {level}, Console: {enable_console}, File: {enable_file}"
    )
    if enable_file:
        logger.info(f"Log files location: {log_path}")


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get a logger instance with consistent configuration.

    Args:
        name: Logger name (defaults to caller's module name)

    Returns:
        Configured logger instance
    """
    if name is None:
        # Get the caller's module name
        import inspect

        frame = inspect.currentframe()
        if frame and frame.f_back:
            name = frame.f_back.f_globals.get("__name__", "registrarmonitor")

    return logging.getLogger(name)


def log_performance(func):
    """
    Decorator to log function performance metrics.

    Usage:
        @log_performance
        def my_function():
            pass
    """
    import functools
    import time

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger = get_logger(func.__module__)
        start_time = time.time()

        try:
            logger.debug(f"Starting {func.__name__}")
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            logger.debug(f"Completed {func.__name__} in {duration:.3f}s")
            return result
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Failed {func.__name__} after {duration:.3f}s: {e}")
            raise

    return wrapper


def log_method_calls(cls):
    """
    Class decorator to log all method calls.

    Usage:
        @log_method_calls
        class MyClass:
            pass
    """
    logger = get_logger(cls.__module__)

    for attr_name in dir(cls):
        attr = getattr(cls, attr_name)
        if callable(attr) and not attr_name.startswith("_"):
            setattr(cls, attr_name, log_performance(attr))

    logger.debug(f"Added logging to all methods in {cls.__name__}")
    return cls
