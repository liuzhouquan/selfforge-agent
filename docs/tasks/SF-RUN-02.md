# SF-RUN-02：运行预算关键轮次引导与收尾保护

> 已核对：PR #38 已合并，merge SHA `74c5d4039b95b2e0bedd821033ffa7a8861b454d`。以下规格/测试是历史记录；当前开发以 [SDD](../SDD.md) 为准，不重新领取本任务。

- 状态：Merged（历史任务）
- Owner：Antigravity Agent
- 基线：`7eabefc67439432001921e5dbe81aad50b2bfc18`
- 分支：`feat/runtime-budget-turn-guidance`
- 依赖：PR #36 已合并入 main；PR #37 发现 57%（4/7）评测失败为 `agent_max_steps`
- PR：[PR #38](https://github.com/liuzhouquan/selfforge-agent/pull/38)

## 目标与范围

在 20 题 × 3 次真实模型评测（SF-EVAL-03）中，57%（4/7）的失败为 `agent_max_steps`。其中两例（`more_itertools_zero_chunk_size` r1 与 `pocketflow_invalid_retries` r2）在第 18 步（最大步数）成功执行并通过了 `run_tests`，但因处于最大步数末轮，工具执行完成后循环即刻终止，无后续轮次输出最终答复，导致被判定为 `max_steps` 失败。另两例的工具元数据分别为：`more_itertools_replace_limit` r2 调用 11 次 bash、0 次 run_tests；`pocketflow_invalid_retries` r3 调用 12 次 bash、1 次 run_tests，最后一次工具是 edit_file。后者不能描述为从未调用 run_tests。

分析发现：
1. 倒数第二轮（`remaining_turns == 2`，即第 `max_steps - 1` 步）：旧提示词与普通预算不足提示词混淆，未明确该轮是能执行工具/测试并留有最终答复轮次的**最后机会**。
2. 最后一轮未通过测试（`remaining_turns == 1 and not tests_passed`）：旧代码存在语义自相矛盾的提示词：“prioritize the required edits and run_tests ... before the final turn”（在最后一轮提示“在最后一轮前完成”），可能影响模型的收尾选择；这是基于可复现提示矛盾的待验证假设，现有记录不证明该提示导致了真实模型失败。
3. 预算紧张时（`remaining_turns <= 3` 或 `remaining_seconds <= 45`）：未警告避免使用 `bash` 代替 `run_tests` 进行非官方验证。

本次改进：
- 在 `src/selfforge_agent/runtime/control.py` 中精细化关键轮次引导：
  - `remaining_turns == 2 and not tests_passed`：明确标识倒数第二轮（Penultimate turn），提示这是最后一次运行工具和测试的机会，引导集中精力执行编辑与 `run_tests`，为最后一轮最终答复留出空间；明确警告勿使用 `bash` 测试。
  - `remaining_turns == 1 and not tests_passed`：消除自相矛盾的“在最后一轮前”提示，明确最后一轮若调用任何工具将耗尽步数以 `max_steps` 结束；若不再调用工具，引导输出最终答复诚实汇报缺少验证（得到合规的 `unverified` 退出）。
  - `remaining_turns <= 3 or remaining_seconds <= 45`：警告禁止使用 `bash` 测试替代 `run_tests` 证据。
- 保持 `src/selfforge_agent/runtime/agent.py` 原生循环和工程约束完全不变：
  - 不人为增加任何额外模型轮次。
  - 不代替模型运行测试。
  - 不将步数超限判定为成功。
  - 最后一轮通过测试时保持禁用工具（`final_only`）。

本次不做：不改变步数上限数值；不放宽 `completed` 判定条件；不修改冻结的 benchmark 资产。
预计触及：`src/selfforge_agent/runtime/control.py`、`tests/test_runtime_budget.py`、`docs/runtime-budget.md`、`docs/backlog.md`。
复用：现有 `budget_notice` 生成逻辑、`ContextManager` 空间预留、`turn_budget` 追踪机制。
约束：[Runtime 约束](../engineering-invariants.md#runtime-model-protocol-and-context)及 [Budget 契约](../runtime-budget.md)。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 倒数第二轮（`remaining_turns == 2` 且未验证）生成提示词 / 观察输出包含 "Penultimate turn"、"final opportunity to run tools and tests"、"run_tests on this turn" | 单元测试断言提示词内容 | 已通过 |
| A2 | 最后一轮未验证（`remaining_turns == 1 and not tests_passed`）生成提示词 / 观察输出包含 "Final turn without passing verification"、警告调用工具将耗尽步数，且**不包含**矛盾的 "before the final turn" | 单元测试断言，证明旧代码存在矛盾断言失败 | 已通过（旧实现失败，新实现通过） |
| A3 | 保持向后兼容：`remaining_turns == 1 and tests_passed` 仍生成 "Final-answer-only"；普通充足预算轮次仍生成规划提示 | 现有及新增断言对比 | 已通过 |
| A4 | Agent 端到端流程：末轮未验证时模型返回文本回复，Agent 干净退出为 `unverified` 并记录 steps=3；若末轮模型仍调用工具，则工具执行后以 `max_steps` 退出 | `test_budget_feedback_penultimate_and_final_unverified_agent_flow` 与 `test_budget_feedback_final_turn_tool_call_exhausts_steps` | 已通过 |
| A5 | 完整测试集回归无破坏 | `uv run --no-sync pytest -q` | 已通过（375 单元测试通过，11 Docker 集成测试通过） |

## 实施顺序

1. 在 `tests/test_runtime_budget.py` 补充缺陷捕捉测试，在旧代码上执行确认失败（已证明旧代码在 A2 断言失败）。
2. 在 `src/selfforge_agent/runtime/control.py` 实现精细化分支逻辑。
3. 补充 A4 真实端到端 Agent 流程测试并验证全部 10 个预算测试通过。
4. 运行全部相关 runtime 模块测试（64 个用例全部通过）。
5. 更新 `docs/runtime-budget.md` 和 `docs/backlog.md`。
6. 执行完整代码质量门禁（Ruff、Git diff、全量测试 386 项全通过）。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync pytest -q tests/test_runtime_budget.py` | 本地 Host Python 3.12 | `feat/runtime-budget-turn-guidance` | 10 passed in 4.80s |
| `uv run --no-sync pytest -q tests/test_runtime*.py tests/test_context*.py` | 本地 Host / Docker | `feat/runtime-budget-turn-guidance` | 64 passed in 58.00s |
| `uv run --no-sync pytest -q` | 本地 Host Python 3.12 | `feat/runtime-budget-turn-guidance` | 375 passed, 11 skipped in 348.31s |
| `uv run --no-sync pytest -q -m integration` | 本地 Docker 29.8.0 | `feat/runtime-budget-turn-guidance` | 11 passed in 60.27s |
| `uv run --no-sync ruff check .` | 静态检查 | `feat/runtime-budget-turn-guidance` | All checks passed |
| `git diff --check` | 差异检查 | `feat/runtime-budget-turn-guidance` | Clean (0 issues) |


审查补充：本次源码仅改变 budget_notice，不新增执行层工具保护。原有 final_only 门槛保持；脚本化回归证明协议/退出状态，尚未以真实模型对照试验证明成功率提升。评测剖析已按私有报告的工具元数据纠正。
