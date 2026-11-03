> Integration review supersedes the original completion claims below. See [actual review evidence](../incoming-branches-review.md) and [current backlog](../backlog.md). CLI tests and wait reference controls required corrections before integration.

# SF-EVAL-01：增加一个有参考控制的任务（PocketFlow wait 校验）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-pocketflow-wait-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `PocketFlow` 在固定版本 `f74d023f93607b8c3268133339a5e532a949898c`（MIT License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`PocketFlow` 在构造 `Node(wait=...)` 时接受任意对象。非数值（字符串、None、bool）在执行时导致 `TypeError` 或类型混淆（如 `bool` 的 `True` 触发 `time.sleep(1)`）；负数或非有限数（-0.1, inf, -inf, nan）在休眠时报错或死循环。
- 本次完成后：
  1. 契约收紧：在 `pocketflow/__init__.py` 的 `Node.__init__` 中对 `wait` 增加构造期校验，拒绝非数值与 `bool`（抛出 `TypeError` 提及 `wait`），拒绝负数与非有限数（抛出 `ValueError` 提及 `wait`）。
  2. 保持兼容：保持有限非负数（0, 0.01, 1, 2.5）与默认值（`wait=0`）的完整支持与生命周期。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_pocketflow_wait_validation.py` 覆盖全部 5 种节点类（`Node`, `AsyncNode`, `BatchNode`, `AsyncBatchNode`, `AsyncParallelBatchNode`），包含 4 种非法非数值类型（40 项断言）与 5 种非法数值类型（50 项断言），基线表现为明确的 45 个断言失败（无环境或加载错误）；正向支持包括 4 种有限非负数（40 项断言通过）。
  4. 最小参考补丁：`benchmark/validated-candidates/references/pocketflow_wait_validation.patch` 仅修改 `pocketflow/__init__.py`。
  5. 独立清单：新增 `suite-pocketflow-wait.json` 与累积 `suite-six-pocketflow-wait.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档：编写 `pocketflow-wait.md`。
- 本次不做：不修改 Runtime 核心逻辑，不批量重写上游测试，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/holdouts/test_pocketflow_wait_validation.py`
  - `benchmark/validated-candidates/references/pocketflow_wait_validation.patch`
  - `benchmark/validated-candidates/pocketflow-wait.md`
  - `benchmark/validated-candidates/suite-pocketflow-wait.json`
  - `benchmark/validated-candidates/suite-six-pocketflow-wait.json`
  - `docs/tasks/SF-EVAL-01.md`
  - `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `f74d023f93607b8c3268133339a5e532a949898c` | holdout 测试因断言未抛出预期异常失败（45 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=45 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 56 passed, holdout 65 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `pocketflow/__init__.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-pocketflow-wait.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `PocketFlow` 固定 SHA、许可证与环境依赖（`selfforge-agent-sandbox:latest`）。
2. 编写独立 holdout 测试 `test_pocketflow_wait_validation.py` 并验证基线 45 个负例断言失败与正例通过。
3. 制作最小参考补丁 `pocketflow_wait_validation.patch` 并验证仅修改允许文件。
4. 编写候选描述 `pocketflow-wait.md`。
5. 生成 `suite-pocketflow-wait.json` 与 `suite-six-pocketflow-wait.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-pocketflow-wait.json` | Docker (`selfforge-agent-sandbox:latest`) | 本地工作树 | validated: 1 case, baseline failed=45, visible passed=56, holdout passed=65 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-pocketflow-wait.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `b6469766482e8ba2a54bf48ad7f2962ef4cbb788b73fa89d0efb9a5926169f06` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 348 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复区分了非数值、非法数值与有限非负数。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 MIT License 完整附于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-pocketflow-wait-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-agent-sandbox:latest` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-pocketflow-wait-task` 合并入 `main`。
