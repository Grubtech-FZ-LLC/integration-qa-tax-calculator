"""
Configuration utilities for the Smart Cal CLI tool.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv


class Config:
    """Configuration manager for the Smart Cal project."""

    def __init__(self, env_file: Optional[str] = None) -> None:
        """Initialize configuration.

        If an env_file path is provided, load environment variables from it.
        Otherwise, do not auto-load a .env file to keep defaults predictable.
        """
        self.env_file = env_file
        self._load_environment()
        self._config = self._load_config()

    def _load_environment(self) -> None:
        """Load environment variables from explicit .env file if provided."""
        if not self.env_file:
            return
        env_path = Path(self.env_file)
        if env_path.exists():
            load_dotenv(env_path)

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from environment variables."""
        return {
            # Core settings
            "log_level": self._get_str("LOG_LEVEL", default="INFO"),
            # MongoDB settings for order verification
            "mongo_url": self._get_str("DB_CONNECTION_URL", default=""),
            "mongo_db": self._get_str("DB_NAME", default="GRUBTECH_MASTER_DATA_STG_V2"),
            "mongo_collection": self._get_str("COLLECTION_NAME", default="PARTNER_RESTAURANT_ORDER"),
            # Tax calculation settings
            "tax_inclusive": self._get_bool("TAX_INCLUSIVE", default=True),
        }

    def _get_str(self, key: str, default: str = "") -> str:
        """Get string configuration value."""
        if self.env_file is None:
            return default
        return os.getenv(key, default)

    def _get_int(self, key: str, default: int = 0) -> int:
        """Get integer configuration value."""
        if self.env_file is None:
            return default
        try:
            return int(os.getenv(key, str(default)))
        except ValueError:
            return default

    def _get_bool(self, key: str, default: bool = False) -> bool:
        """Get boolean configuration value."""
        if self.env_file is None:
            return default
        value = os.getenv(key, str(default)).lower()
        return value in ("true", "1", "yes", "on")

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self._config.get(key, default)

    def __getitem__(self, key: str) -> Any:
        """Get configuration value using bracket notation."""
        return self._config[key]

    def __contains__(self, key: str) -> bool:
        """Check if configuration key exists."""
        return key in self._config

