"""
Configuration file for the Music project.
This file contains environment variables and application settings.
"""

import os
from typing import Optional


class Config:
    """Base configuration class."""

    # Database Configuration
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "postgresql://localhost:5432/mydatabase"
    )
    DATABASE_USER: str = os.getenv("DATABASE_USER", "myuser")
    DATABASE_PASSWORD: str = os.getenv("DATABASE_PASSWORD", "mypassword")

    # API Keys
    API_KEY: str = os.getenv("API_KEY", "your_api_key_here")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "your_secret_key_here")

    # Application Settings
    DEBUG: bool = os.getenv("DEBUG", "True").lower() == "true"
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    PORT: int = int(os.getenv("PORT", "8000"))

    # External Services
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")

    # Music-specific settings
    MUSIC_LIBRARY_PATH: str = os.getenv("MUSIC_LIBRARY_PATH", "./music_library")
    SUPPORTED_FORMATS: list = ["mp3", "wav", "flac", "m4a", "ogg"]
    MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", "100000000"))  # 100MB


class DevelopmentConfig(Config):
    """Development configuration."""

    DEBUG = True
    ENVIRONMENT = "development"


class ProductionConfig(Config):
    """Production configuration."""

    DEBUG = False
    ENVIRONMENT = "production"


class TestingConfig(Config):
    """Testing configuration."""

    DEBUG = True
    ENVIRONMENT = "testing"
    DATABASE_URL = "sqlite:///:memory:"


# Configuration mapping
config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}


def get_config(config_name: Optional[str] = None) -> Config:
    """
    Get configuration based on environment.

    Args:
        config_name: Name of the configuration to use

    Returns:
        Configuration object
    """
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "default")

    return config.get(config_name, config["default"])()
