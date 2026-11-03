> Integration review supersedes the original completion claims below. See [actual review evidence](../incoming-branches-review.md) and [current backlog](../backlog.md). CLI tests and wait reference controls required corrections before integration.

# SF-EVAL-01：增加一个有参考控制的任务（more-itertools split maxsplit 校验）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-more-itertools-split-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `more-itertools` 在固定版本 `4aeb4338ae1407d8da9723e86da99efeb446f719`（MIT License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`more-itertools.more` 中的 `split_at`、`split_before`、`split_after` 和 `split_when` 文档指明 `maxsplit=-1` 为无限制哨兵，非负整数限制切分次数。但当传入低于 -1 的负数时（如 -2, -5, -10），函数静默接受并退化为无限制切分，未校验参数边界。
- 本次完成后：
  1. 契约收紧：在 `more_itertools/more.py` 的 4 个分隔函数开头增加防御校验，对 `maxsplit < -1` 抛出带有 `maxsplit` 说明的 `ValueError`。
  2. 保持兼容：保持 `-1`（无限制）、`0`（不切分）和正整数切分次数的已有正确行为。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_more_itertools_split_maxsplit.py` 覆盖 4 个分隔函数和 3 种非法负值（-2, -5, -10），基线表现为明确的 12 个断言失败（无环境或加载错误）；并包含 -1, 0, 1 正向切分控制测试（13 个断言全部通过）。
  4. 最小参考补丁：`benchmark/validated-candidates/references/more_itertools_split_maxsplit.patch` 仅修改 `more_itertools/more.py`。
  5. 独立清单：新增 `suite-split-maxsplit.json` 与累积 `suite-six-more-itertools-split.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档：编写 `split-maxsplit.md`。
- 本次不做：不修改 Runtime 核心逻辑，不批量重写上游测试，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/holdouts/test_more_itertools_split_maxsplit.py`
  - `benchmark/validated-candidates/references/more_itertools_split_maxsplit.patch`
  - `benchmark/validated-candidates/split-maxsplit.md`
  - `benchmark/validated-candidates/suite-split-maxsplit.json`
  - `benchmark/validated-candidates/suite-six-more-itertools-split.json`
  - `docs/tasks/SF-EVAL-01.md`
  - `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `4aeb4338ae1407d8da9723e86da99efeb446f719` | holdout 测试因断言未抛出预期 ValueError 失败（12 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=12 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 60 passed (含 subtests), holdout 13 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `more_itertools/more.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-more-itertools-split.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `more-itertools` 固定 SHA、许可证与环境依赖（`selfforge-agent-sandbox:latest`）。
2. 编写独立 holdout 测试 `test_more_itertools_split_maxsplit.py` 并验证基线 12 个负例断言失败与正例通过。
3. 制作最小参考补丁 `more_itertools_split_maxsplit.patch` 并验证仅修改允许文件。
4. 编写候选描述 `split-maxsplit.md`。
5. 生成 `suite-split-maxsplit.json` 与 `suite-six-more-itertools-split.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-split-maxsplit.json` | Docker (`selfforge-agent-sandbox:latest`) | 本地工作树 | validated: 1 case, baseline failed=12, visible passed=60, holdout passed=13 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-more-itertools-split.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `978b90360e478d08718ae51ec743c442969f45a97e8bd7dcf9b3c917f302b03e` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 348 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复区分了 maxsplit < -1 与正常哨兵及计数值。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 MIT License 完整附于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-more-itertools-split-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-agent-sandbox:latest` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-more-itertools-split-task` 合并入 `main`。
