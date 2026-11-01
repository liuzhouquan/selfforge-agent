# SF-RUN-01：调查并统一 CLI 与 Web 对同一工作区的并发协调

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/workspace-concurrency-coordination
- 依赖：无
- PR：尚未创建

## 目标与范围

调查并解决 CLI 与 Web 在并发操作同一工作区时的协调冲突。

- 原来状态：Web 端在 `api/coding_jobs.py` 中实现了基于文件锁（`flock`）的 `workspace_guard`，但该锁属于 Web 专有；CLI 子命令（`code` 与 `workspaces remove/revert/commit`）未参与该锁，导致 CLI 与 Web（或两个 CLI 进程）并发访问同一 worktree 时无法感知彼此，可能引发写冲突、并发修改或运行中工作区被外部清理。
- 本次完成后：
  1. 将跨进程文件锁抽象下沉至 `runtime/workspace.py`，形成统一的 `workspace_guard`、`try_workspace_lock` 和 `unlock_workspace`。
  2. `CodingJobManager` 复用统一锁机制，保持 Web 端已有保护与测试兼容。
  3. CLI 的 `code` 任务执行过程持有工作区排他锁；CLI 的 `workspaces remove/revert/commit` 在操作前获取锁，若工作区正被 Web 或其他 CLI 任务占用，安全拒绝并提示清晰错误信息。
  4. 进程异常退出时，依托操作系统内核自动释放 `flock`，无陈旧锁残留。
- 本次不做：不引入复杂分布式锁或全局守护进程调度，不破坏单个进程的本地独立执行能力。
- 预计触及：
  - `src/selfforge_agent/runtime/workspace.py`
  - `src/selfforge_agent/api/coding_jobs.py`
  - `src/selfforge_agent/cli.py`
  - `tests/test_workspace_concurrency.py`
  - `docs/tasks/SF-RUN-01.md`
  - `docs/backlog.md`
- 约束：遵循 `docs/engineering-invariants.md` 关于工作区保护与并发安全的原则。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 一个进程持有工作区锁时，第二个进程执行 CLI `code` 或 Web 任务 | 第二个进程捕获 `WorkspaceError` 并安全退出，提示工作区已被锁定 | 已验证通过 |
| A2 | 一个进程正在工作区中运行任务时，第二个进程尝试执行 `workspaces remove/revert/commit` | 第二个操作被拒绝并明确报错，工作区文件和 Git 状态不被破坏 | 已验证通过 |
| A3 | 持锁进程异常终止（模拟 SIGKILL 或进程退出） | 内核自动释放锁，后续进程可立刻正常获取锁，无需人工介入 | 已验证通过 |
| A4 | 全量既有单测与 durable coding jobs 测试 | pytest 与 ruff 全绿 | 已验证通过 |

## 实施顺序

1. 编写多进程复现与并发冲突回归测试 `tests/test_workspace_concurrency.py`。
2. 在 `runtime/workspace.py` 实现统一的跨进程 `workspace_guard` 与锁管理函数。
3. 在 `api/coding_jobs.py` 对接统一锁实现。
4. 在 `cli.py` 为 `_run_code` 及 `workspaces remove/revert/commit` 接入 `workspace_guard`。
5. 验证多进程测试、全量回归和 Ruff。
6. 更新交接记录并提交。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync pytest -q tests/test_workspace_concurrency.py` | Host | `feat/workspace-concurrency-coordination` | 4 passed in 0.27s（覆盖独立持锁、多进程 SIGKILL 自动释放、JobManager 与 WorkspaceManager 共享锁、CLI 子命令被拒绝） |
| `uv run --no-sync pytest -q tests/test_coding_jobs_durable.py tests/test_workspace.py tests/test_workspace_delivery.py` | Host | `feat/workspace-concurrency-coordination` | 32 passed, 1 warning in 4.01s |
| `uv run --no-sync ruff check .` | Host | `feat/workspace-concurrency-coordination` | All checks passed! |
| `git diff --check` | Host | `feat/workspace-concurrency-coordination` | 0 formatting errors |

## Review

- 验收编号 A1-A4 均有完整回归证据。
- 锁路径默认位于 `${XDG_DATA_HOME:-~/.local/share}/selfforge-agent/locks/`，以 workspace 绝对路径 sha256 建立锁文件，CLI 和 Web 完全共享同一组文件锁。
- 兼容 `CodingJobManager(root=...)` 自定义根目录场景，保持原有 Durable Job 测试 100% 兼容。

## Handoff

- 交付状态：本地实现完成，等待代码审查合并（In Review）。
- 当前 HEAD：独立分支 `feat/workspace-concurrency-coordination`。
- 已完成：A1-A4（跨进程文件锁统一抽离、Web/CLI 共享互斥、异常崩溃内核自动释放、CLI 子命令保护）。
- 未完成/环境限制：无。
- 最近验证：`pytest tests/test_workspace_concurrency.py`（4 passed）、全量相关套件（32 passed）、Ruff（0 告警）。
- 仍在运行：无。
- 下一步：审查并合并 `feat/workspace-concurrency-coordination` 分支；继续推进 Backlog 中后续项。
- 整合/回滚：独立分支，不影响 `main` 分支。
