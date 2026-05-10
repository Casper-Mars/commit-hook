# commit-hook

AI-powered commit message validator.

## Installation

```bash
uv tool install .
```

Or run directly with uvx:

```bash
uvx commit-hook --help
```

## Quick Start

```bash
# Show help
commit-hook --help

# Show version
commit-hook --version
```

## Configuration

Configuration is loaded from a YAML file. Place a `.commit-hook.yaml` in your
repository root to customise validation rules and LLM settings.

```yaml
# .commit-hook.yaml
rules:
  max_length: 72
  require_scope: true
```

## License

MIT
