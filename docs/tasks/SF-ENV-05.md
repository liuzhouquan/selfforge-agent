# SF-ENV-05：离线依赖 Wheelhouse 完整性审计与就绪度诊断

> 已核对：PR #44 已合并，merge SHA `b83dc811b9e041c6c5c952e57412784ce0a35079`。以下规格/测试是历史记录；当前开发以 [SDD](../SDD.md) 为准，不重新领取本任务。

- 状态：Merged（历史任务）
- Owner：Antigravity Agent
- 基线：`602df51ccaf7e81a9eb97651a23c891516e73afc`（`origin/main`）
- 分支 / 所属 worktree：`feat/environment-wheelhouse-audit`
- 依赖：无（基于已合并的 PR #41 / main）
- PR：https://github.com/liuzhouquan/selfforge-agent/pull/44

## 目标与范围

在已有离线依赖锁文件解析（`uv.lock` / `poetry.lock` / `Pipfile.lock` / `requirements.lock`）和离线准备计划（`plan_environment_preparation`）的基础上，提供专门的只读 wheelhouse 审计与诊断工具（`audit_wheelhouse`、CLI `sandbox audit`、API `POST /api/sandbox/audit`），在真正触发庞大的 Docker 依赖镜像构建前，秒级输出 wheelhouse 的完整性就绪诊断，清晰区分并报告：已满足的依赖、缺失的包、哈希不匹配的损坏/未知 wheel 文件、以及不合规文件（符号链接、非法文件名、超限文件）。

原来发生什么：
1. 缺少专门的审计入口：开发者或 CI 无法预先检查本地 wheelhouse 目录是否满足锁文件的全部哈希要求。
2. 现有 `read_locked_wheels` 会在发现任意未知哈希 wheel 时直接抛出异常中断（`Wheel hash is absent from lock`），无法提供全量缺失诊断报告。
3. `sandbox plan` 只能返回 wheel 数量（`wheel_count`），无法指示哪些包已就绪、哪些包缺失。
4. CLI 只有 `sandbox plan` 和 `sandbox build`，缺少轻量级的只读审计诊断命令 `sandbox audit`。

本次完成后应观察到：
1. 实现 `audit_wheelhouse(repo: Path | None = None, *, wheelhouse: Path, lock_file: Path | None = None, requirements_file: Path | None = None, timeout: float = 10.0) -> dict`。
2. 返回详细诊断字典：包含 `ready`（bool）、`package_count`、`matched_count`、`missing_count`、`mismatched_count`、`unsupported_count`、`matched_packages`、`missing_packages`、`mismatched_wheels`、`unsupported_wheels` 以及 `total_wheel_bytes`。
3. CLI 支持 `selfforge-agent sandbox audit --wheelhouse <dir> [--lock <file>] [--requirements <file>] [--repo <dir>] [--output <file>]`：
   - 审计就绪时退出码为 0，不就绪时退出码为 1。
   - 支持 `--output` 输出 JSON 报告。
4. API 新增 `POST /api/sandbox/audit` 接口，支持 Web / 外部客户端发起审计请求。
5. 严格保留安全不变量：wheelhouse 必须是目录且不能是软链接，单个 wheel 限制 64 MB，总大小限制 128 MB，文件数量上限 200，操作严格受超时时间约束，不访问外部网络。

本次不做：
- 自动联网下载缺失的 wheel 文件（依赖由操作者或前置步骤准备）。
- 自动运行 wheel 构建或执行不受信任的 setup.py / build hooks。

预计触及：
- `src/selfforge_agent/runtime/environment.py`
- `src/selfforge_agent/cli.py`
- `src/selfforge_agent/api/app.py`
- `tests/test_wheelhouse_audit.py`
- `docs/tasks/SF-ENV-05.md`
- `docs/backlog.md`

复用：
- 现有 `_read`、`_remaining`、`MAX_WHEEL_BYTES`、`MAX_CONTEXT_BYTES`、`MAX_LOCK_BYTES`、`parse_locked_requirements`、`export_hash_locked_requirements`。

约束：
- 遵循 `docs/engineering-invariants.md` 中关于依赖镜像与离线准备的严格约束；保持只读、无网络、输入受限与沙箱隔离。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 完整满足锁文件全部哈希的 wheelhouse 调用 `audit_wheelhouse` | `test_audit_wheelhouse_all_matched_ready` | 已通过 |
| A2 | 缺少部分依赖 wheel 的 wheelhouse 调用 `audit_wheelhouse` | `test_audit_wheelhouse_missing_package` | 已通过 |
| A3 | 包含未知/损坏哈希 wheel 或非法文件的 wheelhouse 调用审计 | `test_audit_wheelhouse_mismatched_and_unsupported_wheels` | 已通过 |
| A4 | 软链接 wheelhouse、不存在目录或文件超限时严格抛出 ValueError | `test_audit_wheelhouse_symlink_directory_rejected` 与 `test_audit_wheelhouse_nonexistent_directory_rejected` | 已通过 |
| A5 | CLI `selfforge-agent sandbox audit` 命令测试（成功退出 0，未满足退出 1，支持 `--output`） | `test_cli_sandbox_audit` | 已通过 |
| A6 | Web API `POST /api/sandbox/audit` 端点测试 | `test_api_sandbox_audit_endpoint` | 已通过 |

## 实施顺序

1. 编写全面的测试用例 `tests/test_wheelhouse_audit.py`，证明未实现时调用失败（TDD）。
2. 在 `src/selfforge_agent/runtime/environment.py` 中实现 `audit_wheelhouse` 核心函数。
3. 在 `src/selfforge_agent/cli.py` 中添加 `sandbox audit` 子命令与参数解析。
4. 在 `src/selfforge_agent/api/app.py` 中增加 `POST /api/sandbox/audit` 路由。
5. 运行完整单元测试与 Docker 集成测试。
6. 更新任务卡与 backlog，提交并推送创建 PR。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | 全部通过（0 warning） |
| `git diff --check` | Host | 本分支 | 全部通过（无空白/换行缺陷） |
| `uv run --no-sync pytest -q tests/test_wheelhouse_audit.py` | Host | 本分支 | 8 passed in 0.69s |
| `uv run --no-sync pytest -q` | Host | 本分支 | 419 passed, 11 skipped in 275.84s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed, 419 deselected in 48.99s |

## Review

- 验收编号 A1-A6 均在单元测试与集成测试中直接验证覆盖。
- 严格遵循只读审计原则：不下载、不修改文件、不执行任意代码或 hooks，严格受超时限制与 200 文件上限保护。
- 软链接、非法文件名、超限文件被安全拒绝或记录为 unsupported_wheels。
- CLI `sandbox audit` 与 API `POST /api/sandbox/audit` 均已打通并包含回归测试。

## 历史 Handoff（实施时记录）

- 交付状态：本地实现与全量测试完成，准备提交并创建 PR。

Review: audit, builder and plan now share one bounded wheel scanner. Enumeration stops at 200 wheels / 1000 directory entries before sorting; lock bytes count toward128MiB. Oversized inputs are rejected before reads; raced/read errors abort instead of resetting a failed-read allowance. Finite (0,300] timeouts are checked after reads/hashing and before returning. Diagnostics indicate hash availability, not archive/platform/installability proof.
