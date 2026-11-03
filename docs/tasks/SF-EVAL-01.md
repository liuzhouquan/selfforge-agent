# SF-EVAL-01：增加一个有参考控制的任务（boltons negative max_depth 校验）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-boltons-max-depth-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `boltons` 在固定版本 `d31d821d56c8f7756af9c30a864077f2e5855321`（BSD License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`boltons.fileutils.iter_find_files` 的 `max_depth` 参数文档明确说明 `0` 表示仅搜索指定目录，`1` 表示包含一层子目录。然而当传入负数时（如 `max_depth=-1`），函数既不报错也未给出警告，而是由于内部深度判断静默过滤导致不产生任何匹配。
- 本次完成后：
  1. 契约收紧：在 `boltons/fileutils.py` 的 `iter_find_files` 中增加对 `max_depth < 0` 的显式校验，抛出提及 `max_depth` 的 `ValueError`。
  2. 保持兼容：保持 `max_depth=0`、`max_depth=1` 以及默认值 `max_depth=None` 的正常遍历逻辑。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_boltons_negative_max_depth.py` 中覆盖负数深度（-1, -2, -10）必须抛出 `ValueError(match="max_depth")`，以及 0、正数与 None 的非回归断言。基线测试呈现清晰的 3 项断言失败（0 环境/加载错误）；打补丁后 5 项全部通过。
  4. 最小参考补丁：`benchmark/validated-candidates/references/boltons_negative_max_depth.patch` 仅修改 `boltons/fileutils.py`。
  5. 独立清单：新增 `suite-boltons-max-depth.json` 与累积 `suite-six-tasks.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档：编写 `boltons-max-depth.md`。
- 本次不做：不修改 Runtime 核心逻辑，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/holdouts/test_boltons_negative_max_depth.py`
  - `benchmark/validated-candidates/references/boltons_negative_max_depth.patch`
  - `benchmark/validated-candidates/boltons-max-depth.md`
  - `benchmark/validated-candidates/suite-boltons-max-depth.json`
  - `benchmark/validated-candidates/suite-six-tasks.json`
  - `docs/tasks/SF-EVAL-01.md`
  - `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `d31d821d56c8f7756af9c30a864077f2e5855321` | holdout 测试因未抛出预期异常而断言失败（3 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=3 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 13 passed, holdout 5 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `boltons/fileutils.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-tasks.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `boltons` 固定 SHA、许可证与环境依赖（`selfforge-agent-sandbox:latest`）。
2. 编写独立 holdout 测试 `test_boltons_negative_max_depth.py` 并验证基线 3 个负例断言失败与 2 个正例通过。
3. 制作最小参考补丁 `boltons_negative_max_depth.patch` 并验证仅修改允许文件。
4. 编写候选描述 `boltons-max-depth.md`。
5. 生成 `suite-boltons-max-depth.json` 与 `suite-six-tasks.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-boltons-max-depth.json` | Docker (`selfforge-agent-sandbox:latest`) | 本地工作树 | validated: 1 case, baseline failed=3, visible passed=13, holdout passed=5 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-tasks.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `41aa2c2c7563c65a25c5334b5121bd2d56a4dfd96db3f0eb337062e5d549b6ca` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 343 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复正确区分了非法负深度与合法非负深度。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 BSD License 完整附于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-boltons-max-depth-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-agent-sandbox:latest` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-boltons-max-depth-task` 合并入 `main`。
