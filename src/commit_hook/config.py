"""Configuration loading and validation for commit-hook."""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_CFG = ".commit-hook.yaml"
_DEF_PROVIDER = "openai"
_DEF_MODEL = "gpt-4o"
_DEF_MIN_LENGTH = 10
_DEF_FORBID_PATTERNS: list[str] = ["^fix$", "^update$", "^WIP$", "^wip$"]
_DEF_EXCLUDE: list[str] = [
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "*.min.js",
    "*.map",
    "*.svg",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.webp",
    "*.env*",
    "*secret*",
    "*.pem",
]
_DEF_MAX_LINES = 500
_TOP = frozenset({"llm", "rules", "diff"})
_LLM = frozenset({"provider", "model", "api_key_env"})
_RULES = frozenset({"min_length", "forbid_patterns"})
_DIFF = frozenset({"exclude", "max_lines"})


class ConfigError(Exception):
    """Configuration is invalid or incomplete."""


@dataclass
class LLMConfig:
    """LLM provider configuration."""

    provider: str = _DEF_PROVIDER
    model: str = _DEF_MODEL
    api_key: str = ""
    api_key_env: str = ""


@dataclass
class RulesConfig:
    """Local rule check configuration."""

    min_length: int = _DEF_MIN_LENGTH
    forbid_patterns: list[str] = field(default_factory=lambda: list(_DEF_FORBID_PATTERNS))


@dataclass
class DiffConfig:
    """Diff processing configuration."""

    exclude: list[str] = field(default_factory=lambda: list(_DEF_EXCLUDE))
    max_lines: int = _DEF_MAX_LINES


@dataclass
class Config:
    """Top-level commit-hook configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)
    diff: DiffConfig = field(default_factory=DiffConfig)


def load_config(path: str | None = None) -> Config:
    """Load config from .commit-hook.yaml, searching upward from cwd.

    Returns defaults when no file is found. Resolves ``llm.api_key`` from
    the env var named by ``llm.api_key_env``.

    Args:
        path: Config file or directory. File → load directly; directory →
            search upward from there.

    Returns:
        Validated ``Config`` with defaults for missing fields.

    Raises:
        ConfigError: On YAML errors, type mismatches, or missing API key env var.
    """
    if path is not None:
        p = Path(path)
        if p.is_file():
            return _parse(p)
        start = p if p.is_dir() else p.parent
    else:
        start = Path.cwd()
    found = _find_up(start)
    return Config() if found is None else _parse(found)


def _find_up(start_dir: Path) -> Path | None:
    """Walk upward from *start_dir* looking for .commit-hook.yaml."""
    d = start_dir.resolve()
    while True:
        if (d / _CFG).is_file():
            return d / _CFG
        parent = d.parent
        if parent == d:
            return None
        d = parent


def _parse(config_path: Path) -> Config:
    """Parse YAML, validate types, resolve API key, return Config."""
    try:
        with open(config_path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML in {config_path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Config file {config_path} must be a YAML mapping, got {type(raw).__name__}"
        )
    _warn(raw, _TOP)
    llm = _parse_llm(raw.get("llm") or {})
    rules = _parse_rules(raw.get("rules") or {})
    diff = _parse_diff(raw.get("diff") or {})
    if llm.api_key_env:
        key = os.environ.get(llm.api_key_env)
        if key is None:
            raise ConfigError(
                f"Environment variable '{llm.api_key_env}' (set via llm.api_key_env) is not defined"
            )
        llm.api_key = key
    return Config(llm=llm, rules=rules, diff=diff)


def _parse_llm(raw: dict[str, Any]) -> LLMConfig:
    """Parse llm config section."""
    _warn(raw, _LLM)
    return LLMConfig(
        provider=_get(raw, "provider", str, _DEF_PROVIDER),
        model=_get(raw, "model", str, _DEF_MODEL),
        api_key_env=_get(raw, "api_key_env", str, ""),
    )


def _parse_rules(raw: dict[str, Any]) -> RulesConfig:
    """Parse rules config section."""
    _warn(raw, _RULES)
    return RulesConfig(
        min_length=_get_int(raw, "min_length", _DEF_MIN_LENGTH),
        forbid_patterns=_get_list(raw, "forbid_patterns", _DEF_FORBID_PATTERNS),
    )


def _parse_diff(raw: dict[str, Any]) -> DiffConfig:
    """Parse diff config section."""
    _warn(raw, _DIFF)
    return DiffConfig(
        exclude=_get_list(raw, "exclude", _DEF_EXCLUDE),
        max_lines=_get_int(raw, "max_lines", _DEF_MAX_LINES),
    )


def _warn(data: dict[str, Any], known: frozenset[str]) -> None:
    """Emit UserWarning for any key in *data* not found in *known*."""
    for k in sorted(set(data) - known):
        warnings.warn(f"Unknown configuration key: '{k}'", stacklevel=5)


_TYPE_NAMES: dict[type, str] = {str: "string", int: "integer", bool: "boolean", list: "list"}
_AN_TYPES = frozenset({"integer"})


def _get(data: dict[str, Any], key: str, typ: type, default: Any) -> Any:  # noqa: ANN401
    """Return data[key] cast to *typ*, or *default*. Raise ConfigError on mismatch."""
    if key not in data:
        return default
    v = data[key]
    if not isinstance(v, typ):
        label = _TYPE_NAMES.get(typ, typ.__name__)
        article = "an" if label in _AN_TYPES else "a"
        raise ConfigError(f"'{key}' must be {article} {label}, got {type(v).__name__}")
    return v


def _get_int(data: dict[str, Any], key: str, default: int) -> int:
    """Like _get for int but rejects bool (an int subclass)."""
    v = _get(data, key, int, default)
    if isinstance(v, bool):
        raise ConfigError(f"'{key}' must be an integer, got bool")
    assert isinstance(v, int)
    return v


def _get_list(data: dict[str, Any], key: str, default: list[str]) -> list[str]:
    """Return data[key] as list[str], validating each item."""
    val: list[Any] = _get(data, key, list, default)
    for i, item in enumerate(val):
        if not isinstance(item, str):
            raise ConfigError(f"'{key}[{i}]' must be a string, got {type(item).__name__}")
    return list(val)
