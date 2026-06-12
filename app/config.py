"""Application configuration loaded from environment variables."""
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central, typed application settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Multi-Agent Task Manager"
    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./tasks.db"

    telegram_bot_token: Optional[str] = None
    telegram_allowed_chat_id: Optional[str] = None

    gemini_api_key: Optional[str] = None
    gemini_model: str = "gemini-2.5-flash"

    daily_reminder_hour: int = 8
    daily_reminder_minute: int = 30
    timezone: str = "UTC"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
