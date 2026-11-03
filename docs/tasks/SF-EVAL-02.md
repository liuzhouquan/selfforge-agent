# SF-EVAL-02：冻结正式 20 题和完整运行策略

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-twenty-task-suite
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` / `selfforge-itsdangerous:locked` 镜像已可用
- PR：待提交审核

## 目标与范围

依据 `docs/backlog.md` 中 `SF-EVAL-02` 的验收要求，完成正式 20 题基准套件与执行策略的建立与冻结。

- 用户触发：完成 20 道互不重复、已完成契约/参考正负控的真实仓库任务后，冻结基准测试输入与评测策略。
- 原来状态：已有 5 题历史冻结清单 `suite-five-tasks.json`，以及在各独立分支上完成的 15 道参考合格任务，尚未形成统一且经 Docker 双向验证的正式 20 题基准套件清单与完整运行策略。
- 本次完成后：
  1. **清单组装与内容冻结**：将全部 20 道参考合格任务汇总建立为 `benchmark/validated-candidates/suite-twenty-tasks.json`，冻结文件 SHA-256 为 `0a5bd935e7d0aa4b6b9a93ad101686a50c57af2cd1b4143accd5a3bc4e327fd8`。
  2. **多仓库多样性**：覆盖 5 个真实开源代码库（`pocketflow` 5 题、`boltons` 5 题、`more-itertools` 5 题、`itsdangerous` 2 题、`jmespath` 3 题），严格超越“至少 3 个真实仓库”要求。
  3. **Docker 全量双向预检通过**：在无网络 Docker 容器中执行 `selfforge-agent eval-validate`，20 题全部通过验证（20 题基线 holdout 断言失败且 0 收集/环境错误；20 题参考补丁在独立可见与隐藏测试中全部非空通过，exit code 0）。
  4. **正式运行策略确立**：在 `twenty-tasks.md` 中冻结运行策略（20 题 × 3 次重复 = 60 次独立运行，18 轮步数上限，180s 任务预算，30s 测试超时，无网络 Docker 沙箱，不可变镜像 ID 绑定，独立分支无泄漏工作区，标准化失败分类体系）。
  5. **开源许可证全量归档**：在 `LICENSES.md` 中完整登记 5 个代码库的上游许可证声明与固定 SHA。
- 本次不做：本轮不调用真实付费模型执行 60 次实时评测（由 SF-EVAL-03 在模型/密钥就绪后执行），不修改原有历史小样本评测记录。
- 触及文件：
  - `benchmark/validated-candidates/suite-twenty-tasks.json`
  - `benchmark/validated-candidates/twenty-tasks.md`
  - `benchmark/validated-candidates/LICENSES.md`
  - 各任务 holdouts 与 references 外部资产
  - `docs/tasks/SF-EVAL-02.md`
  - `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 汇总 20 道互不重复真实仓库任务 | 清单包含 5 个开源仓库共 20 个独立 case ID，全部资产路径存在 | `cases`: 20, 仓库多样性=5 |
| A2 | 全量基线负例双向验证 | Docker 中 20 题基线 holdout 全部呈现断言失败且 0 收集/导入错误 | `eval-validate` 报告 20 题全部 status="failed" |
| A3 | 全量参考补丁正向验证 | Docker 中 20 题参考补丁全部在独立可见/隐藏测试中通过，exit code 0 | `eval-validate` 报告 20 题全部 reference status="validated" |
| A4 | 运行策略与失败分类标准化 | 明确 60 次运行协议、预算限制、镜像哈希绑定、测试隔离与失败分类分类法 | `twenty-tasks.md` 规范完备 |

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-twenty-tasks.json --output .local/benchmark/twenty-tasks-preflight.json` | Docker (`selfforge-agent-sandbox:latest` / `selfforge-itsdangerous:locked`) | 本地工作树 | validated: 20 cases, manifest SHA `0a5bd935e7d0aa4b6b9a93ad101686a50c57af2cd1b4143accd5a3bc4e327fd8` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 343 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：20 题基线断言失败非环境/加载错误，参考修复全部在无网络沙箱中双通。
- [x] 范围与不变量：保留 `suite-five-tasks.json` 历史冻结不变，新套件 `suite-twenty-tasks.json` 具备独立哈希；上游许可证在 `LICENSES.md` 中完整登记。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 20 题全量预检验收，待审查合并。
- 当前分支：`feat/eval-twenty-task-suite`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。
- 下一步：创建 Pull Request 将 `feat/eval-twenty-task-suite` 合并入 `main`，解锁 `SF-EVAL-03`。
