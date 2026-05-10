"""Tests for CLI module."""

from click.testing import CliRunner

from commit_hook.cli import main


def test_cli_help() -> None:
    """Test that --help produces output without errors."""
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "AI-powered commit message validator" in result.output


def test_cli_version() -> None:
    """Test that --version works."""
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
