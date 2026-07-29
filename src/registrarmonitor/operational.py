"""Read-only operational diagnostics and structured tooling baselines."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .data.database_manager import EXPECTED_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[2]


def _check(name: str, status: str, message: str, **details: object) -> dict[str, Any]:
    return {"name": name, "status": status, "message": message, **details}


def _command_version(command: str, *args: str) -> str | None:
    executable = shutil.which(command)
    if executable is None:
        return None
    result = subprocess.run(
        [executable, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return (result.stdout or result.stderr).strip().splitlines()[0]


def _tool_checks(root: Path) -> list[dict[str, Any]]:
    checks = [
        _check(
            "tool:python",
            "pass",
            platform.python_version(),
            executable=sys.executable,
        )
    ]
    for command, args in (
        ("uv", ("--version",)),
        ("jj", ("--version",)),
        ("node", ("--version",)),
        ("npm", ("--version",)),
    ):
        version = _command_version(command, *args)
        checks.append(
            _check(
                f"tool:{command}",
                "pass" if version else "fail",
                version or "not found on PATH",
            )
        )

    node_pin = (root / ".node-version").read_text(encoding="utf-8").strip()
    installed_node = _command_version("node", "--version")
    checks.append(
        _check(
            "tool:node-pin",
            "pass" if installed_node == f"v{node_pin}" else "fail",
            f"expected v{node_pin}; found {installed_node or 'missing'}",
        )
    )
    return checks


def _configuration_checks(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required = [
        root / "settings.toml",
        root / "pyproject.toml",
        root / "uv.lock",
        root / "assets/website/package.json",
        root / "assets/website/package-lock.json",
    ]
    checks = [
        _check(
            f"config:{path.relative_to(root)}",
            "pass" if path.is_file() else "fail",
            "present" if path.is_file() else "missing",
        )
        for path in required
    ]
    settings: dict[str, Any] = {}
    settings_path = root / "settings.toml"
    if settings_path.is_file():
        try:
            settings = tomllib.loads(settings_path.read_text(encoding="utf-8"))
            checks.append(_check("config:settings-parse", "pass", "valid TOML"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            checks.append(_check("config:settings-parse", "fail", str(error)))

    env_path = root / ".env"
    checks.append(
        _check(
            "config:.env",
            "pass" if env_path.is_file() else "warn",
            "present"
            if env_path.is_file()
            else "absent; optional integrations disabled",
        )
    )
    return checks, settings


def _writable_check(name: str, path: Path) -> dict[str, Any]:
    if path.exists() and not path.is_dir():
        return _check(
            name,
            "fail",
            "exists but is not a directory",
            path=str(path),
        )
    probe_dir = path if path.is_dir() else path.parent
    while not probe_dir.exists() and probe_dir != probe_dir.parent:
        probe_dir = probe_dir.parent
    try:
        with tempfile.NamedTemporaryFile(prefix=".doctor-", dir=probe_dir):
            pass
    except OSError as error:
        return _check(name, "fail", str(error), path=str(path))
    status = "pass" if path.exists() else "warn"
    message = "writable" if path.exists() else f"creatable under {probe_dir}"
    return _check(name, status, message, path=str(path))


def _database_checks(root: Path, settings: dict[str, Any]) -> list[dict[str, Any]]:
    configured = settings.get("directories", {}).get("data_storage", "data")
    data_dir = root / configured
    databases = sorted(data_dir.glob("enrollment*.db")) if data_dir.is_dir() else []
    if not databases:
        return [_check("database:discovery", "warn", "no enrollment databases found")]

    checks: list[dict[str, Any]] = []
    for database in databases:
        try:
            display_path = str(database.relative_to(root))
        except ValueError:
            display_path = str(database)
        try:
            uri = f"{database.resolve().as_uri()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
                foreign_key_issues = len(
                    connection.execute("PRAGMA foreign_key_check").fetchall()
                )
            status = (
                "fail"
                if integrity != "ok" or foreign_key_issues
                else "pass"
                if schema_version == EXPECTED_SCHEMA_VERSION
                else "warn"
            )
            checks.append(
                _check(
                    f"database:{display_path}",
                    status,
                    f"integrity={integrity}; schema={schema_version}; "
                    f"foreign_key_issues={foreign_key_issues}",
                    path=display_path,
                    integrity=integrity,
                    schema_version=schema_version,
                    expected_schema_version=EXPECTED_SCHEMA_VERSION,
                    foreign_key_issues=foreign_key_issues,
                )
            )
        except sqlite3.Error as error:
            checks.append(_check(f"database:{display_path}", "fail", str(error)))
    return checks


def build_doctor_report(root: Path = ROOT) -> dict[str, Any]:
    """Collect bounded diagnostics without printing secrets or mutating databases."""
    checks = _tool_checks(root)
    config_checks, settings = _configuration_checks(root)
    checks.extend(config_checks)

    directories = settings.get("directories", {})
    for key in ("data_storage", "raw_downloads", "text_reports", "logs"):
        configured = directories.get(key)
        if configured:
            checks.append(_writable_check(f"path:{key}", root / configured))
    checks.append(
        _writable_check("path:generated-site", root / "assets/website/public")
    )

    frontend_bin = root / "assets/website/node_modules/.bin"
    for tool in ("vite", "playwright"):
        executable = frontend_bin / tool
        checks.append(
            _check(
                f"frontend:{tool}",
                "pass" if executable.is_file() else "fail",
                "installed" if executable.is_file() else "run make website-install",
            )
        )

    checks.extend(_database_checks(root, settings))
    counts = {
        status: sum(check["status"] == status for check in checks)
        for status in ("pass", "warn", "fail")
    }
    return {
        "format": 1,
        "ok": counts["fail"] == 0,
        "summary": counts,
        "checks": checks,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_baseline(root: Path = ROOT) -> dict[str, Any]:
    """Build a machine-readable snapshot of reproducibility and health inputs."""
    report = build_doctor_report(root)
    return {
        "format": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "platform": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "inputs": {
            ".node-version": (root / ".node-version").read_text().strip(),
            ".python-version": (root / ".python-version").read_text().strip(),
            "package-lock.json": _sha256(root / "assets/website/package-lock.json"),
            "pyproject.toml": _sha256(root / "pyproject.toml"),
            "uv.lock": _sha256(root / "uv.lock"),
        },
        "doctor": report,
    }


def write_json(payload: dict[str, Any], output: Path) -> None:
    """Write a stable JSON representation to a requested artifact path."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
