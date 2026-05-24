from __future__ import annotations

import os
import subprocess
from unittest.mock import patch

from registrarmonitor.services.website_service import WebsiteService


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
