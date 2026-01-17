"""Configuration module for loading environment variables."""

import os
from pathlib import Path


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""
    pass


def load_env_file(env_path: str = None) -> dict:
    """
    Load environment variables from a .env file.

    Args:
        env_path: Path to .env file. If None, looks for .env in project root.

    Returns:
        Dictionary of environment variables from the file.
    """
    if env_path is None:
        # Look for .env in the project root (parent of src/)
        project_root = Path(__file__).parent.parent
        env_path = project_root / ".env"
    else:
        env_path = Path(env_path)

    env_vars = {}

    if not env_path.exists():
        return env_vars

    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            # Parse KEY=value format
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()

                # Remove surrounding quotes if present
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]

                env_vars[key] = value

    return env_vars


class Config:
    """Configuration container for Buda.com API credentials."""

    BASE_URL = "https://www.buda.com/api/v2"

    def __init__(self, api_key: str = None, api_secret: str = None):
        # Load from .env file first
        env_vars = load_env_file()

        # Priority: explicit args > env vars from .env > os.environ
        self.api_key = api_key or env_vars.get("BUDA_API_KEY") or os.environ.get("BUDA_API_KEY")
        self.api_secret = api_secret or env_vars.get("BUDA_API_SECRET") or os.environ.get("BUDA_API_SECRET")

    def validate(self) -> None:
        """
        Validate that required configuration is present.

        Raises:
            ConfigError: If required configuration is missing.
        """
        if not self.api_key:
            raise ConfigError(
                "BUDA_API_KEY not found. "
                "Set it in .env file or as environment variable."
            )
        if not self.api_secret:
            raise ConfigError(
                "BUDA_API_SECRET not found. "
                "Set it in .env file or as environment variable."
            )

    @classmethod
    def load(cls, api_key: str = None, api_secret: str = None) -> "Config":
        """
        Load and validate configuration.

        Args:
            api_key: Optional API key (overrides env vars).
            api_secret: Optional API secret (overrides env vars).

        Returns:
            Validated Config instance.

        Raises:
            ConfigError: If required configuration is missing.
        """
        config = cls(api_key=api_key, api_secret=api_secret)
        config.validate()
        return config
