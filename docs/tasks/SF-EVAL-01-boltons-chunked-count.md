> Integration review supersedes the original completion claims below. See [actual review evidence](../incoming-branches-review.md) and [current backlog](../backlog.md). CLI tests and wait reference controls required corrections before integration.

# SF-EVAL-01：增加一个有参考控制的任务（boltons iterutils.chunked 负数 count 参数校验收紧）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-boltons-chunked-count-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `boltons` 在固定版本 `d31d821d56c8f7756af9c30a864077f2e5855321`（BSD License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`boltons.iterutils.chunked` 接受可选参数 `count` 控制返回的分块数量。但当传入负数 `count < 0` 时，其绕过了参数校验直接落入内部 `itertools.islice(chunk_iter, count)`，抛出标准库的底层错误 `Stop argument for islice() must be None or an integer: 0 <= x <= sys.maxsize.`，缺少与其他参数一致的高层入参校验。
- 本次完成后：
  1. 契约收紧：在 `boltons/iterutils.py` 的 `chunked` 函数中使用模块内既有辅助校验函数 `_validate_positive_int(count, 'chunk count', strictly_positive=False)`，在调用时显式校验 `count >= 0`；对非正整数（`count < 0`）抛出包含 `chunk count` 的 `ValueError`。
  2. 保持兼容：保持 `count=0`（返回空列表）、正整数 `count`、默认 `count=None`（返回全部分块）以及各类 fill 填充参数行为完全不变。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_boltons_chunked_count.py` 中覆盖常规序列、空序列与填充模式下的 10 项参数化负例校验断言，以及合法 count 控制的正例断言。基线呈现清晰的 10 个断言失败（0 collection error）；打补丁后 11 项全部通过。
  4. 最小参考补丁：`benchmark/validated-candidates/references/boltons_chunked_count.patch` 仅在 `boltons/iterutils.py` 添加 2 行校验。
  5. 独立清单：新增 `suite-boltons-chunked-count.json` 与累积 `suite-six-boltons-chunked-count.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档与授权：编写 `boltons-chunked-count.md`，上游 BSD 3-Clause License 既已完整登记于 `LICENSES.md`。
- 本次不做：不修改 Runtime 核心逻辑，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/holdouts/test_boltons_chunked_count.py`
  - `benchmark/validated-candidates/references/boltons_chunked_count.patch`
  - `benchmark/validated-candidates/boltons-chunked-count.md`
  - `benchmark/validated-candidates/suite-boltons-chunked-count.json`
  - `benchmark/validated-candidates/suite-six-boltons-chunked-count.json`
  - `docs/tasks/SF-EVAL-01.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `d31d821d56c8f7756af9c30a864077f2e5855321` | holdout 测试因负数 count 缺少前置校验断言失败（10 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=10 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 58 passed, holdout 11 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `boltons/iterutils.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-boltons-chunked-count.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `boltons` 固定 SHA、许可证（BSD 3-Clause 已在 `LICENSES.md` 登记）与环境依赖（`selfforge-agent-sandbox:latest`）。
2. 编写独立 holdout 测试 `test_boltons_chunked_count.py` 并验证基线 10 个负例断言失败与 1 个正例通过。
3. 制作最小参考补丁 `boltons_chunked_count.patch` 并验证仅修改 `boltons/iterutils.py`。
4. 编写候选描述 `boltons-chunked-count.md`。
5. 生成 `suite-boltons-chunked-count.json` 与 `suite-six-boltons-chunked-count.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-boltons-chunked-count.json` | Docker (`selfforge-agent-sandbox:latest`) | 本地工作树 | validated: 1 case, baseline failed=10, visible passed=58, holdout passed=11 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-boltons-chunked-count.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `313be9eaffff5c5f5c8edf8c49a0ae333b27dc3516a5f0f069c5e1d2278107fb` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 343 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复正确区分了负数分块数与合法正整数/0。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 BSD License 完整登记于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-boltons-chunked-count-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-agent-sandbox:latest` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-boltons-chunked-count-task` 合并入 `main`。
