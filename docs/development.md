# 开发指南

本文件说明怎样推进和验证 selfForge Agent；功能定义以[产品边界](./product-scope.md)为准，执行路径见[架构](./architecture.md)，缺陷证据见[代码审计](./code-audit.md)。[Roadmap](./roadmap.md)记录能力状态，不把候选评测题数量当作产品完成度。

## 当前交付边界

目标是面向 Python Git 仓库的 Coding Agent 与测试框架：用户提交任务，Agent 在独立 worktree 中读、改、测，交付可检查的 Diff、测试结果和会话轨迹。Web 与 CLI 使用同一 Runtime。PR Review、差异测试生成和 Repair 是专项流程。

- 已有：原生 Tool Calling、受限文件工具、Docker 命令执行、可恢复会话、Web 工作台、工作区隔离、pytest 验证与重复评测工具。
- 尚未完成：真实项目依赖环境的可复现配置、完整 PR 交付流程、20 题 × 每题 3 次的正式模型验收。Repair 的 proposer loop 仍独立。
- 暂不增加：Multi-Agent、通用 MCP 平台、额外 RAG、大型前端或另一套 Runtime。
- `completed` 表示最后一次可能持久写入后的 Agent 测试命令成功；不代表需求全部满足，也不防恶意仓库伪造测试证据。正式评测还需独立可见/隐藏测试与允许修改路径检查。

## 模块与变更入口

| 责任 | 主要源码 | 相关回归 |
| --- | --- | --- |
| 工具循环、完成条件、中断恢复 | `runtime/agent.py`、`session.py`、`context.py` | `test_runtime.py`、`test_runtime_recovery.py`、`test_context.py` |
| 文件权限、编辑策略、命令与暂存限制 | `runtime/tools.py`、`execution.py` | `test_runtime.py`、`test_docker_staging.py` |
| 模型协议、回退、配置 | `runtime/model.py`、`providers.py`、`integrations/model_http.py` | `test_model_deadline.py`、`test_providers.py` |
| Web 任务持久化、锁、取消 | `api/coding_jobs.py`、`api/app.py` | `test_coding_jobs_durable.py`、`test_coding_web.py`、`test_web_origin.py` |
| 工作区及 Diff | `runtime/workspace.py`、`workspace_diff.py` | `test_workspace.py` |
| 测试证据、修复与测试生成 | `verification/`、`coding/` | `test_runner_report_api.py`、`test_repair_agent.py`、`test_docker_repair.py`、`test_test_generation.py` |
| 独立任务评测 | `runtime/benchmark.py`、`evaluation.py` | `test_benchmark.py`、离线 Coding 演示 |

源码相对 `src/selfforge_agent/`，测试相对 `tests/`。新增能力先找现有模块的扩展点；只为已确认的问题增加抽象。

## 本地环境与验证

Python 3.11+、Git、uv；完整隔离验证还需要 Docker daemon。安装步骤应使用锁文件：

```sh
uv sync --locked --dev
uv run --no-sync ruff check .
uv run --no-sync pytest -q -m 'not integration'
docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .
uv run --no-sync pytest -q -m integration
# 提交前最终回归（包含可用的 Docker 集成测试）
uv run --no-sync pytest -q
uv run --no-sync ruff check .
git diff --check
```

缺少 daemon/镜像时，Docker 测试会跳过；报告必须写出 skipped 数量，不能称为隔离验证通过。`--no-sync` 仅复用当前环境，不能证明依赖已按锁文件安装；若同步因网络失败，应记录该限制。默认镜像只有 Python/pytest，不具备任意项目依赖。

无需 API Key 的链路演示：

```sh
uv run --no-sync python examples/coding_demo.py
```

它证明工具协议及代码修改/测试链路可执行，不作为真实模型成功率。真实供应商测试与 `eval-suite` 单独运行，记录模型、任务 SHA、镜像 ID、步数/时间预算、重复次数和报告哈希；不得提交密钥、原始会话、工具输出或课程源码。

## 每次开发的最小闭环

1. 检查 Git 工作区、当前 `main` 和未合并 PR，明确本次基线及依赖。独立修复从已合并代码开始，避免无意堆叠 PR。
2. 用代码路径与复现区分 P0/P1/P2；未经验证的推断明确标注。行为缺陷先写能在旧实现失败的回归测试。
3. 更新文档中的边界与验收条件，然后实施小范围修改。保留既有测试，不以删断言获得通过。
4. 先跑相关测试，再跑完整 pytest 和 Ruff。涉及 Web/Docker/跨进程状态时加对应故障或集成验证；只有存在剩余风险或新代码变化时才重复全量验证。
5. 检查 diff、敏感信息和文档一致性，提交并记录 commit/PR。区分“本地修复”“CI 通过”“已合并”，不得混用。

## 近期开发顺序与验收

| 顺序 | 工作 | 验收证据 |
| --- | --- | --- |
| 1 | 修复中断导致测试证据过期却仍可恢复的问题 | 编辑/Host 命令/重测被中断后不沿用旧成功；日志失败时不执行写入；旧会话续跑保守处理未完成调用；重新测试可恢复完成状态 |
| 2 | 统一通用 Coding 的测试证据要求 | 复用 JUnit Runner；覆盖全部 skipped、空测试、缺失或损坏 JUnit、测试超时；与 Repair/benchmark 的更严格门槛逐项对照，明确兼容性 |
| 3 | 可复现的项目依赖环境 | 至少一个需要额外依赖的仓库，固定依赖及镜像标识，离线隔离运行；安装失败有明确诊断，不放开默认执行网络 |
| 4 | 审核并扩展正式评测集 | 每题有行为契约来源、负例基线、可通过的参考修复及正向/边界 holdout；区分 bug fix 与功能变更；冻结策略后完成 ≥3 仓库、20 题、每题 ≥3 次真实模型运行 |
| 5 | 补全交付体验 | 从保留的 worktree 检查 Diff、提交并创建 PR；明确授权边界，覆盖远端失败和恢复，不自动强推或合并 |

只验证 holdout 在原代码上失败，无法证明题目合理或可解；不能用任意输入限制凑任务数量。PR #24 的九题清单在本次检查时仍未合并，属于候选，不替代正式验收。

## 尚需明确的可靠性边界

- 文件工具限制路径，Docker 隔离命令；Host 显式信任后可写工作区。隔离副本中的命令修改不回写。
- 测试成功与工作区内容还没有指纹绑定，外部编辑器改动不一定使证据失效；同一 workspace 的 CLI 并发使用也需进一步审查。
- 会话支持进程中断恢复，不承诺断电后日志和文件写入的事务一致性。完整损坏记录仍明确报错。
- Web 锁跨进程，活动任务数量限制仍按进程。取消不保证即时中止阻塞的 HTTP 请求。
- 审计是分阶段检查，不能以全量 pytest 通过代替真实模型、外部 GitHub 故障或安全攻击链验证。
