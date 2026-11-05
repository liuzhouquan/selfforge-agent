# SF-WS-02：工作区孤立 Worktree 状态审计与安全批量清理

- 状态：In Review
- Owner：Antigravity Agent
- 基线：`602df51ccaf7e81a9eb97651a23c891516e73afc`（`origin/main`）
- 分支 / 所属 worktree：`feat/workspace-prune-stale`
- 依赖：无（基于已合并的 PR #41 / main）
- PR：[#47](https://github.com/liuzhouquan/selfforge-agent/pull/47)

## 目标与范围

在已有 `CodingWorkspaceManager` 工作区隔离管理（创建、列表、单个删除、提交、差异对比与撤销）的基础上，提供批量审计与安全清理机制（`manager.audit()`、`manager.prune()`、CLI `workspaces audit` / `workspaces prune` 以及 API 接口），帮助开发者和自动化管线在多轮任务后安全地发现并清理陈旧、已合并的 worktree，同时严格保护活动中或有未提交修改的工作区。

原来发生什么：
1. `workspaces list` 仅列出 session ID 和路径，无法查看工作区是否 dirty（未提交改动）、是否正在被活动进程加锁占用、以及其分支是否已合并到主干。
2. 缺少批量的清理入口（`prune`）：操作者若想清理遗留工作区，只能逐一手工执行 `workspaces remove <session_id>`。
3. 遇到有修改的工作区或活动锁定时缺乏批量容错和细粒度状态反馈。

本次完成后应观察到：
1. `CodingWorkspaceManager.audit(repository: Path, *, head_ref: str = "HEAD") -> list[dict]`：
   - 细粒度审计每个关联 worktree 的状态：`is_locked`（文件锁占用）、`is_dirty`（未提交改动）、`is_merged`（分支已合入 HEAD）、`prunable`（clean + unlocked + merged）。
2. `CodingWorkspaceManager.prune(repository: Path, *, dry_run: bool = False, merged_only: bool = True, head_ref: str = "HEAD") -> dict`：
   - 默认仅安全清理已合并且无锁、无未提交修改的工作区。
   - 严格保护原则：跳过锁定的工作区（`skipped_locked`）与未提交修改的工作区（`skipped_dirty`），绝不强行清理 dirty 数据。
   - 支持 `--dry-run` 预览，支持 `--include-unmerged` 选项。
3. CLI 支持 `selfforge-agent workspaces audit [--json]` 与 `selfforge-agent workspaces prune [--dry-run] [--include-unmerged] [--json]`。
4. API 扩展支持工作区审计与 prune 路由。
5. 严格保留工程不变量：不执行 force-remove，清理后保留 Git 分支历史，严格使用 advisory workspace lock 防止并发冲突。

本次不做：
- 强制删除 dirty 工作区（`--force` 严格禁止）。
- 物理删除 Git 分支（保留分支元数据）。

预计触及：
- `src/selfforge_agent/runtime/workspace.py`
- `src/selfforge_agent/cli.py`
- `src/selfforge_agent/api/app.py`
- `tests/test_workspace_prune.py`
- `docs/tasks/SF-WS-02.md`
- `docs/backlog.md`

复用：
- 现有 `CodingWorkspaceManager`、`try_workspace_lock`、`unlock_workspace`、`workspace_guard`。

约束：
- 遵循 `docs/engineering-invariants.md` 关于工作区隔离、不可破坏用户持久化改动及并发文件锁保护原则。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 已合并且干净的工作区调用 `audit` 与 `prune` | `audit` 标为 prunable，`prune` 成功移除 worktree 与元数据并保留分支 | 通过 |
| A2 | 含有未提交修改的 dirty 工作区 | `prune` 绝对不删除，记录在 `skipped_dirty` 中 | 通过 |
| A3 | 被活动进程持锁占用的工作区 | `prune` 识别锁冲突，记录在 `skipped_locked` 中 | 通过 |
| A4 | 未合并且未传 `include_unmerged` 的干净工作区 | `prune` 默认跳过，记录在 `skipped_unmerged` 中 | 通过 |
| A5 | 启用 `--dry-run` 时 | 仅报告可清理列表，实际工作区与元数据原封未动 | 通过 |
| A6 | CLI `workspaces audit` 与 `workspaces prune` 命令行测试 | 命令行标准输出与 JSON 模式断言 | 通过 |
| A7 | Web API 审计与 prune 端点测试 | TestClient 接口调用测试 | 通过 |

## 实施顺序

1. 编写全面的测试用例 `tests/test_workspace_prune.py`，证明未实现时调用失败（TDD）。
2. 在 `src/selfforge_agent/runtime/workspace.py` 中实现 `audit` 和 `prune` 核心方法。
3. 在 `src/selfforge_agent/cli.py` 中增加 `audit` 和 `prune` 子命令处理。
4. 在 `src/selfforge_agent/api/app.py` 中增加 API 端点。
5. 运行完整单元测试与 Docker 集成测试。
6. 更新任务卡与 backlog，提交并推送创建 PR。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | 通过（All checks passed!） |
| `git diff --check` | Host | 本分支 | 通过（无空白问题） |
| `uv run --no-sync pytest -q tests/test_workspace_prune.py` | Host | 本分支 | 6 passed in 2.16s |
| `uv run --no-sync pytest -q` | Host | 本分支 | 417 passed, 11 skipped in 377.23s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed in 50.92s |

## Review

- 严格遵循 TDD：首先编写并捕获失败基线，再实现核心逻辑并补齐边界覆盖。
- 严格遵循工程不变量：
  - 绝不强制清理 dirty 工作区，始终保留 Git 分支历史。
  - 使用 advisory workspace 文件锁保护并发安全。
  - 默认只清理合入 HEAD 的干净工作区，支持 `--dry-run` 与 `--include-unmerged`。

## Handoff

- 交付状态：实施与全量验证已完成，PR [#47](https://github.com/liuzhouquan/selfforge-agent/pull/47) 已创建等待审查合并。
- 当前分支：`feat/workspace-prune-stale`。主分支 `main` 保持纯净。

