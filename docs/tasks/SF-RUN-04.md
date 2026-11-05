# SF-RUN-04：只读工具死循环检测与瞬态预算引导

- 状态：In Review
- Owner：Antigravity Agent
- 基线：`602df51ccaf7e81a9eb97651a23c891516e73afc`（`origin/main`）
- 分支 / 所属 worktree：`feat/runtime-tool-loop-guidance`
- 依赖：无（基于已合并的 PR #41 / main）
- PR：https://github.com/liuzhouquan/selfforge-agent/pull/45

## 目标与范围

在模型自主 Coding 循环中，针对模型在复杂任务中容易陷入只读工具（`read_file`、`search_code`）重复调用、无谓耗尽有限步数上限（如 18 步）导致 `agent_max_steps` 失败的痛点，提供实时的只读工具循环检测与瞬态预算引导机制。

原来发生什么：
1. 模型如果多次使用完全相同的参数重复调用 `read_file` 或 `search_code`，Runtime 仅机械执行并返回相同内容，没有任何防循环感知。
2. 瞬态 `budget_notice` 仅提示剩余步数和测试通过状态，无法在模型陷入无意义重复探索时提供针对性的引导。
3. `turn_budget` 轨迹事件没有记录工具重复状态，离线分析时难以定位循环发生点。

本次完成后应观察到：
1. 实现 `detect_tool_loop(messages: list[dict], *, min_repeats: int = 2) -> dict[str, Any] | None`：
   - 追踪当前未修改阶段（自会话开始或上次写入 `edit_file` / 持久化 `bash` 以来）的只读工具调用指纹（规范化参数排序）。
   - 当最近一轮的只读工具调用在当前阶段累计达到 `min_repeats` 次时，准确返回循环诊断信息。
   - 写入操作自动重置检测计数，对修改后重新检查文件的合法行为不误报。
2. 在 `budget_notice` 中集成循环提示：
   - 当检测到只读工具重复时，在瞬态系统通知中追加引导：`Repeated read-only tool call detected: you have called '{tool}' with identical arguments {count} times without intervening edits. Avoid duplicate lookups and proceed with required edits (edit_file) or verification (run_tests).`
3. 在 `CodingAgent` 的 `turn_budget` 轨迹事件中记录 `tool_loop` 字段，提供端到端的可观察性。
4. 严格保持工程不变量：不强行中断模型、不篡改工具输出、不改变已有硬性步数与时间截断、不向持久化会话追加系统通知。

本次不做：
- 强制拦截或伪造工具返回值。
- 自动修改代码替代模型决策。

预计触及：
- `src/selfforge_agent/runtime/control.py`
- `src/selfforge_agent/runtime/agent.py`
- `docs/runtime-budget.md`
- `docs/tasks/SF-RUN-04.md`
- `docs/backlog.md`
- `tests/test_runtime_tool_loop.py`
- `tests/test_runtime_budget.py`

复用：
- 现有 `budget_notice`、`turn_budget` 轨迹事件、`CodingAgent`、`ContextManager`。

约束：
- 遵循 `docs/engineering-invariants.md` 关于 Runtime 预算、控制通知必须为瞬态系统消息的约束。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 无重复或不同参数调用只读工具时 | `test_detect_tool_loop_empty_or_no_tools` 与 `test_detect_tool_loop_single_or_different_calls` | 已通过 |
| A2 | 连续两次使用相同参数调用 `read_file` 或 `search_code` | `test_detect_tool_loop_identical_consecutive_calls` 与 `test_budget_notice_renders_tool_loop_guidance` | 已通过 |
| A3 | 参数键顺序不同但在语义上相等的调用 | `test_detect_tool_loop_normalizes_argument_ordering` | 已通过 |
| A4 | 发生 `edit_file` 写入后重新读取同一文件 | `test_detect_tool_loop_reset_after_edit` | 已通过 |
| A5 | `CodingAgent` 实际循环运行并在下一步预算通知与 trace 中注入警告 | `test_coding_agent_warns_on_repeated_tool_loop_and_traces` | 已通过 |

## 实施顺序

1. 编写全面的测试用例 `tests/test_runtime_tool_loop.py`，证明未实现时调用失败（TDD）。
2. 在 `src/selfforge_agent/runtime/control.py` 中实现 `detect_tool_loop` 并扩展 `budget_notice`。
3. 在 `src/selfforge_agent/runtime/agent.py` 中连接循环检测与 `turn_budget` trace 记录。
4. 更新 `docs/runtime-budget.md` 说明。
5. 运行完整单元测试与 Docker 集成测试。
6. 更新任务卡与 backlog，提交并推送创建 PR。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | 全部通过（0 warning） |
| `git diff --check` | Host | 本分支 | 全部通过（无空白/换行缺陷） |
| `uv run --no-sync pytest -q tests/test_runtime_tool_loop.py tests/test_runtime_budget.py` | Host | 本分支 | 17 passed in 5.72s |
| `uv run --no-sync pytest -q` | Host | 本分支 | 418 passed, 11 skipped in 283.16s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 待运行 |

## Review

- 待实施完成后核对。

## Handoff

- 交付状态：开始实施 TDD 测试与核心逻辑。
