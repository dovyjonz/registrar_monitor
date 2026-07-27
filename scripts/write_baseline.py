"""Write a deterministic, machine-readable baseline for local tooling inputs."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    output = (
        Path(sys.argv[1])
        if len(sys.argv) == 2
        else ROOT / "output" / "tooling-baseline.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    baseline = {
        "format": 1,
        "inputs": {
            ".node-version": (ROOT / ".node-version").read_text().strip(),
            ".python-version": (ROOT / ".python-version").read_text().strip(),
            "package-lock.json": sha256(ROOT / "assets/website/package-lock.json"),
            "pyproject.toml": sha256(ROOT / "pyproject.toml"),
            "uv.lock": sha256(ROOT / "uv.lock"),
        },
    }
    output.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
    print(output)


if __name__ == "__main__":
    main()
