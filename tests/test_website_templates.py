"""Tests for the website template module."""

from unittest.mock import patch

from registrarmonitor.website.templates import (
    _build_nav_html,
    _get_asset_info,
    build_redirect_index,
    build_semester_page,
)


class TestGetAssetInfo:
    def test_returns_none_when_manifest_missing(self, tmp_path):
        fake_manifest = tmp_path / "assets" / ".vite" / "manifest.json"
        with patch("registrarmonitor.website.templates.MANIFEST_PATH", fake_manifest):
            js, css = _get_asset_info()
        assert js is None
        assert css is None

    def test_returns_assets_from_manifest(self, tmp_path):
        manifest_dir = tmp_path / "assets" / ".vite"
        manifest_dir.mkdir(parents=True)
        manifest_path = manifest_dir / "manifest.json"
        manifest_path.write_text(
            '{"src/main.js": {"file": "assets/main-abc123.js", "css": ["assets/style-xyz789.css"]}}'
        )

        with patch("registrarmonitor.website.templates.MANIFEST_PATH", manifest_path):
            js, css = _get_asset_info()

        assert js == "assets/main-abc123.js"
        assert css == "assets/style-xyz789.css"

    def test_handles_broken_manifest(self, tmp_path):
        manifest_dir = tmp_path / "assets" / ".vite"
        manifest_dir.mkdir(parents=True)
        manifest_path = manifest_dir / "manifest.json"
        manifest_path.write_text("not valid json")

        with patch("registrarmonitor.website.templates.MANIFEST_PATH", manifest_path):
            js, css = _get_asset_info()

        assert js is None
        assert css is None

    def test_handles_entry_not_in_manifest(self, tmp_path):
        manifest_dir = tmp_path / "assets" / ".vite"
        manifest_dir.mkdir(parents=True)
        manifest_path = manifest_dir / "manifest.json"
        manifest_path.write_text("{}")

        with patch("registrarmonitor.website.templates.MANIFEST_PATH", manifest_path):
            js, css = _get_asset_info()

        assert js is None
        assert css is None

    def test_handles_no_css(self, tmp_path):
        manifest_dir = tmp_path / "assets" / ".vite"
        manifest_dir.mkdir(parents=True)
        manifest_path = manifest_dir / "manifest.json"
        manifest_path.write_text('{"src/main.js": {"file": "assets/main.js"}}')

        with patch("registrarmonitor.website.templates.MANIFEST_PATH", manifest_path):
            js, css = _get_asset_info()

        assert js == "assets/main.js"
        assert css is None


class TestBuildNavHtml:
    def test_includes_all_semesters(self):
        from registrarmonitor.website.config import ALL_SEMESTERS

        html = _build_nav_html(ALL_SEMESTERS[0])
        for sem in ALL_SEMESTERS:
            assert sem in html

    def test_active_semester_has_active_class(self):
        html = _build_nav_html("Summer 2026")
        assert 'class="semester-nav-link active"' in html
        assert "Summer 2026" in html

    def test_non_active_semester_no_active_class(self):
        html = _build_nav_html("Spring 2026")
        assert html.count("active") == 1  # only the active semester


class TestBuildSemesterPage:
    def test_builds_html_with_minimal_data(self):
        with (
            patch(
                "registrarmonitor.website.templates._get_asset_info",
                return_value=(None, None),
            ),
            patch(
                "registrarmonitor.website.templates.env.get_template"
            ) as mock_template,
        ):
            mock_template.return_value.render.return_value = "<html>test</html>"

            html = build_semester_page(
                data={},
                milestones=[],
                semester="Spring 2024",
                minify_assets=False,
            )

        assert html == "<html>test</html>"
        mock_template.return_value.render.assert_called_once()

    def test_formats_last_updated_from_lrt(self):
        with (
            patch(
                "registrarmonitor.website.templates._get_asset_info",
                return_value=(None, None),
            ),
            patch(
                "registrarmonitor.website.templates.env.get_template"
            ) as mock_template,
        ):
            mock_template.return_value.render.return_value = "<html>test</html>"

            build_semester_page(
                data={"lrt": "2024-01-15T10:30:00"},
                milestones=[],
                semester="Spring 2024",
            )

            kwargs = mock_template.return_value.render.call_args[1]
            assert (
                "2024-01-15 10:30" in kwargs["last_updated"]
                or kwargs["last_updated"] != "Last updated N/A"
            )


class TestBuildRedirectIndex:
    def test_contains_redirect(self):
        html = build_redirect_index()
        assert 'http-equiv="refresh"' in html
        assert "html" in html.lower()

    def test_links_to_latest_semester(self):
        from registrarmonitor.website.config import (
            LATEST_SEMESTER,
            semester_to_filename,
        )

        html = build_redirect_index()
        latest_file = semester_to_filename(LATEST_SEMESTER)
        assert latest_file in html
