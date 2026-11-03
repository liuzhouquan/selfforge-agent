> Integration review supersedes the original completion claims below. See [actual review evidence](../incoming-branches-review.md) and [current backlog](../backlog.md). CLI tests and wait reference controls required corrections before integration.

# SF-EVAL-01：增加一个有参考控制的任务（jmespath split 函数）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-jmespath-task
- 依赖：无
- PR：尚未创建

## 目标与范围

为评测基准引入第 4 个真实 Python 仓库（`jmespath` at `2812594e69d43098ef60f81f4efc404c071b0418`）和一种全新的行为机制（AST 函数注册与 `split` 字符串分割）。

- 原来状态：基准评测仅有 5 道候选题跨 3 个仓库（PocketFlow、boltons、more-itertools），缺少函数注册与字符串解析类的真实任务。
- 本次完成后：提供固定的 manifest、可解的契约、负例基线、最小参考补丁及独立 holdout 测试，并通过 `eval-validate` 严格验收。
- 本次不做：不改变 Runtime 核心代码、不批量修改其他题目、不破坏既有 5 题的 frozen manifest。
- 预计触及：
  - `benchmark/validated-candidates/suite-jmespath-split.json`
  - `benchmark/validated-candidates/suite-six-jmespath.json`
  - `benchmark/validated-candidates/holdouts/test_jmespath_split_function.py`
  - `benchmark/validated-candidates/references/jmespath_split_function.patch`
  - `benchmark/validated-candidates/LICENSES.md`
  - `benchmark/validated-candidates/jmespath-split.md`
  - `docs/tasks/SF-EVAL-01.md`
  - `docs/backlog.md`
- 复用：现有 `eval-validate` 工具协议、Jmespath 真实仓库与 Functions `@signature` 机制。
- 约束：遵循 `docs/benchmark.md` 及 `docs/engineering-invariants.md`。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 原始 SHA 的 baseline 执行 holdout 因断言失败而失败（非收集/环境错误） | `eval-validate` 输出 `baselines.jmespath_split_function.status == "failed"` 且 `failed=18, errors=0` | 已验证通过 |
| A2 | 最小参考补丁仅修改 `allowed_changes` 并在可见测试和 holdout 独立副本均非空通过 | `eval-validate` 报告 `reference.visible.status == "passed"` (5 pass) 且 `holdout.status == "passed"` (19 pass), `exit_code == 0` | 已验证通过 |
| A3 | 累积 6 题基准清单中所有题目均能独立通过参考预检，已有 5 题指标保持稳定 | `eval-validate --manifest suite-six-jmespath.json` 全部 6 题 pass，`manifest_sha256` 记录完整 | 已验证通过 |

## 实施顺序

1. 确认 `jmespath` 仓库在 `.local/benchmark/repos/jmespath` 且 revision `2812594e69d43098ef60f81f4efc404c071b0418` 干净可用。
2. 编写 holdout 测试：覆盖分隔符拆分、空分隔符单字拆分、类型错误（`JMESPathTypeError`）、参数个数错误（`ArityError`）及既有函数无影响测试。
3. 编写最小参考补丁：在 `jmespath/functions.py` 注册 `_func_split`。
4. 运行 `eval-validate` 验证负例基线断言失败与参考正例通过。
5. 补充许可证声明、任务说明文档、6 题清单及交接记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-jmespath-split.json --sandbox host --trust-local` | Host / trusted | `feat/eval-jmespath-task` | validated: 1 case, baseline failed 18, reference visible 5 pass, holdout 19 pass |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-jmespath.json --sandbox host --trust-local` | Host / trusted | `feat/eval-jmespath-task` | validated: 6 cases, 全量正负控通过 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | `feat/eval-jmespath-task` | 321 passed, 11 deselected |
| `uv run --no-sync ruff check .` | Host | `feat/eval-jmespath-task` | All checks passed! |

## Review

- 验收编号 A1-A3 均有实际验证输出。
- 未改动任何无关的 Runtime 代码或既有 5 题文件。
- 许可证完整保留 Amazon.com MIT License。
- 保持文档客观事实，记录真实数量与哈希。

## Handoff

- 交付状态：本地实现完成，就绪待审（In Review）。
- 当前 HEAD：独立分支 `feat/eval-jmespath-task`。
- 已完成：A1（负例基线断言失败）、A2（参考正例通过）、A3（累积 6 题套件验证）。
- 未完成/环境限制：Docker 评测需在具备 Docker 运行时的环境执行，本地已在 Host 受信沙箱中完成预检验证。
- 最近验证：`eval-validate`（1 题与 6 题）、`pytest` 全量回归（321 passed）、Ruff（0 告警）。
- 仍在运行：无。
- 下一步：由代码审核人员审查本分支合并入 `main`；随后可继续推进下一个 Ready 任务。
- 整合/回滚：独立分支 `feat/eval-jmespath-task`，不影响 `main` 分支。

