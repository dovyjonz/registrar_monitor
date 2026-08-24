"""Tests for the config module."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


class TestConfigSingleton:
    """Tests for Config singleton behavior."""

    def test_returns_same_instance(self):
        """Config should return the same instance on repeated calls."""
        import registrarmonitor.config as config_mod

        config_mod.Config._instance = None

        a = config_mod.Config()
        b = config_mod.Config()

        assert a is b

    def test_get_config_returns_dict(self):
        """get_config should return a dict with expected keys."""
        import registrarmonitor.config as config_mod

        config_mod.Config._instance = None

        cfg = config_mod.Config().get_config()

        assert isinstance(cfg, dict)
        assert "data_source" in cfg
        assert "directories" in cfg


class TestConfigPathResolution:
    """Tests for relative path resolution in directories."""

    def test_relative_paths_resolved_to_absolute(self):
        """Relative directory paths should be resolved against project root."""
        import registrarmonitor.config as config_mod

        config_mod.Config._instance = None

        cfg = config_mod.Config().get_config()

        for key, val in cfg.get("directories", {}).items():
            path = Path(val)
            assert path.is_absolute(), f"directories.{key} is not absolute: {val}"


class TestTelegramEnvVars:
    """Tests for telegram config from environment variables."""

    def test_env_vars_override_toml(self, monkeypatch):
        """Telegram env vars should be merged into config."""
        import registrarmonitor.config as config_mod

        config_mod.Config._instance = None
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token_123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "test_chat_456")

        cfg = config_mod.Config().get_config()

        assert cfg["telegram"]["bot_token"] == "test_token_123"
        assert cfg["telegram"]["chat_id"] == "test_chat_456"

    def test_partial_env_vars(self, monkeypatch):
        """Only set env vars should override; unset ones keep TOML values."""
        import registrarmonitor.config as config_mod

        config_mod.Config._instance = None
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "only_token")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

        cfg = config_mod.Config().get_config()

        assert cfg["telegram"]["bot_token"] == "only_token"

    def test_developer_test_user_is_loaded_as_positive_integer(self, monkeypatch):
        """A developer ID should configure private diagnostics access."""
        import registrarmonitor.config as config_mod

        config_mod.Config._instance = None
        monkeypatch.setenv("TELEGRAM_BOT_TEST_USER_ID", "41")

        cfg = config_mod.Config().get_config()

        assert cfg["telegram_bot"]["test_user_id"] == 41

    @pytest.mark.parametrize("value", ["invalid", "0", "-1"])
    def test_invalid_developer_test_user_fails_closed(self, monkeypatch, value):
        """Malformed test-mode configuration must not start a public bot."""
        import registrarmonitor.config as config_mod

        config_mod.Config._instance = None
        monkeypatch.setenv("TELEGRAM_BOT_TEST_USER_ID", value)

        with pytest.raises(ValueError, match="TELEGRAM_BOT_TEST_USER_ID"):
            config_mod.Config()


class TestConfigMissingFile:
    """Tests for missing settings.toml handling."""

    def test_missing_toml_raises(self, monkeypatch):
        """Config should raise when settings.toml cannot be found."""
        import registrarmonitor.config as config_mod

        config_mod.Config._instance = None

        # Patch open to simulate missing file for settings.toml
        real_open = open

        def patched_open(path, *args, **kwargs):
            if "settings.toml" in str(path):
                raise FileNotFoundError(f"No such file: {path}")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", patched_open)

        with pytest.raises(Exception, match="settings.toml"):
            config_mod.Config()
