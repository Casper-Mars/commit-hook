# CLAUDE.md — commit-hook

> AI 辅助开发上下文 | 自动生成于 2026-05-10 | Python 3.10+ / uv

## 项目概述
AI-powered Git commit message validator，通过 `commit-msg` hook 调用 LLM 检查 message 与 diff 一致性。

## 常用命令

```bash
# 环境初始化
uv sync                          # 安装全部依赖（含 dev）

# 质量门禁（提交前必须全过）
uv run ruff check                # Lint
uv run ruff format --check       # 格式化检查
uv run ruff format .             # 自动格式化
uv run mypy src                  # 类型检查（strict）
uv run pytest                    # 全部测试

# CLI 验证
uv run commit-hook --help
uv run commit-hook init          # 安装 hook
uv run commit-hook uninit        # 卸载 hook
uv run commit-hook check FILE    # 检查 commit message

# 一键验证
uv run ruff check && uv run ruff format --check && uv run mypy src && uv run pytest
```

## 目录结构（src layout）

```
src/commit_hook/
├── __init__.py      # 包 init
├── __main__.py      # python -m commit_hook
├── cli.py           # click CLI: init / uninit / check
├── config.py        # .commit-hook.yaml 加载（向上查找 + env var API Key）
├── diff.py          # git diff --cached 提取 + glob 过滤 + 行截断
├── llm.py           # litellm 调用 + JSON Mode + 容错降级
├── rules.py         # 本地规则：min_length + forbid_patterns
└── reporter.py      # rich 终端输出：绿✅/红❌/黄⚠️

tests/
├── test_config.py   # 26 tests
├── test_diff.py     # 39 tests
├── test_rules.py    # 15 tests
├── test_llm.py      # 19 tests
├── test_cli.py      # 13 tests
└── test_reporter.py # 12 tests
```

## 编码规范

| 规则 | 详情 |
|------|------|
| 行宽 | 100 chars |
| 引号 | 双引号 |
| 缩进 | 4 spaces |
| 命名 | 模块 snake_case / 类 PascalCase / 函数 snake_case / 常量 UPPER_SNAKE / 私有 _ 前缀 |
| 类型 | mypy strict，所有公开函数必须类型注解，禁止 `Any` 除非注释说明 |
| Docstring | Google style |
| 行数 | 每模块 ≤ 150 行 |
| 异常 | 不捕获裸 `Exception`，`sys.exit()` 只在 `cli.py` |
| Git | Conventional Commits（feat:/fix:/refactor:/chore:/docs:/test:） |

## 依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| litellm | ≥1.50 | LLM 统一接口 |
| rich | ≥13 | 终端彩色输出 |
| pyyaml | ≥6 | 配置文件解析 |
| click | ≥8 | CLI 框架 |
| pytest | ≥8 (dev) | 测试 |
| ruff | ≥0.8 (dev) | Lint + 格式化 |
| mypy | ≥1.13 (dev) | 类型检查 |

## 配置 (.commit-hook.yaml)

```yaml
llm:
  provider: openai          # openai / anthropic / deepseek / ollama
  model: gpt-4o-mini
  api_key_env: OPENAI_API_KEY  # API Key 只从环境变量读，禁止明文

rules:
  min_length: 10            # commit message 最小长度
  forbid_patterns:          # 精确匹配禁止的 message（re.match）
    - "^fix$"
    - "^update$"
    - "^WIP$"
    - "^wip$"

diff:
  exclude:                  # glob 模式过滤
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
  max_lines: 500            # diff 截断阈值
```

## 流程

```
git commit
  → commit-msg hook
    → commit-hook check "$1"
      → 读取 message
      → 本地规则检查（rules.py）→ 违规则 ❌ exit 1
      → git diff --cached（diff.py）→ 过滤 + 截断
      → 空 diff → ✅ exit 0
      → LLM 评估（llm.py）
        → LLM 不可用 → ⚠️ 降级 exit 0
        → passed → ✅ exit 0
        → not passed → ❌ exit 1（含 issues + suggestion）
```

## 设计决策

- LLM 关注「message 是否准确描述变更意图」，而非格式合规（格式由本地规则管）
- LLM 不可用（超时/网络/API Key 缺失）→ 降级放行（exit 0），不阻断提交
- 配置向上查找（子目录执行自动找到仓库根配置）
- litellm 一把梭：换模型只改 config 的 `model` 字段，代码零改动
- 测试不调用真实 LLM API（mock `litellm.completion`）
