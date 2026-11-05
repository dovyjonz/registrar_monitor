"""Typed application settings.

``settings.toml`` is the source of truth for semesters, scheduling, notification
and website behavior. Secrets come only from the environment or ``.env`` and are
held as ``SecretStr``. Both are loaded and validated once; invalid
configuration fails at startup.

Call sites read typed values from :func:`get_settings`.
"""

import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    SecretStr,
    field_validator,
)
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

ROOT = Path(__file__).resolve().parent.parent.parent
SETTINGS_PATH = ROOT / "settings.toml"
ENV_PATH = ROOT / ".env"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class Directories(_Section):
    text_reports: str
    data_storage: str
    raw_downloads: str
    logs: str
    telegram_bot_state: str

    @field_validator("*")
    @classmethod
    def _absolute(cls, value: str) -> str:
        path = Path(value)
        return str(path if path.is_absolute() else (ROOT / path).resolve())


class DataSource(_Section):
    # PEM file with the registrar's intermediate and root certificates.
    # Unset means default system verification; verification is never disabled.
    ca_bundle: str | None = None

    @field_validator("ca_bundle")
    @classmethod
    def _bundle_path(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        path = Path(value.strip())
        return str(path if path.is_absolute() else (ROOT / path).resolve())


class Notifications(_Section):
    dry_run: bool = False


class Website(_Section):
    generation_timeout_seconds: PositiveInt = 3600
    # Hard memory cap (systemd MemoryMax syntax) for the site build and deploy
    # children, so an out-of-memory kill takes the build and not the poller.
    build_memory_max: str = "600M"
    base_url: str
    web_analytics_token: str = ""
    # Public https URL of the bot's Telegram Mini App API (Cloudflare Tunnel
    # hostname plus /miniapp). Empty turns the Mini App off.
    miniapp_api_url: str = ""

    @field_validator("build_memory_max")
    @classmethod
    def _memory_max(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[1-9][0-9]*[KMGT]?", value):
            raise ValueError("must be a size such as 600M or 1G")
        return value

    @field_validator("base_url")
    @classmethod
    def _origin(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            raise ValueError("website.base_url must be configured")
        return value

    @field_validator("miniapp_api_url")
    @classmethod
    def _miniapp_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value:
            parts = urlsplit(value)
            if parts.scheme != "https" or not parts.hostname or parts.query:
                raise ValueError("website.miniapp_api_url must be a public https URL")
        return value

    @field_validator("web_analytics_token")
    @classmethod
    def _analytics_token(cls, value: str) -> str:
        value = value.strip()
        if value and not re.fullmatch(r"[0-9a-fA-F]{32}", value):
            raise ValueError("must be 32 hexadecimal characters")
        return value


class TelegramBot(_Section):
    retry_base_seconds: float = 5
    retry_max_seconds: float = 900
    per_chat_interval_seconds: float = 1
    global_rate_per_second: float = Field(default=25, gt=0)
    cleanup_interval_seconds: float = 3600
    completed_batch_days: int = 90
    inactive_user_days: int = 180
    # Local webhook listener; cloudflared forwards the public hostname here.
    webhook_listen: str = "127.0.0.1"
    webhook_port: PositiveInt = 8443


class Semester(_Section):
    registrar_url: str | None = None
    deadlines: list[tuple[str, str]] = Field(default_factory=list)
    priorities: dict[str, list[tuple[str, str]]] = Field(default_factory=dict)

    @field_validator("deadlines")
    @classmethod
    def _valid_deadlines(cls, value: list[tuple[str, str]]):
        for when, _label in value:
            datetime.fromisoformat(when)
        return value

    @field_validator("priorities")
    @classmethod
    def _valid_priorities(cls, value: dict[str, list[tuple[str, str]]]):
        for milestones in value.values():
            for when, _label in milestones:
                datetime.fromisoformat(when)
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_nested_delimiter="__")

    timezone: str = "Asia/Almaty"
    directories: Directories
    data_source: DataSource = DataSource()
    notifications: Notifications = Notifications()
    website: Website
    telegram_bot: TelegramBot = TelegramBot()
    semesters: dict[str, Semester] = Field(default_factory=dict)

    # Secrets and operator ids: environment or .env only.
    telegram_bot_token: SecretStr | None = Field(
        default=None, validation_alias="TELEGRAM_BOT_TOKEN"
    )
    telegram_chat_id: SecretStr | None = Field(
        default=None, validation_alias="TELEGRAM_CHAT_ID"
    )
    telegram_bot_test_user_id: PositiveInt | None = Field(
        default=None, validation_alias="TELEGRAM_BOT_TEST_USER_ID"
    )
    telegram_webhook_url: str | None = Field(
        default=None, validation_alias="TELEGRAM_WEBHOOK_URL"
    )
    telegram_webhook_secret: SecretStr | None = Field(
        default=None, validation_alias="TELEGRAM_WEBHOOK_SECRET"
    )
    cloudflare_api_token: SecretStr | None = Field(
        default=None, validation_alias="CLOUDFLARE_API_TOKEN"
    )
    cloudflare_account_id: SecretStr | None = Field(
        default=None, validation_alias="CLOUDFLARE_ACCOUNT_ID"
    )

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator(
        "telegram_bot_token",
        "telegram_chat_id",
        "telegram_bot_test_user_id",
        "telegram_webhook_url",
        "telegram_webhook_secret",
        "cloudflare_api_token",
        "cloudflare_account_id",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # replaced: ENV_PATH is patchable
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        if not SETTINGS_PATH.is_file():
            raise FileNotFoundError(
                f"Configuration file 'settings.toml' not found at {SETTINGS_PATH}"
            )
        return (
            init_settings,
            env_settings,
            DotEnvSettingsSource(settings_cls, env_file=ENV_PATH),
            TomlConfigSettingsSource(settings_cls, toml_file=SETTINGS_PATH),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once per process."""
    return Settings()


def get_timezone(settings: Settings | None = None) -> ZoneInfo:
    """Return the registrar timezone configured in settings.toml."""
    return ZoneInfo((settings if settings is not None else get_settings()).timezone)
