"""End-to-end tests for commit-hook — real git commit flow with mock LLM.

Tests the full pipeline: git commit → commit-msg hook → commit-hook check
→ local rules → staged diff → LLM evaluation → reporter output.

All LLM calls are mocked via a mock litellm package injected through
PYTHONPATH in a custom hook script. Mock behavior is controlled by
environment variables.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import yaml

# Path to the installed commit-hook binary
_VENV_BIN = "/Users/reachlucifer/project/opc/commit-hook/.venv/bin/commit-hook"

# ── Mock litellm source code ───────────────────────────────────────────────

_MOCK_LITELLM_INIT = r'''
"""Mock litellm for e2e testing — controlled by env vars."""
import os, json
from . import exceptions


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, data):
        self.choices = [_FakeChoice(json.dumps(data))]


_PASS_DATA = {
    "passed": True, "score": 85, "accuracy": 9,
    "issues": [], "suggestion": "",
}

_FAIL_DATA = {
    "passed": False, "score": 15, "accuracy": 3,
    "issues": ["message too vague", "does not describe code changes"],
    "suggestion": "Use a more descriptive message like 'feat: add user login'",
}


def completion(*args, **kwargs):
    output_file = os.environ.get("MOCK_PROMPT_OUTPUT", "")
    if output_file:
        with open(output_file, "w") as f:
            for msg in kwargs.get("messages", []):
                f.write(msg.get("content", ""))
            f.write("\n---MODEL---\n")
            f.write(kwargs.get("model", ""))

    behavior = os.environ.get("MOCK_LLM_BEHAVIOR", "pass")
    if behavior == "pass":
        return _FakeResponse(_PASS_DATA)
    elif behavior == "fail":
        return _FakeResponse(_FAIL_DATA)
    elif behavior == "timeout":
        raise exceptions.Timeout("mock timeout")
    elif behavior == "auth_error":
        raise exceptions.AuthenticationError(
            "Invalid API key", llm_provider="test", model="test"
        )
    elif behavior == "connection_error":
        raise exceptions.APIConnectionError(
            "Connection refused", llm_provider="test", model="test"
        )
    else:
        return _FakeResponse(_PASS_DATA)
'''

_MOCK_LITELLM_EXCEPTIONS = r"""
class Timeout(Exception):
    def __init__(self, message="timeout", **kw):
        super().__init__(message)

class APIError(Exception):
    def __init__(self, message="api error", **kw):
        super().__init__(message)

class APIConnectionError(Exception):
    def __init__(self, message="connection error", **kw):
        super().__init__(message)

class AuthenticationError(Exception):
    def __init__(self, message="auth error", **kw):
        super().__init__(message)

class RateLimitError(Exception):
    def __init__(self, message="rate limit", **kw):
        super().__init__(message)

class ServiceUnavailableError(Exception):
    def __init__(self, message="service unavailable", **kw):
        super().__init__(message)
"""


# ── Helpers ────────────────────────────────────────────────────────────────


def _setup_repo(tmp_path: Path) -> Path:
    """Create a git repo and return its path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Tester"],
        cwd=repo,
        check=True,
    )
    return repo


def _install_mock_litellm(repo: Path) -> Path:
    """Create mock litellm package inside repo and return its path."""
    pkg = repo / "litellm"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text(_MOCK_LITELLM_INIT)
    (pkg / "exceptions.py").write_text(_MOCK_LITELLM_EXCEPTIONS)
    return pkg


def _write_hook(
    repo: Path,
    behavior: str,
    *,
    prompt_output: str = "",
    extra_env: dict[str, str] | None = None,
) -> None:
    """Write .git/hooks/commit-msg with mock injection.

    Args:
        repo: Path to git repo root.
        behavior: MOCK_LLM_BEHAVIOR value ("pass", "fail", "timeout", etc.).
        prompt_output: If set, configures MOCK_PROMPT_OUTPUT to capture LLM prompt.
        extra_env: Additional env vars to export in the hook script.
    """
    hook_dir = repo / ".git" / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    extra = ""
    if prompt_output:
        extra += f'export MOCK_PROMPT_OUTPUT="{prompt_output}"\n'
    if extra_env:
        for k, v in extra_env.items():
            extra += f'export {k}="{v}"\n'
    hook = (
        "#!/usr/bin/env bash\n"
        f"export PYTHONPATH={repo}:$PYTHONPATH\n"
        "export COMMIT_HOOK_TEST_KEY=sk-test\n"
        f"export MOCK_LLM_BEHAVIOR={behavior}\n"
        f"{extra}"
        f'exec {_VENV_BIN} check "$1"\n'
    )
    hook_file = hook_dir / "commit-msg"
    hook_file.write_text(hook)
    hook_file.chmod(0o755)


def _write_config(repo: Path, **sections: Any) -> None:
    """Write .commit-hook.yaml to repo.

    Usage: _write_config(repo, llm={"api_key_env": "..."}, rules={"min_length": 10})
    """
    config: dict[str, Any] = {}
    for section, values in sections.items():
        config[section] = values
    (repo / ".commit-hook.yaml").write_text(yaml.dump(config))


def _git_add(repo: Path, *files: str) -> None:
    """Stage files in repo."""
    subprocess.run(["git", "add", *files], cwd=repo, check=True, capture_output=True)


def _write_file(repo: Path, name: str, content: str) -> None:
    """Create a file in repo, creating parent directories as needed."""
    filepath = repo / name
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content)


def _git_commit(
    repo: Path, message: str, *, allow_empty: bool = False, no_verify: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run git commit and return the CompletedProcess."""
    cmd = ["git", "commit"]
    if allow_empty:
        cmd.append("--allow-empty")
    if no_verify:
        cmd.append("--no-verify")
    cmd.extend(["-m", message])
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True)


def _assert_commit_created(repo: Path) -> None:
    """Verify that at least one commit exists."""
    r = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0 and r.stdout.strip(), f"No commit found: {r.stderr}"


# ── Scenario 1: LLM passes → exit 0 ───────────────────────────────────────


def test_e2e_scenario_1_pass(tmp_path: Path) -> None:
    """场景 1（通过）：含 login.py diff + 准确描述 → LLM 判定通过 → exit 0."""
    repo = _setup_repo(tmp_path)
    _install_mock_litellm(repo)
    _write_hook(repo, "pass")
    _write_config(repo, llm={"api_key_env": "COMMIT_HOOK_TEST_KEY"}, rules={"min_length": 10})

    _write_file(repo, "login.py", "def login(email, password):\n    return True\n")
    _git_add(repo, "login.py", ".commit-hook.yaml")

    result = _git_commit(repo, "feat: add user login with email and password")

    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stderr}"
    assert "通过" in result.stderr
    assert "85" in result.stderr
    assert "9" in result.stderr
    _assert_commit_created(repo)


# ── Scenario 2: LLM fails (描述不准) → exit 1 ────────────────────────────


def test_e2e_scenario_2_fail(tmp_path: Path) -> None:
    """场景 2（不通过-描述不准）：含 login.py diff + "fix" → LLM 判定不通过 → exit 1.

    Use relaxed rules so the message passes local checks and reaches LLM.
    """
    repo = _setup_repo(tmp_path)
    _install_mock_litellm(repo)
    _write_hook(repo, "fail")
    _write_config(
        repo,
        llm={"api_key_env": "COMMIT_HOOK_TEST_KEY"},
        rules={"min_length": 3, "forbid_patterns": []},
    )

    _write_file(repo, "login.py", "def login(email, password):\n    return True\n")
    _git_add(repo, "login.py", ".commit-hook.yaml")

    result = _git_commit(repo, "fix")

    # LLM returns fail → exit 1, commit NOT created
    stderr = result.stderr
    assert result.returncode == 1, f"Expected exit 1 (LLM fail), got {result.returncode}: {stderr}"
    assert "不通过" in stderr, f"Expected '不通过' in output, got: {stderr}"
    assert "message too vague" in stderr or "does not describe" in stderr, (
        f"Expected issues in output, got: {stderr}"
    )
    # Verify no commit was created
    r = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0 or not r.stdout.strip(), "Commit should not have been created"


# ── Scenario 3: Empty message → exit 1 ────────────────────────────────────


def test_e2e_scenario_3_empty_message(tmp_path: Path) -> None:
    """场景 3（不通过-空 message）：任意 diff + 空 message → 本地规则拦截 → exit 1."""
    repo = _setup_repo(tmp_path)
    _install_mock_litellm(repo)
    _write_hook(repo, "pass")
    _write_config(repo, llm={"api_key_env": "COMMIT_HOOK_TEST_KEY"}, rules={"min_length": 10})

    _write_file(repo, "app.py", "def main():\n    pass\n")
    _git_add(repo, "app.py", ".commit-hook.yaml")

    result = _git_commit(repo, "")

    stderr = result.stderr
    assert result.returncode == 1, (
        f"Expected exit 1 (empty message), got {result.returncode}: {stderr}"
    )
    assert "不通过" in stderr, f"Expected '不通过' in output, got: {stderr}"
    assert "不能为空" in stderr, f"Expected '不能为空' in output, got: {stderr}"


# ── Scenario 4: LLM unavailable → degraded → exit 0 ──────────────────────


def test_e2e_scenario_4_degraded(tmp_path: Path) -> None:
    """场景 4（降级-LLM 不可用）：错误 API Key → LLM 不可用 → 降级放行 → exit 0."""
    repo = _setup_repo(tmp_path)
    _install_mock_litellm(repo)
    # auth_error simulates an invalid API key
    _write_hook(repo, "auth_error")
    _write_config(repo, llm={"api_key_env": "COMMIT_HOOK_TEST_KEY"}, rules={"min_length": 10})

    _write_file(repo, "login.py", "def login(email, password):\n    return True\n")
    _git_add(repo, "login.py", ".commit-hook.yaml")

    result = _git_commit(repo, "feat: add user login")

    stderr = result.stderr
    assert result.returncode == 0, (
        f"Expected exit 0 (degraded pass), got {result.returncode}: {stderr}"
    )
    assert "降级放行" in stderr, f"Expected '降级放行' in output, got: {stderr}"
    assert "AuthenticationError" in stderr, f"Expected AuthenticationError in output, got: {stderr}"
    _assert_commit_created(repo)


# ── Scenario 5: Diff truncation (>500 lines) → still passes ───────────────


def test_e2e_scenario_5_diff_truncation(tmp_path: Path) -> None:
    """场景 5（diff 截断）：超 500 行 diff + 准确 message → 正常通过."""
    repo = _setup_repo(tmp_path)
    _install_mock_litellm(repo)
    _write_hook(repo, "pass")
    # Use default max_lines=500
    _write_config(repo, llm={"api_key_env": "COMMIT_HOOK_TEST_KEY"}, rules={"min_length": 10})

    # Create a large file with >500 lines
    lines = [f"    line_{i} = {i}" for i in range(600)]
    _write_file(repo, "large.py", "def large_function():\n" + "\n".join(lines) + "\n")
    _git_add(repo, "large.py", ".commit-hook.yaml")

    result = _git_commit(repo, "feat: add large utility function")

    stderr = result.stderr
    assert result.returncode == 0, (
        f"Expected exit 0 (truncated diff pass), got {result.returncode}: {stderr}"
    )
    assert "通过" in stderr, f"Expected '通过' in output, got: {stderr}"
    _assert_commit_created(repo)


# ── Scenario 6: --no-verify skips check ───────────────────────────────────


def test_e2e_scenario_6_no_verify(tmp_path: Path) -> None:
    """场景 6（--no-verify 跳过）：--no-verify 绕过 hook 检查 → 提交成功."""
    repo = _setup_repo(tmp_path)
    _install_mock_litellm(repo)
    # Hook would fail with auth_error, but --no-verify skips it entirely
    _write_hook(repo, "auth_error")
    _write_config(repo, llm={"api_key_env": "COMMIT_HOOK_TEST_KEY"}, rules={"min_length": 10})

    _write_file(repo, "app.py", "print('hello')\n")
    _git_add(repo, "app.py", ".commit-hook.yaml")

    result = _git_commit(repo, "fix", no_verify=True)

    assert result.returncode == 0, (
        f"Expected exit 0 (--no-verify), got {result.returncode}: {result.stderr}"
    )
    # Hook should not produce any output (no stderr from hook)
    assert "降级放行" not in result.stderr and "不通过" not in result.stderr, (
        f"Hook should be skipped entirely, got stderr: {result.stderr}"
    )
    _assert_commit_created(repo)


# ── Scenario 7: Model switching ───────────────────────────────────────────


def test_e2e_scenario_7_model_switch(tmp_path: Path) -> None:
    """场景 7（模型切换）：配置 model=claude-3-5-haiku-latest → litellm 收到正确模型.

    Verifies that the model string passed to litellm.completion matches
    the configured provider/model combination.
    """
    repo = _setup_repo(tmp_path)
    _install_mock_litellm(repo)
    prompt_file = str(repo / "llm_prompt.txt")
    _write_hook(repo, "pass", prompt_output=prompt_file)
    _write_config(
        repo,
        llm={
            "api_key_env": "COMMIT_HOOK_TEST_KEY",
            "provider": "anthropic",
            "model": "claude-3-5-haiku-latest",
        },
        rules={"min_length": 10},
    )

    _write_file(repo, "app.py", "print('hello')\n")
    _git_add(repo, "app.py", ".commit-hook.yaml")

    result = _git_commit(repo, "feat: add hello world script")

    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stderr}"

    # Verify the model string passed to litellm
    prompt_content = Path(prompt_file).read_text()
    assert "---MODEL---" in prompt_content, (
        f"No model info in prompt output: {prompt_content[:200]}"
    )
    model_line = prompt_content.split("---MODEL---")[1].strip()
    assert "claude-3-5-haiku-latest" in model_line, f"Expected claude model, got: {model_line}"
    assert "anthropic" in model_line, f"Expected anthropic provider, got: {model_line}"


# ── Scenario 8: File exclusion ────────────────────────────────────────────


def test_e2e_scenario_8_file_exclusion(tmp_path: Path) -> None:
    """场景 8（排除文件）：diff 含 package-lock.json → 该文件不出现在 LLM 提示中."""
    repo = _setup_repo(tmp_path)
    _install_mock_litellm(repo)
    prompt_file = str(repo / "llm_prompt.txt")
    _write_hook(repo, "pass", prompt_output=prompt_file)
    _write_config(
        repo,
        llm={"api_key_env": "COMMIT_HOOK_TEST_KEY"},
        rules={"min_length": 10},
        # Using default exclude patterns which include "package-lock.json"
    )

    _write_file(
        repo,
        "package-lock.json",
        '{\n  "name": "test",\n  "lockfileVersion": 2,\n  "packages": {}\n}\n',
    )
    _write_file(repo, "src/main.py", "def main():\n    print('hello')\n")
    _git_add(repo, "package-lock.json", "src/main.py", ".commit-hook.yaml")

    result = _git_commit(repo, "feat: add main module")

    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stderr}"

    # Verify package-lock.json is NOT in the LLM prompt
    prompt_content = Path(prompt_file).read_text()
    assert "package-lock.json" not in prompt_content, (
        f"package-lock.json should be excluded from LLM prompt, got: {prompt_content[:500]}"
    )
    assert "src/main.py" in prompt_content, (
        f"src/main.py should be in LLM prompt, got: {prompt_content[:500]}"
    )
    _assert_commit_created(repo)
