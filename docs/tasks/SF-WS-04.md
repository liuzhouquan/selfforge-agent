# SF-WS-04：工作区磁盘配额与存储审计

- 状态：In Review
- Owner：Antigravity Agent
- 基线：`4ecd6a5b6727fb2e2938cf15c929a7001ae4f849`（`origin/main`）
- 分支 / 所属 worktree：`feat/workspace-storage-quota-audit`
- 依赖：无（基于已合并的 PR #47 / main）
- PR：[#54](https://github.com/liuzhouquan/selfforge-agent/pull/54)

## 目标与范围

在已有 `CodingWorkspaceManager` 工作区隔离管理（创建、列表、单个删除、提交、差异对比、撤销、已合并安全清理 prune）的基础上，提供只读工作区磁盘存储空间审计与配额告警机制（`manager.audit_storage()`、CLI `workspaces storage` 以及 API 接口），帮助开发者和自动化系统安全测量多会话 worktree 的磁盘占用、识别临时构建缓存累积，并在超过指定字节配额时给出清晰告警与可回收存储清理建议。

原来发生什么：
1. `workspaces audit` 仅提供状态元数据（锁、dirty、merged、prunable），无法获知每个 worktree 的物理磁盘占用（字节数和文件数）。
2. 在持续测试或长会话开发中，Python 字节码缓存（`__pycache__`）、测试缓存（`.pytest_cache`）、构建目录（`build`、`dist`）以及临时文件会持续累积，缺乏细粒度的缓存占用（`cache_bytes`）与总占用统计。
3. 缺乏全局配额阈值检测机制（`quota_bytes`），当工作区占用超出宿主机预期限制时无法及时在 CLI 和 API 层面阻断或报警。

本次完成后应观察到：
1. 实现安全有界的目录体积统计辅助函数 `measure_directory(path: Path, *, max_files: int = 50_000)`：
   - 严格禁止遍历软链接指向的外部目录（`followlinks=False`），防止链接死循环或意外越界统计宿主机其他目录。
   - 通过 `os.lstat` 获取文件自身大小，捕获并在异常时安全容错（如并发删除文件）。
   - 细粒度识别临时测试与构建缓存目录（`__pycache__`、`.pytest_cache`、`.ruff_cache`、`.mypy_cache`、`build`、`dist` 等）并统计其占用。
   - 文件数量上限有界截断（`max_files`），防止针对超深或恶意文件树的无界遍历。
2. 实现 `CodingWorkspaceManager.audit_storage(repository: Path, *, quota_bytes: int | None = None, head_ref: str = "HEAD") -> dict[str, Any]`：
   - 针对指定仓库下的每个关联 worktree，输出：`session_id`、`path`、`branch`、`base_sha`、`total_bytes`、`cache_bytes`、`file_count`、`is_locked`、`is_dirty`、`is_merged`、`prunable`、`reclaimable_bytes`（已合并且干净则整目录可回收，否则为临时缓存占用）。
   - 汇总统计指标：`workspace_count`、`total_bytes`、`total_cache_bytes`、`reclaimable_bytes`、`quota_bytes`、`quota_exceeded` 以及行动建议 `recommendations`（包括超出配额预警、可清理 worktree 建议、缓存累积提示等）。
3. CLI 扩展支持 `selfforge-agent workspaces storage [--repo <path>] [--quota-bytes <bytes>] [--json] [--output <path>]`：
   - 支持友好的人类可读表格与摘要输出，以及机器可读的 JSON 格式输出。
   - 当指定 `--quota-bytes` 且实际占用超额（`quota_exceeded=True`）时，返回退出码 1；未超额或未指定配额时返回 0。
4. API 扩展支持 `GET /api/workspaces/storage` 与 `GET /api/code/workspaces/storage`，返回结构化存储审计报告。
5. 严格保留工程不变量：
   - 只读审计：不执行物理删除或强行修改用户文件，纯粹输出诊断数据与可回收建议。
   - 存储隔离与并发安全：不跟未授权外部工作区交互。

本次不做：
- 自动强行清空用户的未提交修改。
- 全局物理磁盘监控代理。

预计触及：
- `src/selfforge_agent/runtime/workspace.py`
- `src/selfforge_agent/cli.py`
- `src/selfforge_agent/api/app.py`
- `tests/test_workspace_storage.py`
- `docs/tasks/SF-WS-04.md`
- `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 空仓库或无 worktree 审计 | `audit_storage` 返回 `workspace_count=0`、`total_bytes=0`、`quota_exceeded=False` | 通过 |
| A2 | 存在正常与包含 `__pycache__` 缓存的 worktree | 准确统计总大小 `total_bytes`、缓存大小 `cache_bytes` 与文件计数 | 通过 |
| A3 | 软链接及死循环软链接安全防护 | `measure_directory` 不跟踪目录软链接，不抛出 RecursionError，安全统计 | 通过 |
| A4 | 配额未超额与超额阈值判断 | 指定 `quota_bytes` 时，低于阈值 `quota_exceeded=False`，高于阈值 `quota_exceeded=True` 并生成超额建议 | 通过 |
| A5 | CLI `workspaces storage` 输出与退出码 | `--json` 格式完整；超额时返回非零退出码（exit code 1），未超额返回 0 | 通过 |
| A6 | API `GET /api/workspaces/storage` 路由 | 请求返回与 Pydantic schema 对齐的 JSON 响应 | 通过 |

## 实施顺序

1. 编写全面的 TDD 测试用例 `tests/test_workspace_storage.py`，建立失败基线（ImportError）。
2. 在 `workspace.py` 中实现 `measure_directory` 与 `CodingWorkspaceManager.audit_storage`。
3. 在 `cli.py` 中添加 `workspaces storage` 动作与 `--quota-bytes` / `--output` 参数处理。
4. 在 `app.py` 中注册 `/api/workspaces/storage` 与 `/api/code/workspaces/storage` 路由与响应模型。
5. 运行完整单元测试与 Docker 集成测试套件。
6. 更新任务卡与 backlog，提交并推送创建 PR。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | 全部通过（`All checks passed!`） |
| `git diff --check` | Host | 本分支 | 全部通过（无空白/换行缺陷） |
| `uv run --no-sync pytest -q tests/test_workspace_storage.py` | Host | 本分支 | 11 passed in 2.61s |
| `uv run --no-sync pytest -q -m "not integration"` | Host | 本分支 | 482 passed in 284.31s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed in 42.86s |

## Review

- 严格遵循 TDD：基线未实现前捕获 ImportError 失败基线；实现后 11 项专属用例全部通过。
- 安全边界不变量：`measure_directory` 严格采用 `followlinks=False` 与 `os.lstat`，避免穿越宿主机目录或链接死循环，且设定文件上限有界防范。
- 纯只读性：审计仅收集元数据与磁盘指标，绝不擅自清空或修改任何用户文件或分支。

## Handoff

- 交付状态：实施与全量验证已完成，准备提交并创建 PR。
- 当前分支：`feat/workspace-storage-quota-audit`。主分支 `main` 保持纯净。

## Integration review (2026-11-12)

Reconciled with PR #55 base `944cfca` without discarding original commits.
The original development results above are historical, not the verification
results for the current combination. Corrections, boundaries and evidence:
[PR #48–#54 review](../review-pr48-54.md). Exact tested heads and CI are recorded
in the delivery comments on the corresponding PR. No paid model run.
