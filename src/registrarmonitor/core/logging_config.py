"""
Centralized logging configuration for the registrar monitor application.

This module provides consistent logging setup across all components with
configurable levels, formatters, and handlers.
"""

import json
import logging
import logging.handlers
import sys
from copy import copy
from pathlib import Path

from ..config import get_settings

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
LOG_FILE_NAME = "registrar_monitor.log"


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
        color = self.COLORS.get(record.levelname)
        if color is None:
            return super().format(record)

        colored_record = copy(record)
        colored_record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(colored_record)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, suitable for journald and log shippers."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(
    level: str = "INFO",
    log_dir: str | None = None,
    enable_console: bool = True,
    enable_file: bool = True,
    max_file_size: int = 10_000_000,  # 10MB
    backup_count: int = 5,
    json_logs: bool = False,
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
        json_logs: Emit JSON lines on the console (for services under systemd).
            JSON mode logs to the journal only, so no file handler is added.
    """
    # Convert string level to logging constant
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Get log directory from config or use default
    if log_dir is None:
        try:
            log_dir = get_settings().directories.logs
        except Exception:
            log_dir = "./logs"

    log_path = Path(log_dir)

    # Clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()

    # Set root logger level
    root_logger.setLevel(numeric_level)

    # httpx logs complete request URLs at INFO. Telegram embeds the bot token in
    # those URLs, so third-party transport logs must never inherit our INFO level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Console and file share one plain-text format; JSON lines for services.
    console_stream = sys.stdout
    console_formatter: logging.Formatter
    if json_logs:
        console_formatter = JsonFormatter()
    elif console_stream.isatty():
        console_formatter = ColoredFormatter(fmt=LOG_FORMAT, datefmt=LOG_DATEFMT)
    else:
        console_formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATEFMT)

    if enable_console:
        console_handler = logging.StreamHandler(console_stream)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)

    # Services under systemd log to the journal only.
    write_file = enable_file and not json_logs
    if write_file:
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path / LOG_FILE_NAME,
            maxBytes=max_file_size,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(
            logging.Formatter(fmt=LOG_FORMAT, datefmt=LOG_DATEFMT)
        )
        root_logger.addHandler(file_handler)

    logger = logging.getLogger(__name__)
    logger.debug(
        "Logging initialized level=%s console=%s file=%s json=%s",
        level,
        enable_console,
        write_file,
        json_logs,
    )
    if write_file:
        logger.debug("Log file: %s", log_path / LOG_FILE_NAME)
