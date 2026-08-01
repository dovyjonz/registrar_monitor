"""Tests for the make-free runtime doctor entry point."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

RUNTIME_DOCTOR = Path(__file__).parent.parent / "scripts" / "runtime_doctor.sh"


def test_runtime_doctor_runs_uv_without_make_or_dependency_sync(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    captured_args = tmp_path / "uv-args.txt"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' "$@" > "$UV_CAPTURE"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    fake_make = fake_bin / "make"
    fake_make.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_make.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:/usr/bin:/bin"
    environment["UV_CAPTURE"] = str(captured_args)

    subprocess.run(
        ["/bin/bash", str(RUNTIME_DOCTOR), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert captured_args.read_text(encoding="utf-8").splitlines() == [
        "run",
        "--locked",
        "--no-sync",
        "--no-cache",
        "monitor",
        "doctor",
        "--json",
    ]
