"""Local rule checks executed before LLM validation."""

from __future__ import annotations

import re

from commit_hook.config import RulesConfig


def check_message(message: str, config: RulesConfig) -> list[str]:
    """Execute local rule checks on a commit message.

    Checks are performed in order: empty check, minimum length,
    forbidden pattern matching. All violations are collected
    before returning.

    Args:
        message: The commit message to check.
        config: Local rules configuration.

    Returns:
        A list of violation strings. An empty list means the message
        passes all checks.
    """
    violations: list[str] = []

    if not message:
        violations.append("commit message 不能为空")
        return violations

    if len(message) < config.min_length:
        violations.append(f"message 长度不足，当前 {len(message)} 字符，要求 ≥ {config.min_length}")

    for pattern in config.forbid_patterns:
        if re.match(pattern, message):
            violations.append(f"message 匹配禁止模式: {pattern}")

    return violations
