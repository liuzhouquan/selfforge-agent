# SF-EVAL-05：多轮评测报告回归对比分析与差异报告

> 已核对：PR #46 已合并，merge SHA `8050febd7404f30a233030925ce00986001577a1`。以下规格/测试是历史记录；当前开发以 [SDD](../SDD.md) 为准，不重新领取本任务。

- 状态：Merged（历史任务）
- Owner：Antigravity Agent
- 基线：`602df51ccaf7e81a9eb97651a23c891516e73afc`（`origin/main`）
- 分支 / 所属 worktree：`feat/evaluation-report-diff`
- 依赖：无（基于已合并的 PR #41 / main）
- PR：https://github.com/liuzhouquan/selfforge-agent/pull/46

## 目标与范围

在已有 20 题 / 5 仓库 benchmark 评测与自动 provenance 捕获的基础上，提供专门的评测结果对比与回归分析工具（`diff_eval_reports`、`format_eval_diff`、CLI `selfforge-agent eval-diff`、API `POST /api/benchmark/diff`），在更换模型、调整 Prompt 策略或升级 Runtime 机制后，客观、自动化地对比两轮运行表现，精确指出：总体成功率变化、用例级修复（improved）与倒退（regressed）、失败原因迁移矩阵、耗时与 Token 消耗波动，并支持 CI 门禁检查（`--fail-on-regression`）。

原来发生什么：
1. 缺少专门的评测报告比对工具：不同运行的 `.summary.json` 只能由人工肉眼核对或编写临时脚本对比。
2. 缺乏用例维度的回归警报：无法自动判定某道题是由于新策略修复还是发生了倒退。
3. 缺少结构化 Markdown/JSON 差异报告输出，CI 无法基于评测结果设置防止指标倒退的门禁。

本次完成后应观察到：
1. 实现 `diff_eval_reports(before: dict, after: dict) -> dict`：
   - 总体对比：通过率 delta、成功数 delta、总耗时 delta、Token 用量（prompt/completion/total）delta。
   - 用例迁移分类：自动将用例划分为 `improved`、`regressed`、`unchanged`、`added`、`removed`。
   - 失败分类对比：对比 failure_reasons（如 `agent_max_steps` 增减、`holdout_assertion_failed` 迁移）。
   - 清单校验：核对 `manifest_sha256` 是否一致并显式标记 `manifest_mismatch`。
2. 实现 `format_eval_diff(diff: dict, format: str = "markdown") -> str`：
   - 支持生成清晰整洁的 Markdown 表格与纯文本格式。
3. CLI 支持 `selfforge-agent eval-diff <before.json> <after.json> [--format json|markdown|text] [--output <file>] [--fail-on-regression]`：
   - 支持向文件输出对比报告。
   - 当传入 `--fail-on-regression` 时，若存在倒退用例或总成功率下降，退出码为 1。
4. API 新增 `POST /api/benchmark/diff` 接口。
5. 保持客观评测原则：只读分析，绝不改写或合并历史评测数据。

本次不做：
- 重新运行基准测试或发起模型调用。
- 合并不同策略的历史小样本分数。

预计触及：
- `src/selfforge_agent/runtime/benchmark_diff.py`
- `src/selfforge_agent/runtime/benchmark.py`
- `src/selfforge_agent/cli.py`
- `src/selfforge_agent/api/app.py`
- `tests/test_benchmark_diff.py`
- `docs/tasks/SF-EVAL-05.md`
- `docs/backlog.md`

复用：
- 现有 benchmark report 数据契约（`twenty-v2-model-repeat3.summary.json` 等）。

约束：
- 遵循 `docs/benchmark.md`、`docs/evaluation.md` 与 `docs/engineering-invariants.md` 关于评测独立性与不篡改历史记录的约束。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 对比两份相同或不同评测报告 | `test_diff_eval_reports_basic_and_improved` | 已通过 |
| A2 | 出现用例倒退（如通过率下降）的场景 | `test_diff_eval_reports_regressed` | 已通过 |
| A3 | 清单哈希不一致的两份报告对比 | `test_diff_eval_reports_manifest_mismatch_and_case_diffs` | 已通过 |
| A4 | 格式化输出为 Markdown 与文本 | `test_format_eval_diff_markdown` | 已通过 |
| A5 | CLI `selfforge-agent eval-diff` 正常运行与 `--fail-on-regression` 退出码控制 | `test_cli_eval_diff` | 已通过 |
| A6 | API `POST /api/benchmark/diff` 端点测试 | `test_api_benchmark_diff_endpoint` | 已通过 |

## 实施顺序

1. 编写全面的测试用例 `tests/test_benchmark_diff.py`，证明未实现时调用失败（TDD）。
2. 在 `src/selfforge_agent/runtime/benchmark_diff.py` 中实现核心比较与格式化逻辑。
3. 在 `src/selfforge_agent/cli.py` 中添加 `eval-diff` 子命令。
4. 在 `src/selfforge_agent/api/app.py` 中增加 `POST /api/benchmark/diff` 路由。
5. 运行完整单元测试与 Docker 集成测试。
6. 更新任务卡与 backlog，提交并推送创建 PR。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | 全部通过（0 warning） |
| `git diff --check` | Host | 本分支 | 全部通过（无空白/换行缺陷） |
| `uv run --no-sync pytest -q tests/test_benchmark_diff.py` | Host | 本分支 | 7 passed in 2.37s |
| `uv run --no-sync pytest -q` | Host | 本分支 | 418 passed, 11 skipped in 319.33s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed, 418 deselected in 52.23s |

## Review

- 验收编号 A1-A6 均在单元测试与全量集成测试中直接验证覆盖。
- 差异分析完全遵循客观评测契约：纯只读分析，绝不改写、覆盖或合并历史测试报告。
- 用例级通过率 delta 与分类（improved, regressed, unchanged, added, removed）精确计算。
- 失败原因迁移矩阵与总耗时/Token delta 统计准确。
- CLI `eval-diff` 提供 `--fail-on-regression` 退出码门禁，并支持 JSON、Markdown 与纯文本导出。
- Web API `POST /api/benchmark/diff` 路由同步提供比对支持。

## 历史 Handoff（实施时记录）

- 交付状态：本地实现与全量测试完成，准备提交并创建 PR。

Review: comparisons read real total_duration_seconds/top-level token metrics and retain legacy aliases. Counts and identities are validated; rates use exact count fractions before formatting. Missing SHA/empty/inconsistent reports are errors; incompatible manifests cannot pass --fail-on-regression. Output cannot overwrite either input. Comparisons are descriptive differences, not statistical or causal improvement proof, and include provenance mismatch information. Synthetic fixtures now use valid full hashes and reconciled counts; original assertions remain.
