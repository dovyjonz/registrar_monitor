"""Tests for typed settings."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

import registrarmonitor.config as config_mod

pytestmark = pytest.mark.unit

MINIMAL_TOML = """
[directories]
text_reports = "reports"
data_storage = "data"
raw_downloads = "raw"
logs = "logs"
telegram_bot_state = "data/bot.db"

[website]
preview_base_url = "https://preview.example"
"""


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_BOT_TEST_USER_ID",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def toml_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.toml"
    path.write_text(MINIMAL_TOML)
    monkeypatch.setattr(config_mod, "SETTINGS_PATH", path)
    return path


def load():
    return config_mod.Settings()


def test_repository_settings_are_valid():
    settings = load()
    cfg = settings.as_config()
    assert "ca_bundle" in cfg["data_source"]
    for key, value in cfg["directories"].items():
        assert Path(value).is_absolute(), key


def test_get_config_is_cached_dict():
    assert config_mod.get_config() is config_mod.get_config()
    assert "telegram" not in config_mod.get_config()


def test_relative_paths_resolve_against_project_root(toml_file):
    settings = load()
    assert settings.directories.data_storage == str(
        (config_mod.ROOT / "data").resolve()
    )
    assert settings.data_source.ca_bundle is None


def test_ca_bundle_is_resolved(toml_file):
    toml_file.write_text(MINIMAL_TOML + '\n[data_source]\nca_bundle = "certs/x.pem"\n')
    assert load().data_source.ca_bundle == str(config_mod.ROOT / "certs" / "x.pem")


def test_secrets_are_secretstr_and_never_in_config_dict(toml_file, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-123")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-secret")
    settings = load()

    assert isinstance(settings.telegram_bot_token, SecretStr)
    assert settings.telegram_bot_token.get_secret_value() == "tok-123"
    assert "tok-123" not in repr(settings)
    assert "cf-secret" not in repr(settings)
    assert "tok-123" not in str(settings.as_config())


def test_blank_secret_is_unset(toml_file, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "  ")
    assert load().telegram_bot_token is None


def test_test_user_id_is_positive_integer(toml_file, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TEST_USER_ID", "41")
    assert load().telegram_bot_test_user_id == 41


@pytest.mark.parametrize("value", ["invalid", "0", "-1"])
def test_invalid_test_user_id_fails_closed(toml_file, monkeypatch, value):
    monkeypatch.setenv("TELEGRAM_BOT_TEST_USER_ID", value)
    with pytest.raises(ValidationError, match="TELEGRAM_BOT_TEST_USER_ID"):
        load()


def test_invalid_milestone_time_fails_fast(toml_file):
    toml_file.write_text(
        MINIMAL_TOML + '\n[semesters."Fall 2026"]\ndeadlines = [["soon", "Drop"]]\n'
    )
    with pytest.raises(ValidationError):
        load()


def test_unknown_section_key_fails_fast(toml_file):
    toml_file.write_text(MINIMAL_TOML + '\n[data_source]\nca_bundel = "x.pem"\n')
    with pytest.raises(ValidationError):
        load()


def test_missing_toml_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "SETTINGS_PATH", tmp_path / "settings.toml")
    with pytest.raises(FileNotFoundError, match="settings.toml"):
        load()
