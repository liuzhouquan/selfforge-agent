> Integration review supersedes the original completion claims below. See [actual review evidence](../incoming-branches-review.md) and [current backlog](../backlog.md). CLI tests and wait reference controls required corrections before integration.

# SF-EVAL-01：增加一个有参考控制的任务（PocketFlow BaseNode.next 动作字符串与后继节点类型校验）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-pocketflow-transition-validation-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `pocketflow` 在固定版本 `f74d023f93607b8c3268133339a5e532a949898c`（MIT License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`pocketflow.BaseNode.__sub__` 在定义条件转移时显式要求 action 必须为字符串（`if isinstance(action, str): ...; raise TypeError("Action must be a string")`）。但 `BaseNode.next(node, action="default")` 却没有对 `action` 和 `node` 的类型进行任何校验：传入非字符串 `action`（如数字、列表、None 等）会被直接写入 `self.successors` 字典中；传入非 `BaseNode` 实例（如字符串、数字等）作为后继节点也完全不报错，直到 Flow 编排执行时在 `copy.copy(node)` 或 `node.set_params(...)` 中崩溃。
- 本次完成后：
  1. 契约收紧：在 `BaseNode.next` 入口增加显式类型校验：
     - 若 `action` 不是 `str`，抛出 `TypeError("Action must be a string")`，与 `__sub__` 契约严格对齐；
     - 若 `node` 不是 `BaseNode` 实例，抛出 `TypeError("Successor must be an instance of BaseNode")`。
  2. 保持兼容：保持正向默认转换（`>>`）、条件转换（`- "action" >>`）、覆盖告警与返回值链式调用等既有语义不变。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_pocketflow_transition_validation.py` 中覆盖 `next` 针对非法 action 类型（5 项）、非法 node 类型（5 项）、直接 `>>` 非法后继以及条件转移 `>>` 非法后继的 12 项负例断言，以及多节点拓扑连线的正例保持断言。基线呈现清晰的 12 个断言失败（0 collection error）；打补丁后 13 项全部通过。
  4. 最小参考补丁：`benchmark/validated-candidates/references/pocketflow_transition_validation.patch` 仅在 `pocketflow/__init__.py` 的 `BaseNode.next` 添加 2 行校验。
  5. 独立清单：新增 `suite-pocketflow-transition.json` 与累积 `suite-six-pocketflow-transition-validation.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档与授权：编写 `pocketflow-transition.md`，上游 MIT License 既已完整登记于 `LICENSES.md`。
- 本次不做：不修改 Runtime 核心逻辑，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/holdouts/test_pocketflow_transition_validation.py`
  - `benchmark/validated-candidates/references/pocketflow_transition_validation.patch`
  - `benchmark/validated-candidates/pocketflow-transition.md`
  - `benchmark/validated-candidates/suite-pocketflow-transition.json`
  - `benchmark/validated-candidates/suite-six-pocketflow-transition-validation.json`
  - `docs/tasks/SF-EVAL-01.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `f74d023f93607b8c3268133339a5e532a949898c` | holdout 测试因非法 action/node 未被拒绝断言失败（12 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=12 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 56 passed, holdout 13 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `pocketflow/__init__.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-pocketflow-transition-validation.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `pocketflow` 固定 SHA、许可证（MIT 已在 `LICENSES.md` 登记）与环境依赖（`selfforge-agent-sandbox:latest`）。
2. 编写独立 holdout 测试 `test_pocketflow_transition_validation.py` 并验证基线 12 个负例断言失败与 1 个正例通过。
3. 制作最小参考补丁 `pocketflow_transition_validation.patch` 并验证仅修改 `pocketflow/__init__.py`。
4. 编写候选描述 `pocketflow-transition.md`。
5. 生成 `suite-pocketflow-transition.json` 与 `suite-six-pocketflow-transition-validation.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-pocketflow-transition.json` | Docker (`selfforge-agent-sandbox:latest`) | 本地工作树 | validated: 1 case, baseline failed=12, visible passed=56, holdout passed=13 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-pocketflow-transition-validation.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `7c1776c45dd5bab1b9edbf6a500044d5660b937aa6f6926cffe9bc1906c76aba` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 343 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复正确区分了非法参数与合法节点拓扑。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 MIT License 完整登记于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-pocketflow-transition-validation-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-agent-sandbox:latest` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-pocketflow-transition-validation-task` 合并入 `main`。
