"""LLM integration via litellm for commit-hook."""

from typing import Any


def analyse_commit(diff: str, config: dict[str, Any]) -> str:
    """Analyse a commit diff using an LLM.

    Args:
        diff: The staged diff to analyse.
        config: Configuration dictionary with LLM settings.

    Returns:
        The LLM analysis result as a string.
    """
    del diff, config
    return ""
