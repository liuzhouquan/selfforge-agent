> Integration review supersedes the original completion claims below. See [actual review evidence](../incoming-branches-review.md) and [current backlog](../backlog.md). CLI tests and wait reference controls required corrections before integration.

# SF-EVAL-01：增加一个有参考控制的任务（jmespath replace 函数扩展）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-jmespath-replace-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `jmespath` 在固定版本 `2812594e69d43098ef60f81f4efc404c071b0418`（MIT License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`jmespath` 的内置函数表中包含了 `join`, `starts_with`, `ends_with`, `contains`, `reverse` 等字符串与数组操作，但缺少常规文本处理所需的子串替换函数 `replace`。调用 `replace(...)` 在原始基线上抛出 `UnknownFunctionError`。
- 本次完成后：
  1. 机制扩展：在 `jmespath/functions.py` 的 `Functions` 类中注册 `replace(subject, search, replacement)` 内置函数，要求 3 个入参均为字符串；将 `subject` 中所有出现的 `search` 子串替换为 `replacement`。
  2. 保持兼容：自动继承并保持 `@signature` 的标准类型检查（传入非字符串引发 `JMESPathTypeError`）与参数数量检查（缺少或多于 3 个参数引发 `ArityError`），保持原有内置函数功能不受影响。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_jmespath_replace_function.py` 中覆盖普通子串替换、全局多处替换、字符消除、空搜索串插入、管道/嵌套表达式结合（8 组参数化用例与 1 项组合断言）、9 组非法参数类型校验、参数元数错误断言以及既有函数非回归控制。基线呈现清晰的 19 个断言失败（0 collection error）；打补丁后 20 项全部通过。
  4. 最小参考补丁：`benchmark/validated-candidates/references/jmespath_replace_function.patch` 仅修改 `jmespath/functions.py`。
  5. 独立清单：新增 `suite-jmespath-replace.json` 与累积 `suite-six-jmespath-replace.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档与授权：编写 `jmespath-replace.md` 并在 `LICENSES.md` 中完整登记上游 MIT License。
- 本次不做：不修改 Runtime 核心逻辑，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/LICENSES.md`
  - `benchmark/validated-candidates/holdouts/test_jmespath_replace_function.py`
  - `benchmark/validated-candidates/references/jmespath_replace_function.patch`
  - `benchmark/validated-candidates/jmespath-replace.md`
  - `benchmark/validated-candidates/suite-jmespath-replace.json`
  - `benchmark/validated-candidates/suite-six-jmespath-replace.json`
  - `docs/tasks/SF-EVAL-01.md`
  - `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `2812594e69d43098ef60f81f4efc404c071b0418` | holdout 测试因 `replace()` 未注册断言失败（19 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=19 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 5 passed, holdout 20 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `jmespath/functions.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-jmespath-replace.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `jmespath` 固定 SHA、许可证与环境依赖（`selfforge-agent-sandbox:latest`）。
2. 在 `LICENSES.md` 中补齐 `jmespath` 的开源许可证登记。
3. 编写独立 holdout 测试 `test_jmespath_replace_function.py` 并验证基线 19 个负例断言失败与 1 个正例通过。
4. 制作最小参考补丁 `jmespath_replace_function.patch` 并验证仅修改允许文件。
5. 编写候选描述 `jmespath-replace.md`。
6. 生成 `suite-jmespath-replace.json` 与 `suite-six-jmespath-replace.json`，运行 `eval-validate`。
7. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-jmespath-replace.json` | Docker (`selfforge-agent-sandbox:latest`) | 本地工作树 | validated: 1 case, baseline failed=19, visible passed=5, holdout passed=20 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-jmespath-replace.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `16a98dc1dad06b05a643635f0da71760c37a48eab3f641acbdcb9756838f6b54` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 343 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复正确区分了非字符串参数、参数元数错误与合法子串替换。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 MIT License 完整附于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-jmespath-replace-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-agent-sandbox:latest` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-jmespath-replace-task` 合并入 `main`。
