import os
import toml
from typing import Any

from dotenv import load_dotenv


class Config:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance.load_config()
        return cls._instance

    def load_config(self):
        # Load environment variables from .env file
        load_dotenv()

        try:
            with open("settings.toml", "r") as f:
                self.config = toml.load(f)
        except FileNotFoundError:
            raise Exception("Configuration file 'settings.toml' not found.")

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

    def get_config(self) -> dict[str, Any]:
        return self.config


def get_config() -> dict[str, Any]:
    return Config().get_config()
