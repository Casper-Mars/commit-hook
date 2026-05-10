"""Tests for diff module."""


def test_extract_diff() -> None:
    """Test extract_diff returns None when no repo."""
    from commit_hook.diff import extract_diff

    result = extract_diff()
    assert result is None
