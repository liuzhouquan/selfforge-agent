# SF-RUN-06：上下文 Token 预算感知与高压预警引导

- 状态：In Review
- Owner：Antigravity Agent
- 基线：`4ecd6a5d605c7f3b5b9140f00cd1a5c3f6360c7a`（`origin/main`）
- 分支 / 所属 worktree：`feat/runtime-token-budget-guidance`
- 依赖：无（基于已合并的 PR #47 / main）
- PR：[#53](https://github.com/liuzhouquan/selfforge-agent/pull/53)

## 目标与范围

在自主 Coding 循环中，针对长会话或存在较多工具调用/输出的任务中，模型对上下文 Token 消耗缺乏实时感知，容易在无预警的情况下继续触发大体积输出（如全量读文件或大段终端 dump），从而过早触发上下文压缩（compaction）、丢失近期详细交互信息，甚至耗尽上下文预算导致崩溃的痛点，提供实时的上下文 Token 预算感知、高压预警与压缩后瞬态引导机制。

原来发生什么：
1. `CodingAgent` 虽然在循环中通过 `ContextManager` 进行上下文估算与超限压缩（`compact`），但在每轮向模型下发的瞬态 `budget_notice` 中，仅包含了轮次数、剩余时间和测试通过状态，模型完全无法获知当前上下文已占用多少 Token 或是否处于接近上限的高压危险区。
2. 当上下文被压缩后，模型缺乏明确的瞬态提示，可能因历史摘要与原始上下文的差异而产生混乱。
3. `turn_budget` 轨迹事件没有记录当轮上下文估算 Token 数、预算上限和压缩状态，离线分析时无法追溯上下文膨胀轨迹。

本次完成后应观察到：
1. 实现 `evaluate_context_pressure(estimated_tokens: int, token_budget: int, *, was_compacted: bool = False, high_watermark: float = 0.75) -> dict[str, Any] | None`：
   - 当 `estimated_tokens / token_budget >= high_watermark`（默认 75%）或 `was_compacted` 为 True 时，返回包含 `estimated_tokens`、`token_budget`、`ratio`、`percent`、`was_compacted`、`high_pressure` 的诊断对象。
   - 低于高压水位线且未发生压缩时，返回 `None`。
2. 在 `budget_notice` 中集成上下文感知与瞬态引导：
   - 若上下文处于高压但尚未压缩：提示当前占用估算 Token 与百分比，引导模型避免全量输出或大文件读取，优先采用精简、针对性的修改和测试验证。
   - 若上下文已发生压缩：明确告知模型历史交互已摘要压缩以保持在预算内，提醒模型聚焦于核心需求和通过验证。
   - 若处于最终轮且测试已通过（`final_only`）：严格屏蔽与工具相关的压缩建议，保留原有最终回答要求。
3. 在 `CodingAgent` 的 `turn_budget` 轨迹事件中记录 `context_tokens`、`context_budget` 与 `context_compacted` 字段。
4. 严格保持工程不变量：
   - 瞬态系统消息：提示仅在每轮模型请求前瞬态拼装（`request_messages`），绝不持久化写入 `SessionStore` 或污染保存的消息历史。
   - 只读与非侵入：不篡改历史交互消息，不强行截断工具返回值，保持已有上下文压缩算法不变。

本次不做：
- 更改现有 `ContextManager.compact` 核心算法。
- 引入付费模型或在线分词器。

预计触及：
- `src/selfforge_agent/runtime/control.py`
- `src/selfforge_agent/runtime/agent.py`
- `tests/test_runtime_token_guidance.py`
- `docs/tasks/SF-RUN-06.md`
- `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | Token 占用正常（<75%）且未发生压缩 | `evaluate_context_pressure` 返回 None，`budget_notice` 无上下文压力警告 | 通过 |
| A2 | Token 占用达到或超过高压水位（>=75%） | `evaluate_context_pressure` 返回高压诊断，`budget_notice` 包含针对性高压提示及百分比 | 通过 |
| A3 | 会话已发生过压缩（`was_compacted=True`） | `budget_notice` 包含上下文已压缩告知及当前占用提示 | 通过 |
| A4 | 最终仅回答轮（`final_only=True`） | 无论 Token 压力如何，均省略工具/压缩警告，保持最终陈述引导 | 通过 |
| A5 | `CodingAgent` 实际端到端流程验证 | `turn_budget` trace 准确记录 `context_tokens`、`context_budget`、`context_compacted`；会话持久化文件中无瞬态提示污染 | 通过 |

## 实施顺序

1. 编写全面的 TDD 测试用例 `tests/test_runtime_token_guidance.py`，建立失败基线（ImportError）。
2. 在 `control.py` 中实现 `evaluate_context_pressure`，并扩展 `budget_notice` 支持 `context_info`。
3. 在 `agent.py` 中接入上下文估算、压缩状态检测与 `turn_budget` 轨迹记录。
4. 运行完整单元测试套件与 Docker 集成测试。
5. 更新任务卡与 backlog，提交并推送 PR。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | 全部通过（`All checks passed!`） |
| `git diff --check` | Host | 本分支 | 全部通过（无空白/换行缺陷） |
| `uv run --no-sync pytest -q tests/test_runtime_token_guidance.py` | Host | 本分支 | 12 passed in 1.48s |
| `uv run --no-sync pytest -q tests/test_runtime_budget.py tests/test_runtime_tool_loop.py` | Host | 本分支 | 18 passed in 5.87s |
| `uv run --no-sync pytest -q -m "not integration"` | Host | 本分支 | 483 passed in 326.47s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed in 42.73s |

## Review

- 严格遵循 TDD：未实现前捕获 ImportError 失败基线；实现后 12 项专属用例全部通过。
- 瞬态与安全不变量：高压及压缩提示仅在向模型发送请求时瞬态组装，SessionStore 持久化保存的消息绝不含瞬态提示；trace 中仅记录数值指标而不泄露文本内容。
- 最终轮保护：final_only 轮次严格抑制上下文及工具建议，保障模型聚焦最终总结。

## Handoff

- 交付状态：实施与全量验证已完成，准备提交并创建 PR。
- 当前分支：`feat/runtime-token-budget-guidance`。主分支 `main` 保持纯净。

## Integration review (2026-11-12)

Reconciled with PR #55 base `944cfca` without discarding original commits.
The original development results above are historical, not the verification
results for the current combination. Corrections, boundaries and evidence:
[PR #48–#54 review](../review-pr48-54.md). Exact tested heads and CI are recorded
in the delivery comments on the corresponding PR. No paid model run.
