"""Extended tests for the website service (generate/deploy pathways)."""

import pytest

pytestmark = pytest.mark.unit


import json
from types import SimpleNamespace
from unittest.mock import patch

from registrarmonitor.services.website_service import WebsiteService


@pytest.fixture(autouse=True)
def publishable_catalog():
    with patch(
        "registrarmonitor.services.website_service.build_publication_catalog",
        return_value=[SimpleNamespace(label="Spring 2024")],
    ):
        yield


class TestGenerateSemesterPage:
    def test_generates_empty_page_when_no_courses(self, tmp_path):
        with (
            patch(
                "registrarmonitor.services.website_service.get_semester_data",
                return_value={"cr": {}},
            ),
            patch(
                "registrarmonitor.services.website_service.get_milestones",
                return_value=[],
            ),
            patch(
                "registrarmonitor.services.website_service.build_semester_page",
                return_value="<html>empty semester</html>",
            ),
        ):
            service = WebsiteService(output_dir=tmp_path)
            result = service.generate_semester_page(
                "Spring 2024", publication_semesters=["Spring 2024"]
            )
            output_path = result[0]
            assert output_path == tmp_path / "semesters" / "spring-2024" / "index.html"
            assert output_path is not None
            assert output_path.read_text() == "<html>empty semester</html>"

    def test_generates_html_and_v3_manifest(self, tmp_path):
        data = {
            "cr": {"CS 101": {}},
            "sn": [{"id": 1, "timestamp": "2024-01-15"}],
        }
        with (
            patch(
                "registrarmonitor.services.website_service.get_semester_data",
                return_value=data,
            ),
            patch(
                "registrarmonitor.services.website_service.get_milestones",
                return_value=[],
            ),
            patch(
                "registrarmonitor.services.website_service.build_semester_page",
                return_value="<html>test</html>",
            ),
            patch("registrarmonitor.services.website_service.update_checksum"),
            patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path),
        ):
            service = WebsiteService()
            out_path, size = service.generate_semester_page(
                "Spring 2024", publication_semesters=["Spring 2024"]
            )

        assert out_path is not None
        assert (tmp_path / "semesters" / "spring-2024" / "index.html").exists()
        assert (tmp_path / "data" / "spring-2024" / "manifest.json").exists()
        assert not (tmp_path / "spring2024.json").exists()

    def test_v3_publication_embeds_only_the_manifest_pointer(self, tmp_path):
        data = {
            "cr": {"CS 101": {}},
            "sn": [{"id": 1, "timestamp": "2024-01-15"}],
        }
        with (
            patch(
                "registrarmonitor.services.website_service.get_semester_data",
                return_value=data,
            ),
            patch(
                "registrarmonitor.services.website_service.get_milestones",
                return_value=[],
            ),
            patch(
                "registrarmonitor.website.templates._get_asset_info",
                return_value=(None, None),
            ),
            patch("registrarmonitor.services.website_service.update_checksum"),
        ):
            service = WebsiteService(output_dir=tmp_path)
            out_path, _ = service.generate_semester_page(
                "Spring 2024", publication_semesters=["Spring 2024"]
            )

        assert out_path is not None
        html = out_path.read_text()
        assert 'data-manifest-url="/data/spring-2024/manifest.json"' in html
        assert "spring2024.json" not in html
        assert not (tmp_path / "spring2024.json").exists()

        pointer = json.loads(
            (tmp_path / "data" / "spring-2024" / "manifest.json").read_text()
        )
        manifest = json.loads(
            (tmp_path / "data" / "spring-2024" / pointer["current"]).read_text()
        )
        summary = json.loads(
            (tmp_path / "data" / "blobs" / manifest["summary"]["sha256"])
            .with_suffix(".json")
            .read_text()
        )
        assert manifest["dataModelVersion"] == 3
        assert manifest["summary"]["schemaVersion"] == 1
        assert summary["kind"] == "semester-summary"
        assert manifest["departments"]["CS"]["schemaVersion"] == 1
        department = json.loads(
            (tmp_path / "data" / "blobs" / manifest["departments"]["CS"]["sha256"])
            .with_suffix(".json")
            .read_text()
        )
        assert department["kind"] == "department-detail"


class TestPatchAssetHashes:
    def test_returns_false_when_manifest_missing(self, tmp_path):
        service = WebsiteService()
        with patch.object(service, "website_assets_dir", tmp_path):
            result = service._patch_asset_hashes_in_html()
        assert result is False

    def test_patches_html_files(self, tmp_path):
        public = tmp_path / "public"
        public.mkdir(parents=True)
        assets = public / "assets" / ".vite"
        assets.mkdir(parents=True)
        (assets / "manifest.json").write_text(
            json.dumps(
                {"src/main.js": {"file": "main-abc.js", "css": ["main-style.css"]}}
            )
        )

        html_file = public / "test.html"
        html_file.write_text(
            '<link rel="modulepreload" href="/assets/main-preload-old.js">'
            '<script src="/assets/main-old.js"></script>'
            '<link href="/assets/main-old.css">'
        )

        service = WebsiteService()
        with patch.object(service, "website_assets_dir", tmp_path):
            result = service._patch_asset_hashes_in_html()

        assert result is True
        assert "main-abc.js" in html_file.read_text()
        assert "main-style.css" in html_file.read_text()
        assert "main-old.js" not in html_file.read_text()
        assert "main-preload-old.js" not in html_file.read_text()
        assert html_file.read_text().count("main-abc.js") == 2


class TestValidateAssetReferences:
    def test_already_current_references_pass(self, tmp_path):
        public = tmp_path / "public"
        assets = public / "assets"
        assets.mkdir(parents=True)
        (assets / "main-current.js").write_text("")
        (assets / "main-current.css").write_text("")
        (public / "fall2026.html").write_text(
            '<script src="/assets/main-current.js"></script>'
            '<link href="/assets/main-current.css">'
        )

        service = WebsiteService()
        with patch.object(service, "website_assets_dir", tmp_path):
            assert service._validate_asset_references_in_html() is True

    def test_stale_reference_reports_page_and_url(self, tmp_path, capsys):
        public = tmp_path / "public"
        public.mkdir(parents=True)
        (public / "fall2026.html").write_text(
            '<script src="/assets/main-stale.js"></script>'
        )

        service = WebsiteService()
        with patch.object(service, "website_assets_dir", tmp_path):
            assert service._validate_asset_references_in_html() is False

        output = capsys.readouterr().out
        assert "fall2026.html" in output
        assert "assets/main-stale.js" in output


class TestGenerate:
    def test_skips_when_not_active(self):
        service = WebsiteService()
        with patch.object(service, "is_any_semester_active", return_value=False):
            result = service.generate(force=False)

        assert result is True
        assert service.last_generation_skipped is True

    def test_force_overrides_inactive(self, tmp_path):
        service = WebsiteService()
        with (
            patch.object(service, "is_any_semester_active", return_value=False),
            patch.object(service, "build_frontend_assets", return_value=True),
            patch(
                "registrarmonitor.services.website_service.get_semesters_needing_update",
                return_value=[],
            ),
            patch.object(service, "generate_semester_page", return_value=(None, 0.0)),
            patch(
                "registrarmonitor.website.templates.build_redirect_index",
                return_value="<html>redirect</html>",
            ),
            patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path),
            patch.object(service, "validate_public_output", return_value=[]),
        ):
            result = service.generate(force=True)

        assert result is True
        assert service.last_generation_skipped is False


class TestIsAnySemesterActive:
    def test_returns_false_when_no_milestones(self):
        with patch("registrarmonitor.website.config.get_milestones", return_value=[]):
            service = WebsiteService()
            assert service.is_any_semester_active() is False

    def test_returns_true_when_within_window(self):
        import datetime

        now = datetime.datetime.now()
        past = (now - datetime.timedelta(hours=1)).isoformat()
        future = (now + datetime.timedelta(hours=1)).isoformat()

        with (
            patch(
                "registrarmonitor.website.config.get_milestones",
                return_value=[{"time": past}, {"time": future}],
            ),
            patch(
                "registrarmonitor.website.config.get_configured_semesters",
                return_value=["Spring 2024"],
            ),
        ):
            service = WebsiteService()
            assert service.is_any_semester_active() is True

    def test_accepts_timezone_aware_milestones(self):
        import datetime
        from zoneinfo import ZoneInfo

        now = datetime.datetime.now(ZoneInfo("Asia/Almaty"))

        with (
            patch(
                "registrarmonitor.website.config.get_milestones",
                return_value=[{"time": now.isoformat()}],
            ),
            patch(
                "registrarmonitor.website.config.get_configured_semesters",
                return_value=["Fall 2026"],
            ),
        ):
            service = WebsiteService()
            assert service.is_any_semester_active() is True


class TestValidatePublicOutput:
    """Tests for the public output validation guard."""

    def test_returns_empty_for_clean_output(self, tmp_path):
        service = WebsiteService()
        # Create allowed files
        (tmp_path / "index.html").write_text("<html>")
        (tmp_path / "_headers").write_text("/*")
        (tmp_path / "robots.txt").write_text("User-agent: *")
        (tmp_path / ".checksums.json").write_text("{}")
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "main.js").write_text("")

        with patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path):
            errors = service.validate_public_output()

        assert errors == []

    def test_detects_database_files(self, tmp_path):
        service = WebsiteService()
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "enrollment.db").write_text("")

        with patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path):
            errors = service.validate_public_output()

        # data/ is not an allowed directory, so it is flagged
        assert any("data" in e for e in errors)

    def test_rejects_root_json_payloads(self, tmp_path):
        service = WebsiteService()
        (tmp_path / "summer2026.json").write_text("{}")

        with patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path):
            errors = service.validate_public_output()

        assert any("root JSON payload" in error for error in errors)

    def test_detects_log_files(self, tmp_path):
        service = WebsiteService()
        (tmp_path / "app.log").write_text("")

        with patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path):
            errors = service.validate_public_output()

        assert any("app.log" in e for e in errors)

    def test_detects_env_files(self, tmp_path):
        service = WebsiteService()
        (tmp_path / ".env").write_text("SECRET=1")

        with patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path):
            errors = service.validate_public_output()

        assert any(".env" in e for e in errors)

    def test_detects_ds_store(self, tmp_path):
        service = WebsiteService()
        (tmp_path / ".DS_Store").write_text("")

        with patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path):
            errors = service.validate_public_output()

        assert any(".DS_Store" in e for e in errors)

    def test_allows_clean_course_directory(self, tmp_path):
        service = WebsiteService()
        route = tmp_path / "courses" / "summer-2026" / "csci-101"
        route.mkdir(parents=True)
        (route / "index.html").write_text("")

        with patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path):
            errors = service.validate_public_output()

        assert errors == []

    def test_rejects_obsolete_html_routes(self, tmp_path):
        service = WebsiteService(output_dir=tmp_path)
        (tmp_path / "summer2026.html").write_text("<html>")

        assert any(
            "Obsolete HTML route" in error for error in service.validate_public_output()
        )

    def test_detects_unexpected_directory(self, tmp_path):
        service = WebsiteService()
        (tmp_path / "logs").mkdir()

        with patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path):
            errors = service.validate_public_output()

        assert any("logs" in e for e in errors)

    def test_detects_db_in_assets(self, tmp_path):
        service = WebsiteService()
        assets = tmp_path / "assets"
        assets.mkdir()
        (assets / "data.db").write_text("")

        with patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path):
            errors = service.validate_public_output()

        assert any("data.db" in e for e in errors)

    def test_detects_private_artifact_in_nested_courses_directory(self, tmp_path):
        service = WebsiteService()
        course = tmp_path / "courses" / "summer-2026"
        course.mkdir(parents=True)
        (course / "enrollment.sqlite3").write_text("")

        with patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path):
            errors = service.validate_public_output()

        assert any("courses/summer-2026/enrollment.sqlite3" in e for e in errors)


def test_prunes_unreferenced_immutable_publication_files(tmp_path):
    service = WebsiteService(output_dir=tmp_path)
    course_hash = "111111111111"
    semester_hash = "222222222222"
    pages = [
        tmp_path / "courses" / "fall-2026" / "ant-140" / "index.html",
        tmp_path / "semesters" / "fall-2026" / "index.html",
    ]
    pages[0].parent.mkdir(parents=True)
    pages[0].write_text(
        f'data-preview-state-url="/data/previews/course/{course_hash}.json"'
    )
    pages[1].parent.mkdir(parents=True)
    pages[1].write_text(
        f'<meta property="og:image" content="/preview/semester/fall-2026/{semester_hash}.png">'
    )

    for kind, hashes in {
        "course": [course_hash, "aaaaaaaaaaaa"],
        "semester": [semester_hash, "bbbbbbbbbbbb"],
    }.items():
        root = tmp_path / "data" / "previews" / kind
        root.mkdir(parents=True)
        for digest in hashes:
            (root / f"{digest}.json").write_text("{}")

    semester_root = tmp_path / "data" / "fall-2026"
    manifests_root = semester_root / "manifests"
    blobs_root = tmp_path / "data" / "blobs"
    manifests_root.mkdir(parents=True)
    blobs_root.mkdir(parents=True)
    for digest in ("current", "previous", "obsolete"):
        (blobs_root / f"{digest}.json").write_text("{}")
        (manifests_root / f"{digest}.json").write_text(
            json.dumps(
                {
                    "summary": {"url": f"../../blobs/{digest}.json"},
                    "departments": {},
                }
            )
        )
    (semester_root / "manifest.json").write_text(
        json.dumps(
            {
                "current": "manifests/current.json",
                "previous": "manifests/previous.json",
            }
        )
    )

    assets = tmp_path / "assets"
    (assets / ".vite").mkdir(parents=True)
    (assets / "current.js").write_text("")
    (assets / "obsolete.js").write_text("")
    (assets / ".vite" / "manifest.json").write_text(
        json.dumps(
            {
                "src/main.js": {"file": "assets/current.js"},
            }
        )
    )

    removed = service._prune_unreferenced_publication_files()

    assert removed == {"assets": 1, "blobs": 1, "manifests": 1, "previews": 2}
    assert (tmp_path / "data" / "previews" / "course" / f"{course_hash}.json").exists()
    assert (
        tmp_path / "data" / "previews" / "semester" / f"{semester_hash}.json"
    ).exists()
    assert (manifests_root / "current.json").exists()
    assert (manifests_root / "previous.json").exists()
    assert (blobs_root / "current.json").exists()
    assert (blobs_root / "previous.json").exists()
    assert (assets / "current.js").exists()
