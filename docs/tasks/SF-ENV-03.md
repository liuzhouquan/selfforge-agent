# SF-ENV-03：支持 Pipfile.lock (Pipenv) 格式哈希识别与环境离线计划导出

- 状态：In Review
- Owner：Antigravity Agent
- 基线：`6090dcc5c5188f4c207b1406ce9b3297a731ef8c`（依赖 PR #40）
- 分支 / 所属 worktree：`feat/environment-pipfile-lock-support`
- 依赖：PR #40 (`feat/environment-poetry-lock-support`)
- PR：https://github.com/liuzhouquan/selfforge-agent/pull/41

## 目标与范围

在已有 `uv.lock` 与 `poetry.lock` 离线依赖导出的基础上，支持 Pipenv 项目的 `Pipfile.lock`（JSON 规范格式），实现安全哈希提取、排除非 PyPI 本地/VCS 源、以及在环境规划与 CLI export-lock 中的平滑回退。

原来发生什么：
1. `export_hash_locked_requirements` 仅支持 TOML 格式，当传入 JSON 格式的 `Pipfile.lock` 时抛出 `tomllib.TOMLDecodeError`（`Failed to parse lock file as TOML`）。
2. `plan_environment_preparation` 未包含 `Pipfile.lock` 标准定位，仓库仅含 `Pipfile.lock` 时直接报错 `ValueError: No supported lockfile (uv.lock, poetry.lock, requirements.lock) found in repository`。
3. 显式 `--lock Pipfile.lock` 无法通过结构化锁文件判定。

本次完成后应观察到：
1. 包含 `Pipfile.lock` 的仓库在 `detect_repository_environment` 中正确标记 `has_hash_lock=True`，识别 `Pipfile.lock` 锁文件且不触发缺失警告。
2. `export_hash_locked_requirements` 能自动识别 JSON 格式或 `Pipfile.lock` 文件名，同时解析 `default` 和 `develop` 依赖节，标准化 `==version`，跳过 `git`/`file`/`path`/`editable` 本地源，提取所有 SHA-256 哈希，格式化为 `name==version --hash=sha256:...`。
3. `plan_environment_preparation` 支持自动回退至 `Pipfile.lock` 或显式指定 `--lock <Pipfile.lock>`，生成正确的 `export_command`。
4. CLI `selfforge-agent sandbox export-lock --lock Pipfile.lock` 能够成功执行并输出依赖。

本次不做：
- 自动运行 pipenv 解析或联网下载。
- 非 SHA-256 哈希支持。

预计触及：
- `src/selfforge_agent/runtime/environment.py`
- `src/selfforge_agent/cli.py`
- `tests/test_delivery_revert_and_lock_export.py`
- `tests/test_environment_detection.py`
- `tests/test_environment_plan.py`

复用：
- 现有 `export_hash_locked_requirements`、`parse_locked_requirements`、`json`、`shlex.join`。

约束：
- 严格遵循离线镜像构建契约与环境不可变原则；不执行不受信任代码或网络安装。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 包含标准 `Pipfile.lock` 的仓库调用 `detect_repository_environment` | `test_detect_pipfile_lock_with_hashes_sets_has_hash_lock` | 已通过 |
| A2 | 包含 `Pipfile.lock`（default+develop、多哈希、git源）的文件调用 `export_hash_locked_requirements` | `test_export_hash_locked_requirements_from_pipfile_lock` 及 `test_export_hash_locked_requirements_pipfile_skips_editable_and_vcs` | 已通过 |
| A3 | 仅含 `Pipfile.lock` 的仓库或显式传参调用 `plan_environment_preparation` | `test_plan_environment_with_pipfile_lock` 及 `test_plan_environment_with_explicit_pipfile_lock` | 已通过 |
| A4 | 缺少哈希或版本冲突的 Pipfile.lock 触发明确报错 | `test_export_hash_locked_requirements_pipfile_missing_hashes_raises` 及 `test_export_hash_locked_requirements_pipfile_version_conflict_raises` | 已通过 |
| A5 | CLI `selfforge-agent sandbox export-lock --lock Pipfile.lock --output ...` | `test_cli_sandbox_export_lock_pipfile` | 已通过 |

## 实施顺序

1. 编写捕获缺陷的测试用例（检测、导出、规划），验证在现有实现上失败（报 TOML 解析错误与缺少 lockfile 错误）。
2. 在 `export_hash_locked_requirements` 中增加 JSON 格式自动判定与 Pipfile.lock 结构解析。
3. 扩展 `plan_environment_preparation` 支持识别 `Pipfile.lock` 标准路径与 structured lock 识别。
4. 更新 CLI `--lock` 参数帮助文案。
5. 运行全部单元测试（390 pass）和 Docker 集成测试（11 pass）。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | 全部通过（0 warning） |
| `git diff --check` | Host | 本分支 | 全部通过（无空白/换行缺陷） |
| `uv run --no-sync pytest -q` | Host | 本分支 | 390 passed, 11 skipped in 370.66s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed, 390 deselected in 52.43s |

## Review

- 验收编号 A1-A5 均在单元测试与集成测试中直接验证。
- 完整保留 `uv.lock`、`poetry.lock` 和 `requirements.lock` 现有兼容逻辑与优先级：`uv.lock` > `poetry.lock` > `Pipfile.lock` > `requirements.lock`。
- CLI 参数文档与提示同步更新。

## Handoff

- 交付状态：本地实现与测试完成，等待创建 PR。
