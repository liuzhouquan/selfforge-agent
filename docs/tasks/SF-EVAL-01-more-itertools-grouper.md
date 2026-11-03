> Integration review supersedes the original completion claims below. See [actual review evidence](../incoming-branches-review.md) and [current backlog](../backlog.md). CLI tests and wait reference controls required corrections before integration.

# SF-EVAL-01：增加一个有参考控制的任务（more-itertools grouper 非正数 size 校验）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-more-itertools-grouper-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `more-itertools` 在固定版本 `4aeb4338ae1407d8da9723e86da99efeb446f719`（MIT License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`more_itertools.recipes.grouper(iterable, n, ...)` 通过 `[iter(iterable)] * n` 复制迭代器。当传入非正整数 `n <= 0`（如 0, -1, -2, -5）时，乘法结果为空列表 `[]`，函数静默返回空迭代器（所有模式 `fill`、`ignore`、`strict` 下均产生空输出），既未报错也未进行调用期校验，掩盖了调用方的配置错误。
- 本次完成后：
  1. 契约收紧：在 `more_itertools/recipes.py` 的 `grouper` 中对 `n <= 0` 增加调用期校验（在迭代器开始消费前），抛出 `ValueError`。
  2. 保持兼容：保持合法正整数（1, 2, 3 等）在所有模式（`fill`, `ignore`, `strict`）下的原有正确行为，保留非法 `incomplete` 模式抛出 `ValueError` 的既有行为。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_more_itertools_grouper_size.py` 中覆盖 3 种模式下 4 种非正整数尺寸（0, -1, -2, -5）在调用期即抛出 `ValueError`（12 项断言），以及正向尺寸和非法模式（2 项断言）。基线呈现清晰的 12 个断言未捕获异常失败（0 collection error）；打补丁后 14 项全部通过。
  4. 最小参考补丁：`benchmark/validated-candidates/references/more_itertools_grouper_size.patch` 仅修改 `more_itertools/recipes.py`。
  5. 独立清单：新增 `suite-more-itertools-grouper.json` 与累积 `suite-six-more-itertools-grouper.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档：编写 `more-itertools-grouper.md`。
- 本次不做：不修改 Runtime 核心逻辑，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/holdouts/test_more_itertools_grouper_size.py`
  - `benchmark/validated-candidates/references/more_itertools_grouper_size.patch`
  - `benchmark/validated-candidates/more-itertools-grouper.md`
  - `benchmark/validated-candidates/suite-more-itertools-grouper.json`
  - `benchmark/validated-candidates/suite-six-more-itertools-grouper.json`
  - `docs/tasks/SF-EVAL-01.md`
  - `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `4aeb4338ae1407d8da9723e86da99efeb446f719` | holdout 测试因未在调用期抛出 `ValueError` 断言失败（12 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=12 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 32 passed, holdout 14 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `more_itertools/recipes.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-more-itertools-grouper.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `more-itertools` 固定 SHA、许可证与环境依赖（`selfforge-agent-sandbox:latest`）。
2. 编写独立 holdout 测试 `test_more_itertools_grouper_size.py` 并验证基线 12 个负例断言失败与 2 个正例通过。
3. 制作最小参考补丁 `more_itertools_grouper_size.patch` 并验证仅修改允许文件。
4. 编写候选描述 `more-itertools-grouper.md`。
5. 生成 `suite-more-itertools-grouper.json` 与 `suite-six-more-itertools-grouper.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-more-itertools-grouper.json` | Docker (`selfforge-agent-sandbox:latest`) | 本地工作树 | validated: 1 case, baseline failed=12, visible passed=32, holdout passed=14 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-more-itertools-grouper.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `48d2ccc902f66b9d598f05e542b601f7da95d224590cb3a5f3ae54d1ca6236b2` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 343 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复正确区分了调用期非正数与合法分组正整数。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 MIT License 完整附于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-more-itertools-grouper-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-agent-sandbox:latest` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-more-itertools-grouper-task` 合并入 `main`。
