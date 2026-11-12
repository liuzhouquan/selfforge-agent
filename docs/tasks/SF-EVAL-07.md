# SF-EVAL-07：增加第 2 道真实仓库多文件修改任务的参考正负控（Boltons 映射跨模块联动）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：`4ecd6a5b6727fb2e2938cf15c929a7001ae4f849`（`origin/main`）
- 分支 / 所属 worktree：`feat/eval-boltons-multi-file-task`
- 依赖：无（基于已合并的 PR #47 / main）
- PR：[#52](https://github.com/liuzhouquan/selfforge-agent/pull/52)

## 目标与范围

在已有 20 道单文件任务和 SF-EVAL-06（`jmespath` 多文件任务）的基础上，扩展增加第 2 道真实仓库多文件协同修改（跨模块联动）的基准评测任务，针对 `boltons` 核心映射与递归遍历模块联动契约，附带严格的独立 holdout 测试、最小参考补丁及参考正负控，并在独立新清单中交付，保持历史 20 题清单与冻结成绩不变。

原来发生什么：
1. 现有评测任务集大多限制在单一源码文件内（如单个验证函数或小扩展），跨模块协同用例较少。
2. 在 `boltons` 中，`boltons.iterutils.remap` 在遍历非标准映射时存在跨模块兼容缺陷：
   - 当遇到不可变字典 `FrozenDict`（位于 `boltons.dictutils`）时，`remap` 的 `default_exit` 机械调用原地 `.update()`，直接崩溃抛出 `TypeError: FrozenDict object is immutable`；同时 `FrozenDict.copy()` 错误继承 `dict.copy()` 返回可变普通字典。
   - 当遇到多值保序映射 `OrderedMultiDict`（位于 `boltons.dictutils`）时，`default_enter` 使用常规 `ItemsView(value)`，丢弃了重复键的历史值；`default_exit` 的普通 `.update()` 进一步将每个键压缩至单个值。

本次完成后应观察到：
1. 任务契约与真实仓库：
   - 选取真实仓库 `boltons`（BSD 许可证，固定修订 `d31d821d56c8f7756af9c30a864077f2e5855321`）。
   - 任务定义：在 `boltons.iterutils.remap` 中支持 `boltons.dictutils` 的核心映射类型（`FrozenDict` 与 `OrderedMultiDict`），在保持不可变性与多值保留的前提下完成深层树状遍历与重映射。
2. 多文件跨模块联动修改（`allowed_changes` 涉及 2 个源码模块与 2 个测试文件）：
   - `boltons/dictutils.py`：在 `FrozenDict` 中添加 `copy(self)` 返回 `self`，修复继承导致的破坏不可变性问题。
   - `boltons/iterutils.py`：
     - 在 `default_enter` 中，对具有多值属性的映射（`items(multi=True)` 且具有 `addlist`）保留所有重复键项遍历。
     - 在 `default_exit` 中，对支持 `update_extend` 的多值映射调用 `update_extend`；对一般映射更新时若捕获不可变类型错误 `(AttributeError, TypeError)`，则安全回退为通过类型构造器 `new_parent.__class__(new_items)` 实例化。
   - 测试文件：`tests/test_dictutils.py` 与 `tests/test_iterutils.py` 补充回归测试。
3. 严格的参考正负控与独立性：
   - 独立 holdout 测试用例：`benchmark/validated-candidates/holdouts/test_boltons_remap_mappings.py`。
   - 负控证明：原始未修改基线在 holdout 测试上因断言失败与 TypeError 严格失败（5 failed，0 collection errors）。
   - 正控证明：打入多文件参考补丁（`benchmark/validated-candidates/references/boltons_remap_mappings.patch`）后，visible（60 passed）与 holdout（5 passed）全部通过。
   - 独立新清单：`benchmark/validated-candidates/suite-boltons-remap-mappings.json`，历史清单 `suite-twenty-tasks-v2.json` 保持完全冻结。
4. 运行 `selfforge-agent eval-validate` 资格预检状态为 `validated`。

本次不做：
- 修改历史 20 题清单或篡改历史 53/60 成绩。
- 引入未授权模型调用。

预计触及：
- `benchmark/validated-candidates/suite-boltons-remap-mappings.json`
- `benchmark/validated-candidates/boltons-remap-mappings.md`
- `benchmark/validated-candidates/holdouts/test_boltons_remap_mappings.py`
- `benchmark/validated-candidates/references/boltons_remap_mappings.patch`
- `tests/test_benchmark_boltons_multi_file.py`
- `docs/tasks/SF-EVAL-07.md`
- `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 未打补丁的原始 commit 运行 holdout | holdout 因断言与类型异常严格失败（5 failed），无环境收集错误 | 通过 |
| A2 | 打入多文件参考补丁后运行 visible 与 holdout | visible（60 passed）与 holdout（5 passed）测试均全量 passed | 通过 |
| A3 | 参考补丁修改范围 | 补丁严格修改跨模块源码文件（`dictutils.py`、`iterutils.py`）及对应测试，均在 `allowed_changes` 内 | 通过 |
| A4 | 运行 `selfforge-agent eval-validate` 资格预检 | 独立 manifest 预检状态输出 `validated`，包含明确 baseline/reference 报告 | 通过 |
| A5 | 历史资产不变性检查 | `suite-twenty-tasks-v2.json` 及其 assets hash 完全保持一致未变 | 通过 |

## 实施顺序

1. 编写独立 holdout 测试用例 `holdouts/test_boltons_remap_mappings.py`。
2. 验证基线提交在 holdout 下失败（5 处断言/类型失败负控）。
3. 构建跨模块最小参考补丁 `references/boltons_remap_mappings.patch`。
4. 验证打入补丁后 visible 60 项与 holdout 5 项全部通过（正控）。
5. 编写独立 manifest `suite-boltons-remap-mappings.json` 与说明文档 `boltons-remap-mappings.md`。
6. 编写回归与结构集成测试 `tests/test_benchmark_boltons_multi_file.py`。
7. 运行 `eval-validate`、ruff 与全量测试套件，更新任务卡与 backlog，提交并提 PR。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | 全部通过（All checks passed!） |
| `git diff --check` | Host | 本分支 | 全部通过（无空白/换行缺陷） |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-boltons-remap-mappings.json --sandbox host --trust-local` | Host | 本分支 | 通过（status=validated, cases=1, baseline failed=5, reference visible=60 holdout=5） |
| `uv run --no-sync pytest -q tests/test_benchmark_boltons_multi_file.py` | Host | 本分支 | 3 passed in 6.75s |
| `uv run --no-sync pytest -q` | Host | 本分支 | 474 passed, 11 skipped in 276.10s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed in 40.52s |

## Review

- 严格遵循 TDD：基线无补丁时 holdout 确认 5 处断言与类型失败（负控）；多文件补丁打入后 visible 60 通过、holdout 5 通过。
- 跨模块修改：同时触及 `boltons/dictutils.py` 与 `boltons/iterutils.py`，实现多值保序映射和不可变映射在递归树状重映射中的支持。
- 历史资产保持冻结：`suite-twenty-tasks-v2.json` 及其资产哈希未受任何影响。

## Handoff

- 交付状态：实施与全量验证已完成，等待提交并创建 PR。
- 当前分支：`feat/eval-boltons-multi-file-task`。主分支 `main` 保持纯净。

