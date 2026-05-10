"""Terminal output formatting for commit-hook.

Provides three output modes:
- Green pass (LLM approves or no diff to check).
- Red fail (rule violations or LLM rejection).
- Yellow degraded (LLM unavailable, commit allowed).
"""

from __future__ import annotations

from rich.console import Console


def report_pass(score: int, accuracy: int, *, console: Console | None = None) -> None:
    """Display a green pass message with score and accuracy.

    Args:
        score: Overall quality score (0-100).
        accuracy: Accuracy rating (0-10).
        console: Optional rich Console for output capture during testing.
    """
    out = console if console is not None else Console()
    out.print(
        f"✅ 通过 — Score: {score}/100  Accuracy: {accuracy}/10",
        style="bold green",
    )


def report_fail(
    issues: list[str],
    suggestion: str,
    *,
    console: Console | None = None,
) -> None:
    """Display a red failure message with issues and suggestion.

    Args:
        issues: List of violation strings from rules or LLM.
        suggestion: Suggested improvement text from LLM.
        console: Optional rich Console for output capture during testing.
    """
    out = console if console is not None else Console()
    out.print("❌ 不通过", style="bold red")
    if issues:
        out.print()
        out.print("问题列表:", style="bold")
        for issue in issues:
            out.print(f"  • {issue}")
    if suggestion:
        out.print()
        out.print(f"建议: {suggestion}", style="bold")


def report_degraded(reason: str, *, console: Console | None = None) -> None:
    """Display a yellow degraded-pass warning.

    Args:
        reason: Explanation for why the check was skipped or degraded.
        console: Optional rich Console for output capture during testing.
    """
    out = console if console is not None else Console()
    out.print(f"⚠️ 降级放行: {reason}", style="bold yellow")
