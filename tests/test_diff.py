"""Tests for diff module."""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from commit_hook.config import Config, DiffConfig
from commit_hook.diff import _filter_parts, _is_excluded, _truncate, get_diff

# ---------------------------------------------------------------------------
# Sample diff fixtures
# ---------------------------------------------------------------------------


def _make_simple_diff() -> str:
    """Return a minimal multi-file diff string for testing."""
    return (
        "diff --git a/src/main.py b/src/main.py\n"
        "index 123..456 100644\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -1,3 +1,4 @@\n"
        " context\n"
        "-old\n"
        "+new\n"
        " more\n"
        "diff --git a/package-lock.json b/package-lock.json\n"
        "index abc..def 100644\n"
        "--- a/package-lock.json\n"
        "+++ b/package-lock.json\n"
        "@@ -1,2 +1,2 @@\n"
        " lock change\n"
        "diff --git a/app.min.js b/app.min.js\n"
        "index 111..222 100644\n"
        "--- a/app.min.js\n"
        "+++ b/app.min.js\n"
        "@@ -1,1 +1,1 @@\n"
        " minified\n"
        "diff --git a/docs/logo.svg b/docs/logo.svg\n"
        "index 333..444 100644\n"
        "--- a/docs/logo.svg\n"
        "+++ b/docs/logo.svg\n"
        "@@ -1,1 +1,1 @@\n"
        " svg file\n"
        "diff --git a/.env.local b/.env.local\n"
        "index 555..666 100644\n"
        "--- a/.env.local\n"
        "+++ b/.env.local\n"
        "@@ -1,1 +1,1 @@\n"
        " env var\n"
        "diff --git a/secrets/token.pem b/secrets/token.pem\n"
        "index 777..888 100644\n"
        "--- a/secrets/token.pem\n"
        "+++ b/secrets/token.pem\n"
        "@@ -1,1 +1,1 @@\n"
        " pem key\n"
        "diff --git a/config.yaml b/config.yaml\n"
        "index 999..aaa 100644\n"
        "--- a/config.yaml\n"
        "+++ b/config.yaml\n"
        "@@ -1,2 +1,3 @@\n"
        " settings\n"
        "+new_setting\n"
    )


def _make_large_diff(line_count: int) -> str:
    """Return a diff with *line_count* lines (including header)."""
    header = (
        "diff --git a/large.py b/large.py\n"
        "index 123..456 100644\n"
        "--- a/large.py\n"
        "+++ b/large.py\n"
        "@@ -1,1 +1,1 @@\n"
    )
    body = "\n".join(f"line {i}" for i in range(line_count - 5))
    return header + body


# ---------------------------------------------------------------------------
# get_diff – integration with subprocess
# ---------------------------------------------------------------------------


def test_get_diff_returns_empty_when_no_staged_changes() -> None:
    """Empty string when git diff --cached produces no output."""
    cfg = Config()
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout="", stderr="")
        result = get_diff(cfg)
    assert result == ""


def test_get_diff_calls_git_diff_cached() -> None:
    """Verify the subprocess command is correct."""
    cfg = Config()
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout="diff --git a/x.py b/x.py\n", stderr="")
        get_diff(cfg)
    mock_run.assert_called_once_with(
        ["git", "diff", "--cached"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_get_diff_git_not_found() -> None:
    """FileNotFoundError when git is not on PATH."""
    cfg = Config()
    with (
        mock.patch("subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(FileNotFoundError, match="git command not found"),
    ):
        get_diff(cfg)


def test_get_diff_called_process_error() -> None:
    """subprocess.CalledProcessError propagates."""
    cfg = Config()
    with (
        mock.patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(128, "git"),
        ),
        pytest.raises(subprocess.CalledProcessError),
    ):
        get_diff(cfg)


# ---------------------------------------------------------------------------
# get_diff – filtering
# ---------------------------------------------------------------------------


def test_get_diff_excludes_lock_files() -> None:
    """package-lock.json change is excluded from output."""
    cfg = Config()
    diff = _make_simple_diff()
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout=diff, stderr="")
        result = get_diff(cfg)
    assert "package-lock.json" not in result
    assert "lock change" not in result


def test_get_diff_excludes_min_js() -> None:
    """Minified JS files are excluded."""
    cfg = Config()
    diff = _make_simple_diff()
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout=diff, stderr="")
        result = get_diff(cfg)
    assert "app.min.js" not in result
    assert "minified" not in result


def test_get_diff_excludes_svg() -> None:
    """SVG files are excluded."""
    cfg = Config()
    diff = _make_simple_diff()
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout=diff, stderr="")
        result = get_diff(cfg)
    assert "docs/logo.svg" not in result
    assert "svg file" not in result


def test_get_diff_excludes_env_files() -> None:
    """*.env* files are excluded."""
    cfg = Config()
    diff = _make_simple_diff()
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout=diff, stderr="")
        result = get_diff(cfg)
    assert ".env.local" not in result
    assert "env var" not in result


def test_get_diff_excludes_pem_files() -> None:
    """*.pem files are excluded."""
    cfg = Config()
    diff = _make_simple_diff()
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout=diff, stderr="")
        result = get_diff(cfg)
    assert "secrets/token.pem" not in result
    assert "pem key" not in result


def test_get_diff_keeps_non_excluded_files() -> None:
    """src/main.py and config.yaml should remain in output."""
    cfg = Config()
    diff = _make_simple_diff()
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout=diff, stderr="")
        result = get_diff(cfg)
    assert "src/main.py" in result
    assert "config.yaml" in result


def test_get_diff_all_excluded_returns_empty() -> None:
    """When every file is excluded the result is an empty string."""
    cfg = Config(diff=DiffConfig(exclude=["*"], max_lines=500))
    diff = _make_simple_diff()
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout=diff, stderr="")
        result = get_diff(cfg)
    assert result == ""


# ---------------------------------------------------------------------------
# get_diff – truncation
# ---------------------------------------------------------------------------


def test_get_diff_truncates_long_output() -> None:
    """Output exceeding max_lines is truncated with a marker."""
    cfg = Config(diff=DiffConfig(exclude=[], max_lines=10))
    large = _make_large_diff(line_count=30)
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout=large, stderr="")
        result = get_diff(cfg)
    result_lines = result.splitlines()
    assert len(result_lines) == 11  # 10 kept + truncation marker
    assert result_lines[-1] == "[diff truncated at 30 lines]"


def test_get_diff_no_truncation_on_exact_limit() -> None:
    """Output at exactly max_lines is NOT truncated."""
    cfg = Config(diff=DiffConfig(exclude=[], max_lines=10))
    diff = _make_large_diff(line_count=10)
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout=diff, stderr="")
        result = get_diff(cfg)
    assert "[diff truncated" not in result


def test_get_diff_no_truncation_when_under_limit() -> None:
    """Output under max_lines is preserved in full."""
    cfg = Config(diff=DiffConfig(exclude=[], max_lines=100))
    diff = _make_large_diff(line_count=10)
    with mock.patch("subprocess.run") as mock_run:
        mock_run.return_value = mock.MagicMock(stdout=diff, stderr="")
        result = get_diff(cfg)
    assert "[diff truncated" not in result
    assert result.strip() == diff.strip()


# ---------------------------------------------------------------------------
# _filter_parts unit tests
# ---------------------------------------------------------------------------


def test_filter_parts_removes_matching_globs() -> None:
    """Matching glob patterns cause file blocks to be dropped."""
    raw = (
        "diff --git a/keep.py b/keep.py\n"
        "--- a/keep.py\n"
        "+++ b/keep.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
        "diff --git a/drop.lock b/drop.lock\n"
        "--- a/drop.lock\n"
        "+++ b/drop.lock\n"
        "@@ -1 +1 @@\n"
        " lock\n"
    )
    parts = _filter_parts(raw, ["*.lock"])
    assert len(parts) == 1
    assert "keep.py" in parts[0]
    assert "drop.lock" not in parts[0]


def test_filter_parts_empty_raw() -> None:
    """Empty string yields no parts."""
    assert _filter_parts("", ["*"]) == []


def test_filter_parts_no_patterns() -> None:
    """Empty exclude list keeps everything."""
    raw = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n x\n"
    assert len(_filter_parts(raw, [])) == 1


# ---------------------------------------------------------------------------
# _is_excluded unit tests
# ---------------------------------------------------------------------------


def test_is_excluded_exact_match() -> None:
    """Exact glob match returns True."""
    assert _is_excluded("file.lock", ["*.lock"]) is True


def test_is_excluded_path_with_dirs() -> None:
    """Glob matches against full relative path."""
    assert _is_excluded("a/b/c/file.lock", ["*.lock"]) is True


@pytest.mark.parametrize(
    "path,pattern",
    [
        ("src/.env", "*.env*"),
        ("src/.env.local", "*.env*"),
        ("config/secret.yaml", "*secret*"),
        ("certs/server.pem", "*.pem"),
        ("dist/app.min.js", "*.min.js"),
        ("assets/icon.svg", "*.svg"),
        ("assets/photo.png", "*.png"),
        ("assets/photo.jpg", "*.jpg"),
        ("assets/photo.jpeg", "*.jpeg"),
        ("assets/animation.gif", "*.gif"),
        ("assets/favicon.ico", "*.ico"),
        ("assets/banner.webp", "*.webp"),
        ("vendor/bundle.map", "*.map"),
    ],
)
def test_is_excluded_default_patterns(path: str, pattern: str) -> None:
    """Every default pattern matches its expected file extension."""
    assert _is_excluded(path, [pattern]) is True


def test_is_excluded_no_match() -> None:
    """Non-matching path returns False."""
    assert _is_excluded("src/main.py", ["*.lock"]) is False


def test_is_excluded_empty_list() -> None:
    """Empty pattern list never excludes anything."""
    assert _is_excluded("any/file.txt", []) is False


# ---------------------------------------------------------------------------
# _truncate unit tests
# ---------------------------------------------------------------------------


def test_truncate_under_limit() -> None:
    """No changes when line count <= max."""
    text = "line1\nline2\n"
    lines = ["line1", "line2"]
    assert _truncate(text, lines, 5) == text


def test_truncate_at_limit() -> None:
    """No changes when line count == max_lines."""
    text = "a\nb\nc\n"
    lines = ["a", "b", "c"]
    assert _truncate(text, lines, 3) == text


def test_truncate_over_limit_adds_marker() -> None:
    """Marker appended when total lines exceed max."""
    text = "one\ntwo\nthree\nfour\nfive\n"
    lines = ["one", "two", "three", "four", "five"]
    result = _truncate(text, lines, 2)
    result_lines = result.splitlines()
    assert len(result_lines) == 3  # 2 kept + marker
    assert result_lines[-1] == "[diff truncated at 5 lines]"
    assert result_lines[0] == "one"
    assert result_lines[1] == "two"
