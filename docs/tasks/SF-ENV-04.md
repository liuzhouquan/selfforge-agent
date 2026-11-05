# SF-ENV-04：支持 requirements.lock / pip-compile 格式哈希识别与环境离线计划导出

- 状态：In Review
- Owner：Antigravity Agent
- 基线：`602df51ccaf7e81a9eb97651a23c891516e73afc`（`origin/main`，PR #41 合并后）
- 分支 / 所属 worktree：`feat/environment-pip-compile-lock-support`
- 依赖：PR #41 (`feat/environment-pipfile-lock-support`)
- PR：https://github.com/liuzhouquan/selfforge-agent/pull/43

## 目标与范围

在已有 `uv.lock`、`poetry.lock` 与 `Pipfile.lock` 离线依赖导出的基础上，支持 pip-compile / requirements.lock 生成的标准 flat requirements 格式哈希锁文件，实现规范化导出（多行折叠、大写 SHA256 规范化、去重同版本多哈希合并、版本分叉与条件标记拒绝），并统一 CLI 与离线构建计划导出。

原来发生什么：
1. `export_hash_locked_requirements` 仅支持 TOML（uv/poetry）与 JSON（Pipfile.lock）格式，传入 requirements.lock / pip-compile 锁文件时抛出 `ValueError: Failed to parse lock file as TOML`。
2. 显式 `--lock <file>` 若为 requirements 格式时无法通过规范化哈希导出工具流导出，无法统一清洗和归一化不同来源的 SHA-256 哈希。
3. 大写哈希前缀（如 `SHA256:...`）在 `_hash()` 解析中未被前缀去除导致丢失。

本次完成后应观察到：
1. `export_requirements_txt` 正确解析 pip-compile / flat requirements 锁文件，支持反斜杠折行、多哈希并入同一包条目、大写/小写 `SHA256:` 规范化为 `sha256:[0-9a-f]{64}`。
2. 严格安全校验：拒绝带条件环境变量标记（`;`）、extras（`[...]`）、非哈希选项指令（`-r`、`--index-url` 等）、缺失哈希以及版本分叉。
3. `export_hash_locked_requirements` 自动检测 requirements 锁文件并导出规范化的 `name==version --hash=sha256:...` 列表。
4. `plan_environment_preparation` 支持显式 `--lock` 指定 requirements.lock，生成正确的 `export_command`。仓库自带 `requirements.lock` 时保持无导出开销的直接 `-r` 引用。
5. CLI `selfforge-agent sandbox export-lock --lock requirements.lock --output ...` 能够成功执行并输出依赖。

本次不做：
- 自动运行 pip-compile 或联网解析依赖。
- 非 SHA-256 哈希支持。
- 允许无哈希条目或忽略版本冲突。

预计触及：
- `src/selfforge_agent/runtime/lockfiles.py`
- `src/selfforge_agent/runtime/environment.py`
- `src/selfforge_agent/cli.py`
- `tests/test_delivery_revert_and_lock_export.py`
- `tests/test_environment_plan.py`

复用：
- 现有 `_render`、`_hash`、`parse_locked_requirements`、`export_hash_locked_requirements`。

约束：
- 严格遵循离线镜像构建契约与环境不可变原则；不执行不受信任代码或网络安装。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | pip-compile 格式锁文件（含折行、注释、多哈希、不同大小写）调用 `export_hash_locked_requirements` | `test_export_hash_locked_requirements_pip_compile_multiline` 及 `test_export_hash_locked_requirements_pip_compile_normalizes_uppercase_hashes` | 已通过 |
| A2 | 含有环境变量条件标记（`;`）的 pip-compile 锁文件调用导出时安全拒绝 | `test_export_hash_locked_requirements_pip_compile_rejects_markers` | 已通过 |
| A3 | 缺少哈希或包含不同版本分叉的 pip-compile 锁文件调用导出时明确抛出异常 | `test_export_hash_locked_requirements_pip_compile_rejects_missing_hashes` 及 `test_export_hash_locked_requirements_pip_compile_version_conflict_raises` | 已通过 |
| A4 | 仓库内含 `requirements.lock` 或显式 `--lock` 调用 `plan_environment_preparation` | `test_plan_environment_with_requirements_lock` 及 `test_plan_environment_with_explicit_requirements_lock` | 已通过 |
| A5 | CLI `selfforge-agent sandbox export-lock --lock requirements.lock --output ...` | `test_cli_sandbox_export_lock_pip_compile` | 已通过 |

## 实施顺序

1. 编写捕获缺陷的测试用例（requirements.lock / pip-compile 导出与计划），验证在现有实现上失败（报 TOML 解析错误）。
2. 在 `src/selfforge_agent/runtime/lockfiles.py` 中实现 `export_requirements_txt`，修复 `_hash` 大写前缀处理。
3. 在 `src/selfforge_agent/runtime/environment.py` 中分发 `is_req` 逻辑，识别 flat requirements 格式，保持离线计划中 flat requirements 直接通过 `-r` 下载无需冗余导出。
4. 更新 CLI `--lock` 参数帮助文案。
5. 运行全部单元测试和 Docker 集成测试。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | 全部通过（0 warning） |
| `git diff --check` | Host | 本分支 | 全部通过（无空白/换行缺陷） |
| `uv run --no-sync pytest -q` | Host | 本分支 | 419 passed, 11 skipped in 255.49s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed, 419 deselected in 42.95s |

## Review

- 验收编号 A1-A5 均在单元测试与计划测试中直接覆盖。
- 完整闭环支持现存主流 Python 包管理器与锁定工具（uv.lock / poetry.lock / Pipfile.lock / requirements.lock 及 pip-compile）。
- 保持向后兼容：仓库原有的 `requirements.lock` 依然直接由 `pip download -r requirements.lock` 使用，无额外导出步骤；显式 `--lock` 时执行统一清洗归一化导出。

