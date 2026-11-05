# SF-WS-03：工作区未提交改动与脏状态防覆盖防护

- 状态：In Progress
- Owner：Antigravity Agent
- 基线：`4ecd6a5b6727fb2e2938cf15c929a7001ae4f849`（`origin/main`）
- 分支 / 所属 worktree：`feat/workspace-dirty-state-guard`
- 依赖：无（基于已合并的 PR #47 / main）
- PR：待创建

## 目标与范围

在通用 Coding 运行时中，工作区策略支持两种模式：
1. 默认的 `worktree` 模式：创建独立的 Git worktree，与原仓库的未提交修改天然隔离。
2. 直接工作区（`direct`）模式：直接在原仓库工作目录下由代理进行读写与测试验证。

原来发生什么：
1. 当用户通过 CLI（`selfforge-agent code --workspace direct`）或 Web API 执行直接工作区模式时，系统未检查原仓库是否处于 dirty 状态（含未提交修改、暂存改动或未跟踪文件）。
2. 若开发者在原仓库尚有未保存或未提交的工作进度，直接模式代理的编辑或测试清理行为极易意外覆盖或污染现有代码，造成不可逆的成果丢失。
3. `CodingWorkspaceManager._dirty` 仅作为内部静态方法用于 worktree 清理时的防护，未向外部暴露针对任意仓库路径的细粒度脏状态与未提交文件列表诊断能力。

本次完成后应观察到：
1. `CodingWorkspaceManager` 提供权威、通用的工作区脏状态检查：
   - `check_dirty(path: Path, limit: int = 10) -> tuple[bool, list[str]]`
   - `is_dirty(path: Path) -> bool`
   - `dirty_files(path: Path, limit: int = 10) -> list[str]`
   - 检查 uncommitted changes、staged modifications、untracked files 以及非缓存 ignored 文件。
   - 安全豁免派生构建与测试缓存目录：`__pycache__`、`.pytest_cache`、`.ruff_cache`。
   - 现有的 `_dirty(workspace)` 重构为复用 `is_dirty(workspace.path)`。
2. CLI 直接工作区防覆盖保护：
   - `code` 子命令新增 `--allow-dirty` 选项（默认 False）。
   - 在直接模式下启动新任务（`not args.resume`）时，若检测到原仓库为 dirty 且未传递 `--allow-dirty`，抛出 `WorkspaceError`，列出代表性未提交文件列表，指导用户先 commit/stash 或显式传 `--allow-dirty`。
   - 恢复会话（`args.resume`）或 worktree 模式不受影响。
3. Web API 与前端防覆盖保护：
   - `CodingTaskRequest` 新增 `allow_dirty: bool = False` 字段。
   - 直接模式且非恢复会话时，若仓库 dirty 且未设置 `allow_dirty=True`，返回 HTTP 409 Conflict，详细列出未提交文件样本。
   - 前端 UI 提供直接模式下的 allow-dirty 选项控制。
4. 严格保留工程不变量：
   - 绝不自动执行 stash 或删除用户的未提交文件。
   - worktree 模式保持默认安全隔离。
   - 错误信息与诊断中只展示相对路径样本，不泄漏敏感文件内容。

本次不做：
- 自动执行 `git stash` 或 `git reset`。
- 修改 worktree 内部已有隔离逻辑。

预计触及：
- `src/selfforge_agent/runtime/workspace.py`
- `src/selfforge_agent/cli.py`
- `src/selfforge_agent/api/app.py`
- `src/selfforge_agent/api/static/index.html`
- `src/selfforge_agent/api/static/app.js`
- `tests/test_workspace_dirty_guard.py`
- `docs/tasks/SF-WS-03.md`
- `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 干净仓库使用 `check_dirty` / `is_dirty` | 返回 `(False, [])`，`dirty_files` 返回空列表 | 通过 (`test_is_dirty_clean_repo`) |
| A2 | 含有修改文件（tracked modified）或未跟踪文件（untracked）的仓库 | `is_dirty` 返回 True，`dirty_files` 包含对应相对路径 | 通过 (`test_is_dirty_tracked_modified_file`, `test_is_dirty_untracked_file`, `test_is_dirty_staged_file`) |
| A3 | 仅含有派生缓存（如 `__pycache__`、`.pytest_cache`）的仓库 | 被安全豁免，`is_dirty` 返回 False | 通过 (`test_is_dirty_exempt_caches`, `test_is_dirty_non_cache_ignored_is_dirty`) |
| A4 | CLI 直接模式遇到 dirty 仓库且未传 `--allow-dirty` | 退出码非 0，报错输出包含未提交文件名列表并提示 `--allow-dirty` | 通过 (`test_cli_direct_mode_rejects_dirty_repo_without_allow_dirty`) |
| A5 | CLI 直接模式传 `--allow-dirty` 或恢复已存在会话（`--resume`） | 成功启动，不因 dirty 状态报错阻断 | 通过 (`test_cli_direct_mode_allows_dirty_when_flag_passed`) |
| A6 | CLI worktree 模式遇到 dirty 原仓库 | 正常创建并启动工作区，worktree 保持与原仓库未提交改动隔离 | 通过 (`test_cli_worktree_mode_succeeds_even_when_source_is_dirty`) |
| A7 | Web API 直接模式遇到 dirty 仓库 | 未传 `allow_dirty` 时返回 HTTP 409，传递 `allow_dirty: true` 时成功启动 | 通过 (`test_web_direct_mode_rejects_dirty_repo_without_allow_dirty`, `test_web_direct_mode_allows_dirty_when_allow_dirty_passed`) |

## 实施顺序

1. 编写 TDD 失败测试基线 `tests/test_workspace_dirty_guard.py`，覆盖 A1-A7。
2. 在 `src/selfforge_agent/runtime/workspace.py` 中实现 `check_dirty`、`is_dirty`、`dirty_files` 并重构 `_dirty`。
3. 在 `src/selfforge_agent/cli.py` 中增加 `--allow-dirty` 并在直接模式下引入前置保护。
4. 在 `src/selfforge_agent/api/app.py`、`static/index.html`、`static/app.js` 中增加 `allow_dirty` 支持与校验。
5. 全量运行单元测试、Docker 集成测试及静态检查。
6. 更新任务卡与 backlog，提交并推送创建 PR。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | All checks passed! (0 errors) |
| `git diff --check` | Host | 本分支 | 0 issues |
| `uv run --no-sync pytest -q tests/test_workspace_dirty_guard.py` | Host | 本分支 | 11 passed in 0.94s |
| `uv run --no-sync pytest -q` | Host | 本分支 | 482 passed, 11 skipped, 1 warning in 287.85s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed, 482 deselected, 1 warning in 41.29s |

## Review

1. 统一检测与缓存豁免：`CodingWorkspaceManager.check_dirty` 提供了对工作区或源码仓库的权威检测，精准检测 modified/staged/untracked/ignored 文件，并严格保留对良性构建与测试派生缓存（`__pycache__`、`.pytest_cache`、`.ruff_cache`）的豁免规则，既有 `_dirty` 重构复用消除重复。
2. 直接模式安全门禁：CLI 与 Web API 在直接模式启动新会话时增加强门禁。未经显式 `--allow-dirty` / `allow_dirty=true` 授权，绝不允许直接在脏工作区执行修改，杜绝开发者成果被意外覆盖。
3. 隔离模式不受干扰：`worktree` 模式作为默认策略，依然保持其分支与工作区沙盒隔离特性，原仓库脏改动不干扰新 worktree 的构建与执行。
4. 诊断信息安全：报错与响应中仅返回受污染文件的相对路径采样，绝不泄漏代码内容或敏感凭据。

## Handoff

- 交付状态：实施与全量验证已完成，准备提交并推送 PR。

