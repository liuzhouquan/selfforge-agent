# SF-RUN-05：代码修改目标定位失败与连续重试回退指导

- 状态：In Progress
- Owner：Antigravity Agent
- 基线：`4ecd6a5b6727fb2e2938cf15c929a7001ae4f849`（`origin/main`）
- 分支 / 所属 worktree：`feat/runtime-edit-failure-guidance`
- 依赖：无（基于已合并的 PR #47 / main）
- PR：待创建

## 目标与范围

在通用 Coding 运行时中，模型调用 `edit_file` 时常因目标字符串（`old_text`）包含缩进微调、隐藏空白或上下文行不完全一致而报错（`Tool error: old_text must occur exactly once`）。此时缺乏自适应反馈的模型往往在下一轮盲目微调字符串继续调用 `edit_file`，导致连续失败陷入编辑死循环，浪费步数与预算。

原来发生什么：
1. `runtime/control.py` 的 `detect_tool_loop` 仅检测只读工具（`read_file`、`search_code`）的完全相同参数重复调用，未检测 `edit_file` 的连续报错。
2. 当模型连续多次（>= 2 次）对同一文件编辑失败时，下一轮生成的预算通知（`budget_notice`）未给出针对性的回退检查指导，导致模型在同一文件盲目试错。

本次完成后应观察到：
1. `runtime/control.py` 新增 `detect_edit_failure_loop(messages: list[dict], min_failures: int = 2) -> dict[str, Any] | None`：
   - 分析会话消息历史中的工具调用与工具返回。
   - 跟踪每个路径的连续编辑失败次数（`name == "edit_file"` 且返回内容含 `Tool error:`）。
   - 复位规则：当发生成功的 `edit_file`、对该文件执行了 `read_file` 或出现新的 `user` 消息时，该文件的失败计数重置为 0。
   - 当最近一轮包含对某文件连续失败达到 `min_failures` 次时，返回包含 `path` 和 `count` 的字典。
2. `budget_notice` 扩展支持 `edit_failure: dict | None = None` 参数：
   - 注入瞬态提示：`Consecutive edit failures on '<path>' (<count> times): old_text did not match. Use read_file to inspect the exact lines and indentation of '<path>' before attempting further edits.\n`
3. 严格保留工程不变量：
   - 瞬态性：仅生成在请求时的瞬态系统通知中，绝对不写入持久化会话记录（`messages`）。
   - 建议性：不限制工具权限，不放宽测试通过门禁，不增加额外模型调用。
   - 末轮（`final_only`）静默：在测试已通过的最后一轮，只提示提交最终答复，不注入编辑失败提示。
   - 追踪元数据：在 `turn_budget` 追踪事件中安全记录 `edit_failure` 关联路径（不泄漏代码内容）。

本次不做：
- 自动修改模型的 `old_text` 参数。
- 放宽 `edit_file` 严格精确替换的不变量。

预计触及：
- `src/selfforge_agent/runtime/control.py`
- `src/selfforge_agent/runtime/agent.py`
- `tests/test_runtime_edit_guidance.py`
- `docs/tasks/SF-RUN-05.md`
- `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 连续 2 次对同一文件 `edit_file` 报错失败 | `detect_edit_failure_loop` 检出路径与次数，`budget_notice` 包含针对该文件的 `read_file` 检查建议 | 通过 (`test_detect_edit_failure_loop_triggers_after_consecutive_failures`, `test_budget_notice_renders_edit_failure_guidance`) |
| A2 | 失败后执行了对该文件的 `read_file` | 失败计数重置，下一轮 `detect_edit_failure_loop` 返回 None，通知中不再提示 | 通过 (`test_detect_edit_failure_loop_resets_on_read_file`) |
| A3 | 失败后成功执行了一次 `edit_file` | 失败计数重置，下一轮不再提示 | 通过 (`test_detect_edit_failure_loop_resets_on_successful_edit`) |
| A4 | 出现新的 `user` 任务消息 | 历史失败计数完全清空 | 通过 (`test_detect_edit_failure_loop_resets_on_new_user_message`) |
| A5 | 最终轮且测试已通过（`final_only`） | 编辑失败提示被抑制，仅输出最终答复提示 | 通过 (`test_budget_notice_suppressed_on_final_only`, `test_coding_agent_warns_on_consecutive_edit_failures_and_traces`) |
| A6 | 瞬态与会话记录隔离 | 会话 JSONL 和 messages 中无生成的 advisory notice，仅存在于发送给模型的临时消息列表中 | 通过 (`test_coding_agent_warns_on_consecutive_edit_failures_and_traces`) |

## 实施顺序

1. 编写全面的 TDD 测试套件 `tests/test_runtime_edit_guidance.py`，证明未实现时调用失败（TDD）。
2. 在 `src/selfforge_agent/runtime/control.py` 中实现 `detect_edit_failure_loop` 及扩展 `budget_notice`。
3. 在 `src/selfforge_agent/runtime/agent.py` 中接入检测逻辑与追踪事件。
4. 运行完整单元测试套件与 Docker 集成测试。
5. 更新任务卡与 backlog，提交并提 PR。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | All checks passed! |
| `git diff --check` | Host | 本分支 | 0 issues |
| `uv run --no-sync pytest -q tests/test_runtime_edit_guidance.py` | Host | 本分支 | 12 passed in 1.28s |
| `uv run --no-sync pytest -q` | Host | 本分支 | 483 passed, 11 skipped, 1 warning in 278.33s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed, 483 deselected, 1 warning in 45.51s |

## Review

1. 核心检测与复位：`detect_edit_failure_loop` 准确跟踪同一文件路径（自动规约相对路径）的连续报错次数，对读取检查（`read_file`）、成功修改（`edit_file`）以及新任务（`user` 消息）进行严格复位。
2. 瞬态性与隔离性：修改反馈仅在 `budget_notice` 中瞬态生成，绝不写入持久化 `SessionStore` 会话消息，会话持久化与模型推理历史不污染。
3. 追踪事件无泄露：在 `turn_budget` 追踪事件中仅记录文件相对路径（如 `edit_failure="app.py"`），不记录文件内容或敏感信息。
4. 不变量与门禁保留：不自动修改模型入参，不放宽 `old_text` 精确匹配或测试通过门禁，`final_only` 轮次下保持静默抑制。

## Handoff

- 交付状态：实施与全量验证已完成，准备提交并推送 PR。

