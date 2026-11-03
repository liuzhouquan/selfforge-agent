# SF-EVAL-01：增加一个有参考控制的任务（PocketFlow max_retries 类型校验）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-pocketflow-retry-type-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `PocketFlow` 在固定版本 `f74d023f93607b8c3268133339a5e532a949898c`（MIT License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`PocketFlow` 在构造 `Node(max_retries=...)` 时接受任意对象。非整型类型（如 float `1.5`、`3.14`，string `'2'`，`None`）在调用时因不是整数在迭代或其他操作中引发延迟异常，而 `bool`（`True`, `False`）因是 Python `int` 的子类被隐式转换为 `1` 或 `0`，缺少显式的构造期类型安全保护。
- 本次完成后：
  1. 契约收紧：在 `pocketflow/__init__.py` 的 `Node.__init__` 中对 `max_retries` 增加显式类型校验，拒绝 `bool` 以及非 `int` 类型（抛出提及 `max_retries` 的 `TypeError`）。
  2. 保持兼容：保持合法正整数（1, 3, 10）以及默认值（`max_retries=1`）的正常运行，5 类节点（`Node`, `AsyncNode`, `BatchNode`, `AsyncBatchNode`, `AsyncParallelBatchNode`）全部通过继承一致生效。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_pocketflow_max_retries_type.py` 中覆盖全部 5 种节点类型，分别针对 6 种非法非整型与布尔类型（30 项断言）以及 3 种合法正整型（15 项断言）。基线呈现清晰的 30 个断言未捕获异常失败（0 collection error）；打补丁后 45 项全部通过。
  4. 最小参考补丁：`benchmark/validated-candidates/references/pocketflow_retry_count_type.patch` 仅修改 `pocketflow/__init__.py`。
  5. 独立清单：新增 `suite-pocketflow-retry-type.json` 与累积 `suite-six-tasks.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档：编写 `pocketflow-retry-type.md`。
- 本次不做：不修改 Runtime 核心逻辑，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/holdouts/test_pocketflow_max_retries_type.py`
  - `benchmark/validated-candidates/references/pocketflow_retry_count_type.patch`
  - `benchmark/validated-candidates/pocketflow-retry-type.md`
  - `benchmark/validated-candidates/suite-pocketflow-retry-type.json`
  - `benchmark/validated-candidates/suite-six-tasks.json`
  - `docs/tasks/SF-EVAL-01.md`
  - `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `f74d023f93607b8c3268133339a5e532a949898c` | holdout 测试因未抛出预期 `TypeError` 断言失败（30 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=30 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 56 passed, holdout 45 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `pocketflow/__init__.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-tasks.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `PocketFlow` 固定 SHA、许可证与环境依赖（`selfforge-agent-sandbox:latest`）。
2. 编写独立 holdout 测试 `test_pocketflow_max_retries_type.py` 并验证基线 30 个负例断言失败与 15 个正例通过。
3. 制作最小参考补丁 `pocketflow_retry_count_type.patch` 并验证仅修改允许文件。
4. 编写候选描述 `pocketflow-retry-type.md`。
5. 生成 `suite-pocketflow-retry-type.json` 与 `suite-six-tasks.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-pocketflow-retry-type.json` | Docker (`selfforge-agent-sandbox:latest`) | 本地工作树 | validated: 1 case, baseline failed=30, visible passed=56, holdout passed=45 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-tasks.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `796e93b8d8f35308822d034d6a7f2adb1616c3bb6fbf3f7fa0074dff367c07d3` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 343 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复正确区分了 `bool`、非整型与合法正整数。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 MIT License 完整附于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-pocketflow-retry-type-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-agent-sandbox:latest` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-pocketflow-retry-type-task` 合并入 `main`。
