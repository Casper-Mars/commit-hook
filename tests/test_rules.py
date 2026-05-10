"""Tests for the local rules module."""

from __future__ import annotations

from commit_hook.config import RulesConfig
from commit_hook.rules import check_message


def test_empty_message_is_blocked() -> None:
    """空 message → 拦截，输出「commit message 不能为空」."""
    cfg = RulesConfig()
    violations = check_message("", cfg)
    assert violations == ["commit message 不能为空"]


def test_fix_is_blocked_by_forbid_patterns() -> None:
    """ "fix" → 被 forbid_patterns 拦截，终端显示命中哪个规则."""
    cfg = RulesConfig()
    violations = check_message("fix", cfg)
    assert len(violations) >= 1
    assert any("^fix$" in v for v in violations)


def test_update_is_blocked() -> None:
    """ "update" → 被 forbid_patterns 拦截."""
    cfg = RulesConfig()
    violations = check_message("update", cfg)
    has_update_pattern = any("^update$" in v for v in violations)
    assert has_update_pattern


def test_wip_is_blocked() -> None:
    """ "WIP" → 被 forbid_patterns 拦截."""
    cfg = RulesConfig()
    violations = check_message("WIP", cfg)
    has_wip_pattern = any("^WIP$" in v for v in violations)
    assert has_wip_pattern


def test_wip_lowercase_is_blocked() -> None:
    """ "wip" → 被 forbid_patterns 拦截."""
    cfg = RulesConfig()
    violations = check_message("wip", cfg)
    has_wip_pattern = any("^wip$" in v for v in violations)
    assert has_wip_pattern


def test_message_shorter_than_min_length_is_blocked() -> None:
    """message 长度 < min_length → 拦截，输出长度信息."""
    cfg = RulesConfig(min_length=10, forbid_patterns=[])
    violations = check_message("abc", cfg)
    assert len(violations) == 1
    assert "长度不足" in violations[0]
    assert "3 字符" in violations[0]
    assert "≥ 10" in violations[0]


def test_message_at_min_length_passes_length_check() -> None:
    """message 长度等于 min_length 时不触发长度违规."""
    cfg = RulesConfig(min_length=4, forbid_patterns=[])
    violations = check_message("abcd", cfg)
    assert all("长度不足" not in v for v in violations)


def test_valid_message_passes() -> None:
    """ "feat: add user login" → 放行."""
    cfg = RulesConfig()
    violations = check_message("feat: add user login", cfg)
    assert violations == []


def test_fix_prefix_not_blocked() -> None:
    """ "fix: something" should not match ^fix$ and should pass."""
    cfg = RulesConfig()
    violations = check_message("fix: resolve bug", cfg)
    assert violations == []


def test_update_prefix_not_blocked() -> None:
    """ "update: something" should not match ^update$ and should pass."""
    cfg = RulesConfig()
    violations = check_message("update: changelog", cfg)
    assert violations == []


def test_wip_prefix_not_blocked() -> None:
    """ "WIP: something" should not match ^WIP$ and should pass."""
    cfg = RulesConfig()
    violations = check_message("WIP: frontend rewrite", cfg)
    assert violations == []


def test_custom_forbid_patterns() -> None:
    """Custom forbid_patterns are respected."""
    cfg = RulesConfig(min_length=1, forbid_patterns=["^TODO$", "^XXX$"])
    violations = check_message("TODO", cfg)
    assert any("^TODO$" in v for v in violations)


def test_multiple_violations_are_collected() -> None:
    """A message can trigger both length and pattern violations."""
    cfg = RulesConfig(min_length=100, forbid_patterns=["^fix$"])
    violations = check_message("fix", cfg)
    assert len(violations) == 2
    assert any("长度不足" in v for v in violations)
    assert any("^fix$" in v for v in violations)


def test_empty_whitespace_is_not_empty_string() -> None:
    """Whitespace-only messages are not considered empty."""
    cfg = RulesConfig(min_length=1, forbid_patterns=[])
    violations = check_message("   ", cfg)
    assert "不能为空" not in violations


def test_no_pattern_no_min_length() -> None:
    """With no patterns and min_length=0, all non-empty messages pass."""
    cfg = RulesConfig(min_length=0, forbid_patterns=[])
    violations = check_message("x", cfg)
    assert violations == []
