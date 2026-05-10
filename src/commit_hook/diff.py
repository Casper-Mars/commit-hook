"""Diff extraction utilities for commit-hook."""

from __future__ import annotations

import fnmatch
import subprocess

from commit_hook.config import Config

_LINE_LIMIT_MSG = "[diff truncated at {total} lines]"


def get_diff(config: Config) -> str:
    """Extract the staged diff, filtering and truncating according to *config*.

    Runs ``git diff --cached``, excludes files matching any glob in
    ``config.diff.exclude`` via :func:`fnmatch.fnmatch`, and truncates the
    output to ``config.diff.max_lines`` when exceeded.

    Args:
        config: The commit-hook configuration containing diff settings.

    Returns:
        The filtered and possibly truncated diff as a string.  An empty
        string when there are no staged changes or when every file is
        excluded.

    Raises:
        subprocess.CalledProcessError: If ``git diff --cached`` fails (e.g.
            not in a Git repository or git not installed).
        FileNotFoundError: If ``git`` is not available on ``PATH``.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError("git command not found on PATH") from exc
    raw = proc.stdout
    if not raw:
        return ""
    lines = raw.splitlines(keepends=False)
    diff_parts: list[str] = _filter_parts(raw, config.diff.exclude)
    result = "".join(diff_parts)
    return _truncate(result, lines, config.diff.max_lines)


def _filter_parts(raw: str, exclude_patterns: list[str]) -> list[str]:
    """Split raw diff into per-file chunks and drop excluded ones.

    Args:
        raw: Full output from ``git diff --cached``.
        exclude_patterns: Glob patterns to match against file paths.

    Returns:
        List of text chunks for non-excluded files.
    """
    parts: list[str] = []
    current = ""
    current_path: str | None = None
    for line in raw.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current_path is not None and not _is_excluded(current_path, exclude_patterns):
                parts.append(current)
            current = line
            current_path = _extract_path(line)
        else:
            current += line
    if current_path is not None and not _is_excluded(current_path, exclude_patterns):
        parts.append(current)
    return parts


def _extract_path(diff_line: str) -> str:
    """Extract the file path from a ``diff --git a/<path> b/<path>`` header.

    Args:
        diff_line: The first line of a diff block.

    Returns:
        The file path portion (e.g. ``src/main.py``).
    """
    tokens = diff_line.split()
    if len(tokens) >= 4 and tokens[2].startswith("a/"):
        return tokens[2][2:]  # strip "a/" prefix
    return tokens[-1] if tokens else ""


def _is_excluded(path: str, patterns: list[str]) -> bool:
    """Return True when *path* matches any glob in *patterns*.

    Args:
        path: A file path relative to the repository root.
        patterns: Glob patterns (``fnmatch`` style).

    Returns:
        ``True`` if the file should be excluded.
    """
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _truncate(text: str, lines: list[str], max_lines: int) -> str:
    """Truncate *text* when total line count exceeds *max_lines*.

    Preserves the original line count from the pre-filtered *lines* list so
    the truncation message reflects the *full* diff size.  When the output
    is truncated the last line will be the informational marker.

    Args:
        text: The already-filtered diff text.
        lines: The full pre-filtered line list (for the count).
        max_lines: Maximum number of lines to keep.

    Returns:
        Possibly truncated text.
    """
    total = len(lines)
    if total > max_lines:
        kept = text.splitlines(keepends=True)[:max_lines]
        kept.append(_LINE_LIMIT_MSG.format(total=total) + "\n")
        return "".join(kept)
    return text
