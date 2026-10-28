from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "Agentic Workflow for Literature Synthesis"
    app_version: str = "0.1.0"
    environment: Literal["development",
                         "testing", "production"] = "development"
    api_v1_prefix: str = "/api/v1"

    log_level: Literal["DEBUG", "INFO",
                       "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Database configuration will be added when we integrate PostgreSQL.
    database_url: str | None = Field(default=None, repr=False)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
