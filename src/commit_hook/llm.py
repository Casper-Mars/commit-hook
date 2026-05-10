"""LLM integration via litellm for commit-hook."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import litellm

from commit_hook.config import LLMConfig

_SYSTEM_PROMPT = (
    "You are a commit message evaluator. Judge whether the message "
    "accurately describes the code change intent (NOT format compliance).\n"
    "Metrics: accuracy 0-10 (how well it captures the change), "
    "score 0-100 (overall quality).\n"
    'Respond ONLY with JSON: {"passed": bool, "score": int, '
    '"accuracy": int, "issues": [...], "suggestion": "..."}\n'
    "passed=true requires score>=28 AND accuracy>=7."
)


@dataclass
class LLMResult:
    """LLM evaluation result for a commit message."""

    passed: bool
    score: int
    accuracy: int
    issues: list[str] = field(default_factory=list)
    suggestion: str = ""


class LLMUnavailableError(Exception):
    """LLM is unavailable; caller should allow the commit (exit code 0)."""

    exit_code: int = 0


def llm_evaluate(diff: str, message: str, cfg: LLMConfig) -> LLMResult:
    """Evaluate commit message via LLM.

    Raises LLMUnavailableError (exit 0) when LLM is unreachable.
    """
    api_key = cfg.api_key or os.environ.get(cfg.api_key_env, "")
    if not api_key:
        raise LLMUnavailableError(
            f"API key not configured. Set env var '{cfg.api_key_env}' or specify llm.api_key."
        )

    prompt = (
        f"## Code Diff\n\n```diff\n{diff}\n```\n\n"
        f"## Commit Message\n\n{message}\n\n"
        "Evaluate the commit message above. Respond with a JSON object "
        "containing passed, score, accuracy, issues, and suggestion fields."
    )

    try:
        kwargs: dict[str, Any] = {
            "model": f"{cfg.provider}/{cfg.model}",
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "api_key": api_key,
            "timeout": 10,
            "response_format": {"type": "json_object"},
        }
        if cfg.api_base:
            kwargs["api_base"] = cfg.api_base
        resp = litellm.completion(**kwargs)
    except (
        litellm.exceptions.Timeout,
        litellm.exceptions.APIError,
        litellm.exceptions.APIConnectionError,
        litellm.exceptions.AuthenticationError,
        litellm.exceptions.RateLimitError,
        litellm.exceptions.ServiceUnavailableError,
    ) as exc:
        raise LLMUnavailableError(f"LLM {type(exc).__name__}: {exc}") from exc

    raw = resp.choices[0].message.content
    if raw is None:
        raise LLMUnavailableError("LLM returned empty response")

    return _parse_response(raw)


def _extract_json(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_response(raw: str) -> LLMResult:
    data = _load_json(raw)
    try:
        passed = bool(data["passed"])
        score = _coerce_int(data["score"], "score")
        accuracy = _coerce_int(data["accuracy"], "accuracy")
        issues = _coerce_str_list(data.get("issues", []), "issues")
        suggestion = str(data.get("suggestion", ""))
    except (KeyError, ValueError, TypeError) as exc:
        raise LLMUnavailableError(
            f"LLM response missing required fields or invalid types: {exc}"
        ) from exc
    return LLMResult(
        passed=passed, score=score, accuracy=accuracy, issues=issues, suggestion=suggestion
    )


def _load_json(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    json_str = _extract_json(raw)
    if json_str is None:
        raise LLMUnavailableError("LLM response is not valid JSON and no JSON object found")
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise LLMUnavailableError(
            f"Failed to parse extracted JSON from LLM response: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise LLMUnavailableError(f"LLM response is not a JSON object, got {type(parsed).__name__}")
    return parsed


def _coerce_int(value: object, label: str) -> int:
    if isinstance(value, int | float | str):
        return int(value)
    raise TypeError(f"{label} must be numeric, got {type(value).__name__}")


def _coerce_str_list(value: object, label: str) -> list[str]:
    if isinstance(value, list):
        return [str(i) for i in value]
    raise TypeError(f"{label} must be a list, got {type(value).__name__}")
