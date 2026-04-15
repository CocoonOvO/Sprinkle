"""Configuration management for Sprinkle."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ============================================================================
# Pydantic Models (used by pydantic-settings for env var override)
# ============================================================================

class AppConfig(BaseModel):
    """Application configuration."""
    name: str = "Sprinkle"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False


class DatabaseConfig(BaseModel):
    """Database configuration."""
    driver: str = "postgresql"
    host: str = "localhost"
    port: int = 5432
    name: str = "sprinkle_db"
    user: str = "cream"
    password: str = ""

    @property
    def url(self) -> str:
        """Get the database URL."""
        return f"{self.driver}://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class RedisConfig(BaseModel):
    """Redis configuration."""
    host: str = "localhost"
    port: int = 6379
    db: int = 0

    @property
    def url(self) -> str:
        """Get the Redis URL."""
        return f"redis://{self.host}:{self.port}/{self.db}"


class Settings(BaseSettings):
    """Main settings class that supports env var override.
    
    Environment variables use double underscore for nesting:
    - APP__NAME, APP__DEBUG, APP__HOST, APP__PORT
    - DATABASE__HOST, DATABASE__PORT, DATABASE__NAME, etc.
    - REDIS__HOST, REDIS__PORT, REDIS__DB
    """
    app: AppConfig = Field(default_factory=AppConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )


# ============================================================================
# YAML Config Loader
# ============================================================================

def load_yaml_config(config_path: Optional[str] = None) -> dict:
    """Load configuration from YAML file."""
    if config_path is None:
        # Look for config.yaml in common locations
        config_path = os.environ.get(
            "SPRINKLE_CONFIG_PATH",
            str(Path(__file__).parent.parent.parent / "config.yaml")
        )
    
    config_file = Path(config_path)
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_settings(config_path: Optional[str] = None) -> Settings:
    """Get application settings.
    
    Priority (highest to lowest):
    1. Environment variables (APP__NAME, DATABASE__HOST, etc.)
    2. YAML config file (loaded from config_path)
    3. Default values
    
    Environment variables override YAML config, which overrides defaults.
    """
    yaml_config = load_yaml_config(config_path)
    
    # Build settings from YAML first
    settings_kwargs = {}
    
    if "app" in yaml_config:
        settings_kwargs["app"] = AppConfig(**yaml_config["app"])
    if "database" in yaml_config:
        settings_kwargs["database"] = DatabaseConfig(**yaml_config["database"])
    if "redis" in yaml_config:
        settings_kwargs["redis"] = RedisConfig(**yaml_config["redis"])
    
    # Create settings - env vars are processed automatically by pydantic-settings
    # when we use _env_file=None to prevent loading .env file, but keep env parsing
    # Actually we need to let pydantic-settings handle env vars itself
    
    # Create settings with YAML values first, then env vars will override
    # Since pydantic-settings processes env vars at init time, we need to
    # construct the settings differently
    
    # First create with YAML values
    settings = Settings(**settings_kwargs)
    
    # Now manually apply environment variable overrides
    # This is a workaround because pydantic-settings env vars don't override
    # explicitly passed values
    
    _apply_env_overrides(settings)
    
    return settings


def _apply_env_overrides(settings: Settings) -> None:
    """Apply environment variable overrides to settings.
    
    Pydantic-settings doesn't override explicitly passed values with env vars,
    so we need to manually apply them.
    """
    # App config overrides
    if "APP__NAME" in os.environ:
        settings.app.name = os.environ["APP__NAME"]
    if "APP__HOST" in os.environ:
        settings.app.host = os.environ["APP__HOST"]
    if "APP__PORT" in os.environ:
        settings.app.port = int(os.environ["APP__PORT"])
    if "APP__DEBUG" in os.environ:
        settings.app.debug = os.environ["APP__DEBUG"].lower() in ("true", "1", "yes")
    
    # Database config overrides
    if "DATABASE__DRIVER" in os.environ:
        settings.database.driver = os.environ["DATABASE__DRIVER"]
    if "DATABASE__HOST" in os.environ:
        settings.database.host = os.environ["DATABASE__HOST"]
    if "DATABASE__PORT" in os.environ:
        settings.database.port = int(os.environ["DATABASE__PORT"])
    if "DATABASE__NAME" in os.environ:
        settings.database.name = os.environ["DATABASE__NAME"]
    if "DATABASE__USER" in os.environ:
        settings.database.user = os.environ["DATABASE__USER"]
    if "DATABASE__PASSWORD" in os.environ:
        settings.database.password = os.environ["DATABASE__PASSWORD"]
    
    # Redis config overrides
    if "REDIS__HOST" in os.environ:
        settings.redis.host = os.environ["REDIS__HOST"]
    if "REDIS__PORT" in os.environ:
        settings.redis.port = int(os.environ["REDIS__PORT"])
    if "REDIS__DB" in os.environ:
        settings.redis.db = int(os.environ["REDIS__DB"])


# ============================================================================
# Dataclass-style Config (for backward compatibility)
# ============================================================================

@dataclass
class AppConfigDC:
    """Application configuration (dataclass style)."""
    name: str = "Sprinkle"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False


@dataclass
class DatabaseConfigDC:
    """Database configuration (dataclass style)."""
    driver: str = "postgresql"
    host: str = "localhost"
    port: int = 5432
    name: str = "sprinkle_db"
    user: str = "cream"
    password: str = ""

    @property
    def url(self) -> str:
        """Get the database URL."""
        return f"{self.driver}://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


@dataclass
class RedisConfigDC:
    """Redis configuration (dataclass style)."""
    host: str = "localhost"
    port: int = 6379
    db: int = 0

    @property
    def url(self) -> str:
        """Get the Redis URL."""
        return f"redis://{self.host}:{self.port}/{self.db}"


# Create a default instance
settings = get_settings()