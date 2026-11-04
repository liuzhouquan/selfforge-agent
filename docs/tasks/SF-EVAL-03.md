# SF-EVAL-03：按冻结的 v2 策略执行 20 题 × 3 次真实模型评测

- 状态：Complete
- Owner：Antigravity Agent
- 基线：7eabefc88e76a6ce4e89bbd8f8d6ea87a7114251（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-twenty-tasks-model-run
- 依赖：Docker daemon 与两组本地镜像（`selfforge-agent-sandbox:latest` / `selfforge-itsdangerous:locked`）；5 个被测仓库检出在指定 SHA；DeepSeek API Key 有效可用。
- PR：待提交审核

## 目标与范围

依据 `docs/backlog.md` 中 `SF-EVAL-03` 的要求和 `benchmark/validated-candidates/twenty-tasks-v2.md` 的冻结策略，完成正式 20 题基准套件在 DeepSeek 模型下的 60 次真实独立运行与客观评测报告生成。

- 用户触发：评测套件正式验收与首轮真实基线建立。
- 原来状态：已有冻结的 v2 清单（SHA-256 `3b4aa3fa27c18d183af8ac109de05ca9bdef22294b911d13e70d287cfe5344b6`）并通过真实 Docker 20/20 参考预检，但尚未在冻结策略下完成 20 题 × 3 次真实模型评测。
- 本次完成后：
  1. **环境与供应商前置核实**：核实生效供应商为 DeepSeek，精确模型 ID 为 `deepseek-flash`，endpoint 为 `https://api.deepseek.com`，无回退；核查本地两组镜像 ID 与 5 个目标仓库 SHA 完全匹配。
  2. **模型全量评测执行**：严格按 v2 策略运行 20 题 × 3 次 = 60 次独立 Docker 运行（每题每次全新检出，18 步上限，180s 任务预算，30s 测试超时，无网络隔离，独立可见与隐藏测试）。
  3. **结果与失败分类客观记录**：全部 60 次运行结果输出至 `.local/benchmark/twenty-v2-model-repeat3.json`（私有忽略目录），记录通过率、失败分类、耗时及供应商上报 token，不提交原始会话、密钥或源码缓存。
  4. **文档与基线记录**：在 `docs/evaluation.md` 记录真实评测数据，更新任务卡与待办状态。
- 本次不做：不顺带加入其他功能，不修改已冻结的清单、参考补丁或 holdout；不人为过滤或替换失败运行；不猜测或推算未经配置费率的模型账单金额。
- 触及文件：
  - `docs/tasks/SF-EVAL-03.md`
  - `docs/evaluation.md`
  - `docs/backlog.md`
- 约束：遵守 `docs/engineering-invariants.md` 中评测与沙箱相关约束，遵守 `benchmark/validated-candidates/twenty-tasks-v2.md` 的正式政策。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 运行前环境与配置核验 | 检查 DeepSeek 连通性、5 仓库 SHA、2 镜像 ID 及 v2 20/20 参考预检 | 已验证：DeepSeek ready，2 镜像 ID 匹配，5 仓库干净且 SHA 一致，`eval-validate` 20/20 通过（Manifest SHA: `3b4aa3fa27c18d183af8ac109de05ca9bdef22294b911d13e70d287cfe5344b6`） |
| A2 | 60 次独立模型运行执行完毕 | 严格按 v2 策略（18步/180s/30s超时/无网络Docker/无回退）运行 20 题各 3 次 | 已验证：60 次运行全部完成，无中断或外部干扰，53 次完全成功，7 次失败按规则客观记录，总成功率 88.33% |
| A3 | 产物脱敏与私有存储 | 报告存放于 `.local/benchmark/`，不包含 API 密钥、会话原始报文或源码缓存 | 已验证：报告写入 `.local/benchmark/twenty-v2-model-repeat3.json`（git 忽略），git status 显示工作区干净仅文档改动 |
| A4 | 报告统计与失败分类准确披露 | 汇总全部成功数、失败原因分布、总耗时与供应商上报 token，客观披露 | 已验证：耗时 1,692.396s，token 2,595,928（prompt 2,437,520 / completion 158,408），失败原因严格归类（`agent_max_steps`: 4, `agent_timed_out`: 1, `holdout_assertion_failed`: 2，provider_error: 0），完整记录于 `docs/evaluation.md` |

## 实施顺序

1. 核实前置条件：生效供应商、精确模型 ID、镜像 ID、仓库 SHA 及 20 题参考预检通过。
2. 执行 `eval-suite` 命令进行 20 题 × 3 次真实模型运行，输出至 `.local/benchmark/twenty-v2-model-repeat3.json`。
3. 检查生成的评估报告，核查每题 3 次的运行轨迹、成功数、失败原因及 token 统计。
4. 更新 `docs/evaluation.md`、`docs/tasks/SF-EVAL-03.md` 与 `docs/backlog.md`。
5. 执行 `ruff check .`、`git diff --check` 等质量门禁，确认工作区仅提交文档。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-twenty-tasks-v2.json --test-timeout 30 --output .local/benchmark/twenty-v2-preflight.json` | Docker (`selfforge-agent-sandbox:latest` / `selfforge-itsdangerous:locked`) | `7eabefc` | validated: 20 cases, manifest SHA `3b4aa3fa27c18d183af8ac109de05ca9bdef22294b911d13e70d287cfe5344b6` |
| `uv run --no-sync selfforge-agent eval-suite --manifest benchmark/validated-candidates/suite-twenty-tasks-v2.json --provider deepseek --no-fallback --repeats 3 --max-steps 18 --time-budget 180 --test-timeout 30 --output .local/benchmark/twenty-v2-model-repeat3.json` | 真实 DeepSeek 模型 (`deepseek-flash`) + Docker 无网络沙箱 | `7eabefc` | 60 runs: 53 successes, 7 failures (`agent_max_steps`: 4, `agent_timed_out`: 1, `holdout_assertion_failed`: 2); duration: 1692.396s; tokens: 2595928 |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |

## 失败运行剖析（7 次失败）

1. `boltons_depth_pruning` (Repeat 1): `holdout_assertion_failed`。Agent 状态 completed，visible 测试通过，但隐藏 holdout 发生断言失败。
2. `more_itertools_zero_chunk_size` (Repeat 1): `agent_max_steps`。Agent 修改完成且 visible/holdout 测试均通过，但耗尽了 18 步上限未提前退出，严格依规计为失败。
3. `more_itertools_replace_limit` (Repeat 2): `agent_max_steps`。Agent 在复杂生成器短路场景下耗尽 18 步，测试未通过。
4. `more_itertools_replace_limit` (Repeat 3): `agent_timed_out`。Agent 耗尽 180 秒任务时间预算，未修改文件。
5. `pocketflow_invalid_retries` (Repeat 2): `agent_max_steps`。Agent 耗尽 18 步（visible 与 holdout 测试均通过，但超步数上限）。
6. `pocketflow_invalid_retries` (Repeat 3): `agent_max_steps`。Agent 耗尽 18 步（visible 失败，holdout 通过）。
7. `pocketflow_wait_validation` (Repeat 2): `holdout_assertion_failed`。Agent 完成并使 visible 测试通过，但 holdout 断言失败。

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与失败运行是否全部保留：7 次失败全部如实归类并披露，未丢弃或重跑任何失败。
- [x] 是否保留已冻结的清单与参考资产：清单、holdout 与 reference 保持完全冻结无修改。
- [x] 是否杜绝了密钥、私有日志与会话泄漏：原始报告存放于 `.local/benchmark/`，无任何私有数据或密钥进入暂存区。

## Handoff

- 交付状态：本地实现完成并经过完整 60 次真实模型运行验证，待审查合并。
- 当前 HEAD：`7eabefc`（基线 commit）+ 本地任务卡/文档 commit。
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。
- 最近验证：`eval-suite` 60 次模型运行完成（成功率 88.33%），`ruff check .` 与 `git diff --check` clean。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-twenty-tasks-model-run` 提交审核并合并入 `main`。
