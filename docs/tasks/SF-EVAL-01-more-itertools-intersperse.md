> Integration review supersedes the original completion claims below. See [actual review evidence](../incoming-branches-review.md) and [current backlog](../backlog.md). CLI tests and wait reference controls required corrections before integration.

# SF-EVAL-01：增加一个有参考控制的任务（more-itertools intersperse 非正间隔参数校验收紧）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-more-itertools-intersperse-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `more-itertools` 在固定版本 `4aeb4338ae1407d8da9723e86da99efeb446f719`（MIT License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`more_itertools.intersperse(e, iterable, n=1)` 在非正间隔入参时的校验行为不一致。对于 `n=0`，其显式校验抛出 `ValueError('n must be > 0')`；但对于负数 `n < 0`（如 -1, -2, -5），其绕过了该检查落入 `else` 分支中的 `chunked(iterable, n)`，导致在调用时抛出 `ValueError('n must be at least 0')`，与函数语义和报错契约不一致。
- 本次完成后：
  1. 契约收紧：在 `more_itertools/more.py` 的 `intersperse` 入口统一将 `if n == 0:` 改为 `if n <= 0:`，在调用时将所有非正整数（`n <= 0`）统一抛出 `ValueError('n must be > 0')`。
  2. 保持兼容：保持正整数间隔（`n >= 1`）、空可迭代对象以及不同类型填充元素的行为完全不变。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_more_itertools_intersperse_n.py` 中覆盖 `n in [-1, -2, -5, -10]` 与非空/空输入的 8 项参数化断言、`n=0` 既有行为断言以及正整数保持断言。基线呈现清晰的 8 个断言失败（0 collection error）；打补丁后 11 项全部通过。
  4. 最小参考补丁：`benchmark/validated-candidates/references/more_itertools_intersperse_n.patch` 仅修改 `more_itertools/more.py` 中的 1 行判断。
  5. 独立清单：新增 `suite-more-itertools-intersperse.json` 与累积 `suite-six-more-itertools-intersperse.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档与授权：编写 `more-itertools-intersperse.md`，上游 MIT License 既已完整登记于 `LICENSES.md`。
- 本次不做：不修改 Runtime 核心逻辑，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/holdouts/test_more_itertools_intersperse_n.py`
  - `benchmark/validated-candidates/references/more_itertools_intersperse_n.patch`
  - `benchmark/validated-candidates/more-itertools-intersperse.md`
  - `benchmark/validated-candidates/suite-more-itertools-intersperse.json`
  - `benchmark/validated-candidates/suite-six-more-itertools-intersperse.json`
  - `docs/tasks/SF-EVAL-01.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `4aeb4338ae1407d8da9723e86da99efeb446f719` | holdout 测试因负数报错信息不一致断言失败（8 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=8 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 6 passed, holdout 11 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `more_itertools/more.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-more-itertools-intersperse.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `more-itertools` 固定 SHA、许可证（MIT 已在 `LICENSES.md` 登记）与环境依赖（`selfforge-agent-sandbox:latest`）。
2. 编写独立 holdout 测试 `test_more_itertools_intersperse_n.py` 并验证基线 8 个负例断言失败与 3 个正例通过。
3. 制作最小参考补丁 `more_itertools_intersperse_n.patch` 并验证仅修改 `more_itertools/more.py`。
4. 编写候选描述 `more-itertools-intersperse.md`。
5. 生成 `suite-more-itertools-intersperse.json` 与 `suite-six-more-itertools-intersperse.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-more-itertools-intersperse.json` | Docker (`selfforge-agent-sandbox:latest`) | 本地工作树 | validated: 1 case, baseline failed=8, visible passed=6, holdout passed=11 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-more-itertools-intersperse.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `539eb29676def519c8a869e9d7e1017a9cce4b9879d769e161e15435ac7b4676` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 343 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复正确区分了非正整数尺寸与合法正尺寸。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 MIT License 完整登记于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-more-itertools-intersperse-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-agent-sandbox:latest` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-more-itertools-intersperse-task` 合并入 `main`。
