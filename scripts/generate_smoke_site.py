"""Generate the browser-test site from isolated deterministic enrollment data."""

from pathlib import Path
from shutil import rmtree

from seed_smoke_data import main as seed_smoke_data

from registrarmonitor.config import get_settings
from registrarmonitor.services.website_service import WebsiteService


def main() -> None:
    fixture_root = Path("output/generated-site-smoke").resolve()
    rmtree(fixture_root, ignore_errors=True)
    data_dir = fixture_root / "data"
    data_dir.mkdir(parents=True)

    get_settings().directories.data_storage = str(data_dir)
    seed_smoke_data(data_dir=data_dir)

    if not WebsiteService().generate(force=True):
        raise SystemExit("Failed to generate deterministic browser-test site")


if __name__ == "__main__":
    main()
