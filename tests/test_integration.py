"""Integration tests for commit-hook — full module chain verification.

Tests each acceptance criterion end-to-end through the CLI with mocked
external dependencies (git, LLM). Verifies correct reporter output and
exit codes for every chain path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from commit_hook.cli import main
from commit_hook.config import Config, ConfigError, LLMConfig, RulesConfig
from commit_hook.llm import LLMResult, LLMUnavailableError


@pytest.fixture
def runner() -> CliRunner:
    """Isolated Click CLI runner."""
    return CliRunner()


@pytest.fixture
def msg_file(tmp_path: Path) -> Path:
    """Return path to a writable COMMIT_EDITMSG file inside tmp_path."""
    return tmp_path / "COMMIT_EDITMSG"


def _cfg(
    *,
    min_length: int = 10,
    api_key: str = "sk-test",
    forbid_patterns: list[str] | None = None,
) -> Config:
    """Build a Config for integration tests."""
    pats = forbid_patterns if forbid_patterns is not None else []
    return Config(
        llm=LLMConfig(provider="openai", model="gpt-4o", api_key=api_key),
        rules=RulesConfig(min_length=min_length, forbid_patterns=pats),
    )


# ── 链路 1: 配置 → 本地规则 → Reporter ───────────────────────────────────


class TestChain1LocalRules:
    """验收标准 1: min_length=10, "fix bug" → 规则不通过 → exit 1 → 红色 fail."""

    def test_min_length_violation_triggers_fail(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """提交 "fix bug"（7字符）→ 长度不足 → exit 1, 红色 fail."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("fix bug")

        with patch("commit_hook.cli.load_config", return_value=_cfg(min_length=10)):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 1
        assert "不通过" in result.output
        assert "长度不足" in result.output
        assert "7 字符" in result.output
        assert "≥ 10" in result.output

    def test_rules_fail_skips_diff_and_llm(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """规则失败时不应调用 diff 和 LLM（避免浪费资源）."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("fix bug")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg(min_length=10)),
            patch("commit_hook.cli.get_diff") as mock_diff,
            patch("commit_hook.cli.llm_evaluate") as mock_llm,
        ):
            runner.invoke(main, ["check", str(msg_file)])

        mock_diff.assert_not_called()
        mock_llm.assert_not_called()

    def test_rules_also_block_forbidden_pattern(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """提交 "fix" → forbid_patterns 命中 ^fix$ → exit 1."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("fix")

        with patch("commit_hook.cli.load_config", return_value=_cfg(forbid_patterns=["^fix$"])):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 1
        assert "不通过" in result.output
        assert "禁止模式" in result.output
        assert "^fix$" in result.output


# ── 链路 2 LLM 通过: 配置 → Diff → LLM → Reporter ──────────────────────────


class TestChain2LLMPass:
    """验收标准 2: Mock LLM 返回通过 → exit 0 → 绿色 pass."""

    def test_llm_pass_green_output(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM 返回 passed=true → exit 0, 绿色通过."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add user login")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                return_value=LLMResult(passed=True, score=85, accuracy=9),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output
        assert "85" in result.output
        assert "9" in result.output

    def test_full_chain_flow_order(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证完整链路顺序: config → rules ✓ → diff → LLM → reporter."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: implement user authentication flow")

        call_order: list[str] = []

        with (
            patch("commit_hook.cli.load_config") as mock_cfg,
            patch("commit_hook.cli.check_message") as mock_rules,
            patch("commit_hook.cli.get_diff") as mock_diff,
            patch("commit_hook.cli.llm_evaluate") as mock_llm,
        ):
            mock_cfg.return_value = _cfg()
            mock_rules.return_value = []  # pass
            mock_diff.return_value = "diff --git a/x b/x"
            mock_llm.return_value = LLMResult(passed=True, score=80, accuracy=8)

            # Record call order via side_effect
            mock_rules.side_effect = lambda *a, **kw: call_order.append("rules") or []
            mock_diff.side_effect = lambda *a, **kw: call_order.append("diff") or "x"
            mock_llm.side_effect = lambda *a, **kw: (
                call_order.append("llm") or LLMResult(passed=True, score=80, accuracy=8)
            )

            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert call_order == ["rules", "diff", "llm"]


# ── 链路 2 LLM 失败: 配置 → Diff → LLM → Reporter ─────────────────────────


class TestChain2LLMFail:
    """验收标准 3: Mock LLM 返回不通过 → exit 1 → 红色 fail，含 issues 和 suggestion."""

    def test_llm_fail_with_details(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM 返回 passed=false → exit 1, 输出 issues + suggestion."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("refactor: optimize query")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                return_value=LLMResult(
                    passed=False,
                    score=20,
                    accuracy=3,
                    issues=["message too vague", "missing scope"],
                    suggestion="Use conventional commits: feat(scope): description",
                ),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 1
        assert "不通过" in result.output
        # Both issues visible
        assert "message too vague" in result.output
        assert "missing scope" in result.output
        # Suggestion visible
        assert "conventional commits" in result.output.lower()

    def test_llm_fail_with_multiple_issues(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """验证多个 issue 都正确渲染在输出中.

        使用能通过本地规则的 message，确保 LLM 链路被触发.
        """
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("refactor: rewrite core processing pipeline")

        issues = ["does not describe what changed", "too short", "no context", "ambiguous wording"]
        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                return_value=LLMResult(
                    passed=False,
                    score=10,
                    accuracy=2,
                    issues=issues,
                    suggestion="Be more specific about the change",
                ),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 1
        for issue in issues:
            assert issue in result.output


# ── 链路 2 降级: 配置 → Diff → LLM → Reporter ─────────────────────────


class TestChain2Degraded:
    """验收标准 4: Mock LLM 超时/失败 → 降级 → exit 0 → 黄色降级输出."""

    def test_llm_timeout_degraded(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM 超时 → 降级放行 → exit 0."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add login")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                side_effect=LLMUnavailableError("LLM Timeout: request timed out after 10s"),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "降级放行" in result.output
        assert "Timeout" in result.output

    def test_llm_connection_error_degraded(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM 连接失败 → 降级放行 → exit 0."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("chore: update deps")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                side_effect=LLMUnavailableError("APIConnectionError: connection refused"),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "降级放行" in result.output
        assert "APIConnectionError" in result.output


# ── 验收标准 5: 配置缺失时使用默认值 ─────────────────────────────────────────


class TestDefaultConfigFallback:
    """验收标准 5: 配置缺失时正确使用默认值走完整链路."""

    def test_no_config_rules_reject_empty_message(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无配置文件 → 默认规则 active → 空 message 被拦截."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("")

        result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 1
        assert "不通过" in result.output
        assert "不能为空" in result.output

    def test_no_config_rules_reject_forbidden_pattern(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无配置文件 → 默认 forbid_patterns → "WIP" 被拦截."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("WIP")

        result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 1
        assert "不通过" in result.output
        assert "禁止模式" in result.output

    def test_no_config_valid_message_triggers_full_chain(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无配置文件 + valid message → 走完整链路（需 mock diff + LLM）."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: implement user authentication with OAuth2")

        with (
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                return_value=LLMResult(passed=True, score=80, accuracy=8),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        # No config file → defaults used → rules pass → LLM pass → exit 0
        assert result.exit_code == 0
        assert "通过" in result.output

    def test_no_config_empty_diff_skips_llm(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无配置文件 → rules pass → diff 为空 → 跳过 LLM → pass."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add user profile page")

        with (
            patch("commit_hook.cli.get_diff", return_value=""),
            patch("commit_hook.cli.llm_evaluate") as mock_llm,
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output
        assert "无变更内容" in result.output
        mock_llm.assert_not_called()


# ── 验收标准 6: 异常处理 ──────────────────────────────────────────────────


class TestExceptionHandling:
    """验收标准 6: 模块抛异常时被正确捕获并输出有意义信息（不崩溃、不静默）."""

    def test_diff_called_process_error_treated_as_empty(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_diff 抛 CalledProcessError → 降级为空 diff → 不崩溃."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add login")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch(
                "commit_hook.cli.get_diff",
                side_effect=subprocess.CalledProcessError(128, "git"),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        # Diff error → empty diff → skip LLM → pass (degraded gracefully)
        assert result.exit_code == 0
        assert "通过" in result.output

    def test_diff_file_not_found_error_treated_as_empty(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_diff 抛 FileNotFoundError → 降级为空 diff → 不崩溃."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add login")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch(
                "commit_hook.cli.get_diff",
                side_effect=FileNotFoundError("git command not found"),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output

    def test_llm_unavailable_reports_cause(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLMUnavailableError → 降级输出含原因 → exit 0."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add login")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                side_effect=LLMUnavailableError("API key not configured"),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "降级放行" in result.output
        assert "API key" in result.output

    def test_unexpected_exception_not_silently_swallowed(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM 抛非预期异常（如 RuntimeError）→ 不静默吞掉."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add login")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                side_effect=RuntimeError("Unexpected internal crash in LLM module!"),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        # 非预期异常不应该静默通过
        assert result.exit_code != 0, (
            f"非预期异常不应静默通过, got exit_code={result.exit_code}, "
            f"output={result.output[:200]}"
        )
        # 错误信息应该在输出中可见（Click CliRunner 会捕获 traceback）
        assert (
            result.exception is not None
            or "RuntimeError" in result.output
            or ("Unexpected" in result.output)
        ), f"错误信息缺失: exit={result.exit_code}, output={result.output[:300]}"

    def test_config_error_propagates_with_meaningful_info(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ConfigError 带明确信息传播."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add login")

        with patch(
            "commit_hook.cli.load_config",
            side_effect=ConfigError("Failed to parse YAML: syntax error at line 5"),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        # ConfigError 应该被传播，不能静默
        assert result.exit_code != 0, f"ConfigError 不应静默通过, exit_code={result.exit_code}"
        assert (
            result.exception is not None
            or "parse" in result.output.lower()
            or ("ConfigError" in result.output)
        ), f"ConfigError 信息不可见: output={result.output[:300]}"

    def test_rule_module_unexpected_error_not_silent(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rules 模块内部抛 RuntimeError → 不静默."""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add login")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch(
                "commit_hook.cli.check_message",
                side_effect=RuntimeError("Regex pattern compilation failed!"),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        # 不应该静默通过
        assert result.exit_code != 0, f"规则模块异常不应静默通过, exit_code={result.exit_code}"
        error_visible = (
            result.exception is not None
            or "Regex" in result.output
            or "RuntimeError" in result.output
        )
        assert error_visible, f"规则模块异常信息不可见: output={result.output[:300]}"
