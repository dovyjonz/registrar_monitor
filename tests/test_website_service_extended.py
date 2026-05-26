"""Extended tests for the website service (generate/deploy pathways)."""

import json
from unittest.mock import MagicMock, patch

from registrarmonitor.services.website_service import WebsiteService


class TestGenerateSemesterPage:
    def test_returns_none_when_no_courses(self):
        with (
            patch(
                "registrarmonitor.services.website_service.get_semester_data",
                return_value={"cr": {}},
            ),
            patch(
                "registrarmonitor.services.website_service.MILESTONES_MAP",
                return_value={},
            ),
        ):
            service = WebsiteService()
            result = service.generate_semester_page("Spring 2024")
            assert result == (None, 0.0)

    def test_generates_html_and_json(self, tmp_path):
        data = {
            "cr": {"CS 101": {}},
            "sn": [{"id": 1, "timestamp": "2024-01-15"}],
        }
        with (
            patch(
                "registrarmonitor.services.website_service.get_semester_data",
                return_value=data,
            ),
            patch("registrarmonitor.services.website_service.MILESTONES_MAP", {}),
            patch(
                "registrarmonitor.services.website_service.build_semester_page",
                return_value="<html>test</html>",
            ),
            patch(
                "registrarmonitor.services.website_service.semester_to_filename",
                return_value="spring2024.html",
            ),
            patch("registrarmonitor.services.website_service.update_checksum"),
            patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path),
        ):
            service = WebsiteService()
            out_path, size = service.generate_semester_page("Spring 2024")

        assert out_path is not None
        assert (tmp_path / "spring2024.html").exists()
        assert (tmp_path / "spring2024.json").exists()


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
            '<script src="assets/main-old.js"></script><link href="assets/main-old.css">'
        )

        service = WebsiteService()
        with patch.object(service, "website_assets_dir", tmp_path):
            result = service._patch_asset_hashes_in_html()

        assert result is True
        assert "main-abc.js" in html_file.read_text()
        assert "main-style.css" in html_file.read_text()
        assert "main-old.js" not in html_file.read_text()


class TestGenerate:
    def test_skips_when_not_active(self):
        service = WebsiteService()
        with patch.object(service, "is_any_semester_active", return_value=False):
            result = service.generate(force=False)

        assert result is True
        assert service.last_generation_skipped is True

    def test_force_overrides_inactive(self):
        service = WebsiteService()
        with (
            patch.object(service, "is_any_semester_active", return_value=False),
            patch.object(service, "build_frontend_assets", return_value=True),
            patch(
                "registrarmonitor.website.checksums.get_semesters_needing_update",
                return_value=[],
            ),
            patch.object(service, "generate_semester_page", return_value=(None, 0.0)),
            patch(
                "registrarmonitor.website.templates.build_redirect_index",
                return_value="<html>redirect</html>",
            ),
            patch("registrarmonitor.services.website_service.OUTPUT_DIR") as mock_dir,
        ):
            mock_dir.__truediv__.return_value.write_text = MagicMock()
            mock_dir.mkdir = MagicMock()
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
            patch("registrarmonitor.website.config.ALL_SEMESTERS", ["Spring 2024"]),
        ):
            service = WebsiteService()
            assert service.is_any_semester_active() is True
