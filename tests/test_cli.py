"""Tests for CLI module."""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from commit_hook.cli import CONFIG_PATH, CONFIG_TEMPLATE, HOOK_CONTENT, HOOK_PATH, main
from commit_hook.config import Config, LLMConfig, RulesConfig
from commit_hook.llm import LLMResult, LLMUnavailableError


@pytest.fixture
def runner() -> CliRunner:
    """Return an isolated Click CLI runner."""
    return CliRunner()


# ---------------------------------------------------------------------------
# Help & version (keep existing tests)
# ---------------------------------------------------------------------------


def test_cli_help(runner: CliRunner) -> None:
    """Test that --help produces output without errors."""
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "AI-powered commit message validator" in result.output


def test_cli_version(runner: CliRunner) -> None:
    """Test that --version works."""
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


# ---------------------------------------------------------------------------
# init / uninit
# ---------------------------------------------------------------------------


class TestInit:
    """Tests for the ``init`` command."""

    def test_creates_hook_file(self, tmp_path: Path, monkeypatch: Any) -> None:
        """init creates the hook wrapper with correct content and permissions."""
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, ["init"])
        assert result.exit_code == 0

        hook_file = tmp_path / HOOK_PATH
        assert hook_file.exists()
        assert hook_file.read_text() == HOOK_CONTENT
        assert hook_file.stat().st_mode & 0o755 == 0o755

    def test_existing_hook_not_overwritten(self, tmp_path: Path, monkeypatch: Any) -> None:
        """When hook already exists, init prints a message and does nothing."""
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        existing = "#!/bin/sh\necho old\n"
        (tmp_path / HOOK_PATH).write_text(existing)
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, ["init"])
        assert result.exit_code == 0
        assert "already exists" in result.output
        assert (tmp_path / HOOK_PATH).read_text() == existing

    def test_init_generates_config(self, tmp_path: Path, monkeypatch: Any) -> None:
        """config 不存在时，init 生成 .commit-hook.yaml + 安装 hook."""
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, ["init"])
        assert result.exit_code == 0

        # hook installed
        hook_file = tmp_path / HOOK_PATH
        assert hook_file.exists()
        assert hook_file.read_text() == HOOK_CONTENT

        # config generated
        config_file = tmp_path / CONFIG_PATH
        assert config_file.exists()
        assert config_file.read_text() == CONFIG_TEMPLATE
        assert ".commit-hook.yaml 已生成" in result.output

    def test_init_config_exists_not_overwritten(self, tmp_path: Path, monkeypatch: Any) -> None:
        """config 已存在时，不覆盖（验证内容不变）."""
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)

        existing_config = "llm:\n  provider: ollama\n  model: llama3\n"
        (tmp_path / CONFIG_PATH).write_text(existing_config)

        result = CliRunner().invoke(main, ["init"])
        assert result.exit_code == 0

        # config unchanged
        assert (tmp_path / CONFIG_PATH).read_text() == existing_config
        assert ".commit-hook.yaml 已存在，不覆盖" in result.output

        # hook still installed (independent)
        assert (tmp_path / HOOK_PATH).exists()

    def test_init_config_generated_when_hook_exists(self, tmp_path: Path, monkeypatch: Any) -> None:
        """hook 已存在但 config 不存在时，跳过 hook 但生成 config."""
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        existing_hook = "#!/bin/sh\necho old hook\n"
        (tmp_path / HOOK_PATH).write_text(existing_hook)
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, ["init"])
        assert result.exit_code == 0

        # hook not overwritten
        assert (tmp_path / HOOK_PATH).read_text() == existing_hook
        assert "already exists" in result.output

        # config generated
        config_file = tmp_path / CONFIG_PATH
        assert config_file.exists()
        assert config_file.read_text() == CONFIG_TEMPLATE
        assert ".commit-hook.yaml 已生成" in result.output


class TestUninit:
    """Tests for the ``uninit`` command."""

    def test_removes_hook_file(self, tmp_path: Path, monkeypatch: Any) -> None:
        """uninit removes the hook when it exists."""
        git_dir = tmp_path / ".git" / "hooks"
        git_dir.mkdir(parents=True)
        (tmp_path / HOOK_PATH).write_text("content")
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, ["uninit"])
        assert result.exit_code == 0
        assert "Hook removed" in result.output
        assert not (tmp_path / HOOK_PATH).exists()

    def test_no_hook_prints_message(self, tmp_path: Path, monkeypatch: Any) -> None:
        """When no hook exists, uninit prints a message without error."""
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, ["uninit"])
        assert result.exit_code == 0
        assert "No hook found" in result.output


# ---------------------------------------------------------------------------
# check command
# ---------------------------------------------------------------------------


def _create_commit_msg(path: Path, content: str) -> Path:
    """Write a temporary commit message file and return its path."""
    msg_file = path / "COMMIT_EDITMSG"
    msg_file.write_text(content)
    return msg_file


@contextmanager
def _mock_config(**overrides: Any) -> Any:
    """Patch load_config to return a default Config, optionally overridden.

    Usage as a context manager::

        with _mock_config() as cfg_patch:
            ...
    """
    with patch("commit_hook.cli.load_config") as mock_load:
        cfg = Config(
            llm=LLMConfig(provider="openai", model="gpt-4o", api_key="sk-test"),
            rules=RulesConfig(min_length=10),
        )
        for key, value in overrides.items():
            section, attr = key.split("__")
            getattr(getattr(cfg, section), attr).__setattr__(attr, value)  # noqa: B010
        mock_load.return_value = cfg
        yield mock_load


class TestCheck:
    """Tests for the ``check`` command."""

    def test_empty_message_blocked_by_rules(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Empty commit message is caught by local rules → exit 1."""
        monkeypatch.chdir(tmp_path)
        msg_file = _create_commit_msg(tmp_path, "")

        with _mock_config():
            result = CliRunner().invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 1
        assert "不通过" in result.output
        assert "不能为空" in result.output

    def test_forbidden_pattern_blocked(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Message matching forbid pattern is caught by local rules → exit 1."""
        monkeypatch.chdir(tmp_path)
        msg_file = _create_commit_msg(tmp_path, "fix")

        with _mock_config():
            result = CliRunner().invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 1
        assert "不通过" in result.output
        assert "禁止模式" in result.output

    def test_empty_diff_passes(self, tmp_path: Path, monkeypatch: Any) -> None:
        """When staged diff is empty, check passes (skip LLM)."""
        monkeypatch.chdir(tmp_path)
        msg_file = _create_commit_msg(tmp_path, "feat: add user login")

        with _mock_config(), patch("commit_hook.cli.get_diff", return_value=""):
            result = CliRunner().invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output
        assert "无变更内容" in result.output

    def test_llm_passes(self, tmp_path: Path, monkeypatch: Any) -> None:
        """LLM returns passed=true → green pass output + exit 0."""
        monkeypatch.chdir(tmp_path)
        msg_file = _create_commit_msg(tmp_path, "feat: add user login")

        with (
            _mock_config(),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                return_value=LLMResult(passed=True, score=85, accuracy=9),
            ),
        ):
            result = CliRunner().invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output
        assert "85" in result.output
        assert "9" in result.output

    def test_llm_fails(self, tmp_path: Path, monkeypatch: Any) -> None:
        """LLM returns passed=false → red fail output + exit 1."""
        monkeypatch.chdir(tmp_path)
        msg_file = _create_commit_msg(tmp_path, "refactor: optimize query performance")

        with (
            _mock_config(),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                return_value=LLMResult(
                    passed=False,
                    score=20,
                    accuracy=3,
                    issues=["message too vague"],
                    suggestion="Use conventional commits format",
                ),
            ),
        ):
            result = CliRunner().invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 1
        assert "不通过" in result.output
        assert "message too vague" in result.output
        assert "Use conventional commits format" in result.output

    def test_llm_unavailable_degraded(self, tmp_path: Path, monkeypatch: Any) -> None:
        """LLMUnavailableError → yellow degraded output + exit 0."""
        monkeypatch.chdir(tmp_path)
        msg_file = _create_commit_msg(tmp_path, "feat: add login")

        with (
            _mock_config(),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                side_effect=LLMUnavailableError("timeout"),
            ),
        ):
            result = CliRunner().invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "降级放行" in result.output
        assert "timeout" in result.output

    def test_diff_error_falls_back_to_empty(self, tmp_path: Path, monkeypatch: Any) -> None:
        """When get_diff raises CalledProcessError, treat as empty diff."""
        monkeypatch.chdir(tmp_path)
        msg_file = _create_commit_msg(tmp_path, "feat: add user login")

        with (
            _mock_config(),
            patch(
                "commit_hook.cli.get_diff",
                side_effect=subprocess.CalledProcessError(128, "git"),
            ),
        ):
            result = CliRunner().invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output

    # ------------------------------------------------------------------
    # FR-006: [skip-validate] marker tests
    # ------------------------------------------------------------------

    def test_skip_validate_marker_present(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Message with [skip-validate] marker → print skip message + exit 0.

        All downstream checks (config, diff, LLM) are short-circuited.
        """
        monkeypatch.chdir(tmp_path)
        msg_file = _create_commit_msg(tmp_path, "WIP: rough draft [skip-validate]")

        load_config_mock = MagicMock()
        get_diff_mock = MagicMock()
        llm_evaluate_mock = MagicMock()

        with (
            patch("commit_hook.cli.load_config", load_config_mock),
            patch("commit_hook.cli.get_diff", get_diff_mock),
            patch("commit_hook.cli.llm_evaluate", llm_evaluate_mock),
        ):
            result = CliRunner().invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "skip-validate" in result.output
        assert "跳过检查" in result.output
        load_config_mock.assert_not_called()
        get_diff_mock.assert_not_called()
        llm_evaluate_mock.assert_not_called()

    def test_skip_validate_marker_absent_normal_flow(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Message without [skip-validate] → normal P0 flow proceeds."""
        monkeypatch.chdir(tmp_path)
        msg_file = _create_commit_msg(tmp_path, "feat: add user login")

        with (
            _mock_config(),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                return_value=LLMResult(passed=True, score=85, accuracy=9),
            ),
        ):
            result = CliRunner().invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output
        assert "skip-validate" not in result.output

    def test_rule_violations_skip_llm(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Local rule violations skip diff extraction and LLM entirely."""
        monkeypatch.chdir(tmp_path)
        msg_file = _create_commit_msg(tmp_path, "WIP")

        get_diff_mock = MagicMock()
        llm_mock = MagicMock()

        with (
            _mock_config(),
            patch("commit_hook.cli.get_diff", get_diff_mock),
            patch("commit_hook.cli.llm_evaluate", llm_mock),
        ):
            CliRunner().invoke(main, ["check", str(msg_file)])

        get_diff_mock.assert_not_called()
        llm_mock.assert_not_called()
