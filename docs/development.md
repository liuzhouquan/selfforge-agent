# 开发指南

本指南回答新贡献者或 coding agent 的四个问题：先读什么、改哪里、怎样验收、如何交接。产品目标是面向 Python Git 仓库的 Coding Agent 与测试框架；CLI/Web 共用 Runtime，PR Review/差异测试/Repair 是专项流程。

## Reading order

1. [AGENTS.md](../AGENTS.md)：自动读取的短入口、任务边界与通用交付规则。
2. [Agent 协作流程](./agent-workflow.md)：确认现场 → 写任务 → 实施 → 验证 → 交接；小任务自主推进，不等待形式审批。
3. [产品边界](./product-scope.md)与[可接手任务](./backlog.md)：分清目标、已实现、待验证和本轮选中的一个工作单元。
4. 下方模块表，以及涉及模块的[工程约束](./engineering-invariants.md)和专门契约；无需一开始通读所有历史报告。
5. 需要追踪执行路径时读[架构](./architecture.md)；调查旧问题时再读带基线 SHA 的审计/评测记录。

跨模块或行为变更使用[任务卡](./task-template.md)。Owner、分支、基线、验收、真实结果和下一步放在同一个记录里，避免依赖聊天上下文。人和 agent 使用相同流程；是否启动并行 agent 由当前用户/工具授权决定。

## 文档职责与冲突处理

| 文件 | 回答的问题 | 更新时机 |
| --- | --- | --- |
| 当前用户任务 / 任务卡 | 这一轮到底做什么、哪些不做、谁接手 | 开工、必要范围变化、交接 |
| `AGENTS.md` | 全局规则、读取顺序、交付要求 | 协作规则变化；保持简短 |
| `product-scope.md` / `.en.md` | 最终产品边界及完成条件 | 产品能力/验收边界变化；两种语言同步 |
| `backlog.md` | 下一位可以接哪个小任务、前提是否满足 | 认领/完成/优先级变化 |
| `engineering-invariants.md` 及链接契约 | 哪些执行、权限、状态和证据规则不能回退 | 所属行为变化且有对应验证时 |
| `architecture.md` | 实际模块职责和调用路径 | 数据流/职责变化 |
| `roadmap.md` | 能力级状态和较长期方向 | 功能状态变化；不当作一次任务清单 |
| 审计/评测/整合记录 | 某个 SHA 下的观察与证据 | 保留历史；补后续链接，不能冒充当前待办 |

用户指令优先。发现文档互相冲突或实现不符时，用源码路径和复现确认实际行为，修正所属文档或实现；不能仅因为“代码现在如此”就放宽安全/证据规则。当前状态只在所属文件维护，README 链接过去；测试数、模型分数只在对应 SHA/策略的证据记录维护，避免多处复制漂移。

## 当前能力与边界

已有一套原生 Tool Calling Runtime、受限工具、会话/轨迹、Docker 执行、Web 工作台、持久 worktree、非空 JUnit 与内容指纹证据，以及显式 commit/PR 交付。已有只读依赖/布局发现、有限锁导出、离线镜像准备、风险排序、堆栈关联、交互审批和可选配置费率估算。

正式 20 题 × 每题至少 3 次真实模型验收仍未完成；自动安装构建环境、因果反事实验证和模型专用精确 Tokenizer 仍开放。Repair proposer loop 仍独立。详细状态以产品边界为准，下一步从 Backlog 选择，不重复实现已合并能力。

当前不增加多 Agent 产品编排、通用 MCP 平台、额外 RAG、大型前端或另一套 Runtime；开发时多人/agent 分工与产品内的 Multi-Agent 能力是两件事。

`completed` 要求非空 JUnit 成功、测试前后及当前工作区指纹匹配，并有非空最终答复；它不代表全部用户需求已经被充分验证。独立 Eval 还有允许修改路径和可见/隐藏测试门槛。指纹不是锁、原子快照或恶意 Host 防御；Web 锁不代表 CLI/外部进程已有统一协调。

会话恢复覆盖进程中断，不承诺断电事务一致性；取消不保证立即中止阻塞中的 HTTP 请求。这些边界不能因本地测试通过而写成已解决。

## Module entry points

源码相对 `src/selfforge_agent/`，测试相对 `tests/`。新增行为先找现有扩展点；不要并建另一套 Runtime、Runner 或持久状态系统。

| 责任 | 主要源码 | 优先回归/契约 |
| --- | --- | --- |
| Native loop、预算、恢复、上下文、审批 | `runtime/agent.py`、`runtime/session.py`、`runtime/context.py`、`runtime/control.py` | `test_runtime.py`、`test_runtime_recovery.py`、`test_runtime_composition.py`、`test_runtime_budget.py`、`test_context*.py`；[Runtime 约束](./engineering-invariants.md#runtime-model-protocol-and-context) |
| 文件/搜索/执行器与暂存 | `runtime/tools.py`、`runtime/execution.py` | `test_runtime.py`、`test_docker_staging.py`；[工具与沙箱](./engineering-invariants.md#tools-sandbox-and-dependency-environments) |
| 依赖准备、环境绑定与发现 | `runtime/images.py`、`runtime/environment.py` | `test_dependency_images.py`、`test_session_environment.py`、`test_environment_detection.py`、`test_delivery_revert_and_lock_export.py`；[依赖镜像](./dependency-images.md) |
| 模型、回退和配置 | `runtime/model.py`、`providers.py`、`integrations/model_http.py`、`coding/model_client.py` | `test_model_deadline.py`、`test_providers.py`、`test_repair_agent.py` |
| Web 任务、序列化、锁、取消、UI | `api/coding_jobs.py`、`api/app.py`、`api/static/` | `test_coding_jobs_durable.py`、`test_coding_web.py`、`test_web_origin.py`、`test_session_metrics.py`；[状态与 Web 约束](./engineering-invariants.md#private-state-providers-web-jobs-and-workspaces) |
| 工作区、Diff、提交/PR/还原 | `runtime/workspace.py`、`runtime/workspace_diff.py`、`runtime/delivery.py` | `test_workspace.py`、`test_workspace_delivery.py`、`test_delivery_safety.py`；[交付契约](./pr-delivery.md) |
| JUnit、诊断、内容证据 | `verification/`、`runtime/fingerprint.py`、`runtime/failure_artifacts.py` | `test_coding_evidence.py`、`test_test_diagnostics.py`、`test_workspace_evidence.py`、`test_workspace_fingerprint.py`；[证据约束](./engineering-invariants.md#junit-diagnostics-and-workspace-evidence) |
| Review、差异测试、Repair | `review/`、`coding/` | `test_repair_agent.py`、`test_docker_repair.py`、`test_test_generation.py`、`test_risk_prioritization.py`、`test_causal_investigation.py`；[专项约束](./engineering-invariants.md#specialized-pr-review-testing-and-repair) |
| 用量与可选费用 | `runtime/session.py`、`runtime/pricing.py`、`coding/agent.py` | `test_session_metrics.py`、`test_pricing.py`、`test_repair_agent.py`；[计量规则](./token-metrics.md) |
| 独立评测与参考预检 | `runtime/benchmark.py`、`runtime/evaluation.py` | `test_benchmark.py`、`test_benchmark_isolation.py`、`test_reference_validation.py`；[评测约束](./engineering-invariants.md#independent-evaluation-and-reference-controls) |

## Verification gates

需要 Python 3.11+、Git、uv；当前 GitHub CI 使用 Python 3.12。没有配置类型检查器，不要把 Ruff 通过写成类型检查通过。

```sh
uv sync --locked --dev
# 按本任务选择相关测试；以下只是交付变更的例子
uv run --no-sync pytest -q tests/test_delivery_safety.py
uv run --no-sync ruff check .
# Docker 相关/最终隔离验证前启用 daemon 并准备镜像
docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .
# 对最终组合执行一次完整回归
uv run --no-sync pytest -q
git diff --check
```

镜像构建命令在仓库根目录执行；默认镜像只有 Python/pytest，不包含任意项目依赖。无需重复执行与本次无关且已通过的全套命令；源码再改动、出现失败或未解决风险时，重跑相关检查并确认最终组合。CI 按不重叠的 `not integration` / `integration` 分组覆盖完整测试集，不相加不同分支/重叠选择的数量。

| 变更/环境 | 最低需要的证据 |
| --- | --- |
| 运行行为或状态 | 有意义的相关回归、最终组合完整 pytest、Ruff；缺陷要有旧行为失败证据 |
| Docker/权限/跨进程 | 对应集成或故障验证；缺 daemon/镜像的 skip 必须说明，等待可用环境/远端结果 |
| API/UI | 检查实际序列化响应；JS 变更检查 `node --check src/selfforge_agent/api/static/app.js`；交互变化做浏览器 smoke 或明确尚未覆盖 |
| 仅文档/模板 | 相对链接及锚点、引用源码/测试路径、状态一致性、`git diff --check`；本地无需 Python pytest |
| 模型或 Eval | 固定任务/报告哈希、模型/供应商策略、镜像、预算与重复次数；真实服务结果单列 |

本机没有 Docker 时可先执行 `uv run --no-sync pytest -q -m 'not integration'`。完整 pytest 中 Docker 项跳过不能称为“隔离通过”；保留 skipped 数量/原因，远端 CI 必须实际构建镜像并执行集成组。依赖同步失败时说明原因；`--no-sync` 本身不证明锁文件环境已经安装。

无需 API Key 的链路演示为 `uv run --no-sync python examples/coding_demo.py`；它证明协议与改测链路，不代表模型能力。真实供应商/写入 GitHub 的操作沿用当前授权和预算，记录 mock 与真实调用的区别，不重复请求已有授权。

`.github/workflows/ci.yml` 在 PR 和 main push 上执行锁定依赖、Ruff、单元与 Docker 集成测试；工作流 token 保持只读，不能加入模型密钥。CI 配置变更需检查真实远端运行，不能用本地 pytest 代替。

## 交付完成条件

任务卡的每个验收编号都有实际结果或明确未完成项；错误路径、消费者和必要的集成边界已覆盖；没有删除/削弱旧断言；源码、文档声明与验证口径一致；敏感 diff 已检查；HEAD、PR、CI 和剩余事项已写入交接。

已实现、本地验证、CI 通过、已合并是不同状态。仅在当前用户授权整合后完成合并；整合时保留提交历史并确认 main。完整流程及可复制的接手指令见 [Agent 协作流程](./agent-workflow.md)。
