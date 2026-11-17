# PR #91–#100 审查、修复与合并记录

日期：2026-11-17。固定基线 `c5cbeffe332964d08b712036919873aeb1e13f46`；最终受测源码 `41cd9e2c8aed7461529f181c035054dfc8b17770`。使用独立 `review/pr91-100` worktree，保留十个 PR 的原始提交，逐项补修，再按准确 head 的成功 CI 依次合并。需求源为 [SDD](./SDD.md) 中的十项任务。

## Standards

最终复核未发现剩余可复现阻断，无新增主观 smell 建议。标准源为 [AGENTS.md](../AGENTS.md)、[CONTRIBUTING.md](../CONTRIBUTING.md)、[工程不变量](./engineering-invariants.md)和[开发验证门槛](./development.md#verification-gates)。

原实现存在未知调用被漏出成本总计、writer 写出 reader 无法读取的会话、gh 输出限额事后检查、只依据 Base 就声称 Head 命中、管道 I/O 错误被当作 EOF 等硬违反。逐项用真实文件/Git/进程或受控故障注入复现后修复；协议增补前独立复验公开边界 **19 passed，6.63 秒**；最后针对最终源码的 native 七参数边界独立复验 **7 passed，0.24 秒**。此计数不与完整集合相加。

#94 的 remote 选项执行猜测已撤回：实际 push 使用 `--`，还核验远端存在；未将未经成立的注入推断计为漏洞。

## Spec

最终复核未发现剩余可复现阻断。独立运行本批十个安全专项 **320 passed，35.86 秒**；完整结果另列，不相加。本轮修复前共有 **54 个确定失败用例**，另有正向对照；重复运行同一反例不重复计数。

| PR / 任务 | 原实现问题、修复与证明边界 |
| --- | --- |
| #91 / SF-AGENT-01 | 14 个失败用例：非 mapping 指标、不可哈希历史 reply ID/工具名、非 callable 取消谓词、超大时间整数，以及缺失调用 ID 导致 assistant/reply 不配对。安全回退指标，过滤历史标识符，统一生成唯一 ID 并同时写入 assistant 和 reply；保留原有写前证据撤销。初次相关回归96 passed；最后4例使用实际Adapter加HTTP替身确认坏function/name/arguments/type不能继续污染原生历史，规范化envelope并保留诊断且不执行坏调用，增补相关回归78 passed；另补控制字符/65字符名称的2负例和64字符名称的1正控；最后相关回归68 passed，独立#91 head安全专项53 passed。 |
| #92 / SF-REPAIR-01 | 4 个失败用例：内部目录符链可读取 tests/受保护文件，及 usage 或费率未知时丢弃该次调用、错误报告可校准部分成本。复用 ToolRegistry 的逐级 no-follow、有界普通文件读取；每条成本记录均保留未知占位。真实 Git worktree 与显式 trusted Host 的小型 pytest fixture 验证修复，不称作真实仓库 benchmark。安全专项 31 passed；既有 Repair 回归纳入最终完整集合。 |
| #93 / SF-SESS-01 | 4 个失败、1 个正控：resolve 吞掉 root/祖先符链，writer 可以写出超过 16 MiB 的不可恢复会话。目录创建/打开逐级锚定，session、trace、regression 读取复用该边界；锁内修尾后检查 end+payload，超限不追加。trace 保留尽力写入策略，容量拒绝保留已提交完整事件（未提交尾可先被修复）；session 明确报错。相关测试 55 passed。 |
| #94 / SF-DELIV-01 | 3 个失败用例：真实 gh 替身 producer 在 stdout/stderr 越界后仍继续执行；合法 200000 字符正文触发系统 argv 限制。复用 run_bounded 实时分别限制双流；大正文使用 mode-0600 临时文件和 --body-file，调用后清理。真实本地 bare remote push 与 gh CLI 替身验证，无真实发布 PR。相关测试 53 passed。 |
| #95 / SF-IMG-01 | 主体实现保留；补 3 个失败用例，镜像构建/内部 Docker 包装/剩余时间边界的超大整数原泄漏 OverflowError，现在作为非法时间受控拒绝。相关测试 57 passed。依赖来源、无网络镜像构建、固定哈希约束保留。 |
| #96 / SF-CAUSAL-01 | 5 个失败用例：九位行号前缀被截成目标行，显示位置携带 tab/ESC/DEL，以及 Base 命中而 Head 无关仍声明关联。数字结束边界防止伪匹配，清洗显示控制字符；先确认 Head 实际命中才执行 Base 降级。相关测试 29 passed；始终 causally_linked=False，位置关联不构成因果证明。 |
| #97 / SF-PROC-01 | 4 个失败用例：真实子进程 stdout 的 EIO 被当 EOF 返回成功；UTF-8 参数/终止字节边界超限；超大整数超时泄漏异常。读取错误传播并在 finally 清理进程，参数上限按实际编码及终止字节校验，超时先检范围。相关进程/worktree/交付测试 61 passed。 |
| #98 / SF-BMDIFF-01 | 8 个失败用例：数值 summary、case ratio 可混入表格/换行内容，bool/NaN/inf delta 可被输出。格式化前检查 count、rate、delta 一致性和可渲染的 duration/tokens/case/failure-reason 结构，坏数据受控拒绝；保留既有合法输出格式与表格名称转义。相关测试 32 passed。 |
| #99 / SF-JOB-01 | 4 个失败用例：manager 按字符接受超过 Agent 字节预算的任务，resume 类型未预检；超大结果或 session 状态写异常导致持久记录留在 running。调度前统一 UTF-8 字节预算与 bool 校验；异常路径丢弃无法持久化的旧 result、发布有界 failed 记录，并保留 finally 解锁。相关测试 35 passed，包含真实后台线程故障路径。 |
| #100 / SF-FDBK-01 | 5 个失败、7 个正控：合法 50–99 字符预算遇 JUnit 详情会抛异常，无详情时默认文字可超过上限。内部 formatter 使用自身合法最小预算，再按调用方上限裁剪；无证据分支也裁剪。覆盖 50、64、99、100、2000、50000 两类输入，相关反馈/诊断测试 63 passed。 |

两轴结论分别为 Standards 0 剩余阻断、0 新主观建议；Spec 0 剩余阻断。测试数量不是完成度评分，历史各分支计数不累加。

## 验证环境与完整结果

完整组合 **1356 passed，0 skipped**，574.64 秒；1条既有Starlette/httpx弃用警告。[最终准确组合CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44008150709) 1341单元+15Docker通过；Ruff通过；SDD结构及本地引用43 tasks / 171 acceptance items通过；空白检查通过。

```sh
uv sync --locked --dev --default-index https://pypi.org/simple
uv run --no-sync ruff check .
uv run --no-sync python scripts/check_sdd.py
git diff --check
uv run --no-sync python scripts/prepare_eval_sources.py --profile v3 --offline --root "$SELFFORGE_EVAL_SOURCE_ROOT"
SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q
```

五源 jmespath、boltons、pocketflow、more-itertools、itsdangerous 均通过 v3 offline 预检。实际 Docker 基础镜像 `sha256:8d45ef8ef046ddf7320d86bb0a10d2f09855e8765f41874397604d15bb4b7853`，锁定依赖镜像 `sha256:6b13b3c0348cf54585daf0e9ceff7b072efe91cbf76ad9c1bbd595b019b18e1d`。没有付费模型调用或新增真实模型成绩，没有改 UI。

#92 读取实现改用已有目录 FD 边界后，旧 Path.read_text 故障注入已转到实际 os.open 边界；读取失败安全省略的原断言保持。早期不合适的 trace 测试把尽力写入误期望为上抛，已按现有契约改为检查旧字节不变，并重新在未修源码确认失败，再恢复修复验证通过。测试替身不作为真实外部 GitHub 或模型服务的证明。

状态对账只改 SDD/本报告，执行文档引用及空白检查，不重跑 Python 来为文字改动增加测试数量。完整源码测试绑定上述准确 SHA；后续 main CI 结论在各 PR 交付描述中回填，避免递归文档提交。

## 合并记录

| PR | 准确审查 head | 准确 head CI | Merge SHA |
| --- | --- | --- | --- |
| [#91](https://github.com/liuzhouquan/selfforge-agent/pull/91) | `4012eb318b8497e36307d0fc3b2a98aed3f9ed52` | [44008139052](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44008139052)：单元及Docker成功 | `5821941c2f2699e682d5638f31669fb8b80047f4` |
| [#92](https://github.com/liuzhouquan/selfforge-agent/pull/92) | `431761fedace7907e439f1078ef4fa4d8eff125a` | [44003480075](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44003480075)：单元及Docker成功 | `2ecfb8b340d6fb59c6edc44766ed5d967b97bc44` |
| [#93](https://github.com/liuzhouquan/selfforge-agent/pull/93) | `d06a6b0ce59a1d2dd3e7a1c10e38d7077d656934` | [44003903794](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44003903794)：单元及Docker成功 | `495c564bbdd88c4dcd79264c3757bf733519831d` |
| [#94](https://github.com/liuzhouquan/selfforge-agent/pull/94) | `33a3e09886829107e64f44957505174967a26c30` | [44004288724](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44004288724)：单元及Docker成功 | `6fb71b3ca21756a53442fc7b3ee6c54916db789f` |
| [#95](https://github.com/liuzhouquan/selfforge-agent/pull/95) | `074614b82b95f07e176f66dec86cffb76eea58e5` | [44004553999](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44004553999)：单元及Docker成功 | `c044c87a2888846102f648f208267d18d197f2fb` |
| [#96](https://github.com/liuzhouquan/selfforge-agent/pull/96) | `9806b56689cace6e057d7a92b570a6c25c8a3081` | [44004768722](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44004768722)：单元及Docker成功 | `8f88d04de43c9c228f20aba4a175eb25bafbf7bb` |
| [#97](https://github.com/liuzhouquan/selfforge-agent/pull/97) | `9b29c2aacc13250ec4387450ada4d86b8505cb99` | [44005082682](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44005082682)：单元及Docker成功 | `8b1ccae96729f4dc8c130f3fd9de56c8baa19c86` |
| [#98](https://github.com/liuzhouquan/selfforge-agent/pull/98) | `9f585e711b5e0547b33a127ef24ac88c4fd84d25` | [44005320871](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44005320871)：单元及Docker成功 | `2325e3425ae86434281e8354a620f3bbd05785a4` |
| [#99](https://github.com/liuzhouquan/selfforge-agent/pull/99) | `b8e6f72fc718da4e0431f5fc43d0d1b54a4ff4a6` | [44005534857](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44005534857)：单元及Docker成功 | `0104c8f46f5661de63b2446d5035b699b1c0ac18` |
| [#100](https://github.com/liuzhouquan/selfforge-agent/pull/100) | `41cd9e2c8aed7461529f181c035054dfc8b17770` | [44008150709](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44008150709)：单元及Docker成功 | `80f7f85809494f5d34dba855a691b66403157216` |

每个准确 head CI 通过后按 #91→#100 合并，未强推或改写原历史。最后主线源码和测试内容与完整受测组合一致；后续仅文档对账。原 main checkout 快进至最终主线且保持干净；最终准确 main SHA 和 CI 链接见十个 PR 的交付描述。

协议增补过程：第一轮组合 `320d09d8c596514d039ce0bee18a383d5032e7c1` 完整1349 passed、无跳过，但最后协议复核补出4个反例；随后补齐名称字符/长度的2个负例和1个正控，一并修复并回填#91原分支；完整组合纳入新#91准确head后tree不变，再从新的准确源码重新执行完整测试。中途运行未完成，不计为验收通过。旧运行不是新最终源码的验收证明。
