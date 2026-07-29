from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

import pytest

SITE_SMOKE_PATH = Path(__file__).parent.parent / "scripts" / "site_smoke.py"
SPEC = spec_from_file_location("site_smoke", SITE_SMOKE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
site_smoke = module_from_spec(SPEC)
SPEC.loader.exec_module(site_smoke)


def test_local_path_resolves_root_and_page_relative_links(tmp_path):
    output_dir = tmp_path / "public"
    page = output_dir / "courses" / "fall-2026" / "csci-101.html"

    with patch.object(site_smoke, "OUTPUT_DIR", output_dir):
        assert site_smoke.local_path("/assets/main.js", page) == (
            output_dir / "assets" / "main.js"
        )
        assert site_smoke.local_path("../../fall2026.html", page) == (
            page.parent / "../../fall2026.html"
        )
        assert site_smoke.local_path("https://example.com/main.js", page) is None


def test_main_checks_nested_course_pages(tmp_path):
    output_dir = tmp_path / "public"
    course_dir = output_dir / "courses" / "fall-2026"
    course_dir.mkdir(parents=True)
    (output_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (course_dir / "csci-101.html").write_text(
        '<script src="../../assets/missing.js"></script>',
        encoding="utf-8",
    )

    with (
        patch.object(site_smoke, "OUTPUT_DIR", output_dir),
        patch.object(
            site_smoke.WebsiteService,
            "validate_public_output",
            return_value=[],
        ),
        pytest.raises(SystemExit, match=r"courses/fall-2026/csci-101\.html"),
    ):
        site_smoke.main()


def test_main_fails_when_json_payload_is_missing(tmp_path):
    output_dir = tmp_path / "public"
    output_dir.mkdir()
    (output_dir / "fall2026.html").write_text(
        '<body data-json-url="missing.json"></body>',
        encoding="utf-8",
    )

    with (
        patch.object(site_smoke, "OUTPUT_DIR", output_dir),
        patch.object(
            site_smoke.WebsiteService,
            "validate_public_output",
            return_value=[],
        ),
        pytest.raises(
            SystemExit,
            match=r"fall2026\.html: missing missing\.json",
        ),
    ):
        site_smoke.main()


def test_main_accepts_present_and_external_json_payloads(tmp_path):
    output_dir = tmp_path / "public"
    output_dir.mkdir()
    (output_dir / "fall2026.html").write_text(
        '<body data-json-url="fall2026.json"></body>',
        encoding="utf-8",
    )
    (output_dir / "fall2026.json").write_text("{}", encoding="utf-8")
    (output_dir / "external.html").write_text(
        '<body data-json-url="https://example.com/fall2026.json"></body>',
        encoding="utf-8",
    )

    with (
        patch.object(site_smoke, "OUTPUT_DIR", output_dir),
        patch.object(
            site_smoke.WebsiteService,
            "validate_public_output",
            return_value=[],
        ),
    ):
        site_smoke.main()
