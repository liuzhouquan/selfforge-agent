# SF-EVAL-01：增加一个有参考控制的任务（itsdangerous TimestampSigner 负数 max_age 校验收紧）

- 状态：In Review
- Owner：Antigravity Agent
- 基线：7e3ea6b283af390351f6cff55886ef7efe9049dc（已核对 origin/main）
- 分支 / 所属 worktree：feat/eval-itsdangerous-max-age-task
- 依赖：Docker daemon 与 `selfforge-agent-sandbox:latest` / `selfforge-itsdangerous:locked` 镜像已可用
- PR：待提交审核

## 目标与范围

为 `itsdangerous` 在固定版本 `672971d66a2ef9f85151e53283113f33d642dabd`（BSD License）提供 1 个参考合格评测题。

- 用户触发：评测套件预检与多仓库评测运行。
- 原来状态：`itsdangerous.timed.TimestampSigner.unsign`、`validate` 以及 `TimedSerializer.loads` 接受入参 `max_age`。但对非法的负数 `max_age < 0` 缺少显式入参校验：当传入 `max_age < 0` 时，即使令牌刚刚生成（有效签名），`unsign` 和 `loads` 也会抛出 `SignatureExpired`（误认为是令牌过期而非入参非法）；对于破坏签名的令牌则抛出 `BadSignature`；在 `validate()` 中捕获 `BadSignature` 后直接返回 `False`，掩盖了调用方传入负数有效期的配置错误。
- 本次完成后：
  1. 契约收紧：在 `TimestampSigner.unsign` 入口校验 `if max_age is not None and max_age < 0: raise ValueError("max_age must be >= 0")`，在进行任何签名比对或时间戳解析之前统一拒绝负数有效期。
  2. 保持兼容：保持 `max_age >= 0`、`max_age=0` 与默认 `max_age=None`（不限制有效期）的既有正常行为完全不变。
  3. 独立 holdout：在 `benchmark/validated-candidates/holdouts/test_itsdangerous_negative_max_age.py` 中覆盖 `unsign`（有效令牌与无效令牌）、`validate` 以及 `loads` 针对 `max_age in [-1, -2, -10]` 的 12 项负例异常断言，以及正整数与 None 的保持断言。基线呈现清晰的 12 个断言失败（0 collection error）；打补丁后 13 项全部通过。
  4. 最小参考补丁：`benchmark/validated-candidates/references/itsdangerous_negative_max_age.patch` 仅在 `src/itsdangerous/timed.py` 添加 2 行校验。
  5. 独立清单：新增 `suite-itsdangerous-max-age.json` 与累积 `suite-six-tasks.json`（保留 `suite-five-tasks.json` 历史冻结不变）。
  6. 文档与授权：编写 `itsdangerous-max-age.md`，并在 `LICENSES.md` 中完整登记上游 BSD 3-Clause License。
- 本次不做：不修改 Runtime 核心逻辑，不修改原有历史评测清单，不声称上游确认了该需求。
- 触及文件：
  - `benchmark/validated-candidates/LICENSES.md`
  - `benchmark/validated-candidates/holdouts/test_itsdangerous_negative_max_age.py`
  - `benchmark/validated-candidates/references/itsdangerous_negative_max_age.patch`
  - `benchmark/validated-candidates/itsdangerous-max-age.md`
  - `benchmark/validated-candidates/suite-itsdangerous-max-age.json`
  - `benchmark/validated-candidates/suite-six-tasks.json`
  - `docs/tasks/SF-EVAL-01.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 给定原始 baseline revision `672971d66a2ef9f85151e53283113f33d642dabd` | holdout 测试因负数 max_age 未抛出 ValueError 校验失败（12 个断言失败，0 collection error） | `eval-validate` 报告 status="failed", failed=12 |
| A2 | 给定打上参考补丁的独立 checkout | visible 与 holdout 均通过，无跳过与错误 | visible 2 passed, holdout 13 passed, exit 0 |
| A3 | 参考补丁范围受限 | 仅修改 allowed_changes 中的 `src/itsdangerous/timed.py` | `eval-validate` changed_files 验证通过 |
| A4 | 累积套件回归验证 | `suite-six-tasks.json` 6 题全部在 Docker 中通过预检与双向验证 | 6/6 validated, exit 0 |

## 实施顺序

1. 确认 `itsdangerous` 固定 SHA、许可证并在 `LICENSES.md` 登记，确认容器镜像 `selfforge-itsdangerous:locked` 就绪。
2. 编写独立 holdout 测试 `test_itsdangerous_negative_max_age.py` 并验证基线 12 个负例断言失败与 1 个正例通过。
3. 制作最小参考补丁 `itsdangerous_negative_max_age.patch` 并验证仅修改 `src/itsdangerous/timed.py`。
4. 编写候选描述 `itsdangerous-max-age.md`。
5. 生成 `suite-itsdangerous-max-age.json` 与 `suite-six-tasks.json`，运行 `eval-validate`。
6. 更新任务卡与交付记录。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-itsdangerous-max-age.json` | Docker (`selfforge-itsdangerous:locked`) | 本地工作树 | validated: 1 case, baseline failed=12, visible passed=2, holdout passed=13 |
| `uv run --no-sync selfforge-agent eval-validate --manifest benchmark/validated-candidates/suite-six-tasks.json` | Docker (live daemon) | 本地工作树 | validated: 6 cases, manifest SHA `5f737274d30c92f7a4a1d611060f72cf45704aaea087b67c8299e314e81e4d02` |
| `uv run --no-sync ruff check .` | Host | 本地工作树 | All checks passed! |
| `git diff --check` | Host | 本地工作树 | 无空白字符或格式错误 |
| `uv run --no-sync pytest -q -m 'not integration'` | Host | 本地工作树 | 343 passed, 11 deselected |

## Review

- [x] 验收编号是否都有证据：A1-A4 均有实际命令输出与生成的 JSON 证明。
- [x] 错误路径与断言：基线断言失败非环境/加载错误，参考修复正确区分了负数有效期与合法有效期。
- [x] 范围与不变量：不修改旧 manifest（保留 `suite-five-tasks.json`），独立 holdout 与 reference 存放在外部目录，使用预构建 hash-locked 镜像。
- [x] 权限与许可证：上游 BSD License 完整登记于 `LICENSES.md`。

## Handoff

- 交付状态：本地已实现并通过真实 Docker 验收，待审查合并。
- 当前分支：`feat/eval-itsdangerous-max-age-task`
- 已完成：A1, A2, A3, A4。
- 未完成/环境限制：无。依赖的 Docker 镜像 `selfforge-itsdangerous:locked` 已在本地 daemon 就绪并通过 `eval-validate`。
- 最近验证：`eval-validate`（6 题全部 validated）、`ruff check .`（All checks passed）、`git diff --check`（Clean）。
- 仍在运行：无。
- 下一步：创建 Pull Request 将 `feat/eval-itsdangerous-max-age-task` 合并入 `main`。
