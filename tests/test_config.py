"""Tests for config module."""

from __future__ import annotations

import os
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from commit_hook.config import (
    _CFG,
    _DEF_EXCLUDE,
    _DEF_FORBID_PATTERNS,
    _DEF_MAX_LINES,
    _DEF_MIN_LENGTH,
    _DEF_MODEL,
    _DEF_PROVIDER,
    Config,
    ConfigError,
    LLMConfig,
    load_config,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Isolated temporary directory that serves as the repo root."""
    return tmp_path


@pytest.fixture
def use_config(temp_dir: Path) -> Callable[[dict[str, Any]], None]:
    """Write a .commit-hook.yaml in *temp_dir* and chdir there."""

    def _write(content: dict[str, Any]) -> None:
        (temp_dir / _CFG).write_text(yaml.dump(content), encoding="utf-8")
        os.chdir(temp_dir)

    return _write


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------


def test_defaults_when_no_config(temp_dir: Path) -> None:
    """Return built-in defaults when no config file exists."""
    os.chdir(temp_dir)
    cfg = load_config()
    assert cfg.llm.provider == _DEF_PROVIDER
    assert cfg.llm.model == _DEF_MODEL
    assert cfg.llm.api_key == ""
    assert cfg.llm.api_key_env == ""
    assert cfg.rules.min_length == _DEF_MIN_LENGTH
    assert cfg.rules.forbid_patterns == _DEF_FORBID_PATTERNS
    assert cfg.diff.exclude == _DEF_EXCLUDE
    assert cfg.diff.max_lines == _DEF_MAX_LINES


def test_default_dataclass_constructors() -> None:
    """Dataclass constructors yield defaults."""
    cfg = Config()
    assert cfg.llm.provider == _DEF_PROVIDER
    assert cfg.rules.min_length == _DEF_MIN_LENGTH
    assert cfg.rules.forbid_patterns == _DEF_FORBID_PATTERNS
    assert cfg.diff.exclude == _DEF_EXCLUDE
    assert cfg.diff.max_lines == _DEF_MAX_LINES


# ---------------------------------------------------------------------------
# Full / partial config
# ---------------------------------------------------------------------------


def test_full_config(use_config: Callable[[dict[str, Any]], None]) -> None:
    """All fields are loaded from a complete config file."""
    use_config(
        {
            "llm": {
                "provider": "anthropic",
                "model": "claude-3-opus",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
            "rules": {
                "min_length": 20,
                "forbid_patterns": ["^tmp$", "^wip$"],
            },
            "diff": {"exclude": ["*.lock", "*.min.js"], "max_lines": 300},
        }
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
        cfg = load_config()
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model == "claude-3-opus"
    assert cfg.llm.api_key == "sk-ant-secret"
    assert cfg.llm.api_key_env == "ANTHROPIC_API_KEY"
    assert cfg.rules.min_length == 20
    assert cfg.rules.forbid_patterns == ["^tmp$", "^wip$"]
    assert cfg.diff.exclude == ["*.lock", "*.min.js"]
    assert cfg.diff.max_lines == 300


def test_partial_config_falls_back_to_defaults(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    """Missing fields get defaults."""
    use_config({"llm": {"provider": "gemini"}})
    cfg = load_config()
    assert cfg.llm.provider == "gemini"
    assert cfg.llm.model == _DEF_MODEL
    assert cfg.llm.api_key_env == ""
    assert cfg.rules.min_length == _DEF_MIN_LENGTH
    assert cfg.rules.forbid_patterns == _DEF_FORBID_PATTERNS


def test_empty_config_file(temp_dir: Path) -> None:
    """An empty YAML file is treated as all defaults."""
    (temp_dir / _CFG).write_text("", encoding="utf-8")
    os.chdir(temp_dir)
    cfg = load_config()
    assert cfg.llm.provider == _DEF_PROVIDER


# ---------------------------------------------------------------------------
# Upward search
# ---------------------------------------------------------------------------


def test_upward_search(temp_dir: Path) -> None:
    """Config is found by walking up from a subdirectory."""
    config_path = temp_dir / _CFG
    config_path.write_text(yaml.dump({"llm": {"provider": "deepseek"}}), encoding="utf-8")
    sub = temp_dir / "a" / "b"
    sub.mkdir(parents=True)
    os.chdir(sub)
    cfg = load_config()
    assert cfg.llm.provider == "deepseek"


def test_upward_search_stops_at_root(temp_dir: Path) -> None:
    """Search stops at filesystem root; defaults are returned."""
    sub = temp_dir / "x" / "y"
    sub.mkdir(parents=True)
    os.chdir(sub)
    cfg = load_config()
    assert cfg.llm.provider == _DEF_PROVIDER


def test_explicit_file_path(temp_dir: Path) -> None:
    """load_config with an explicit file path loads that config."""
    p = temp_dir / "custom.yaml"
    p.write_text(yaml.dump({"diff": {"max_lines": 100}}), encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.diff.max_lines == 100


def test_explicit_dir_path(temp_dir: Path) -> None:
    """load_config with a directory path searches upward from there."""
    (temp_dir / _CFG).write_text(yaml.dump({"diff": {"max_lines": 200}}), encoding="utf-8")
    cfg = load_config(str(temp_dir))
    assert cfg.diff.max_lines == 200


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------


def test_api_key_env_success(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    """api_key is resolved from the environment variable."""
    use_config({"llm": {"api_key_env": "TEST_API_KEY"}})
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TEST_API_KEY", "sk-test-123")
        cfg = load_config()
    assert cfg.llm.api_key == "sk-test-123"


def test_api_key_env_missing_raises(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    """ConfigError is raised when the referenced env var is absent."""
    use_config({"llm": {"api_key_env": "MISSING_KEY"}})
    with pytest.raises(ConfigError, match="MISSING_KEY"):
        load_config()


def test_api_key_env_empty_no_error(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    """When api_key_env is empty, no env lookup occurs."""
    use_config({"llm": {"provider": "openai"}})
    cfg = load_config()
    assert cfg.llm.api_key == ""


# ---------------------------------------------------------------------------
# Unknown key warnings
# ---------------------------------------------------------------------------


def test_unknown_top_key_warns(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    """Unknown top-level key emits UserWarning."""
    use_config({"llm": {"provider": "openai"}, "unknown_section": {"x": 1}})
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cfg = load_config()
        assert cfg.llm.provider == "openai"
    warn_msgs = [str(x.message) for x in w]
    assert any("unknown_section" in m for m in warn_msgs)


def test_unknown_sub_field_warns(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    """Unknown field inside a known section emits UserWarning."""
    use_config({"llm": {"provider": "openai", "temperature": 0.7}})
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cfg = load_config()
        assert cfg.llm.provider == "openai"
    warn_msgs = [str(x.message) for x in w]
    assert any("temperature" in m for m in warn_msgs)


# ---------------------------------------------------------------------------
# Type validation errors
# ---------------------------------------------------------------------------


def test_provider_not_string(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    use_config({"llm": {"provider": 123}})
    with pytest.raises(ConfigError, match="'provider' must be a string"):
        load_config()


def test_model_not_string(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    use_config({"llm": {"model": True}})
    with pytest.raises(ConfigError, match="'model' must be a string"):
        load_config()


def test_min_length_not_int(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    use_config({"rules": {"min_length": "long"}})
    with pytest.raises(ConfigError, match="'min_length' must be an integer"):
        load_config()


def test_min_length_bool_rejected(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    """bool is rejected even though it is an int subclass."""
    use_config({"rules": {"min_length": True}})
    with pytest.raises(ConfigError, match="'min_length' must be an integer"):
        load_config()


def test_forbid_patterns_not_list(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    use_config({"rules": {"forbid_patterns": "^fix$"}})
    with pytest.raises(ConfigError, match="'forbid_patterns' must be a list"):
        load_config()


def test_forbid_patterns_item_not_str(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    use_config({"rules": {"forbid_patterns": ["^fix$", 1, "^wip$"]}})
    with pytest.raises(ConfigError, match=r"forbid_patterns\[1\].*string"):
        load_config()


def test_max_lines_not_int(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    use_config({"diff": {"max_lines": "three"}})
    with pytest.raises(ConfigError, match="'max_lines' must be an integer"):
        load_config()


def test_exclude_not_list(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    use_config({"diff": {"exclude": "*.lock"}})
    with pytest.raises(ConfigError, match="'exclude' must be a list"):
        load_config()


def test_exclude_item_not_str(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    use_config({"diff": {"exclude": ["*.lock", 123]}})
    with pytest.raises(ConfigError, match=r"exclude\[1\].*string"):
        load_config()


# ---------------------------------------------------------------------------
# YAML parse errors
# ---------------------------------------------------------------------------


def test_invalid_yaml(temp_dir: Path) -> None:
    """ConfigError is raised for unparseable YAML."""
    p = temp_dir / _CFG
    p.write_text("llm: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Failed to parse YAML"):
        load_config(str(p))


def test_yaml_not_mapping(temp_dir: Path) -> None:
    """ConfigError is raised when YAML root is not a mapping."""
    p = temp_dir / _CFG
    p.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML mapping"):
        load_config(str(p))


# ---------------------------------------------------------------------------
# Config dataclass immutability of defaults
# ---------------------------------------------------------------------------


def test_default_forbid_patterns_are_independent() -> None:
    """Modifying one config's forbid_patterns does not affect another."""
    cfg1 = Config()
    cfg2 = Config()
    cfg1.rules.forbid_patterns.append("^tmp$")
    assert "^tmp$" not in cfg2.rules.forbid_patterns
    assert len(cfg2.rules.forbid_patterns) == len(_DEF_FORBID_PATTERNS)


def test_api_key_env_not_set_means_no_resolution() -> None:
    """When api_key_env is empty string, no env resolution occurs."""
    cfg = Config(llm=LLMConfig(provider="openai", api_key_env=""))
    assert cfg.llm.api_key == ""


def test_api_base_default_empty() -> None:
    """api_base default value is empty string."""
    cfg = LLMConfig()
    assert cfg.api_base == ""

    full_cfg = Config()
    assert full_cfg.llm.api_base == ""


def test_api_base_from_config(
    use_config: Callable[[dict[str, Any]], None],
) -> None:
    """api_base is loaded correctly from YAML config."""
    use_config(
        {
            "llm": {
                "provider": "ollama",
                "model": "llama3",
                "api_base": "http://localhost:11434",
            },
        }
    )
    cfg = load_config()
    assert cfg.llm.api_base == "http://localhost:11434"
