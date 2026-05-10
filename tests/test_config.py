"""Tests for config module."""


def test_load_config_empty() -> None:
    """Test loading an empty config."""
    import tempfile

    from commit_hook.config import load_config

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("")
    result = load_config(f.name)
    assert result == {}
