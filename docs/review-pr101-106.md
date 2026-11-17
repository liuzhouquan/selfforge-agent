# PR #101–#106 审查、修复与合并记录

日期：2026-11-17。固定基线 `015dc6a75f0671a3537edc7706937a0826128bf9`；最终受测源码 `dea260d967531d6beabc19fc62eb3afdf06e82fd`。使用独立 `review/pr101-106` worktree，保留六个 PR 原始提交，逐项补修后按准确 head 成功 CI 合并。需求源为 [SDD](./SDD.md) 六项任务；不改 UI，不调用付费模型。

## Standards

最终只读复核未发现剩余可复现阻断，无新增主观 smell 建议。标准源：[AGENTS.md](../AGENTS.md)、[CONTRIBUTING.md](../CONTRIBUTING.md)、[工程不变量](./engineering-invariants.md)、[验证门槛](./development.md#verification-gates)。

独立边界复验先有 19 项通过；#106 最终校验边界 8 项通过；审批 JSON 修复另有 1 项通过。计数不与完整集合相加。修复前均先建立具体失败用例；真实文件、Git、显式 trusted Host、小型 pytest、HTTP 边界替身及受控故障注入按各自能力提供证据。镜像构造校验测试本身不证明 Docker 执行，真实 Docker 证据来自完整集合及 CI。

## Spec

最终只读复核未发现剩余可复现代码阻断。独立六专项 **301 passed**，1 条既有依赖弃用警告。本轮建立 **56 个确定失败用例**，另补正向兼容对照；重复运行同一反例不重复计数。

| PR / 任务 | 原问题、修复与证明边界 |
| --- | --- |
| #101 / SF-MODEL-02 | 17 个失败：畸形工具定义进入提供者、非文本 content 与非字符串 finish_reason 被接受、巨大整数溢出、低于最小超时，以及长 turn 预算越过 HTTP 单次上限。严格校验工具名/函数、响应类型和数值；HTTP 单次最多 300 秒，turn 的共享绝对截止时间保持。使用实际 request_json 加 urlopen 边界替身，未调用真实提供者。正控确认总 Agent 预算大于单次 turn 上限仍合法，不为此修改 CodingAgent。 |
| #102 / SF-REGRESS-01 | 4 个失败：Pydantic before validator 抛 TypeError 导致请求 500；测试 SHA256 错用允许 40 位 Git SHA 的模式；巨大 timeout 溢出。改为 Pydantic ValueError，实际 HTTP 返回 422；测试摘要严格 64 hex，超时先检查范围再转换。相关回归 88 passed。 |
| #103 / SF-EVAL-11 | 12 个失败：Agent 可以改弱可见测试而冒充成功；非法 UTF-8 源码中断整套；模型异常正文泄漏；全 skip 被算成功；预设集合可修改；名称空白被隐藏；超大预算溢出；步数与共享 Agent 冲突。冻结 case，复用 no-follow 有界读取，只提取 app.py，在另一个临时目录安装原始测试并独立消费正 JUnit；每例隔离错误，公开报告只留错误类别。相关 eval/runtime 回归 124 passed。此路径仍为显式 trusted Host 的单源码微型 demo，独立目录不是对恶意代码的沙箱，不是实仓 benchmark。 |
| #104 / SF-CLI-02 | 9 个失败：真实 MonetaryCost 导致文本/JSON/文件导出全部崩溃；CLI 接受底层不支持的步数/context/timeout 或低于规定的时间预算；交互审批提示污染 JSON stdout。费用转 JSON 数据，执行前参数范围与共享 Agent/Runner 对齐；JSON 模式审批提示与输入提示走 stderr，保留 stdin 授权。真实 CLI/Git、实际结果模型和边界替身；最终相关 CLI 回归 39 passed。 |
| #105 / SF-API-02 | 6 个失败：新增 ref 白名单误拒绝 feature+fix 和 HEAD~1，影响 audit/storage/prune。只做有界、无控制/空白、非选项及路径穿越防御，保留 Git 提交表达式；实际仓库内已有 rev-parse --verify --end-of-options 完成解析。六例使用真实 Git/workspace 与 TestClient；相关 API/清理/存储/请求验证 122 passed。 |
| #106 / SF-VERIF-02 | 8 个失败：合法命名 sha256 digest 被镜像白名单拒绝，构造入口漏拒前导 -；Host/Docker Runner 对 bool、字符串、超大整数 timeout 缺乏受控拒绝。保留命名 digest，构造时拒前导选项，先类型/范围后 finite 校验。相关 Runner/API/JUnit 回归 35 passed；JUnit 和进程超时清理原防御保留。下游原已有镜像选项防御，不将构造漏检夸大成选项执行。 |

共享规格已对账：SF-EVAL-11 最多 100 步；SF-CLI-02 最多 100 步、context 至少 500，time budget 0.1–86400；test timeout 为 Host 最多 3600、Docker 工具最多 180 秒。原任务更宽的数字与现有共享契约冲突，未放宽底层执行器。

两处旧 fallback/reasoning 测试使用缺少 function/name 的工具占位，现改为合法 read_file 定义，原 fallback 与 reasoning 保留断言不变；回填 #101 后同步 #102，后者早期 CI 的两项失败由此闭合。#105 的 regression 请求空字节测试精确改验 Pydantic 422、body.repository、value_error；其他路径仍验 400，未放宽为任选状态码。

两轴分别为 Standards 0 剩余阻断、0 新主观建议；Spec 0 剩余代码阻断。测试数不代表完成度，历史分支计数不相加。

## 完整验证与环境

完整组合 **1657 passed，0 skipped**，754.93 秒；1 条既有 Starlette/httpx 弃用警告。[最终准确组合CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44079340729) 单元与Docker均成功。Ruff通过；SDD结构及本地引用49 tasks / 195 acceptance items通过；空白检查通过。

```sh
uv sync --locked --dev --default-index https://pypi.org/simple
uv run --no-sync ruff check .
uv run --no-sync python scripts/check_sdd.py
git diff --check
uv run --no-sync python scripts/prepare_eval_sources.py --profile v3 --offline --root "$SELFFORGE_EVAL_SOURCE_ROOT"
SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q
```

五源 jmespath、boltons、pocketflow、more-itertools、itsdangerous 均通过 v3 offline 预检。Docker 基础镜像 `sha256:8d45ef8ef046ddf7320d86bb0a10d2f09855e8765f41874397604d15bb4b7853`；锁定依赖镜像 `sha256:6b13b3c0348cf54585daf0e9ceff7b072efe91cbf76ad9c1bbd595b019b18e1d`。第一次完整运行因审批 JSON 新反例而主动中断，不计通过；只有最终源码下重新运行的完整结果有效。模型、HTTP 替身不证明真实服务，未新增 live-model 成绩。

准确 head 校验及按序合并后，对比 main 与受测组合全树一致，再仅修改 SDD/本报告。文档检查不重复 Python 测试；最终 main CI 准确结论回填六个 PR 描述，避免递归文档提交。

## 交付

| PR | 准确审查 head | 准确 head CI | Merge SHA |
| --- | --- | --- | --- |
| [#101](https://github.com/liuzhouquan/selfforge-agent/pull/101) | `b54c4a7dd2b9ecaffc17552abe774d9116e44fac` | [44076910464](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44076910464)：单元及Docker成功 | `9c438ed7083f3a55e006b5694da70efaf2189afc` |
| [#102](https://github.com/liuzhouquan/selfforge-agent/pull/102) | `02fd6256e49f76d55bba0cf897930062d6b215dd` | [44079517530](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44079517530)：单元及Docker成功 | `0f3dae46b63436d96535c3e4c0d0ed6a04a9c52a` |
| [#103](https://github.com/liuzhouquan/selfforge-agent/pull/103) | `e74842a26da4af8ef82a5324434a97437fa15048` | [44077278786](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44077278786)：单元及Docker成功 | `bad34e5c7c86637b6059ae53fc3aab208e0cf62e` |
| [#104](https://github.com/liuzhouquan/selfforge-agent/pull/104) | `a0f096508e41866d8ecbc3751e662d1d775c408c` | [44079313186](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44079313186)：单元及Docker成功 | `ddfd51fc622c281e8a4a646c3c723f3024034ca9` |
| [#105](https://github.com/liuzhouquan/selfforge-agent/pull/105) | `8312781f29ea5d3b7c2076bae6cc2348e0dc00eb` | [44078754661](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44078754661)：单元及Docker成功 | `14a91b7834bfe82495067425058a99ef7068b462` |
| [#106](https://github.com/liuzhouquan/selfforge-agent/pull/106) | `dea260d967531d6beabc19fc62eb3afdf06e82fd` | [44079340729](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44079340729)：单元及Docker成功 | `010897851b27dc920e1892cf5c35568bfee3682d` |
