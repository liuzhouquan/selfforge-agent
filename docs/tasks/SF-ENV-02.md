# SF-ENV-02：支持 Poetry 锁文件哈希识别与环境离线计划导出

- 状态：In Review
- Owner：Antigravity Agent
- 基线：`7eabefc67439432001921e5dbe81aad50b2bfc18`
- 分支 / 所属 worktree：`feat/environment-poetry-lock-support`
- 依赖：无
- PR：https://github.com/liuzhouquan/selfforge-agent/pull/40

## 目标与范围

支持在包含 `poetry.lock` 的仓库中检测哈希锁定状态，并允许通过 CLI/API 的 `sandbox export-lock` 与 `sandbox plan` 导出符合 pip `--require-hashes` 规范的固定依赖清单与离线镜像准备计划。

原来发生什么：
1. `detect_repository_environment` 仅检查 `--hash=sha256:` 字符串（pip requirements 格式），导致 `uv.lock` 和 `poetry.lock` 仓库被误判为 `has_hash_lock=False` 并输出警告。
2. `export_hash_locked_requirements` 仅支持 `uv.lock` 的 `wheels` 和 `sdist` 格式，对 `poetry.lock`（`files = [ { file = "...", hash = "sha256:..." } ]` 及 Poetry 1.0 的 `[metadata.files]`）报错 `ValueError: Lock file includes an external package without supported SHA-256 hashes`。
3. `plan_environment_preparation` 未包含 `poetry.lock` 标准定位，仓库仅含 `poetry.lock` 时直接报错 `ValueError: No supported lockfile (uv.lock, requirements.lock) found in repository`。

本次完成后应观察到：
1. 包含 `poetry.lock` 或 `uv.lock` 的仓库在 `detect_repository_environment` 中正确标记 `has_hash_lock=True`，且不输出缺哈希警告。
2. `export_hash_locked_requirements` 能够解析 Poetry 1.2+、2.0+ 及 1.0 格式中的 SHA-256 哈希，正确跳过 git/editable/virtual 本地源，导出格式为 `name==version --hash=sha256:...`。
3. `plan_environment_preparation` 支持自动回退至 `poetry.lock` 或显式指定 `--lock <poetry.lock>`，生成正确的 `export_command`。
4. CLI `selfforge-agent sandbox export-lock --lock poetry.lock` 能够成功执行并输出依赖。

本次不做：
- 自动联网下载 wheel 或执行依赖解析安装。
- 非 SHA-256 算法支持。

预计触及：
- `src/selfforge_agent/runtime/environment.py`
- `src/selfforge_agent/cli.py`
- `tests/test_delivery_revert_and_lock_export.py`
- `tests/test_environment_detection.py`
- `tests/test_environment_plan.py`

复用：
- 现有 `parse_locked_requirements`、`export_hash_locked_requirements`、`tomllib`。

约束：
- 离线镜像构建契约与环境不可变原则；不执行不受信任代码或网络安装。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 包含标准 `poetry.lock` 的仓库调用 `detect_repository_environment` | `test_detect_poetry_lock_with_hashes_sets_has_hash_lock` | 已通过 |
| A2 | 包含 `poetry.lock`（多文件/单文件/1.0 metadata/git源）的文件调用 `export_hash_locked_requirements` | `test_export_hash_locked_requirements_from_poetry_lock` 等 | 已通过 |
| A3 | 仅含 `poetry.lock` 的仓库或显式传参调用 `plan_environment_preparation` | `test_plan_environment_with_poetry_lock` 及 `test_plan_environment_with_explicit_poetry_lock` | 已通过 |
| A4 | 缺少哈希或版本冲突的 lockfile 触发明确报错 | `test_export_hash_locked_requirements_poetry_missing_hashes_raises` 等 | 已通过 |
| A5 | CLI `selfforge-agent sandbox export-lock --lock poetry.lock --output ...` | `test_cli_sandbox_export_lock_poetry` | 已通过 |

## 实施顺序

1. 编写捕获缺陷的测试用例（检测、导出、规划），验证在现有实现上失败。
2. 修复 `detect_repository_environment` 支持 TOML 格式哈希识别与 5MB 限制流式首块读取。
3. 扩展 `export_hash_locked_requirements` 支持 Poetry `files` 与 `[metadata.files]` 格式。
4. 扩展 `plan_environment_preparation` 支持识别 `poetry.lock` 与 explicit lock 识别。
5. 运行全部单元测试（382 pass）和 Docker 集成测试（11 pass）。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | 全部通过（0 warning） |
| `git diff --check` | Host | 本分支 | 全部通过（无空白/换行缺陷） |
| `uv run --no-sync pytest -q` | Host | 本分支 | 382 passed, 11 skipped in 373.74s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed, 382 deselected in 56.46s |

## Review

- 验收编号 A1-A5 均在单元测试与集成测试中直接验证。
- 完整保留 `uv.lock` 和 `requirements.lock` 现有兼容逻辑与优先级。
- CLI 参数文档与提示同步更新。

## Handoff

- 交付状态：本地实现与测试完成，等待创建 PR。
