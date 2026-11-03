"""Typed application settings.

``settings.toml`` is the source of truth for semesters, scheduling, notification
and website behavior. Secrets come only from the environment or ``.env`` and are
held as ``SecretStr``. Both are loaded and validated once; invalid
configuration fails at startup.

Most call sites read the plain dictionary from :func:`get_config` (secrets are
never part of it). Code that needs a secret or a typed value calls
:func:`get_settings`.
"""

from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
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

# Env-only values that must never appear in the dictionary view.
_SECRET_FIELDS = {
    "telegram_bot_token",
    "telegram_chat_id",
    "telegram_bot_test_user_id",
    "cloudflare_api_token",
    "cloudflare_account_id",
}


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class Directories(_Section):
    text_reports: str
    data_storage: str
    raw_downloads: str
    input_files: str
    logs: str
    telegram_bot_state: str
    pdf_output: str = "assets/pdf"

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
    file_write_delay: float = 3
    dry_run: bool = False


class Website(_Section):
    update_interval: PositiveInt = 30
    generation_timeout_seconds: PositiveInt = 3600
    pages_project_name: str = "registrar-monitor"
    base_url: str = ""
    preview_base_url: str


class StorageSemester(_Section):
    metadata_mode: Literal["raw-enriched", "legacy-preserving"]
    mode: Literal["legacy", "shadow", "v2", "finalized"]


class Storage(_Section):
    migration_order: list[str] = Field(default_factory=list)
    semesters: dict[str, StorageSemester] = Field(default_factory=dict)


class TelegramBot(_Section):
    retry_base_seconds: float = 5
    retry_max_seconds: float = 900
    per_chat_interval_seconds: float = 1
    global_rate_per_second: float = Field(default=25, gt=0)
    cleanup_interval_seconds: float = 3600
    completed_batch_days: int = 90
    inactive_user_days: int = 180


class HealthMonitor(_Section):
    interval_seconds: PositiveInt = 30
    unhealthy_checks_before_alert: PositiveInt = 2


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
    model_config = SettingsConfigDict(extra="ignore")

    timezone: str = "Asia/Almaty"
    directories: Directories
    data_source: DataSource = DataSource()
    notifications: Notifications = Notifications()
    website: Website
    storage: Storage = Storage()
    telegram_bot: TelegramBot = TelegramBot()
    health_monitor: HealthMonitor = HealthMonitor()
    semesters: dict[str, Semester] = Field(default_factory=dict)
    pdf_settings: dict[str, Any] = Field(default_factory=dict)

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

    def as_config(self) -> dict[str, Any]:
        """Plain dictionary of the non-secret settings."""
        return self.model_dump(exclude=_SECRET_FIELDS)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate settings once per process."""
    return Settings()


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    """Return the non-secret settings as a dictionary."""
    return get_settings().as_config()


def get_timezone(config: dict[str, Any] | None = None) -> ZoneInfo:
    """Return the registrar timezone configured in settings.toml."""
    cfg = config if config is not None else get_config()
    return ZoneInfo(cfg.get("timezone", "Asia/Almaty"))
