# SF-ENV-06：依赖声明与锁文件一致性新鲜度校验与 Dry-run 诊断

- 状态：In Review
- Owner：Antigravity Agent
- 基线：`4ecd6a5d605c7f3b5b9140f00cd1a5c3f6360c7a`（`origin/main`）
- 分支 / 所属 worktree：`feat/environment-lock-freshness-diagnosis`
- 依赖：无（基于已合并的 PR #47 / main）
- PR：[#51](https://github.com/liuzhouquan/selfforge-agent/pull/51)

## 目标与范围

在已有离线依赖锁文件解析（`uv.lock` / `poetry.lock` / `Pipfile.lock` / `requirements.lock`）、环境准备计划（`plan_environment_preparation`）与 wheelhouse 审计（`audit_wheelhouse`）的基础上，提供依赖声明与锁文件之间的一致性新鲜度校验与只读 dry-run 诊断（`check_dependency_freshness`、CLI `sandbox freshness` / `sandbox check`、API `POST /api/sandbox/freshness`）。

原来发生什么：
1. 现有工具链（`sandbox plan`、`sandbox audit`）仅审计锁文件与本地 wheelhouse 的匹配情况，完全假定锁文件必然与代码仓库的依赖声明（`pyproject.toml` / `requirements.in` / `Pipfile`）一致。
2. 当开发者在声明文件中增加了新包或修改了版本约束，但遗漏了重新锁定（即锁文件陈旧、依赖缺失或版本冲突）时，系统缺乏前置一致性诊断。
3. 这种陈旧脱节会导致离线镜像构建成功后，在执行测试阶段遭遇 `ModuleNotFoundError` 或版本冲突，浪费模型预算和排错时间。

本次完成后应观察到：
1. 实现 `check_dependency_freshness(repo: Path, *, lock_file: Path | None = None, requirements_file: Path | None = None) -> dict[str, Any]`：
   - 声明解析：安全解析 PEP 621 `pyproject.toml`（`project.dependencies`、`project.optional-dependencies`）、Poetry（`tool.poetry.dependencies`）、`requirements.in` 以及 `Pipfile` 中的包声明。
   - 环境标记过滤：使用 PEP 508 环境变量标记过滤不属于当前运行环境的依赖（如 `python_version < "3.8"` 在 Python 3.12 下被安全跳过）。
   - 依赖覆盖校验（`missing_packages`）：核对声明的直接依赖包是否均已包含在锁文件中。
   - 版本兼容校验（`incompatible_packages`）：使用 `packaging.specifiers.SpecifierSet` 校验锁定版本是否满足声明的版本约束范围。
   - 时间戳新鲜度诊断（`is_stale_by_mtime`）：检测声明文件是否晚于锁文件被修改。
   - 诊断返回值结构清晰标准：包含 `fresh`（bool）、`status`、`declared_count`、`locked_count`、`missing_packages`、`incompatible_packages`、`is_stale_by_mtime`、`warnings`。
2. 整合进 `plan_environment_preparation`：
   - 在生成的计划字典中增加 `"freshness": freshness_report` 字段。
   - 当新鲜度不合格时，在计划的 `warnings` 中输出重新锁定指导建议。
3. CLI 支持 `selfforge-agent sandbox freshness --repo <dir> [--lock <file>] [--requirements <file>] [--output <file>]`（兼容 `sandbox check`）：
   - 新鲜一致时退出码为 0，存在缺失或冲突时退出码为 1。
   - 支持 `--output` 输出完整 JSON 报告。
4. Web API 新增 `POST /api/sandbox/freshness` 端点。
5. 严格保留工程不变量：
   - 纯只读离线检查，绝不自动联网，绝不隐式修改用户文件。
   - 安全静态解析，绝不执行不受信任的 setup.py 或动态代码。
   - 缺失声明文件时优雅降级（`no_declarations`，不误报失败）。

本次不做：
- 自动运行 `pip-compile` 或 `uv lock`（禁止静默更改用户工程依赖）。
- 动态运行 setup.py 或构建依赖树。

预计触及：
- `src/selfforge_agent/runtime/environment.py`
- `src/selfforge_agent/cli.py`
- `src/selfforge_agent/api/app.py`
- `tests/test_dependency_freshness.py`
- `docs/tasks/SF-ENV-06.md`
- `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 声明与锁文件完全一致的仓库 | `check_dependency_freshness` 返回 `fresh=True`，`status="fresh"`，无 missing 或 incompatible | 通过 (`test_freshness_clean_consistent_repo`) |
| A2 | 声明了新依赖但锁文件中缺失 | `fresh=False`，`missing_packages` 包含该依赖名称和来源，输出警告 | 通过 (`test_freshness_missing_dependency`) |
| A3 | 声明的版本范围与锁定的实际版本冲突 | `fresh=False`，`incompatible_packages` 报告冲突包名、声明约束和锁定版本 | 通过 (`test_freshness_incompatible_version`) |
| A4 | 声明文件更新时间明显晚于锁文件 | `is_stale_by_mtime=True`，报告包含时间戳脱节警告 | 通过 (`test_freshness_mtime_drift`) |
| A5 | 声明含有不匹配当前环境的 PEP 508 标记 | 自动安全跳过该条件依赖，不误报为 missing | 通过 (`test_freshness_pep508_markers_filtered`) |
| A6 | 仅有锁文件而无声明文件的仓库 | 优雅降级，`status="no_declarations"`，`fresh=True`，无异常中断 | 通过 (`test_freshness_no_declaration_file_graceful`) |
| A7 | `plan_environment_preparation` 整合 | 计划中包含 `freshness` 键，脱节时自动在 `warnings` 中加入重新锁定提示 | 通过 (`test_plan_environment_preparation_integrates_freshness`) |
| A8 | CLI `sandbox freshness` 命令 | 诊断通过返回 0，脱节返回 1，支持 `--output` 保存 JSON 报告 | 通过 (`test_cli_sandbox_freshness_exit_codes_and_output`) |
| A9 | API `POST /api/sandbox/freshness` 端点 | TestClient 发送请求，返回完整诊断 JSON | 通过 (`test_api_sandbox_freshness_endpoint`) |

## 实施顺序

1. 编写全面的 TDD 测试套件 `tests/test_dependency_freshness.py`，证明未实现时调用失败（TDD）。
2. 在 `src/selfforge_agent/runtime/environment.py` 中实现依赖声明提取器与 `check_dependency_freshness`。
3. 在 `plan_environment_preparation` 中接入新鲜度诊断结果。
4. 在 `src/selfforge_agent/cli.py` 中增加 `sandbox freshness` / `sandbox check` 处理。
5. 在 `src/selfforge_agent/api/app.py` 中增加 `POST /api/sandbox/freshness` 路由。
6. 运行全量单元测试与 Docker 集成测试。
7. 更新任务卡与 backlog，提交并推送创建 PR。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync ruff check .` | Host | 本分支 | All checks passed! (0 errors) |
| `git diff --check` | Host | 本分支 | 0 issues |
| `uv run --no-sync pytest -q tests/test_dependency_freshness.py` | Host | 本分支 | 12 passed in 0.63s |
| `uv run --no-sync pytest -q` | Host | 本分支 | 483 passed, 11 skipped, 1 warning in 303.32s |
| `uv run --no-sync pytest -q -m integration` | Host Docker | 本分支 | 11 passed, 483 deselected, 1 warning in 46.90s |

## Review

1. 多源依赖声明支持与安全解析：支持 PEP 621 `pyproject.toml`（`project.dependencies`）、Poetry `tool.poetry.dependencies`（自动转换 caret/tilde 版本语法）、`requirements.in` 与 `Pipfile`；采用标准 TOML 解析，绝不执行不受信任的 setup.py 代码。
2. PEP 508 环境变量标记与环境过滤：精确过滤不适用于当前 Python 环境的条件依赖（如旧版本专用的 compatibility 包），杜绝误报。
3. 锁文件覆盖与规范化比对：使用 PEP 503 标准化包名（`canonicalize_name`）与 PEP 440 `SpecifierSet` 校验版本范围兼容性，精确诊断缺失包与版本冲突。
4. MTime 时间戳脱节诊断：在声明文件更新时间晚于锁文件时自动输出预警，避免开发者修改配置后遗忘重新锁定。
5. 全流程无缝集成：`plan_environment_preparation`、CLI `sandbox freshness` 与 API `POST /api/sandbox/freshness` 统一接入，严格保持离线与只读安全不变量。

## Handoff

- 交付状态：实施与全量验证已完成，PR [#51](https://github.com/liuzhouquan/selfforge-agent/pull/51) 已创建等待审查合并。
- 当前分支：`feat/environment-lock-freshness-diagnosis`。主分支 `main` 保持纯净。

## Integration review (2026-11-12)

Reconciled with PR #55 base `944cfca` without discarding original commits.
The original development results above are historical, not the verification
results for the current combination. Corrections, boundaries and evidence:
[PR #48–#54 review](../review-pr48-54.md). Exact tested heads and CI are recorded
in the delivery comments on the corresponding PR. No paid model run.
