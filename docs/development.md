# 开发指南

本文件说明怎样推进和验证 selfForge Agent；功能定义以[产品边界](./product-scope.md)为准，执行路径见[架构](./architecture.md)，缺陷证据见[代码审计](./code-audit.md)。[Roadmap](./roadmap.md)记录能力状态，不把候选评测题数量当作产品完成度。

## 当前交付边界

目标是面向 Python Git 仓库的 Coding Agent 与测试框架：用户提交任务，Agent 在独立 worktree 中读、改、测，交付可检查的 Diff、测试结果和会话轨迹。Web 与 CLI 使用同一 Runtime。PR Review、差异测试生成和 Repair 是专项流程。

- 已有：原生 Tool Calling、受限文件工具、Docker 执行、可恢复会话、Web 工作台、独立工作区、非空 JUnit 验证、预算反馈、参考修复预检与私有失败诊断；显式离线依赖镜像和会话环境绑定已接入同一 Runtime。
- 尚未完成：自动依赖识别与更广的依赖配置、完整产品 PR 交付流程、20 题 × 每题 3 次的正式模型验收。Repair 的 proposer loop 仍独立。
- 暂不增加：Multi-Agent、通用 MCP 平台、额外 RAG、大型前端或另一套 Runtime。
- `completed` 要求最后一次可能持久写入后 `run_tests` 有非空 JUnit 通过证据、测试前后和当前工作区指纹一致，且模型给出非空最终答复；不代表需求全部满足，也不防恶意仓库伪造证据。正式评测还需独立可见/隐藏测试与允许修改路径检查。

七个 PR 的组合与验证记录见[Runtime 整合](./runtime-integration.md)。独立分支历史测试数不能相加，也不能用于替代组合版本的全量回归。

## 模块与变更入口

| 责任 | 主要源码 | 相关回归 |
| --- | --- | --- |
| 工具循环、完成条件、中断恢复、预算、上下文与审批 | `runtime/agent.py`、`session.py`、`fingerprint.py`、`context.py`、`control.py` | `test_runtime.py`、`test_runtime_recovery.py`、`test_context.py`、`test_context_precision.py`、`test_session_metrics.py`、`test_runtime_budget.py`、`test_runtime_composition.py`、`test_workspace_evidence.py`、`test_workspace_fingerprint.py` |
| 依赖镜像与会话环境 | `runtime/images.py`、`environment.py` | `test_dependency_images.py`、`test_session_environment.py`、`test_environment_detection.py` |
| 文件权限、编辑策略、命令与暂存限制 | `runtime/tools.py`、`execution.py` | `test_runtime.py`、`test_docker_staging.py` |
| 模型协议、回退、配置 | `runtime/model.py`、`providers.py`、`integrations/model_http.py` | `test_model_deadline.py`、`test_providers.py` |
| Web 任务持久化、锁、取消 | `api/coding_jobs.py`、`api/app.py` | `test_coding_jobs_durable.py`、`test_coding_web.py`、`test_web_origin.py` |
| 工作区、Diff 与交付 | `runtime/workspace.py`、`workspace_diff.py`、`delivery.py` | `test_workspace.py`、`test_workspace_delivery.py` |
| 测试证据、诊断、修复、测试生成与风险归因 | `verification/`、`coding/`、`runtime/pricing.py` | `test_coding_evidence.py`、`test_test_diagnostics.py`、`test_runner_report_api.py`、`test_repair_agent.py`、`test_docker_repair.py`、`test_test_generation.py`、`test_risk_prioritization.py`、`test_causal_investigation.py`、`test_pricing.py` |
| 独立评测、参考修复与私有快照 | `runtime/benchmark.py`、`evaluation.py`、`failure_artifacts.py` | `test_benchmark.py`、`test_benchmark_isolation.py`、`test_reference_validation.py`、离线 Coding 演示 |

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
| 已整合 | 中断后的验证状态恢复 | 写入前失效、未完成调用保守恢复；组合测试覆盖 JUnit 成功后中断写入，再续跑失败、编辑、重测成功 |
| 已整合 | 通用 Coding 测试证据 | 共用非空 JUnit Runner；全部 skipped、空/损坏报告、超时和失败诊断均有回归，不弱化 Repair/benchmark 门槛 |
| 已整合 | 显式可复现项目依赖环境 | 哈希锁定 wheels、离线安装和镜像 ID；会话模式/镜像不可静默更换 |
| 已整合 | 工作区与测试证据绑定 | 前后测试、后续模型请求和最终答复时校验内容；恢复/外部编辑撤销旧证据；有界扫描与 v2 迁移有回归，真实 Docker 用例需 daemon/镜像 |
| 本轮实现 | 依赖环境识别与 Web 重启恢复验证 | 识别 src/flat 布局、pyproject/requirements、锁文件及 pytest 目录；回归覆盖异常退出后重启续跑至完成 |
| 本轮实现 | 补全交付体验 | 从保留的 worktree 检查 Diff、提交并创建 PR；明确授权边界，排除受保护文件，附带测试验证状态，覆盖远端与 gh 失败恢复，不自动强推或合并 |
| 本轮实现 | 风险自动排序、因果归因与成本核算 | 依据严重度、类别与代码特征排序风险假设；差异测试失败因果回溯至目标行/文件；校准模型计费与总成本核算；单测覆盖全链路 |
| 本轮实现 | 精确 Token 估算、结构化压缩、全链路成本与交互审批 | 覆盖代码语法与 CJK 的精确 Token 估算；结构化操作摘要与保留近期交互轮次；Agent/Repair/CLI/Web 全链路 Token 与校准成本追踪；CLI 交互式审批 `--interactive` 与 `--summary` 诊断 |
| 本轮实现 | 工作区还原、锁文件依赖导出与 PR 修复环境绑定 | 支持 worktree 恢复已跟踪文件并清理未跟踪文件同时保护凭据；支持解析 uv.lock 导出沙箱离线构建所需的哈希锁定 requirements；PR 修复接口支持指定自定义镜像与执行超时 |
| 4 | 审核并扩展正式评测集 | 每题有行为契约来源、负例基线、可通过的参考修复及正向/边界 holdout；区分 bug fix 与功能变更；冻结策略后完成 ≥3 仓库、20 题、每题 ≥3 次真实模型运行 |

只验证 holdout 在原代码上失败，无法证明题目合理或可解；不能用任意输入限制凑任务数量。九题清单已包含在组合交付中，仍属于候选，不替代正式验收。现有[五题参考控制集](../benchmark/validated-candidates/iterator-traversal.md)跨三个仓库，全部通过 Docker 负例/参考正例；其中异步空输入、Unicode 分隔与零分块任务和早期候选重叠，不能额外计数。目录深度剪枝与替换回调停止涉及不同执行机制；前者保留边界目录匹配和忽略规则，后者是明确的功能扩展，不能宣称为上游确认的 bug。

## 尚需明确的可靠性边界

- 文件工具限制路径，Docker 隔离命令；Host 显式信任后可写工作区。隔离副本中的命令修改不回写。
- 测试证据已有[有界内容指纹绑定](./workspace-evidence.md)，能在执行/恢复时发现外部改动；它不是原子快照、持续文件监控或 CLI 工作区锁。最后检查后的竞态、排除路径与 Host 外部依赖不在保证范围内。
- 会话支持进程中断恢复，不承诺断电后日志和文件写入的事务一致性。完整损坏记录仍明确报错。
- Web 锁跨进程，活动任务数量限制仍按进程。取消不保证即时中止阻塞的 HTTP 请求。
- 审计是分阶段检查，不能以全量 pytest 通过代替真实模型、外部 GitHub 故障或安全攻击链验证。
