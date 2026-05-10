"""Tests for the reporter module."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from commit_hook.reporter import report_degraded, report_fail, report_pass


def _console() -> Console:
    """Return a Console that writes to a StringIO for output capture."""
    return Console(file=io.StringIO(), force_terminal=True, color_system="truecolor")


def _out(console: Console) -> str:
    """Extract plain-text output from a StringIO-backed Console."""
    assert console.file is not None
    return console.file.getvalue()  # type: ignore[no-any-return]


class TestReportPass:
    """Tests for report_pass — green pass output."""

    def test_basic_output(self) -> None:
        """Output contains checkmark, score, and accuracy labels."""
        c = _console()
        report_pass(85, 9, console=c)
        text = _out(c)
        assert "通过" in text
        assert "85" in text
        assert "9" in text

    def test_perfect_score(self) -> None:
        """Score 100 and accuracy 10 are rendered correctly."""
        c = _console()
        report_pass(100, 10, console=c)
        text = _out(c)
        assert "100" in text
        assert "10" in text

    def test_low_scores(self) -> None:
        """Score 0 and accuracy 0 render without error."""
        c = _console()
        report_pass(0, 0, console=c)
        text = _out(c)
        assert "0" in text


class TestReportFail:
    """Tests for report_fail — red failure output."""

    def test_basic_output(self) -> None:
        """Output contains failure marker."""
        c = _console()
        report_fail([], "", console=c)
        text = _out(c)
        assert "不通过" in text

    def test_with_issues(self) -> None:
        """Issues are listed in the output."""
        c = _console()
        report_fail(["issue one", "issue two"], "", console=c)
        text = _out(c)
        assert "issue one" in text
        assert "issue two" in text
        assert "问题列表" in text

    def test_with_suggestion(self) -> None:
        """Suggestion text appears when provided."""
        c = _console()
        report_fail([], "Try harder", console=c)
        text = _out(c)
        assert "Try harder" in text
        assert "建议" in text

    def test_with_both_issues_and_suggestion(self) -> None:
        """Both issues and suggestion are rendered together."""
        c = _console()
        report_fail(["bad"], "fix it", console=c)
        text = _out(c)
        assert "bad" in text
        assert "fix it" in text


class TestReportDegraded:
    """Tests for report_degraded — yellow warning output."""

    def test_basic_output(self) -> None:
        """Output contains warning marker and reason."""
        c = _console()
        report_degraded("network timeout", console=c)
        text = _out(c)
        assert "降级放行" in text
        assert "network timeout" in text

    def test_empty_reason(self) -> None:
        """Empty reason still produces the warning prefix."""
        c = _console()
        report_degraded("", console=c)
        text = _out(c)
        assert "降级放行" in text


@pytest.mark.parametrize(
    "fn",
    [report_pass, report_fail, report_degraded],
)
def test_reporter_accepts_none_console(fn: object) -> None:
    """All reporter functions work when console is None (default)."""
    if fn is report_pass:
        fn(50, 5)
    elif fn is report_fail:
        fn([], "")
    elif fn is report_degraded:
        fn("test")
