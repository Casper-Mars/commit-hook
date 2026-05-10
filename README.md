# commit-hook

AI-powered Git commit message validator. Validates commit messages against configurable
local rules and LLM-powered semantic checks, ensuring every commit message is meaningful
and accurately describes the change.

## Prerequisites

- **Python** 3.10 or later
- **uv** — the fast Python package manager ([install guide](https://docs.astral.sh/uv/getting-started/installation/))
- **Git** 2.x

## Installation

### 1. Clone the repository

```bash
git clone <your-repo-url> commit-hook
cd commit-hook
```

### 2. Install with uv

```bash
uv tool install .
```

This builds the package and installs the `commit-hook` CLI globally (into `~/.local/bin` by default).
Make sure `~/.local/bin` is in your `PATH`:

```bash
# Add to your shell profile (~/.bashrc / ~/.zshrc) if not already present
export PATH="$HOME/.local/bin:$PATH"
```

### 3. Verify installation

```bash
commit-hook --version
# → commit-hook, version 0.1.0

commit-hook --help
# → Shows all available commands
```

### Alternative: run without installing

If you prefer not to install globally, use `uvx` to run directly:

```bash
uvx commit-hook --help
```

## Quick Start

### Enable the hook in a repository

```bash
cd /path/to/your-project
commit-hook init
```

This installs a `commit-msg` hook wrapper into `.git/hooks/commit-msg`. From now on,
every `git commit` will be validated by commit-hook.

### Create a config file (optional)

Place a `.commit-hook.yaml` in your repository root to customize validation rules:

```yaml
# .commit-hook.yaml
llm:
  provider: openai
  model: gpt-4o-mini
  api_key_env: OPENAI_API_KEY

rules:
  min_length: 10
  forbid_patterns:
    - "^fix$"
    - "^update$"
    - "^WIP$"
    - "^wip$"
```

If no config file is found, commit-hook uses sensible defaults (local rules only, no LLM).

### Make a commit

```bash
git add .
git commit -m "feat: add user authentication with JWT"
```

commit-hook will:
1. Check your message against local rules (length, forbidden patterns)
2. Extract the staged diff
3. Ask the LLM whether the message accurately describes your change
4. Show a pass ✅ / fail ❌ / degraded ⚠️ result

## Usage

```
commit-hook [OPTIONS] COMMAND [ARGS]...
```

### Commands

#### `init`

Install the `commit-msg` hook into `.git/hooks/commit-msg`.

```bash
commit-hook init
```

The hook wrapper is a simple bash script that invokes `commit-hook check` for every commit.
Running `init` again when the hook already exists is harmless — it prints a message and does
nothing.

#### `uninit`

Remove the installed hook.

```bash
commit-hook uninit
```

#### `check`

Validate a commit message file. Normally invoked by the hook, but you can run it manually:

```bash
commit-hook check .git/COMMIT_EDITMSG
```

Or against any file containing a commit message:

```bash
echo "feat: add login page" > /tmp/msg.txt
commit-hook check /tmp/msg.txt
```

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Pass or degraded — commit allowed |
| `1` | Violations found — commit blocked |

## Configuration Reference

Full `.commit-hook.yaml` with all options and defaults:

```yaml
llm:
  provider: openai              # openai / anthropic / deepseek / ollama / ...
  model: gpt-4o-mini
  api_key_env: OPENAI_API_KEY   # API key read from this env var (never store keys in config)

rules:
  min_length: 10                # Minimum commit message length (characters)
  forbid_patterns:              # Exact-match regex patterns that are rejected (re.match)
    - "^fix$"
    - "^update$"
    - "^WIP$"
    - "^wip$"

diff:
  exclude:                      # Glob patterns for files to exclude from diff
    - "*.lock"
    - "*.min.js"
    - "*.map"
    - "*.svg"
    - "*.png"
    - "*.jpg"
    - "*.jpeg"
    - "*.gif"
    - "*.ico"
    - "*.webp"
    - "*.env*"
    - "*secret*"
    - "*.pem"
  max_lines: 500                # Truncate diff beyond this many lines
```

### Config file discovery

commit-hook searches upward from the current working directory for `.commit-hook.yaml`.
This means you can run the tool from any subdirectory — it will automatically find the
config at the repository root.

If no config file is found, the tool operates with defaults:
- **LLM**: disabled (no API key needed)
- **Rules**: `min_length: 10`, forbidden patterns for `fix`, `update`, `WIP`, `wip`
- **Diff**: standard exclude list, `max_lines: 500`

### API Key

The LLM provider's API key is **always** read from an environment variable — never stored
in the config file. Set `llm.api_key_env` to the variable name, then export it in your shell:

```bash
export OPENAI_API_KEY="sk-..."
```

## Skip Validation

To bypass all checks (both local rules and LLM), include `[skip-validate]` anywhere in
your commit message:

```bash
git commit -m "WIP: rough draft [skip-validate]"
```

When commit-hook detects this marker, it prints a warning and exits with code 0 — the
commit proceeds immediately. This is intentionally permissive: the marker is a deliberate
choice by the developer, so the tool trusts it completely.

## Validation Pipeline

```
git commit
  → commit-msg hook
    → commit-hook check "$1"
      → read message
      → [skip-validate]? → ⚠️ skip all checks, exit 0
      → local rules (length, forbidden patterns) → ❌ exit 1 if violated
      → git diff --cached (filtered + truncated)
      → empty diff? → ✅ exit 0
      → LLM evaluation
        → LLM unavailable? → ⚠️ degraded, exit 0
        → passed? → ✅ exit 0
        → failed? → ❌ exit 1 (with issues + suggestions)
```

## Development

### Setup

```bash
git clone <repo-url>
cd commit-hook
uv sync          # Install all dependencies including dev tools
```

### Quality Gates

All checks must pass before submitting:

```bash
uv run ruff check              # Lint
uv run ruff format --check     # Format check
uv run mypy src                # Strict type check
uv run pytest                  # Run all tests
```

One-liner:

```bash
uv run ruff check && uv run ruff format --check && uv run mypy src && uv run pytest
```

### Project Structure

```
src/commit_hook/
├── cli.py           # Click CLI: init / uninit / check
├── config.py        # YAML config loading (upward search + env var resolution)
├── diff.py          # git diff --cached extraction + glob filtering + line truncation
├── llm.py           # litellm-powered LLM evaluation with graceful degradation
├── rules.py         # Local rules: min_length + forbid_patterns
└── reporter.py      # Rich terminal output (✅ pass / ❌ fail / ⚠️ degraded)

tests/
├── test_cli.py      # CLI integration tests
├── test_config.py   # Config loading & validation
├── test_diff.py     # Diff extraction & filtering
├── test_e2e.py      # End-to-end tests with real git repos
├── test_edge_cases.py  # Boundary & exception tests
├── test_integration.py # Full pipeline integration tests
├── test_llm.py      # LLM evaluation (mocked)
├── test_reporter.py # Output formatting
└── test_rules.py    # Local rule enforcement
```

## License

MIT
