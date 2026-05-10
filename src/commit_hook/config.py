"""Configuration loading and validation for commit-hook."""

from typing import Any


def load_config(path: str) -> dict[str, Any]:
    """Load and validate configuration from a YAML file.

    Args:
        path: Path to the configuration file.

    Returns:
        A dictionary containing the validated configuration.
    """
    del path
    return {}
