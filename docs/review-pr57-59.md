# PR #57–#59 审查与整合

2026-11-12；审查基点 `e07153e092150ab2d1f86aa98545a676836b6e14`。
按用户授权检查、修复并依次合并，保留所有原分支提交。当前任务状态仍只维护于 [SDD](./SDD.md)。

## Standards

规范轴发现 5 项，均已修复并添加反例：

- #57 的 `git checkout` 可执行 smudge filter，违反只准备固定 Git 对象的边界；改为 `update-ref` / `symbolic-ref`，不检出源码。
- #57 在普通文件检查前阻塞打开 FIFO 状态文件，绕过总预算；改用 `O_NONBLOCK` 后检查文件类型。
- #58 用 `ast.walk` 接受 helper 内嵌函数、错误层级与不可收集类的测试节点；限定模块/测试类直接成员，并拒绝静态 `__test__ = False`。
- #59 把 pytest 的任意失败算作断言失败；现在要求实际断言诊断以及旧 exit 1 / 新 exit 0。
- #59 的 Git/workspace 操作和最终指纹没有共用总预算；传递剩余 deadline，base staging 预留 0.25 秒给临时 worktree 清理，不增加执行预算。

最严重项为 #57 的外部过滤器执行与 #59 的错误确认；没有把一般命名或抽象偏好列为阻断问题。

## Spec

规格轴发现 4 项，均已修复：

- #59 最终指纹扫描异常时曾重试并置 `changed=False`，可把旧失败/新通过继续标为 confirmed。现在任何快照扫描失败均返回 error/snapshot_error，保留测试摘要但撤销确认。
- #59 非断言运行异常误确认，与规范轴重复。
- #59 总预算缺口，与规范轴重复。
- #58 虚假 pytest 节点误放行，与规范轴重复。

最严重项为无法验证最终指纹时仍确认成功。两轴合计涉及 **6 个独立缺陷**；没有扩大产品范围。

## 复现与验证

修复前已实际观察：FIFO CLI 超过 3 秒仍阻塞；准备源时 smudge 标记文件被创建；6 组虚假节点被检查器返回成功；旧 RuntimeError/新通过、最终指纹不可读两种情形错误返回 confirmed。对应新增回归在修复后通过。慢 Git CLI 与最终指纹检查另有总预算回归。

组合源码为 `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`（与修复提交 `c5115e8` 的 tree 相同，前者补入最新 main 合并历史）。

- `uv sync --locked --dev --default-index https://pypi.org/simple`：通过；默认镜像当时返回 403，使用官方源未改锁文件。
- `uv run --no-sync python scripts/prepare_eval_sources.py --root .local/benchmark/repos --timeout 120`：两项真实固定 GitHub 源 ready；随后 `--offline` 也成功。原目录有 symlink 的缓存被正确拒绝，未修改它们。
- `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`：**653 passed，0 skipped**，394.00 秒；1 条既有 Starlette/httpx 弃用警告。
- 本机实际 Docker 镜像：`sha256:abad14d310ba4ebd9e05b65ab6b4782a195f44987beab82e79087184e1767d20`。包含候选评测控制与回归验证的真实 Docker 测试，不依赖模型密钥。
- #59 与共享消费者定向回归：59 passed、3 integration deselected；完整组合已覆盖所选集成测试，未把 deselected 算作通过。
- Ruff、`python scripts/check_sdd.py`、`git diff --check`：通过。SDD 检查仅校结构和本地引用，不能证明测试执行或语义充分。

## 远端交付

| PR | 准确审查 head | CI | Merge SHA |
| --- | --- | --- | --- |
| [#57](https://github.com/liuzhouquan/selfforge-agent/pull/57) | `3623a22f9ddc1de68255d49eb66bca9420e97cb9` | [43188237786](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43188237786)：575 单元 + 13 Docker，通过 | `fa387bc4e427d9a682a00932802a7e4c8920bee7` |
| [#58](https://github.com/liuzhouquan/selfforge-agent/pull/58) | `212620664f49cc12428b5f8b100a7a71c406d9dd` | [43188609500](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43188609500)：620 单元 + 13 Docker，通过 | `06787d8a04cfa8d3b38c16e2b49ce7071f12c129` |
| [#59](https://github.com/liuzhouquan/selfforge-agent/pull/59) | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220` | [43189888813](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813)：639 单元 + 14 Docker，通过 | `b3ad75dfdfedefcd3218f790ae26f04b086b9fc1` |

不同版本的测试数量不相加。最终 main CI 结果在 PR #59 的交付描述中记录，避免为记录本次文档提交 SHA 而递归改文档。此后状态对账只改文档，源代码以组合验证版本为准。
