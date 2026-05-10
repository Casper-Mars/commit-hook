"""Tests for llm module."""


def test_analyse_commit_empty() -> None:
    """Test analyse_commit returns empty string for empty inputs."""
    from commit_hook.llm import analyse_commit

    result = analyse_commit("", {})
    assert result == ""
