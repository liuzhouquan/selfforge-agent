# SF-EVAL-06：增加1道真实仓库多文件修改任务的参考正负控

> 已核对：PR #48 已合并，merge SHA `9e791cdd74920f57fa9a65347af613ba17ec51f3`。以下规格/测试是历史记录；当前开发以 [SDD](../SDD.md) 为准，不重新领取本任务。

- 状态：Merged（历史任务）
- Owner：Antigravity Agent
- 基线：`4ecd6a5d605c7f3b5b9140f00cd1a5c3f6360c7a`（`origin/main`）
- 分支 / 所属 worktree：`feat/eval-multi-file-task`
- 依赖：无（基于已合并的 PR #47 / main）
- PR：[#48](https://github.com/liuzhouquan/selfforge-agent/pull/48)

## 目标与范围

在现有 20 道单文件（多为单一模块参数校验或小扩展）评测任务集的基础上，扩展增加 1 道真实仓库多文件协同修改（跨模块联动）的基准评测任务，附带严格的参考正负控，并在独立新清单中交付，绝不污染或重写历史 20 题清单与冻结成绩。

原来发生什么：
1. 现有 20 题候选集任务（`suite-twenty-tasks-v2.json`）修改范围均仅限单个源码文件，缺乏跨模块多文件协同契约。
2. 缺乏针对跨文件联动修改（如异常类定义、函数注册与调用、AST 解释器选项贯穿）的测试与参考基准。

本次完成后应观察到：
1. 任务契约与真实仓库：
   - 选取真实仓库 `jmespath`（MIT 许可证，固定修订 `2812594e69d43098ef60f81f4efc404c071b0418`）。
   - 任务定义：实现未知函数调用智能诊断与建议机制（Did-You-Mean），并在 Options 中提供可配置开关控制。
2. 多文件跨模块联动修改（`allowed_changes` 涉及 3 个源码文件）：
   - `jmespath/exceptions.py`：扩展 `UnknownFunctionError`，记录 `function_name` 与可选的 `similar` 建议，并格式化 `"Unknown function: <name>(), did you mean: <similar>()?"`。
   - `jmespath/functions.py`：在 `call_function` 中利用 `difflib.get_close_matches` 对未知函数计算最接近的内置函数候选并传入异常。
   - `jmespath/visitor.py`：在 `Options` 增加 `suggest_functions=True` 配置项，并在 `TreeInterpreter.visit_function_expression` 中按选项受控启用/禁用建议。
   - 测试文件：`tests/test_functions.py` 补充回归测试。
3. 严格的参考正负控与独立性：
   - 独立 holdout 测试用例：`benchmark/validated-candidates/holdouts/test_jmespath_unknown_function.py`。
   - 负控证明：原始未修改基线在 holdout 测试上因断言失败而严格失败（并非环境错误）。
   - 正控证明：应用多文件最小参考补丁（`benchmark/validated-candidates/references/jmespath_unknown_function.patch`）后，visible 与 holdout 均通过。
   - 独立新清单：`benchmark/validated-candidates/suite-jmespath-unknown-function.json`，保留已有历史清单（`suite-twenty-tasks-v2.json` 等）完全冻结不变。
4. 运行 `selfforge-agent eval-validate` 资格验证通过。

本次不做：
- 修改历史 20 题清单或篡改历史 53/60 成绩。
- 引入付费模型调用。

预计触及：
- `benchmark/validated-candidates/suite-jmespath-unknown-function.json`
- `benchmark/validated-candidates/jmespath-unknown-function.md`
- `benchmark/validated-candidates/holdouts/test_jmespath_unknown_function.py`
- `benchmark/validated-candidates/references/jmespath_unknown_function.patch`
- `tests/test_benchmark_multi_file.py`
- `docs/tasks/SF-EVAL-06.md`
- `docs/backlog.md`

约束：
- 遵循 `docs/engineering-invariants.md` 及 `benchmark/validated-candidates/twenty-tasks-v2.md` 的独立清单与参考预检不变量。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 未打补丁的原始 commit 运行 holdout | holdout 因断言失败而状态为 failed，无环境收集错误 | 通过 |
| A2 | 打入多文件参考补丁后运行 visible 与 holdout | visible 与 holdout 测试均 passed（通过） | 通过 |
| A3 | 参考补丁修改范围 | 补丁严格修改多于 1 个源码文件（`exceptions.py`、`functions.py`、`visitor.py`），且均在 `allowed_changes` 内 | 通过 |
| A4 | 运行 `selfforge-agent eval-validate` 资格预检 | 独立 manifest 预检状态输出 `validated`，包含明确 baseline/reference 报告 | 通过 |
| A5 | 历史资产不变性检查 | `suite-twenty-tasks-v2.json` 及其 assets hash 完全保持一致未变 | 通过 |

## 实施顺序

1. 编写独立 holdout 测试用例 `holdouts/test_jmespath_unknown_function.py`。
2. 验证基线提交在 holdout 下失败（负控）。
3. 构建多文件最小参考补丁 `references/jmespath_unknown_function.patch`。
4. 验证打入补丁后基线与 holdout 全部通过（正控）。
5. 编写独立 manifest `suite-jmespath-unknown-function.json` 与文档 `jmespath-unknown-function.md`。
6. 编写集成测试 `tests/test_benchmark_multi_file.py`。
7. 运行 `eval-validate` 与完整测试套件，更新任务卡与 backlog，提交并提 PR。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | 通过（All checks passed!） |
| `git diff --check` | Host | 本分支 | 通过（无空白错误） |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-jmespath-unknown-function.json --sandbox host --trust-local` | Host | 本分支 | 通过（status=validated, cases=1, baseline failed=3, reference passed visible=8 holdout=4） |
| `uv run --no-sync pytest -q tests/test_benchmark_multi_file.py` | Host | 本分支 | 3 passed in 3.23s |
| `uv run --no-sync pytest -q` | Host | 本分支 | 474 passed, 11 skipped in 270.59s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed in 41.09s |

## Review

- 严格遵循 TDD：基线无补丁时 holdout 确认 3 处断言失败（负控）；多文件补丁打入后 visible 8 通过、holdout 4 通过。
- 补丁跨越 3 个核心源码模块（`exceptions.py`、`functions.py`、`visitor.py`）及 1 个测试文件，完全契合多文件跨模块协同修改要求。
- 历史资产保持冻结：`suite-twenty-tasks-v2.json` 及其资产哈希未受任何影响。

## 历史 Handoff（实施时记录）

- 交付状态：实施与全量验证已完成，PR [#48](https://github.com/liuzhouquan/selfforge-agent/pull/48) 已创建等待审查合并。
- 当前分支：`feat/eval-multi-file-task`。主分支 `main` 保持纯净。

## Integration review (2026-11-12)

Reconciled with PR #55 base `944cfca` without discarding original commits.
The original development results above are historical, not the verification
results for the current combination. Corrections, boundaries and evidence:
[PR #48–#54 review](../review-pr48-54.md). Exact tested heads and CI are recorded
in the delivery comments on the corresponding PR. No paid model run.
