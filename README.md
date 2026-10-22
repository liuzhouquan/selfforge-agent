# selfForge Agent

**A Python Coding Agent with Reliable Execution and Self-Testing**

具备完整基础 Harness、以自主编程与测试验证为特色的 Python Coding Agent。

[简体中文](./README.md) · [English](./README.en.md) · [架构](./docs/architecture.md) · [路线图](./docs/roadmap.md)

```text
用户任务 → 原生 Tool Calling → 搜索与阅读代码 → 精确修改 → 运行 pytest → 检查退出码与轨迹
```

先运行一个无需 API Key 的端到端演示：

```sh
uv sync
uv run python examples/coding_demo.py
# 两道生成夹具：search_code → read_file → edit_file → run_tests → 客观断言
```

这是协议与执行链路演示；真实模型评测见下文。通用 Coding 任务由 `selfforge-agent code` 执行。PR Review、Base/Head/Repaired 测试修复是保留的专项工作流。

## 核心能力与状态

| 能力 | 状态 | 当前边界 |
| --- | --- | --- |
| Coding Agent Runtime | Implemented | OpenAI-compatible 原生工具调用，多轮执行，步数/时间限制，供应商回退 |
| 工具与会话 | Implemented | read/edit/search/bash/run_tests；JSON Schema、工作区路径校验、JSONL 恢复与轨迹 |
| Context 管理 | Implemented | 工具输出截断、近似 token 预算、保留任务约束的确定性摘要压缩 |
| Docker 执行器 | Implemented | 真实 Docker 容器测试通过；无网络、资源限制，临时工作区通过 tar 送入 tmpfs，产物单独取回 |
| Self-Testing | Implemented | 通过真实 pytest 退出码判断；未运行测试的任务标记 `unverified` |
| Web Coding 工作台 | Implemented | 本机 Git 仓库任务、实时状态与轨迹、取消和会话续跑；后台任务仅在服务进程内保留 |
| Coding 工作区 | Implemented | 默认从已提交的 HEAD 创建独立分支与持久 worktree；可续跑、在网页查看有界 Diff、手动清理 |
| PR Review / Repair | Implemented | Web 审查与 checkout；修复编辑复用受限 Tool Registry、Docker 执行器、JUnit 证据与 JSONL 轨迹，模型提案循环仍独立 |
| 针对性测试生成 | Implemented（单候选） | 模型生成一个 pytest 文件，在 Base/Head 隔离运行；仅 Base 通过且 Head 断言失败时给出差异证据 |

## Quick Start

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/)；PR 工作流还需要 Git 和 [GitHub CLI](https://cli.github.com/)。在仓库目录执行：

```sh
uv sync
export DEEPSEEK_API_KEY='你的密钥'  # 或 OPENROUTER_API_KEY
# 为默认隔离执行构建镜像；需先启用 Docker
docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .
uv run selfforge-agent code --repo ./your-project --task '修复失败的测试，并运行测试验证'
```

`code` 默认从 Git 仓库已提交的 HEAD 创建 `selfforge/SESSION_ID` 分支和独立 worktree。原仓库的未提交改动不会带入；Agent 的文件工具只修改新 worktree。工作区保存在 `${XDG_DATA_HOME:-~/.local/share}/selfforge-agent/worktrees/`，任务结束后保留供检查与提交。需要已有的首次 Git commit。确需直接修改原仓库时，显式传 `--workspace direct`。

命令与测试默认通过 Docker 执行，容器禁用网络、限制 CPU/内存/进程数。工具把排除了 `.env`、本地密钥和会话目录的临时工作区副本送入容器 tmpfs；容器命令造成的文件修改不会回写，正式编辑由 `edit_file` 完成。示例镜像只预装 pytest；项目有额外依赖时需要扩展镜像。Docker 不可用时，对**可信仓库**可显式使用 `--sandbox host --trust-local`。模型会收到读取的代码和工具输出。

DeepSeek 与 OpenRouter 默认 URL 和模型已配置；Web 工作台还支持本机保存密钥、URL 与模型列表自动同步。自定义 OpenAI-compatible 端点可用 `--model ID --base-url URL`，密钥从 `SELFFORGE_API_KEY` 或 `OPENAI_API_KEY` 读取。`--provider openrouter` 可指定供应商，默认自动路由并在调用失败时切换备用供应商。

也可启动本地 Web 工作台，在浏览器中输入仓库路径和自然语言任务：

```sh
uv run uvicorn selfforge_agent.api.app:app --host 127.0.0.1 --port 8000
# 打开 http://127.0.0.1:8000，选择 Docker，启动 Coding Agent
```

页面显示任务状态、独立工作区路径、相对基线 commit 的有界 Diff 与最近的 JSONL 轨迹，可取消或用会话 ID 继续。工作区无未提交或未跟踪文件时可在页面清理；分支仍保留。新会话写入用户数据目录的 `selfforge-agent/sessions/`；旧版直写会话需选择“直接修改原仓库”后按 ID 续跑。服务重启后会话可续跑，但运行中任务的状态不会保留。选择直写需单次确认；Web 的本机命令执行还需要 `SELFFORGE_ENABLE_HOST_TESTS=1` 和单次勾选信任。

## CLI

```sh
# 通用 Coding：自主检查、修改并运行测试
uv run selfforge-agent code --repo ./your-project --task '修复 add 的回归' \
  --max-steps 12 --time-budget 300

# 继续一个已有会话；新会话保存在用户数据目录
uv run selfforge-agent code --repo ./your-project --resume SESSION_ID --task '继续并验证'
uv run selfforge-agent trace --repo ./your-project SESSION_ID

# 检查工作区中的 Git diff；提交后清理 worktree，保留分支
uv run selfforge-agent workspaces list --repo ./your-project
git -C /path/from/workspaces/list diff
uv run selfforge-agent workspaces remove SESSION_ID --repo ./your-project

# 只有明确需要时才直接修改原仓库
uv run selfforge-agent code --repo ./your-project --workspace direct --task '修复 add'
# 续跑旧版直接修改仓库的会话也需 --workspace direct

# 单独运行测试，默认使用 Docker
uv run selfforge-agent test --repo ./your-project --path tests

# 无 API Key 的真实 Docker 修复演示（先构建镜像）
uv run python examples/repair_demo.py

# 已信任的本机测试，可保留 JUnit 计数
uv run selfforge-agent test --repo ./your-project --sandbox host --trust-local

# 专项：本地 PR 比较审查与 Base/Head/Repaired 修复
uv run selfforge-agent review --repo ./your-project --base BASE --head HEAD
uv run selfforge-agent repair --repo ./your-project --base BASE --head HEAD \
  --task '修复现有测试证明的回归' --patch-output repair.patch

# 专项：生成单个候选回归测试并在 Base/Head 差异验证
uv run selfforge-agent generate-test --repo ./your-project --base BASE --head HEAD \
  --task 'add(2, 3) 应返回 5' --output test-evidence.json --test-output test_regression.py

# 仅对可信 PR 显式选择主机执行
uv run selfforge-agent repair --repo ./your-project --base BASE --head HEAD \
  --task '修复回归' --sandbox host --trust-local-tests
```

Agent 状态有 `completed`（最后修改后 pytest 成功）、`unverified`（结束但缺少测试证据）、`failed`、`max_steps`、`timed_out`、`cancelled`。新会话与轨迹保存在 `${XDG_DATA_HOME:-~/.local/share}/selfforge-agent/sessions/`，不会进入目标仓库；旧会话仍可通过 ID 恢复。`workspaces remove` 不使用强制删除；工作区有改动时会拒绝清理。运行中可用 Ctrl-C 中断；当前取消检查发生在模型调用和工具调用之间，单个调用仍受自身超时限制。

## 架构

```mermaid
flowchart LR
  U[用户任务] --> I[CLI / Web 工作台]
  I --> W[CodingWorkspaceManager / Git worktree]
  I --> A[CodingAgent]
  W --> A
  A --> C[ContextManager]
  C --> M[OpenAIChatAdapter]
  M --> T[ToolRegistry]
  T --> F[文件与搜索]
  T --> X[Docker 或可信 Host 执行器]
  X --> P[pytest]
  A --> S[SessionStore / JSONL Trace]
  P --> A
  PR[PR Review / Repair] --> R[RepairAgent]
  R --> T
  R --> E[Git worktree / DockerTestRunner]
  E --> X
  E --> S
```

Runtime 分为 Agent Loop、模型适配器、工具注册、执行器、上下文与会话模块，详见[架构文档](./docs/architecture.md)。PR Repair 的精确替换已使用同一个工具注册器及受限编辑策略；它的模型提案与有界修复循环仍独立于通用 Tool Calling Agent。

## Verification 与 Evaluation

通用 Coding Agent 的完成判断以 `run_tests` 的实际退出码为证据；若最后一次编辑之后没有成功测试，则返回 `unverified`。`eval` 还会独立重新运行夹具测试，并检查源文件确实发生变化，不采信模型口头结论。PR Repair 则要求 Base 测试通过、Head 出现断言失败、Repaired 测试通过，才输出已验证补丁；这证明现有测试的回归已修复，尚不证明某条审查发现的因果关系。

`generate-test` 生成一个 pytest 候选文件并分别在 Base/Head 执行；仅当 Base 通过、Head 有断言失败且没有收集错误时，`--test-output` 才写出该测试。其他结果为 `inconclusive` 或 `error`。测试只在临时 worktree 中运行，显式导出前不会进入项目。差异结果也不能单独证明某条审查发现的因果关系。

```sh
uv run selfforge-agent eval --offline       # 确定性工具协议回归，不代表模型能力
uv run selfforge-agent eval --provider deepseek  # 真正调用模型，运行生成夹具
uv run pytest
uv run ruff check .
```

2026-10-17 的一次本地运行：离线协议夹具 **2/2**；DeepSeek 真实工具调用 **2/2**。两道夹具分别修复加法和姓名规范化，成功条件是文件已修改且独立 pytest 退出码为 0。样本很小，不构成通用 Coding Agent 基准；详情和复现限制见[评测记录](./docs/evaluation.md)。

## Specialized Workflows：PR Review

Web 工作台也可以导入自己或他人的可访问 GitHub PR，查看 Diff 与启发式/模型发现、下载可编辑分支、生成差异验证测试，并运行验证修复。审查发现始终标为 `suspected`；导入与审查不会执行 PR 代码。Web 测试生成与修复默认在 Docker 中执行；选择主机执行时，仍需 `SELFFORGE_ENABLE_HOST_TESTS=1` 和单次明确勾选信任。工具不会自动推送、评论或合并 PR。修复轨迹保存在 checkout 的 `.selfforge/sessions/`，可用 `selfforge-agent trace --repo CHECKOUT RUN_ID` 查看。

## Roadmap

下一步优先实现多候选且与具体风险关联的测试生成、PR 风险驱动测试、跨进程任务协调及更广的客观评测。见[路线图](./docs/roadmap.md)。
