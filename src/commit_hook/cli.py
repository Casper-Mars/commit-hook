"""CLI entrypoint for commit-hook.

Provides three commands:
- ``init``: Install the commit-msg hook wrapper into .git/hooks/.
- ``uninit``: Remove the installed hook file.
- ``check``: Validate a commit message against local rules and an optional LLM.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from commit_hook.config import load_config
from commit_hook.diff import get_diff
from commit_hook.llm import LLMUnavailableError, llm_evaluate
from commit_hook.reporter import report_degraded, report_fail, report_pass
from commit_hook.rules import check_message

HOOK_PATH = ".git/hooks/commit-msg"
HOOK_CONTENT = """#!/usr/bin/env bash
# commit-hook — installed by `commit-hook init`
exec commit-hook check "$1"
"""


@click.group()
@click.version_option(version="0.1.0", prog_name="commit-hook")
def main() -> None:
    """AI-powered commit message validator.

    Validate commit messages against configurable rules using LLMs.
    """


@main.command()
def init() -> None:
    """Install commit-msg hook into .git/hooks/commit-msg.

    Creates a shell wrapper that invokes ``commit-hook check`` for every
    commit.  Does nothing when the hook already exists, printing a hint.
    """
    hook_file = Path(HOOK_PATH)
    if hook_file.exists():
        click.echo(f"Hook already exists at {hook_file}, not overwriting.", err=True)
        return
    hook_file.parent.mkdir(parents=True, exist_ok=True)
    hook_file.write_text(HOOK_CONTENT, encoding="utf-8")
    hook_file.chmod(0o755)
    click.echo(f"Hook installed at {hook_file}")


@main.command()
def uninit() -> None:
    """Remove commit-msg hook from .git/hooks/commit-msg.

    Prints a message when no hook is found instead of raising an error.
    """
    hook_file = Path(HOOK_PATH)
    if hook_file.exists():
        hook_file.unlink()
        click.echo(f"Hook removed from {hook_file}")
    else:
        click.echo(f"No hook found at {hook_file}", err=True)


@main.command()
@click.argument("file", type=click.Path(exists=True))
def check(file: str) -> None:
    """Validate commit message in FILE.

    Execution order: local rules → staged diff → LLM evaluation.
    sys.exit(0) on pass or degraded; sys.exit(1) on violations.
    """
    # 1. Read commit message
    message = Path(file).read_text(encoding="utf-8").strip()

    # 2. Load config
    config = load_config()

    # 3. Local rules check
    violations = check_message(message, config.rules)
    if violations:
        report_fail(violations, "")
        sys.exit(1)

    # 4. Get staged diff
    try:
        diff = get_diff(config)
    except (FileNotFoundError, subprocess.CalledProcessError):
        diff = ""
    if not diff.strip():
        click.echo("✅ 通过 — 无变更内容，跳过 LLM 检查")
        sys.exit(0)

    # 5. LLM evaluation
    try:
        result = llm_evaluate(diff, message, config.llm)
    except LLMUnavailableError as exc:
        report_degraded(str(exc))
        sys.exit(0)

    if result.passed:
        report_pass(result.score, result.accuracy)
        sys.exit(0)
    else:
        report_fail(result.issues, result.suggestion)
        sys.exit(1)
