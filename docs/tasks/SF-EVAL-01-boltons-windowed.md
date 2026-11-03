> Integration review supersedes the original completion claims below. See [actual review evidence](../incoming-branches-review.md) and [current backlog](../backlog.md). CLI tests and wait reference controls required corrections before integration.

# SF-EVAL-01：增加一个有参考控制的任务（boltons windowed 非正尺寸入参校验）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-boltons-windowed-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `boltons` 在固定版本 `d31d821d56c8f7756af9c30a864077f2e5855321`（BSD License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`boltons.iterutils` 中的 `windowed` 与 `windowed_iter` 接受非正窗口尺寸（`size <= 0`）。当 `size=0` 时静默返回空列表或空迭代器；当 `size < 0` 时推迟至内部 `itertools.tee` 失败并报出通用错误，缺少与 `chunked` 一致的显式入参校验。
- 本次完成后：
  1. 契约收紧：在 `boltons/iterutils.py` 的 `windowed_iter` 入口使用 `_validate_positive_int(size, 'window size')`，在调用时显式校验 `size` 必须为正整数；对非正整数（`size <= 0`，如 0, -1, -2, -5）抛出包含 `window size` 的 `ValueError`。
  2. 保持兼容：`windowed` 直接调用 `windowed_iter` 获得同等入参保护；保持正整数尺寸、空输入与各类 fill 填充参数的既有滑动窗口切片行为不变。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_boltons_windowed_size.py` 中覆盖 `windowed` 与 `windowed_iter` 针对 `0, -1, -2, -5`（分别配合默认与显式 fill）的 8 项参数化异常校验断言，以及 1 组正整数/空序列保持断言。基线呈现清晰的 8 个断言失败（0 collection error）；打补丁后 9 项全部通过。
  4. 最小参考补丁：`benchmark/validated-candidates/references/boltons_windowed_size.patch` 仅在 `boltons/iterutils.py` 添加 1 行。
  5. 独立清单：新增 `suite-boltons-windowed.json` 与累积 `suite-six-boltons-windowed.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档与授权：编写 `boltons-windowed.md`，上游 BSD License 既已登记于 `LICENSES.md`。
- 本次不做：不修改 Runtime 核心逻辑，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/holdouts/test_boltons_windowed_size.py`
  - `benchmark/validated-candidates/references/boltons_windowed_size.patch`
  - `benchmark/validated-candidates/boltons-windowed.md`
  - `benchmark/validated-candidates/suite-boltons-windowed.json`
  - `benchmark/validated-candidates/suite-six-boltons-windowed.json`
  - `docs/tasks/SF-EVAL-01.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `d31d821d56c8f7756af9c30a864077f2e5855321` | holdout 测试因缺少非正尺寸校验断言失败（8 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=8 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 58 passed, holdout 9 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `boltons/iterutils.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-boltons-windowed.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `boltons` 固定 SHA、许可证（BSD 3-Clause 已在 `LICENSES.md` 登记）与环境依赖（`selfforge-agent-sandbox:latest`）。
2. 编写独立 holdout 测试 `test_boltons_windowed_size.py` 并验证基线 8 个负例断言失败与 1 个正例通过。
3. 制作最小参考补丁 `boltons_windowed_size.patch` 并验证仅修改 `boltons/iterutils.py`。
4. 编写候选描述 `boltons-windowed.md`。
5. 生成 `suite-boltons-windowed.json` 与 `suite-six-boltons-windowed.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-boltons-windowed.json` | Docker (`selfforge-agent-sandbox:latest`) | 本地工作树 | validated: 1 case, baseline failed=8, visible passed=58, holdout passed=9 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-boltons-windowed.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `d7682f870414e7ce36180b0d4a125d891411938742d31842c9ee5ed3049b5145` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 343 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复正确区分了非正整数尺寸与合法正尺寸。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 BSD License 完整登记于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-boltons-windowed-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-agent-sandbox:latest` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-boltons-windowed-task` 合并入 `main`。
