# SF-EVAL-04：在评测输出自动记录应用 SHA、Python/Docker 版本和生效预算/供应商配置

> 已核对：PR #42 已合并，merge SHA `8e21c4e4b63e28a907cf948bcae7329a8936ffae`。以下规格/测试是历史记录；当前开发以 [SDD](../SDD.md) 为准，不重新领取本任务。

- 状态：Merged（历史任务）
- Owner：Antigravity Agent
- 基线：`602df51ccaf7e81a9eb97651a23c891516e73afc`
- 分支 / 所属 worktree：`feat/eval-automatic-run-provenance`
- 依赖：PR #37, PR #38, PR #39, PR #40, PR #41 已合并入 main
- PR：https://github.com/liuzhouquan/selfforge-agent/pull/42

## 目标与范围

在评测执行（`evaluate_suite` 与 CLI `selfforge-agent eval-suite`）输出中自动捕获完整的执行来源元数据（Run Provenance），解决早期评测记录（如 53/60）中因未内嵌应用 SHA、工具链版本和调用配置导致的来源回溯断层问题。

原来发生什么：
1. `evaluate_suite` 返回的字典仅包含用例统计、耗时、Token 总量与结果列表，缺少运行环境来源元数据：
   - 未记录执行评测的 selfForge Agent 应用 Git commit SHA；
   - 未记录运行 Python 解释器版本与系统平台信息；
   - 未记录 Docker daemon/client 版本（在 Docker 模式下）；
   - 未在元数据中整合当前生效的预算配置（`max_steps`、`time_budget`、`test_timeout`、`repeats`）；
   - 未记录当前启用的供应商模型配置（在 CLI 评测或自定义模型调用时丢弃了端点与模型名）。
2. CLI `eval-suite` 调用 `evaluate_suite` 时未向底层传递 `providers` 配置，下游输出无法获知所使用的供应商与模型信息。

本次完成后应观察到：
1. `evaluate_suite` 顶层输出包含 `"provenance"` 字典，包含以下不可变字段：
   - `commit_sha` 及 `app_commit_sha`：自动检测应用所在的 Git 仓库 commit SHA（40 位规范小写十六进制字符串；若脱离 git 仓库或失败则安全降级为 None）；
   - `python_version`：当前 Python 解释器版本（例如 `3.12.13`）；
   - `platform`：操作系统平台标识（例如 `Linux-6.6.137+...`）；
   - `docker_version`：在 `sandbox="docker"` 时自动检测 Docker 版本（例如 `29.8.0`）；在 `sandbox="host"` 时置为 None；
   - `budgets`：生效预算字典，包含 `max_steps`、`time_budget`、`test_timeout`、`repeats`；
   - `providers`：经敏感信息过滤后的供应商与模型配置列表，仅保留 `name`、`model`、`base_url`；若未显式指定则根据运行结果中记录的供应商与模型安全推断；
   - `started_at` 与 `completed_at`：ISO 8601 UTC 时间戳字符串，满足 `started_at <= completed_at`。
2. 绝对安全过滤：即使传入包含 API 密钥（如 `key`、`api_key`）的 `ProviderConfig` 或字典，`provenance["providers"]` 也会严格剔除凭证字段，绝不泄漏或序列化到评测报告中。
3. CLI `selfforge-agent eval-suite` 在执行时将当前路由的 providers 传递给 `evaluate_suite`，使终端打印及 `--output` 文件包含完整的执行来源信息。
4. 完全向下兼容现有消费端：保留所有现有顶层字段（`version`, `cases`, `runs`, `successes`, `images`, `results` 等），不重跑、不修改历史 53/60 等冻结评测报告。

本次不做：
- 不重跑或修改历史 `twenty-v2-model-repeat3.summary.json` 等已冻结数据。
- 不引入重型外部遥测依赖或上报服务。

预计触及：
- `src/selfforge_agent/runtime/benchmark.py`
- `src/selfforge_agent/cli.py`
- `tests/test_benchmark.py`
- `docs/backlog.md`
- `docs/tasks/SF-EVAL-04.md`

复用：
- Python 标准库 `platform`、`datetime.UTC`、`subprocess`、`re`、`os`。
- 现有 `resolve_docker_image` 与 `evaluate_suite` 架构。

约束：
- 严格遵循 `docs/engineering-invariants.md`：评测报告不得包含私有密钥、私有路径或隐藏测试文本；严格区分启发性推测与事实归因。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 调用 `evaluate_suite(..., sandbox="host")` / 观察 `report["provenance"]` 完整包含 40 位 `commit_sha`、`python_version`、`platform`、`docker_version is None`、生效 `budgets` 以及 `started_at <= completed_at` | `test_evaluate_suite_records_run_provenance` 断言（旧实现因 KeyError 失败） | 已通过 |
| A2 | 传入含 API 密钥的 `ProviderConfig` 与字典 / 观察 `prov["providers"]` 仅保留 `name`, `model`, `base_url`，序列化后绝无密钥字符串 | `test_evaluate_suite_sanitizes_providers` 断言（旧实现因 TypeError 失败） | 已通过 |
| A3 | 应用 commit SHA 检测（环境变量覆盖规范化、非法格式丢弃、无 git 降级） | `test_detect_app_commit_sha_helper`（旧实现因 ImportError 失败） | 已通过 |
| A4 | Docker 版本检测（Server 版本提取、`docker --version` 正则提取、无 Docker 优雅降级为 None） | `test_detect_docker_version_helper`（旧实现因 ImportError 失败） | 已通过 |
| A5 | CLI `selfforge-agent eval-suite` / 观察输出文件包含 `provenance`，且 CLI 正确透传 providers 至评估器 | `test_cli_eval_suite_outputs_provenance`（旧实现因 providers 未透传失败） | 已通过 |
| A6 | 完整测试集回归无破坏 | `uv run --no-sync pytest -q` 与 `pytest -q -m integration` | 全部通过（416 单元测试 + 11 Docker 集成测试） |

## 实施顺序

1. 编写捕获缺陷与接口缺失的专项测试用例，验证在旧代码上全部失败（KeyError, TypeError, ImportError, 缺失 providers 透传）。
2. 在 `src/selfforge_agent/runtime/benchmark.py` 中实现 `_detect_app_commit_sha` 与 `_detect_docker_version` 辅助函数。
3. 在 `evaluate_suite` 中引入 `providers` 参数，记录 UTC 时间戳与过滤后的敏感配置，组装并返回 `provenance` 字段。
4. 在 `src/selfforge_agent/cli.py` 的 `eval-suite` 中向 `evaluate_suite` 透传 `providers=providers`。
5. 运行 Ruff 静态语法检查（规范使用 `datetime.UTC` 与规范导入顺序）与 `git diff --check`。
6. 运行全量单元测试与 Docker 集成测试验证。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | `feat/eval-automatic-run-provenance` | 全部通过（All checks passed!） |
| `git diff --check` | Host | `feat/eval-automatic-run-provenance` | 全部通过（无空白/换行缺陷） |
| `uv run --no-sync pytest -q tests/test_benchmark.py` | Host | `feat/eval-automatic-run-provenance` | 23 passed, 1 skipped in 96.42s |
| `uv run --no-sync pytest -q` | Host | `feat/eval-automatic-run-provenance` | 416 passed, 11 skipped in 317.57s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | `feat/eval-automatic-run-provenance` | 11 passed, 416 deselected in 41.11s |

## Review

- A1-A6 验收项均具备自动化测试或实机执行日志支撑。
- 供应商配置序列化严格剥离 `key`、`api_key`，防止凭证泄漏。
- Git SHA 与 Docker 版本检测均具备防崩溃保护与类型规范化（小写 40 位 hex 校验）。
- 保留所有既有评测指标与字段，不影响下游消费。

## 历史 Handoff（实施时记录）

- 交付状态：PR 已创建（#42），CI 与评审就绪
- 当前 HEAD：`9a9fecd`（分支提交），基于 main 基线 `602df51ccaf7e81a9eb97651a23c891516e73afc`
- 已完成：A1, A2, A3, A4, A5, A6
- 未完成/环境限制：无
- 最近验证：`uv run --no-sync pytest -q`（416 passed）与 `uv run --no-sync pytest -q -m integration`（11 passed）
- 仍在运行：无
- 下一步：等待 PR #42 审查合并；合并后推进下一个 Ready 工作单元

Review: provenance is captured before preflight/model execution. Installed packages never fall back to the task working directory's Git SHA. Environment SHAs are labelled declared; source dirty state is recorded where available. URLs omit userinfo/query/fragment and retain only public standard endpoint paths; uncommon paths are omitted. Docker version remains explicitly engine-or-client evidence, not proof of both. Historical reports are not modified.
