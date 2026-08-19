"""Tests for the website service."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.services.website_service import WebsiteService


@pytest.fixture(autouse=True)
def publishable_catalog():
    with patch(
        "registrarmonitor.services.website_service.build_publication_catalog",
        return_value=[SimpleNamespace(label="Summer 2026")],
    ):
        yield


def test_isolated_generation_leaves_default_private_artifacts_untouched(
    tmp_path: Path,
) -> None:
    default_output = tmp_path / "default-public"
    isolated_output = tmp_path / "isolated-public"
    protected = default_output / "data" / "enrollment_summer_2025.db"
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"private-database-sentinel")
    before = protected.read_bytes()
    service = WebsiteService(output_dir=isolated_output)

    def generate_semester(*_args, **_kwargs):
        page = isolated_output / "summer2025.html"
        page.write_text("<html></html>")
        return page, 1.0

    with (
        patch("registrarmonitor.services.website_service.OUTPUT_DIR", default_output),
        patch.object(service, "is_any_semester_active", return_value=True),
        patch.object(service, "build_frontend_assets", return_value=True),
        patch.object(service, "generate_semester_page", side_effect=generate_semester),
        patch(
            "registrarmonitor.services.website_service.get_semesters_needing_update",
            return_value=["Summer 2025"],
        ),
    ):
        assert service.generate(force=True) is True
        assert service.deploy() is False

    assert (isolated_output / "index.html").is_file()
    assert (isolated_output / "_headers").is_file()
    assert (isolated_output / "robots.txt").is_file()
    assert protected.read_bytes() == before


def test_build_frontend_assets_installs_when_lockfile_is_newer(tmp_path):
    service = WebsiteService()
    service.website_assets_dir = tmp_path

    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    installed_lock = node_modules / ".package-lock.json"
    package_lock = tmp_path / "package-lock.json"
    package_json = tmp_path / "package.json"

    installed_lock.write_text("{}")
    package_lock.write_text("{}")
    package_json.write_text("{}")

    os.utime(installed_lock, (100, 100))
    os.utime(package_lock, (200, 200))
    os.utime(package_json, (200, 200))

    with (
        patch("subprocess.run") as run,
        patch.object(service, "_patch_asset_hashes_in_html", return_value=True),
    ):
        assert service.build_frontend_assets() is True

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [["npm", "install"], ["npm", "run", "build"]]
    assert all(call.kwargs["cwd"] == tmp_path for call in run.call_args_list)
    assert all(call.kwargs["check"] is True for call in run.call_args_list)


def test_build_frontend_assets_installs_when_declared_dependency_is_missing(tmp_path):
    service = WebsiteService()
    service.website_assets_dir = tmp_path

    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    installed_lock = node_modules / ".package-lock.json"
    package_lock = tmp_path / "package-lock.json"
    package_json = tmp_path / "package.json"

    installed_lock.write_text("{}")
    package_lock.write_text("{}")
    package_json.write_text('{"dependencies":{"chartjs-plugin-zoom":"^2.2.0"}}')

    os.utime(installed_lock, (300, 300))
    os.utime(package_lock, (200, 200))
    os.utime(package_json, (200, 200))

    with (
        patch("subprocess.run") as run,
        patch.object(service, "_patch_asset_hashes_in_html", return_value=True),
    ):
        assert service.build_frontend_assets() is True

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [["npm", "install"], ["npm", "run", "build"]]


def test_build_frontend_assets_skips_install_when_dependencies_are_current(tmp_path):
    service = WebsiteService()
    service.website_assets_dir = tmp_path

    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "chartjs-plugin-zoom").mkdir()
    installed_lock = node_modules / ".package-lock.json"
    package_lock = tmp_path / "package-lock.json"
    package_json = tmp_path / "package.json"

    installed_lock.write_text("{}")
    package_lock.write_text("{}")
    package_json.write_text('{"dependencies":{"chartjs-plugin-zoom":"^2.2.0"}}')

    os.utime(installed_lock, (300, 300))
    os.utime(package_lock, (200, 200))
    os.utime(package_json, (200, 200))

    with (
        patch("subprocess.run") as run,
        patch.object(service, "_patch_asset_hashes_in_html", return_value=True),
    ):
        assert service.build_frontend_assets() is True

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [["npm", "run", "build"]]


def test_build_frontend_assets_returns_false_when_install_fails(tmp_path):
    service = WebsiteService()
    service.website_assets_dir = tmp_path

    package_lock = tmp_path / "package-lock.json"
    package_json = tmp_path / "package.json"
    package_lock.write_text("{}")
    package_json.write_text("{}")

    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["npm", "install"]),
    ):
        assert service.build_frontend_assets() is False


def test_build_frontend_assets_returns_false_when_asset_patch_fails(tmp_path):
    service = WebsiteService()
    service.website_assets_dir = tmp_path

    with (
        patch.object(
            service, "_frontend_dependencies_need_install", return_value=False
        ),
        patch("subprocess.run"),
        patch.object(service, "_patch_asset_hashes_in_html", return_value=False),
        patch.object(service, "_validate_asset_references_in_html") as validate,
    ):
        assert service.build_frontend_assets() is False

    validate.assert_not_called()


def test_generate_fails_when_frontend_build_fails(tmp_path):
    service = WebsiteService()
    service.website_assets_dir = tmp_path

    with (
        patch.object(service, "is_any_semester_active", return_value=True),
        patch.object(service, "build_frontend_assets", return_value=False),
    ):
        assert service.generate(force=True) is False


def test_deploy_command_refuses_deploy_after_skipped_generation():
    from registrarmonitor.cli.commands import DeployCommand

    with patch("registrarmonitor.cli.commands.WebsiteService") as service_cls:
        service = service_cls.return_value
        service.generate.return_value = True
        service.last_generation_skipped = True

        assert DeployCommand().run(deploy=True) is False

    service.deploy.assert_not_called()


def test_deploy_command_generates_local_prototype_without_deploying():
    from registrarmonitor.cli.commands import DeployCommand

    with patch("registrarmonitor.cli.commands.WebsiteService") as service_cls:
        service = service_cls.return_value
        service.generate_prototype.return_value = True

        assert DeployCommand().run(prototype=True, semester="summer2026") is True

    service.generate_prototype.assert_called_once_with(semester_key="summer2026")
    service.generate.assert_not_called()
    service.deploy.assert_not_called()


def test_deploy_command_refuses_to_deploy_local_prototype():
    from registrarmonitor.cli.commands import DeployCommand

    with patch("registrarmonitor.cli.commands.WebsiteService") as service_cls:
        service = service_cls.return_value

        assert DeployCommand().run(prototype=True, deploy=True) is False

    service.generate_prototype.assert_not_called()
    service.generate.assert_not_called()
    service.deploy.assert_not_called()


def test_deploy_missing_cloudflare_api_token_skips_wrangler(tmp_path, monkeypatch):
    service = WebsiteService()
    service.website_assets_dir = tmp_path
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)

    with (
        patch.object(service, "validate_public_output", return_value=[]),
        patch("subprocess.run") as run,
    ):
        assert service.deploy(project_name="registrar-monitor") is False

    run.assert_not_called()


def test_deploy_missing_cloudflare_account_id_skips_wrangler(tmp_path, monkeypatch):
    service = WebsiteService()
    service.website_assets_dir = tmp_path
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)

    with (
        patch.object(service, "validate_public_output", return_value=[]),
        patch("subprocess.run") as run,
    ):
        assert service.deploy(project_name="registrar-monitor") is False

    run.assert_not_called()


def test_deploy_rejects_missing_frontend_assets(tmp_path, monkeypatch, capsys):
    service = WebsiteService()
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    (tmp_path / "assets").mkdir()
    (tmp_path / "semesters" / "fall-2026").mkdir(parents=True)
    (tmp_path / "semesters" / "fall-2026" / "index.html").write_text(
        '<link rel="stylesheet" href="/assets/main-missing.css">'
        '<script type="module" src="/assets/main-missing.js"></script>'
    )

    with (
        patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path),
        patch("subprocess.run") as run,
    ):
        assert service.deploy(project_name="registrar-monitor") is False

    run.assert_not_called()
    output = capsys.readouterr().out
    assert "Missing frontend asset referenced by" in output
    assert "/assets/main-missing.css" in output
    assert "/assets/main-missing.js" in output


def test_wrangler_pages_deploy_does_not_use_unsupported_minify_flag(
    tmp_path, monkeypatch
):
    service = WebsiteService()
    service.website_assets_dir = tmp_path
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")

    with (
        patch.object(service, "validate_public_output", return_value=[]),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        assert service.deploy(project_name="registrar-monitor") is True

    assert "--minify" not in run.call_args.args[0]


def test_wrangler_pages_deploy_prints_failure_output(tmp_path, capsys, monkeypatch):
    service = WebsiteService()
    service.website_assets_dir = tmp_path
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")

    with (
        patch.object(service, "validate_public_output", return_value=[]),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 1
        assert service.deploy(project_name="registrar-monitor") is False

    output = capsys.readouterr().out
    assert "Deployment failed with exit code: 1" in output
    assert "capture_output" not in run.call_args.kwargs
    assert "stdout" not in run.call_args.kwargs
    assert "stderr" not in run.call_args.kwargs
    assert run.call_args.kwargs["timeout"] == 900


def test_wrangler_pages_deploy_invokes_expected_command(tmp_path, monkeypatch):
    service = WebsiteService()
    service.website_assets_dir = tmp_path
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")

    with (
        patch.object(service, "validate_public_output", return_value=[]),
        patch("subprocess.run") as run,
    ):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        assert service.deploy(project_name="registrar-monitor") is True

    assert run.call_args.args[0] == [
        "npx",
        "wrangler",
        "pages",
        "deploy",
        "public",
        "--project-name",
        "registrar-monitor",
    ]


def test_wrangler_pages_deploy_rejects_private_output(tmp_path, monkeypatch):
    service = WebsiteService()
    service.website_assets_dir = tmp_path
    public = tmp_path / "public"
    private_file = public / "courses" / "summer-2026" / "registrar.db"
    private_file.parent.mkdir(parents=True)
    private_file.write_text("")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")

    with (
        patch("registrarmonitor.services.website_service.OUTPUT_DIR", public),
        patch("subprocess.run") as run,
    ):
        assert service.deploy(project_name="registrar-monitor") is False

    run.assert_not_called()


def test_generate_headers_contains_security_headers(tmp_path):
    """_headers should include X-Frame-Options, X-Content-Type-Options, etc."""
    service = WebsiteService()
    with (
        patch.object(service, "is_any_semester_active", return_value=True),
        patch.object(service, "build_frontend_assets", return_value=True),
        patch(
            "registrarmonitor.services.website_service.get_semesters_needing_update",
            return_value=[],
        ),
        patch(
            "registrarmonitor.website.templates.build_redirect_index",
            return_value="<html>redirect</html>",
        ),
        patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path),
        patch.object(service, "validate_public_output", return_value=[]),
    ):
        tmp_path.mkdir(exist_ok=True)
        service.generate(force=True)

    headers_path = tmp_path / "_headers"
    assert headers_path.exists()
    content = headers_path.read_text()
    assert "X-Frame-Options: DENY" in content
    assert "X-Content-Type-Options: nosniff" in content
    assert "Referrer-Policy:" in content
    assert "Permissions-Policy:" in content
    assert "Content-Security-Policy:" in content
    assert "script-src 'self'" in content
    assert "https://static.cloudflareinsights.com" in content
    assert "connect-src 'self' https://cloudflareinsights.com" in content
    assert "X-Robots-Tag:" in content
    assert "/courses/*\n  Cache-Control: no-cache" in content
    assert (
        "/data/previews/*\n  Cache-Control: public, max-age=31536000, immutable"
        in content
    )


def test_generate_creates_robots_txt(tmp_path):
    """generate() should let crawlers retrieve the noindexed pages."""
    service = WebsiteService()
    with (
        patch.object(service, "is_any_semester_active", return_value=True),
        patch.object(service, "build_frontend_assets", return_value=True),
        patch(
            "registrarmonitor.services.website_service.get_semesters_needing_update",
            return_value=[],
        ),
        patch(
            "registrarmonitor.website.templates.build_redirect_index",
            return_value="<html>redirect</html>",
        ),
        patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path),
        patch.object(service, "validate_public_output", return_value=[]),
    ):
        tmp_path.mkdir(exist_ok=True)
        service.generate(force=True)

    robots_path = tmp_path / "robots.txt"
    assert robots_path.exists()
    content = robots_path.read_text()
    assert "Allow: /" in content


def test_generate_always_excludes_pages_from_search_results():
    service = WebsiteService()
    assert "X-Robots-Tag: noindex, nofollow" in service._build_headers_content()
    assert "Allow: /" in service._build_robots_content()


def test_generate_fails_when_public_validation_has_issues(tmp_path):
    service = WebsiteService()

    with (
        patch.object(service, "is_any_semester_active", return_value=True),
        patch.object(service, "build_frontend_assets", return_value=True),
        patch(
            "registrarmonitor.services.website_service.get_semesters_needing_update",
            return_value=[],
        ),
        patch(
            "registrarmonitor.website.templates.build_redirect_index",
            return_value="<html>redirect</html>",
        ),
        patch("registrarmonitor.services.website_service.OUTPUT_DIR", tmp_path),
        patch.object(
            service,
            "validate_public_output",
            return_value=["Unexpected directory: data/"],
        ),
    ):
        tmp_path.mkdir(exist_ok=True)
        assert service.generate(force=True) is False


def test_generation_reuses_semester_data_while_publishing_clean_routes(tmp_path):
    service = WebsiteService(output_dir=tmp_path)
    semester_data = {"cr": {}, "sn": []}

    with (
        patch.object(service, "is_any_semester_active", return_value=True),
        patch.object(service, "build_frontend_assets", return_value=True),
        patch(
            "registrarmonitor.services.website_service.get_semesters_needing_update",
            return_value=["Summer 2026"],
        ),
        patch(
            "registrarmonitor.services.website_service.get_semester_data",
            return_value=semester_data,
        ) as get_data,
        patch.object(
            service,
            "generate_semester_page",
            wraps=service.generate_semester_page,
        ),
        patch("registrarmonitor.services.website_service.update_checksum"),
        patch(
            "registrarmonitor.services.website_service.build_semester_page",
            return_value="<html></html>",
        ),
        patch.object(service, "validate_public_output", return_value=[]),
    ):
        assert service.generate(force=True) is True

    get_data.assert_called_once_with("Summer 2026", minify=True, archive_window=False)
    assert (tmp_path / "semesters" / "summer-2026" / "index.html").is_file()
