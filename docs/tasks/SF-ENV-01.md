# SF-ENV-01：为锁定依赖配置定义离线环境准备计划与检查

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/environment-preparation-plan
- 依赖：无
- PR：待提交审核

## 目标与范围

在只读环境识别（`sandbox detect`）与严格离线镜像构建（`sandbox build`）之间，定义并实现确定性的离线环境准备计划（Environment Preparation Plan，`sandbox plan`）。

- 原来状态：已有 `detect` 只读分析依赖文件，以及 `build` 进行严格离线镜像构建。但当依赖缺失或需要为特定仓库准备 wheelhouse 时，缺乏结构化的计划生成器；操作员无法获知当前 wheelhouse 中缺少的具体包、哈希及安全下载命令，`build` 只能直接报错退出。
- 本次完成后：
  1. 在 `runtime/environment.py` 实现 `plan_environment_preparation(repository, ...)`。
  2. 支持从 `uv.lock` 自动推导或直接读取哈希锁定的 `requirements.lock`。
  3. 审计指定的 `wheelhouse` 目录：比对各个已存在的 `.whl` 文件 SHA-256 哈希与锁文件要求，计算出已匹配的 wheels、缺失的包及缺少的哈希。
  4. 产出结构化计划，包含：`status`（`ready` 或 `pending_wheels`）、`layout`、`packages` 清单、`wheelhouse` 审计结果（`total_wheels`, `satisfied_count`, `missing_count`, `missing_packages`）、受限安全的二进制下载命令示例（`pip download --only-binary=:all: --require-hashes`）以及构建命令（`selfforge-agent sandbox build`）。
  5. 在 CLI `selfforge-agent sandbox plan` 和 API `POST /api/sandbox/plan` 提供完整接口。
- 本次不做：不执行任意仓库构建脚本（setup.py / hooks），不自动联网下载外部二进制文件，不弱化 `--network=none` 与 `--require-hashes` 离线构建约束。
- 触及文件：
  - `src/selfforge_agent/runtime/environment.py`
  - `src/selfforge_agent/cli.py`
  - `src/selfforge_agent/api/app.py`
  - `tests/test_environment_plan.py`
  - `docs/tasks/SF-ENV-01.md`
  - `docs/backlog.md`
- 约束：遵循 `docs/engineering-invariants.md` 关于依赖隔离与离线构建安全性的原则。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定包含 uv.lock 或 requirements.lock 的项目与不完整的 wheelhouse | 计划正确识别布局与包，status 标为 `pending_wheels`，列出缺失包与安全下载命令 | `tests/test_environment_plan.py::test_plan_environment_with_missing_wheels` 通过 |
| A2 | 给定 wheelhouse 已齐备所有要求的哈希 wheel 文件 | 计划 status 标为 `ready`，列出精确的 `selfforge-agent sandbox build` 命令 | `tests/test_environment_plan.py::test_plan_environment_ready_when_wheels_present` 通过 |
| A3 | CLI `selfforge-agent sandbox plan` 与 Web API `POST /api/sandbox/plan` | 成功输出格式化 JSON 计划，无效路径正确报错（400/200） | `tests/test_environment_plan.py::test_plan_environment_cli_output` 与 `test_plan_environment_api_endpoint` 通过 |
| A4 | 全量既有测试回归与 Ruff 检查 | pytest 与 ruff 全绿 | 348 passed, 0 failed; ruff check passed |

## 实施顺序

1. 编写准备计划的单元测试 `tests/test_environment_plan.py`。
2. 在 `runtime/environment.py` 实现 `plan_environment_preparation`。
3. 在 `cli.py` 增加 `sandbox plan` 子命令。
4. 在 `api/app.py` 增加 `POST /api/sandbox/plan` 路由与请求响应模型。
5. 运行全部测试与代码检查。
6. 更新任务卡与交接记录并提交。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync pytest tests/test_environment_plan.py` | Host (Python 3.12, WSL2 Ubuntu) | 本地工作树 | 5 passed in 0.42s |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 348 passed, 11 deselected, 1 warning in 246.01s |
| `uv run --no-sync pytest -v -m integration` | Docker Desktop 4.92.0 / `selfforge-agent-sandbox:latest` | 本地工作树 | 11 passed in 48.33s |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有专属单元测试与全量回归保障。
- [x] 错误、取消、部分写入、恢复和输入边界：无 lockfile 时抛出清晰 `ValueError`，无效 requirements 或 wheelhouse 抛出明确异常并在 CLI/API 映射为合理错误。
- [x] 序列化/API/UI 消费者：CLI `sandbox plan` 支持 `--repo`, `--wheelhouse`, `--tag`, `--lock`, `--requirements`, `--output`；API 提供 `POST /api/sandbox/plan` 并做本地客户端限制（`_require_local`）。
- [x] 保留原断言与安全门槛：离线构建依然坚持 `--network=none` 与 `--require-hashes`；不执行任意 setup.py / hooks。

## Handoff

- 交付状态：本地已实现，单元/离线测试及真实 Docker 端到端集成测试全绿，待审核合并。
- 当前分支：`feat/environment-preparation-plan`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。宿主机 Docker Desktop WSL 集成已成功连接，11 项真实 Docker 集成测试全部通过。
- 最近验证：`uv run --no-sync pytest tests/test_environment_plan.py`（5 passed）、`uv run --no-sync pytest -q -m 'not integration'`（348 passed）、`uv run --no-sync pytest -v -m integration`（11 passed）、`uv run --no-sync ruff check .`（All checks passed）。
- 仍在运行：无。
- 下一步：审查本分支并创建 Pull Request 合并至 `main`。
