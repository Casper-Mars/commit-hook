"""Tests for llm module."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import litellm
import pytest

from commit_hook.config import LLMConfig
from commit_hook.llm import LLMResult, LLMUnavailableError, llm_evaluate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_response(text: str) -> MagicMock:
    """Build a mock litellm completion response with the given text."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    return resp


def _make_config(
    provider: str = "openai",
    model: str = "gpt-4o-mini",
    api_key: str = "sk-test",
    api_key_env: str = "",
) -> LLMConfig:
    """Build an LLMConfig for testing."""
    return LLMConfig(provider=provider, model=model, api_key=api_key, api_key_env=api_key_env)


def _make_litellm_exc(exc_cls: type[Exception], message: str = "boom") -> Exception:
    """Construct a litellm exception with the minimum required args."""
    if exc_cls is litellm.exceptions.APIError:
        return exc_cls(status_code=500, message=message, llm_provider="test", model="test")
    return exc_cls(message=message, llm_provider="test", model="test")


# ---------------------------------------------------------------------------
# Normal cases
# ---------------------------------------------------------------------------
def test_passed() -> None:
    """LLM returns passed=true → LLMResult with passed=True."""
    payload = {"passed": True, "score": 35, "accuracy": 9, "issues": [], "suggestion": ""}
    mock_resp = _make_response(json.dumps(payload))

    with patch("commit_hook.llm.litellm.completion", return_value=mock_resp):
        result = llm_evaluate("diff content", "feat: add login", _make_config())

    assert result.passed is True
    assert result.score == 35
    assert result.accuracy == 9
    assert result.issues == []
    assert result.suggestion == ""


def test_failed() -> None:
    """LLM returns passed=false with issues and suggestion."""
    payload = {
        "passed": False,
        "score": 15,
        "accuracy": 3,
        "issues": ["message too vague"],
        "suggestion": "Describe what was changed, not why",
    }
    mock_resp = _make_response(json.dumps(payload))

    with patch("commit_hook.llm.litellm.completion", return_value=mock_resp):
        result = llm_evaluate("diff content", "fix", _make_config())

    assert result.passed is False
    assert result.score == 15
    assert result.accuracy == 3
    assert result.issues == ["message too vague"]
    assert result.suggestion == "Describe what was changed, not why"


def test_api_key_from_env() -> None:
    """API key resolved from environment variable via api_key_env."""
    payload = {"passed": True, "score": 30, "accuracy": 8, "issues": [], "suggestion": ""}
    mock_resp = _make_response(json.dumps(payload))
    cfg = _make_config(api_key="", api_key_env="TEST_API_KEY")

    with (
        patch("commit_hook.llm.litellm.completion", return_value=mock_resp),
        patch.dict("os.environ", {"TEST_API_KEY": "env-key"}),
    ):
        result = llm_evaluate("diff", "msg", cfg)

    assert result.passed is True


def test_model_and_provider_passed_to_litellm() -> None:
    """The model string passed to litellm is provider/model."""
    payload = {"passed": True, "score": 30, "accuracy": 8, "issues": [], "suggestion": ""}
    mock_resp = _make_response(json.dumps(payload))
    cfg = _make_config(provider="anthropic", model="claude-3-5-haiku-latest")

    with patch("commit_hook.llm.litellm.completion", return_value=mock_resp) as mock_fn:
        llm_evaluate("diff", "msg", cfg)

    call_kwargs = mock_fn.call_args.kwargs
    assert call_kwargs["model"] == "anthropic/claude-3-5-haiku-latest"


# ---------------------------------------------------------------------------
# API key missing
# ---------------------------------------------------------------------------
def test_missing_api_key_raises() -> None:
    """No API key configured → LLMUnavailableError."""
    cfg = _make_config(api_key="", api_key_env="")

    with pytest.raises(LLMUnavailableError) as exc_info:
        llm_evaluate("diff", "msg", cfg)

    assert "API key" in str(exc_info.value)
    assert exc_info.value.exit_code == 0


def test_missing_api_key_env_var_raises() -> None:
    """api_key_env is set but the env var is missing → LLMUnavailableError."""
    cfg = _make_config(api_key="", api_key_env="MISSING_KEY")

    with patch.dict("os.environ", {}, clear=True), pytest.raises(LLMUnavailableError) as exc_info:
        llm_evaluate("diff", "msg", cfg)

    assert "API key" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------
def test_timeout_raises() -> None:
    """litellm Timeout → LLMUnavailableError."""
    exc = _make_litellm_exc(litellm.exceptions.Timeout, "timed out")

    with (
        patch("commit_hook.llm.litellm.completion", side_effect=exc),
        pytest.raises(LLMUnavailableError) as exc_info,
    ):
        llm_evaluate("diff", "msg", _make_config())

    assert "timed out" in str(exc_info.value)


# ---------------------------------------------------------------------------
# API / connection errors
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("exc_cls", "msg_fragment"),
    [
        (litellm.exceptions.APIError, "apierror"),
        (litellm.exceptions.APIConnectionError, "apiconnectionerror"),
        (litellm.exceptions.AuthenticationError, "authenticationerror"),
        (litellm.exceptions.RateLimitError, "ratelimiterror"),
        (litellm.exceptions.ServiceUnavailableError, "serviceunavailableerror"),
    ],
)
def test_litellm_errors_convert_to_unavailable(
    exc_cls: type[Exception],
    msg_fragment: str,
) -> None:
    """Various litellm exceptions → LLMUnavailableError."""
    exc = _make_litellm_exc(exc_cls, "boom")

    with (
        patch("commit_hook.llm.litellm.completion", side_effect=exc),
        pytest.raises(LLMUnavailableError) as exc_info,
    ):
        llm_evaluate("diff", "msg", _make_config())

    assert msg_fragment in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# JSON parsing / response handling
# ---------------------------------------------------------------------------
def test_non_json_response_extracts_json_block() -> None:
    """Response wraps JSON in extra text → extract and parse successfully."""
    payload = {"passed": True, "score": 30, "accuracy": 8, "issues": [], "suggestion": ""}
    raw = f"Here is the result:\n```json\n{json.dumps(payload)}\n```\nDone."
    mock_resp = _make_response(raw)

    with patch("commit_hook.llm.litellm.completion", return_value=mock_resp):
        result = llm_evaluate("diff", "msg", _make_config())

    assert result.passed is True
    assert result.score == 30


def test_non_json_no_braces_raises() -> None:
    """Response is plain text with no JSON → LLMUnavailableError."""
    mock_resp = _make_response("Sorry, I cannot evaluate this.")

    with (
        patch("commit_hook.llm.litellm.completion", return_value=mock_resp),
        pytest.raises(LLMUnavailableError) as exc_info,
    ):
        llm_evaluate("diff", "msg", _make_config())

    assert "no json object found" in str(exc_info.value).lower()


def test_invalid_json_inside_braces_raises() -> None:
    """Response has braces but content is not valid JSON → LLMUnavailableError."""
    mock_resp = _make_response("{not valid json at all}")

    with (
        patch("commit_hook.llm.litellm.completion", return_value=mock_resp),
        pytest.raises(LLMUnavailableError) as exc_info,
    ):
        llm_evaluate("diff", "msg", _make_config())

    assert "parse" in str(exc_info.value).lower()


def test_json_missing_required_fields_raises() -> None:
    """JSON is valid but missing 'passed'/'score'/'accuracy' → LLMUnavailableError."""
    payload: dict[str, Any] = {"passed": True}  # missing score, accuracy
    mock_resp = _make_response(json.dumps(payload))

    with (
        patch("commit_hook.llm.litellm.completion", return_value=mock_resp),
        pytest.raises(LLMUnavailableError) as exc_info,
    ):
        llm_evaluate("diff", "msg", _make_config())

    assert "missing" in str(exc_info.value).lower() or "required" in str(exc_info.value).lower()


def test_null_content_raises() -> None:
    """LLM returns None content → LLMUnavailableError."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = None

    with (
        patch("commit_hook.llm.litellm.completion", return_value=mock_resp),
        pytest.raises(LLMUnavailableError) as exc_info,
    ):
        llm_evaluate("diff", "msg", _make_config())

    assert "empty" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# LLMResult dataclass
# ---------------------------------------------------------------------------
def test_llmresult_defaults() -> None:
    """LLMResult default field values are correct."""
    result = LLMResult(passed=True, score=30, accuracy=8)
    assert result.issues == []
    assert result.suggestion == ""
    assert result.passed is True


def test_llmresult_all_fields() -> None:
    """LLMResult stores all fields correctly."""
    result = LLMResult(
        passed=False,
        score=10,
        accuracy=2,
        issues=["vague", "too short"],
        suggestion="Be more specific",
    )
    assert result.passed is False
    assert result.score == 10
    assert result.accuracy == 2
    assert len(result.issues) == 2
    assert "specific" in result.suggestion


def test_api_base_passed_to_litellm() -> None:
    """When api_base is set, it is passed to litellm.completion."""
    payload = {"passed": True, "score": 30, "accuracy": 8, "issues": [], "suggestion": ""}
    mock_resp = _make_response(json.dumps(payload))
    cfg = _make_config(api_key="sk-test")
    cfg.api_base = "http://localhost:11434"

    with patch("commit_hook.llm.litellm.completion", return_value=mock_resp) as mock_fn:
        llm_evaluate("diff", "msg", cfg)

    call_kwargs = mock_fn.call_args.kwargs
    assert call_kwargs["api_base"] == "http://localhost:11434"


def test_api_base_empty_not_passed() -> None:
    """When api_base is empty, it is not passed to litellm.completion."""
    payload = {"passed": True, "score": 30, "accuracy": 8, "issues": [], "suggestion": ""}
    mock_resp = _make_response(json.dumps(payload))
    cfg = _make_config(api_key="sk-test")
    # explicitly ensure api_base is empty
    cfg.api_base = ""

    with patch("commit_hook.llm.litellm.completion", return_value=mock_resp) as mock_fn:
        llm_evaluate("diff", "msg", cfg)

    call_kwargs = mock_fn.call_args.kwargs
    assert "api_base" not in call_kwargs
