> Integration review supersedes the original completion claims below. See [actual review evidence](../incoming-branches-review.md) and [current backlog](../backlog.md). CLI tests and wait reference controls required corrections before integration.

# SF-EVAL-01：增加一个有参考控制的任务（itsdangerous 空密钥防御）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-itsdangerous-task
- 依赖：Docker daemon 与 `selfforge-itsdangerous:locked` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `pallets/itsdangerous` 在固定版本 `672971d66a2ef9f85151e53283113f33d642dabd`（BSD-3-Clause）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`itsdangerous` 在构造 `Signer([])`、`Serializer([])`、`TimestampSigner([])` 等实例时，接受空密钥序列并默默产出空的 `secret_keys` 列表，直到后续调用 `sign()`、`unsign()`、`dumps()`、`loads()` 或访问 `.secret_key` 属性时才以 `IndexError: list index out of range` 崩溃。
- 本次完成后：
  1. 契约收紧：在 `src/itsdangerous/signer.py` 的 `_make_keys_list` 中，对传入空密钥序列的情况在初始化期抛出带有 `secret_key` 说明的 `ValueError`。
  2. 保持兼容：保持单字符串、单字节、非空密钥列表/元组及其密钥轮换（key rotation）的原有正确行为。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_itsdangerous_empty_secret_keys.py` 覆盖 6 个工厂类（`Signer`, `Serializer`, `TimestampSigner`, `TimedSerializer`, `URLSafeSerializer`, `URLSafeTimedSerializer`）和 3 种空输入（`[]`, `()`, `iter([])`），并包含字符串、字节、多密钥轮转和所有序列化器的正向控制测试。
  4. 最小参考补丁：`benchmark/validated-candidates/references/itsdangerous_empty_secret_keys.patch` 仅修改 `src/itsdangerous/signer.py` 与 `tests/test_itsdangerous/test_signer.py`。
  5. 独立清单：新增 `suite-itsdangerous.json` 与累积 `suite-six-itsdangerous.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 许可证与文档：在 `LICENSES.md` 记录 Pallets BSD-3-Clause 声明，编写 `itsdangerous-keys.md`。
- 本次不做：不修改 Runtime 核心逻辑，不批量重写上游测试，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/holdouts/test_itsdangerous_empty_secret_keys.py`
  - `benchmark/validated-candidates/references/itsdangerous_empty_secret_keys.patch`
  - `benchmark/validated-candidates/LICENSES.md`
  - `benchmark/validated-candidates/itsdangerous-keys.md`
  - `benchmark/validated-candidates/suite-itsdangerous.json`
  - `benchmark/validated-candidates/suite-six-itsdangerous.json`
  - `docs/tasks/SF-EVAL-01.md`
  - `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `672971d66a2ef9f85151e53283113f33d642dabd` | holdout 测试因断言未抛出预期 ValueError 失败（18 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=18 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 17 passed, holdout 21 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 2 个文件 | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-itsdangerous.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `itsdangerous` 固定 SHA、许可证与环境依赖（`selfforge-itsdangerous:locked`）。
2. 编写独立 holdout 测试 `test_itsdangerous_empty_secret_keys.py` 并验证基线 18 个负例断言失败与正例通过。
3. 制作最小参考补丁 `itsdangerous_empty_secret_keys.patch` 并验证仅修改允许文件。
4. 编写候选描述 `itsdangerous-keys.md`，更新 `LICENSES.md`。
5. 生成 `suite-itsdangerous.json` 与 `suite-six-itsdangerous.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-itsdangerous.json` | Docker (`selfforge-itsdangerous:locked`) | 本地工作树 | validated: 1 case, baseline failed=18, visible passed=17, holdout passed=21 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-itsdangerous.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `574173c0c46b663c66e88e861ea0469aa2be434187be3c71a92177da1da4574e` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 348 passed, 11 deselected in 238s |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复区分了空输入与正常密钥。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 BSD-3-Clause 完整附于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-itsdangerous-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-itsdangerous:locked` 与 sandbox 镜像已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-itsdangerous-task` 合并入 `main`。
