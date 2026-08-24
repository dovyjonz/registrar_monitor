import os
from typing import Any
from zoneinfo import ZoneInfo

import toml
from dotenv import load_dotenv


class Config:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            try:
                cls._instance.load_config()
            except Exception:
                cls._instance = None
                raise
        return cls._instance

    def load_config(self):
        # Load environment variables from .env file
        load_dotenv()

        try:
            from pathlib import Path

            # Path to the root directory where settings.toml lives
            root_dir = Path(__file__).parent.parent.parent
            settings_path = root_dir / "settings.toml"

            with open(settings_path) as f:
                self.config = toml.load(f)

            # Make all directory paths absolute relative to the project root
            if "directories" in self.config:
                for key, val in self.config["directories"].items():
                    # If it's a relative path, resolve it against root_dir
                    path_obj = Path(val)
                    if not path_obj.is_absolute():
                        self.config["directories"][key] = str(
                            (root_dir / val).resolve()
                        )
        except FileNotFoundError:
            raise Exception(
                f"Configuration file 'settings.toml' not found at {settings_path}"
            )

        # Initialize telegram config from environment variables
        # This allows keeping secrets out of version control via .env file
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")

        if bot_token or chat_id:
            # Create telegram section if it doesn't exist
            if "telegram" not in self.config:
                self.config["telegram"] = {}

            if bot_token:
                self.config["telegram"]["bot_token"] = bot_token
            if chat_id:
                self.config["telegram"]["chat_id"] = chat_id

        test_user = os.environ.get("TELEGRAM_BOT_TEST_USER_ID")
        if test_user:
            try:
                test_user_id = int(test_user)
            except ValueError as error:
                raise ValueError(
                    "TELEGRAM_BOT_TEST_USER_ID must be a positive integer"
                ) from error
            if test_user_id <= 0:
                raise ValueError("TELEGRAM_BOT_TEST_USER_ID must be a positive integer")
            self.config.setdefault("telegram_bot", {})["test_user_id"] = test_user_id

    def get_config(self) -> dict[str, Any]:
        return self.config


def get_config() -> dict[str, Any]:
    return Config().get_config()


def get_timezone(config: dict[str, Any] | None = None) -> ZoneInfo:
    """Return the registrar timezone configured in settings.toml."""
    cfg = config if config is not None else get_config()
    return ZoneInfo(cfg.get("timezone", "Asia/Almaty"))
