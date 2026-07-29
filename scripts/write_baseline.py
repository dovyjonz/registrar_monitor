"""Write a machine-readable baseline for tooling inputs and operational health."""

from __future__ import annotations

import sys
from pathlib import Path

from registrarmonitor.operational import ROOT, build_baseline, write_json


def main() -> None:
    output = (
        Path(sys.argv[1])
        if len(sys.argv) == 2
        else ROOT / "output" / "tooling-baseline.json"
    )
    write_json(build_baseline(ROOT), output)
    print(output)


if __name__ == "__main__":
    main()
