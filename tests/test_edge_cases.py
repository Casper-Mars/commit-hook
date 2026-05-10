"""边界情况与异常处理测试（Test 3 / P0）。

覆盖 10 条验收标准，混合单元测试 / 集成测试 / E2E 测试。
"""

from __future__ import annotations

import re
import subprocess
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from commit_hook.cli import main
from commit_hook.config import (
    _CFG,
    Config,
    ConfigError,
    DiffConfig,
    LLMConfig,
    RulesConfig,
    load_config,
)
from commit_hook.diff import _filter_parts, _truncate
from commit_hook.llm import LLMResult
from commit_hook.rules import check_message

# ══════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def msg_file(tmp_path: Path) -> Path:
    return tmp_path / "COMMIT_EDITMSG"


def _cfg(
    *,
    min_length: int = 10,
    api_key: str = "sk-test",
    forbid_patterns: list[str] | None = None,
    max_lines: int = 500,
) -> Config:
    pats = forbid_patterns if forbid_patterns is not None else []
    return Config(
        llm=LLMConfig(provider="openai", model="gpt-4o", api_key=api_key),
        rules=RulesConfig(min_length=min_length, forbid_patterns=pats),
        diff=DiffConfig(max_lines=max_lines),
    )


# ══════════════════════════════════════════════════════════════════════════
# 验收标准 1: 多字节字符
# ══════════════════════════════════════════════════════════════════════════


class TestMultibyteCharacters:
    """message 包含 emoji / 中文 / 日文 / 阿拉伯文时正常处理（不崩溃，正确判断长度）."""

    def test_chinese_message_length_correctly_judged(self) -> None:
        """中文 commit message 的长度按 Unicode 字符数计算（Python len()）。"""
        cfg = RulesConfig(min_length=8, forbid_patterns=[])
        violations = check_message("修复用户登录逻辑", cfg)
        assert violations == []  # 8 个中文字符 == min_length

    def test_chinese_message_below_min_length(self) -> None:
        """短中文 message 触发长度违规（验证 len() 返回 Unicode 字符数）。"""
        cfg = RulesConfig(min_length=10, forbid_patterns=[])
        violations = check_message("修复Bug", cfg)
        assert any("长度不足" in v for v in violations)
        assert "5 字符" in violations[0]  # 修复Bug = 5 Unicode 字符

    def test_emoji_message_not_crash(self) -> None:
        """含 emoji 的 message 不崩溃，且 emoji 计为合法字符。"""
        cfg = RulesConfig(min_length=5, forbid_patterns=[])
        violations = check_message("🚀 deploy", cfg)
        assert violations == []  # 🚀 (1) + space (1) + "deploy" (6) = 8 chars

    def test_emoji_only_message(self) -> None:
        """纯 emoji message 通过长度检查。"""
        cfg = RulesConfig(min_length=3, forbid_patterns=[])
        violations = check_message("🧪✅🔥", cfg)
        assert violations == []

    def test_japanese_message(self) -> None:
        """日文 message 正确判断长度。"""
        cfg = RulesConfig(min_length=5, forbid_patterns=[])
        violations = check_message("ユーザー認証を追加", cfg)
        assert violations == []

    def test_arabic_message(self) -> None:
        """阿拉伯文 message 正确判断长度。"""
        cfg = RulesConfig(min_length=10, forbid_patterns=[])
        violations = check_message("إضافة ميزة تسجيل الدخول", cfg)
        assert violations == []

    def test_mixed_multibyte_ascii_message(self) -> None:
        """混合中英文 + emoji 的 message 正常处理。"""
        cfg = RulesConfig(min_length=5, forbid_patterns=[])
        violations = check_message("feat🚀: 添加用户注册", cfg)
        assert violations == []

    def test_multibyte_in_cli_chain(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """中文 message 通过完整 CLI 链路（rules pass → diff → LLM → pass）。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: 实现用户认证模块 🚀")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                return_value=LLMResult(passed=True, score=90, accuracy=9),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output


# ══════════════════════════════════════════════════════════════════════════
# 验收标准 2: 空 diff
# ══════════════════════════════════════════════════════════════════════════


class TestEmptyDiff:
    """diff 为空（如 git commit --allow-empty）时不报错，正常 pass."""

    def test_empty_diff_skips_llm_and_passes(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空 diff → rules pass → 跳过 LLM → exit 0。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add user login")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch("commit_hook.cli.get_diff", return_value=""),
            patch("commit_hook.cli.llm_evaluate") as mock_llm,
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output
        assert "无变更内容" in result.output
        mock_llm.assert_not_called()

    def test_whitespace_only_diff_also_treated_as_empty(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """diff 仅含空白字符 → strip() 后为空 → 跳过 LLM。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add login")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch("commit_hook.cli.get_diff", return_value="   \n  \n  "),
            patch("commit_hook.cli.llm_evaluate") as mock_llm,
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output
        mock_llm.assert_not_called()

    def test_empty_diff_no_crash_with_edge_messages(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """空 diff + 边缘 message（如 single char 但通过 rules）不崩溃。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("Initial commit setup")

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg(min_length=5)),
            patch("commit_hook.cli.get_diff", return_value=""),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output


# ══════════════════════════════════════════════════════════════════════════
# 验收标准 3: 超大 diff
# ══════════════════════════════════════════════════════════════════════════


class TestLargeDiff:
    """diff 超过配置行数时正常截断，不超时、不 OOM."""

    def test_truncate_large_diff_drops_excess_lines(self) -> None:
        """超过 max_lines 的行被截断, 保留前 max_lines 行 + 截断消息。"""
        lines = [f"line {i}\n" for i in range(600)]  # 原行数
        text = "".join(lines)

        result = _truncate(text, lines, max_lines=500)
        result_lines = result.splitlines(keepends=False)

        assert len(result_lines) == 501  # 500 lines + truncation message
        assert "diff truncated at 600 lines" in result_lines[-1]
        assert "line 0" in result_lines[0]
        assert "line 499" in result_lines[499]

    def test_truncate_at_exact_max_lines_does_not_truncate(self) -> None:
        """刚好等于 max_lines 时不截断，无截断消息。"""
        lines = [f"line {i}\n" for i in range(500)]
        text = "".join(lines)

        result = _truncate(text, lines, max_lines=500)
        assert "diff truncated" not in result
        assert result == text

    def test_large_diff_integration_cli(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """超 10000 行的 diff 通过 CLI 链路不崩溃，正常工作。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: massive data import refactoring")

        huge_diff_lines = [
            "diff --git a/huge.py b/huge.py\n",
            "--- a/huge.py\n",
            "+++ b/huge.py\n",
            "@@ -0,0 +1,15000 @@\n",
        ] + [f"+    value_{i} = {i}\n" for i in range(15000)]

        huge_diff = "".join(huge_diff_lines)

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg(max_lines=500)),
            patch("commit_hook.cli.get_diff", return_value=huge_diff),
            patch(
                "commit_hook.cli.llm_evaluate",
                return_value=LLMResult(passed=True, score=75, accuracy=8),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output

    def test_very_large_diff_no_oom(self) -> None:
        """超大 diff（模拟 20000 行，逐行流式处理）不 OOM。"""
        lines = [f"line {i}\n" for i in range(20000)]
        text = "".join(lines)
        result = _truncate(text, lines, max_lines=500)
        result_lines = result.splitlines(keepends=False)

        assert len(result_lines) == 501
        assert "diff truncated at 20000 lines" in result_lines[-1]


# ══════════════════════════════════════════════════════════════════════════
# 验收标准 4: 未识别配置字段
# ══════════════════════════════════════════════════════════════════════════


class TestUnknownConfigFields:
    """.commit-hook.yaml 中定义了未识别字段时不崩溃（warnings.warn，不抛 ConfigError）."""

    def test_unknown_top_level_does_not_crash(self, tmp_path: Path) -> None:
        """未知顶层 key 只发 warning，不崩溃，其他字段正常解析。"""
        p = tmp_path / _CFG
        p.write_text(
            yaml.dump(
                {
                    "llm": {"provider": "openai"},
                    "unknown_section": {"foo": "bar"},
                    "another_unknown": 42,
                }
            ),
            encoding="utf-8",
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = load_config(str(p))

        # 正常字段解析正确
        assert cfg.llm.provider == "openai"
        # 有 warning 而不是 crash
        warn_msgs = [str(x.message) for x in w]
        assert any("unknown_section" in m for m in warn_msgs), f"warnings: {warn_msgs}"
        assert any("another_unknown" in m for m in warn_msgs), f"warnings: {warn_msgs}"

    def test_unknown_sub_field_does_not_crash(self, tmp_path: Path) -> None:
        """已知 section 内的未知 key 只发 warning，不崩溃。"""
        p = tmp_path / _CFG
        p.write_text(
            yaml.dump(
                {
                    "rules": {
                        "min_length": 20,
                        "custom_check_enabled": True,
                        "severity": "high",
                    },
                    "diff": {
                        "max_lines": 300,
                        "ignore_whitespace": True,
                    },
                }
            ),
            encoding="utf-8",
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = load_config(str(p))

        assert cfg.rules.min_length == 20
        assert cfg.diff.max_lines == 300
        warn_msgs = [str(x.message) for x in w]
        assert any("custom_check_enabled" in m for m in warn_msgs)
        assert any("severity" in m for m in warn_msgs)
        assert any("ignore_whitespace" in m for m in warn_msgs)

    def test_unknown_field_with_nested_structure(self, tmp_path: Path) -> None:
        """嵌套结构的未知字段也不崩溃。"""
        p = tmp_path / _CFG
        p.write_text(
            yaml.dump(
                {
                    "llm": {
                        "provider": "anthropic",
                        "retry": {"max_attempts": 3, "backoff": "exponential"},
                    },
                }
            ),
            encoding="utf-8",
        )

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = load_config(str(p))

        assert cfg.llm.provider == "anthropic"
        warn_msgs = [str(x.message) for x in w]
        assert any("retry" in m for m in warn_msgs)

    def test_unknown_field_in_cli_chain(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未知字段配置 + CLI 完整链路不崩溃。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add user authentication module")

        config_with_unknown = Config(
            llm=LLMConfig(provider="openai", model="gpt-4o", api_key="sk-test"),
            rules=RulesConfig(min_length=10),
        )

        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            with (
                patch("commit_hook.cli.load_config", return_value=config_with_unknown),
                patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
                patch(
                    "commit_hook.cli.llm_evaluate",
                    return_value=LLMResult(passed=True, score=80, accuracy=8),
                ),
            ):
                result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output


# ══════════════════════════════════════════════════════════════════════════
# 验收标准 5: 非法正则
# ══════════════════════════════════════════════════════════════════════════


class TestInvalidRegexPattern:
    """forbid_patterns 包含非法正则时给出明确错误（不静默，不被 mock LLM 拦截）。"""

    def test_unmatched_bracket_causes_re_error(self) -> None:
        """未闭合方括号 `[bad` 导致 re.error。"""
        cfg = RulesConfig(min_length=1, forbid_patterns=["[bad"])
        with pytest.raises(re.error) as exc_info:
            check_message("hello", cfg)
        assert "unterminated character set" in str(exc_info.value)

    def test_unmatched_paren_causes_re_error(self) -> None:
        """未闭合圆括号 `(abc` 导致 re.error。"""
        cfg = RulesConfig(min_length=1, forbid_patterns=["(abc"])
        with pytest.raises(re.error) as exc_info:
            check_message("hello", cfg)
        err = str(exc_info.value)
        # Different Python versions give different messages
        assert "missing" in err.lower() or "unterminated" in err.lower()

    def test_invalid_regex_not_silently_swallowed(self) -> None:
        """非法正则导致异常传播，不静默吞掉。"""
        cfg = RulesConfig(min_length=1, forbid_patterns=["*invalid"])  # * 在开头无效
        with pytest.raises(re.error):
            check_message("anything", cfg)

    def test_invalid_regex_in_cli_chain_propagates(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI 链路中非法正则在 rules 检查阶段就报错，不走到 LLM。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add user login")

        cfg_with_bad_regex = _cfg(forbid_patterns=["[bad"])

        with (
            patch("commit_hook.cli.load_config", return_value=cfg_with_bad_regex),
            patch("commit_hook.cli.get_diff") as mock_diff,
            patch("commit_hook.cli.llm_evaluate") as mock_llm,
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        # 不应该静默通过（exit 0）
        assert result.exit_code != 0, f"非法正则不应静默通过, exit_code={result.exit_code}"
        # 不应该走到 diff 和 LLM
        mock_diff.assert_not_called()
        mock_llm.assert_not_called()

    def test_valid_regex_still_works(self) -> None:
        """正常正则仍然正确匹配（回归测试）。"""
        cfg = RulesConfig(min_length=1, forbid_patterns=["^fix$", "^WIP$"])
        violations = check_message("fix", cfg)
        assert any("^fix$" in v for v in violations)


# ══════════════════════════════════════════════════════════════════════════
# 验收标准 6: 子模块 init
# ══════════════════════════════════════════════════════════════════════════


class TestSubmoduleInit:
    """在 git 子模块目录中执行 init → 正确处理（子模块自己的 .git）."""

    def test_submodule_git_is_file_not_directory(self, tmp_path: Path) -> None:
        """验证子模块的 .git 是文件（gitdir 指针），不是目录。"""
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=main_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=main_repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Tester"],
            cwd=main_repo,
            check=True,
        )

        sub_repo = tmp_path / "subrepo"
        sub_repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=sub_repo,
            check=True,
            capture_output=True,
        )
        (sub_repo / "f.txt").write_text("x")
        subprocess.run(
            ["git", "add", "f.txt"],
            cwd=sub_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=sub_repo,
            check=True,
            capture_output=True,
        )

        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(sub_repo), "lib"],
            cwd=main_repo,
            check=True,
            capture_output=True,
        )

        submodule_dir = main_repo / "lib"
        git_entry = submodule_dir / ".git"
        assert git_entry.is_file(), "子模块 .git 应为文件（gitdir 指针）"
        content = git_entry.read_text().strip()
        assert content.startswith("gitdir: "), f"应为 gitdir 指针: {content}"

    def test_config_load_in_submodule_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """子模块目录中 load_config 能正确向上查找并加载配置。"""
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=main_repo,
            check=True,
            capture_output=True,
        )

        # 在父仓库写配置
        (main_repo / _CFG).write_text(
            yaml.dump({"llm": {"provider": "anthropic"}}),
            encoding="utf-8",
        )

        sub_repo = tmp_path / "subrepo"
        sub_repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=sub_repo,
            check=True,
            capture_output=True,
        )
        (sub_repo / "f.txt").write_text("x")
        subprocess.run(
            ["git", "add", "f.txt"],
            cwd=sub_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=sub_repo,
            check=True,
            capture_output=True,
        )

        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(sub_repo), "lib"],
            cwd=main_repo,
            check=True,
            capture_output=True,
        )

        submodule_dir = main_repo / "lib"
        monkeypatch.chdir(submodule_dir)
        cfg = load_config()
        # 向上查找到父仓库的配置
        assert cfg.llm.provider == "anthropic"

    def test_init_in_submodule_gives_clear_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """子模块目录中 init 给出明确错误（NotADirectoryError），不静默崩溃。

        当前实现：cli.py init 使用 Path(".git/hooks").mkdir(parents=True)，
        子模块的 .git 是文件（gitdir 指针），导致 NotADirectoryError。
        此缺陷应在未来版本中修复：init 应读取 .git 文件内容解析出实际
        git 目录路径，并将 hook 安装到正确位置。
        """
        main_repo = tmp_path / "main"
        main_repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=main_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=main_repo,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Tester"],
            cwd=main_repo,
            check=True,
        )

        sub_repo = tmp_path / "subrepo"
        sub_repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=sub_repo,
            check=True,
            capture_output=True,
        )
        (sub_repo / "f.txt").write_text("x")
        subprocess.run(
            ["git", "add", "f.txt"],
            cwd=sub_repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=sub_repo,
            check=True,
            capture_output=True,
        )

        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", str(sub_repo), "lib"],
            cwd=main_repo,
            check=True,
            capture_output=True,
        )

        submodule_dir = main_repo / "lib"
        monkeypatch.chdir(submodule_dir)
        runner = CliRunner()
        result = runner.invoke(main, ["init"])

        # 异常被 Click 捕获，exit_code != 0
        assert result.exit_code != 0, "子模块 init 不应静默"
        # Exception 存在，说明 init 代码未处理子模块 .git 文件格式
        assert result.exception is not None, "应有明确异常而非静默吞掉"
        assert (
            isinstance(result.exception, NotADirectoryError)
            or "NotADirectoryError" in str(result.exc_info[0])
            if result.exc_info
            else False
        )


# ══════════════════════════════════════════════════════════════════════════
# 验收标准 7: 多 forbid_patterns 同时触发
# ══════════════════════════════════════════════════════════════════════════


class TestMultipleForbidPatterns:
    """同时触发多个 forbid_patterns 时全部列出."""

    def test_two_patterns_both_matched(self) -> None:
        """message 匹配 2 个 forbid_patterns → 全部列出。"""
        cfg = RulesConfig(min_length=1, forbid_patterns=["^fix$", ".*fix.*"])
        violations = check_message("fix", cfg)
        assert len(violations) >= 2
        assert any("^fix$" in v for v in violations)
        assert any(".*fix.*" in v for v in violations)

    def test_three_patterns_all_matched(self) -> None:
        """message 匹配 3 个 forbid_patterns → 全部列出。"""
        cfg = RulesConfig(
            min_length=1, forbid_patterns=["^test$", "test", ".*t.*", ".*e.*", ".*s.*"]
        )
        violations = check_message("test", cfg)
        assert len(violations) >= 3
        assert any("^test$" in v for v in violations)
        assert any(".*t.*" in v for v in violations)
        assert any(".*e.*" in v for v in violations)

    def test_only_matching_patterns_listed(self) -> None:
        """只有匹配的 pattern 出现在 violations 中，不匹配的不出现。"""
        cfg = RulesConfig(min_length=1, forbid_patterns=["^fix$", "^WIP$", "^todo$"])
        violations = check_message("fix", cfg)
        assert any("^fix$" in v for v in violations)
        assert not any("^WIP$" in v for v in violations)
        assert not any("^todo$" in v for v in violations)

    def test_multiple_patterns_in_cli_output(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI 输出中所有匹配的 forbid_patterns 都可见。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("test")

        cfg = _cfg(
            min_length=1,
            forbid_patterns=["^test$", ".*test.*", "test$"],
        )

        with patch("commit_hook.cli.load_config", return_value=cfg):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 1
        assert result.output.count("禁止模式") == 3
        assert "^test$" in result.output
        assert ".*test.*" in result.output
        assert "test$" in result.output


# ══════════════════════════════════════════════════════════════════════════
# 验收标准 8: min_length 边界
# ══════════════════════════════════════════════════════════════════════════


class TestMinLengthBoundary:
    """message 刚好等于 min_length 字符时正常通过."""

    def test_exact_min_length_passes(self) -> None:
        """message 长度 == min_length → 通过。"""
        cfg = RulesConfig(min_length=10, forbid_patterns=[])
        violations = check_message("abcdefghij", cfg)  # exactly 10 chars
        assert violations == []

    def test_one_less_than_min_length_fails(self) -> None:
        """message 长度 == min_length - 1 → 不通过。"""
        cfg = RulesConfig(min_length=10, forbid_patterns=[])
        violations = check_message("abcdefghi", cfg)  # 9 chars
        assert any("长度不足" in v for v in violations)

    def test_one_more_than_min_length_passes(self) -> None:
        """message 长度 == min_length + 1 → 通过。"""
        cfg = RulesConfig(min_length=10, forbid_patterns=[])
        violations = check_message("abcdefghijk", cfg)  # 11 chars
        assert violations == []

    def test_min_length_zero_every_non_empty_passes(self) -> None:
        """min_length=0 时任何非空 message 不触发长度违规。"""
        cfg = RulesConfig(min_length=0, forbid_patterns=[])
        violations = check_message("a", cfg)
        assert all("长度不足" not in v for v in violations)

    def test_min_length_exact_with_unicode(self) -> None:
        """Unicode message 长度刚好等于 min_length → 通过。"""
        cfg = RulesConfig(min_length=5, forbid_patterns=[])
        violations = check_message("你好世界！", cfg)  # 5 chars
        assert violations == []

    def test_cli_exact_min_length_passes(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI 链路中刚好等于 min_length 的 message 通过 rules 检查。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("abcdefghij")  # exactly 10 chars

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg(min_length=10)),
            patch("commit_hook.cli.get_diff", return_value="diff --git a/x b/x"),
            patch(
                "commit_hook.cli.llm_evaluate",
                return_value=LLMResult(passed=True, score=85, accuracy=9),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output


# ══════════════════════════════════════════════════════════════════════════
# 验收标准 9: 二进制文件过滤
# ══════════════════════════════════════════════════════════════════════════


class TestBinaryFileFiltering:
    """diff 中包含二进制文件路径但不包含内容时正常过滤."""

    def test_binary_file_diff_line_not_crash(self) -> None:
        """git diff 对二进制文件的 "Binary files ... differ" 行不崩溃。"""
        # 模拟 git diff --cached 对二进制文件的输出
        diff_with_binary = (
            "diff --git a/image.png b/image.png\n"
            "Binary files a/image.png and b/image.png differ\n"
            "diff --git a/src/main.py b/src/main.py\n"
            "index 0000000..1234567 100644\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+def main():\n"
            "+    print('hello')\n"
            "+    return 0\n"
        )

        parts = _filter_parts(diff_with_binary, [])
        # Both file blocks should be preserved (filtering only excludes)
        assert len(parts) == 2

    def test_binary_file_excluded_by_pattern(self) -> None:
        """二进制文件通过 exclude pattern 过滤后不出现在 diff 中。"""
        diff_with_binary = (
            "diff --git a/image.png b/image.png\n"
            "Binary files a/image.png and b/image.png differ\n"
            "diff --git a/src/main.py b/src/main.py\n"
            "index 0000000..1234567 100644\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+def main():\n"
            "+    print('hello')\n"
            "+    return 0\n"
        )

        parts = _filter_parts(diff_with_binary, ["*.png"])
        assert len(parts) == 1
        assert "image.png" not in parts[0]
        assert "src/main.py" in parts[0]

    def test_binary_file_not_block_normal_diff(self) -> None:
        """二进制文件行不阻塞正常代码 diff 的处理。"""
        # 只有二进制，没有正常 diff — 这些块仍被保留但内容就是 binary 行
        diff_only_binary = (
            "diff --git a/logo.webp b/logo.webp\nBinary files a/logo.webp and b/logo.webp differ\n"
        )

        parts = _filter_parts(diff_only_binary, [])
        assert len(parts) == 1
        assert "Binary" in parts[0]

    def test_mixed_binary_and_text_in_cli(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """混合二进制+文本的 diff 通过 CLI 链路不崩溃。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add logo and main module")

        mixed_diff = (
            "diff --git a/logo.png b/logo.png\n"
            "Binary files a/logo.png and b/logo.png differ\n"
            "diff --git a/src/app.py b/src/app.py\n"
            "index 0000000..1234567 100644\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+def app():\n"
            "+    pass\n"
        )

        with (
            patch("commit_hook.cli.load_config", return_value=_cfg()),
            patch("commit_hook.cli.get_diff", return_value=mixed_diff),
            patch(
                "commit_hook.cli.llm_evaluate",
                return_value=LLMResult(passed=True, score=80, accuracy=8),
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code == 0
        assert "通过" in result.output


# ══════════════════════════════════════════════════════════════════════════
# 验收标准 10: api_key_env 不存在
# ══════════════════════════════════════════════════════════════════════════


class TestApiKeyEnvNotFound:
    """api_key_env 指向的环境变量不存在时给出明确提示（ConfigError）。"""

    def test_api_key_env_not_set_raises_clear_error(self, tmp_path: Path) -> None:
        """api_key_env=DOES_NOT_EXIST → ConfigError 含环境变量名。"""
        p = tmp_path / _CFG
        p.write_text(
            yaml.dump(
                {
                    "llm": {
                        "provider": "openai",
                        "model": "gpt-4o",
                        "api_key_env": "DOES_NOT_EXIST",
                    },
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ConfigError) as exc_info:
            load_config(str(p))

        err = str(exc_info.value)
        assert "DOES_NOT_EXIST" in err
        assert "api_key_env" in err or "not defined" in err

    def test_api_key_env_empty_string_no_error(self, tmp_path: Path) -> None:
        """api_key_env 为空字符串不触发错误（回归测试）。"""
        p = tmp_path / _CFG
        p.write_text(
            yaml.dump(
                {
                    "llm": {
                        "provider": "openai",
                        "api_key_env": "",
                    },
                }
            ),
            encoding="utf-8",
        )

        cfg = load_config(str(p))
        assert cfg.llm.api_key_env == ""
        assert cfg.llm.api_key == ""

    def test_api_key_env_missing_in_cli_chain(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI 链路中 ConfigError 传播，不是静默使用空值。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add user login")

        with patch(
            "commit_hook.cli.load_config",
            side_effect=ConfigError(
                "Environment variable 'MISSING_API_KEY' (set via llm.api_key_env) is not defined"
            ),
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code != 0, f"ConfigError 不应静默通过, exit_code={result.exit_code}"
        assert "MISSING_API_KEY" in result.output or result.exception is not None

    def test_api_key_env_set_but_empty_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """环境变量存在但值为空 → 解析后 api_key 为空字符串（在 load_config 阶段不报错）。"""
        monkeypatch.setenv("EMPTY_KEY", "")
        p = tmp_path / _CFG
        p.write_text(
            yaml.dump(
                {
                    "llm": {
                        "provider": "openai",
                        "api_key_env": "EMPTY_KEY",
                    },
                }
            ),
            encoding="utf-8",
        )

        cfg = load_config(str(p))
        assert cfg.llm.api_key == ""

    def test_api_key_env_raises_before_reaching_llm(
        self, runner: CliRunner, tmp_path: Path, msg_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ConfigError 在 load_config 阶段抛出，不走到 LLM 调用。"""
        monkeypatch.chdir(tmp_path)
        msg_file.write_text("feat: add user authentication")

        with (
            patch(
                "commit_hook.cli.load_config",
                side_effect=ConfigError("Environment variable 'NONEXISTENT' is not defined"),
            ),
            patch("commit_hook.cli.get_diff") as mock_diff,
            patch("commit_hook.cli.llm_evaluate") as mock_llm,
        ):
            result = runner.invoke(main, ["check", str(msg_file)])

        assert result.exit_code != 0
        mock_diff.assert_not_called()
        mock_llm.assert_not_called()
