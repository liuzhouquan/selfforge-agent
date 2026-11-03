> Integration review supersedes the original completion claims below. See [actual review evidence](../incoming-branches-review.md) and [current backlog](../backlog.md). CLI tests and wait reference controls required corrections before integration.

# SF-EVAL-01：增加一个有参考控制的任务（jmespath lower 内置函数注册）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-jmespath-lower-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `jmespath` 在固定版本 `2812594e69d43098ef60f81f4efc404c071b0418`（MIT License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`jmespath` 包含了常用字符串与集合内置函数（如 `join`、`starts_with`、`ends_with`、`reverse`），但未提供将字符串转为小写的内置函数 `lower(string)`。当表达式中调用 `lower(...)` 时直接抛出 `jmespath.exceptions.UnknownFunctionError: Unknown function: lower()`。
- 本次完成后：
  1. 契约定义：在 `jmespath/functions.py` 的 `Functions` 类中注册内置函数 `_func_lower(self, arg)`，签名要求单个 `string` 参数（`@signature({'types': ['string']})`），通过 `arg.lower()` 返回全小写字符串。
  2. 保持兼容：保持现存内置函数行为与元类自动函数注册机制完全不变；保留既有的参数数量校验（非 1 个参数抛出 `ArityError`）与类型校验（非字符串类型抛出 `JMESPathTypeError`）。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_jmespath_lower_function.py` 中覆盖全大写、混合大小写、Unicode 特殊字符（如德语、希腊语、法语重音字符）、空串、嵌套投影与管道表达式的转换，以及 8 项参数化非法类型断言和 3 项参数数量错误断言，并附加既有函数回归控制断言。基线呈现清晰的 19 个断言失败（0 collection error）；打补丁后 20 项全部通过。
  4. 最小参考补丁：`benchmark/validated-candidates/references/jmespath_lower_function.patch` 仅在 `jmespath/functions.py` 添加 4 行代码注册函数，并在 `tests/test_functions.py` 中添加对应的回归测试方法。
  5. 独立清单：新增 `suite-jmespath-lower.json` 与累积 `suite-six-jmespath-lower.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档与授权：编写 `jmespath-lower.md`，上游 MIT License 完整登记于 `LICENSES.md`。
- 本次不做：不修改 Runtime 核心逻辑，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/LICENSES.md`
  - `benchmark/validated-candidates/holdouts/test_jmespath_lower_function.py`
  - `benchmark/validated-candidates/references/jmespath_lower_function.patch`
  - `benchmark/validated-candidates/jmespath-lower.md`
  - `benchmark/validated-candidates/suite-jmespath-lower.json`
  - `benchmark/validated-candidates/suite-six-jmespath-lower.json`
  - `docs/tasks/SF-EVAL-01.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `2812594e69d43098ef60f81f4efc404c071b0418` | holdout 测试因缺失 `lower` 函数抛出 `UnknownFunctionError` 导致断言失败（19 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=19 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 6 passed, holdout 20 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `jmespath/functions.py` 与 `tests/test_functions.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-jmespath-lower.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `jmespath` 固定 SHA、许可证（MIT License 登记于 `LICENSES.md`）与环境依赖（`selfforge-agent-sandbox:latest`）。
2. 编写独立 holdout 测试 `test_jmespath_lower_function.py` 并验证基线 19 个断言失败与 1 个对照正例通过。
3. 制作最小参考补丁 `jmespath_lower_function.patch` 并验证仅修改 `jmespath/functions.py` 与 `tests/test_functions.py`。
4. 编写候选描述 `jmespath-lower.md`。
5. 生成 `suite-jmespath-lower.json` 与 `suite-six-jmespath-lower.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-jmespath-lower.json` | Docker (`selfforge-agent-sandbox:latest`) | 本地工作树 | validated: 1 case, baseline failed=19, visible passed=6, holdout passed=20 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-jmespath-lower.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `51e710b9b4b623d265ddb0973ac8f209130884e7263d6d12e25ab5ec969a4e45` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 343 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复正确支持各字符集的小写转换与类型/参数量校验。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 MIT License 完整登记于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-jmespath-lower-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-agent-sandbox:latest` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-jmespath-lower-task` 合并入 `main`。

