# PR #79–#90 审查、修复与合并记录

日期：2026-11-16。固定基线 `14203249e58444780422567ce98606217ea1ba36`；最终受测源码 `25dd53cc31317bdf6a3612f7ca3ed3388487e097`。按用户授权保留原分支历史，使用独立 `review/pr79-90` worktree 累积整合，再按准确 head 的 CI 结果依次合并。规格以 [SDD](./SDD.md) 的十二项任务为准。

## Standards

独立 Standards 审查与最终复核未发现剩余可复现阻断，未新增判断性 smell 建议。修复遵循 [AGENTS.md](../AGENTS.md)、[工程不变量](./engineering-invariants.md)与[开发验证门槛](./development.md#verification-gates)。

- #80/#83/#87 原输出限制在完整捕获后才生效；复用 `core/process.py`，对 stdout/stderr 在执行期间分别限额，截止时间覆盖管道 EOF 和等待，清理拥有的进程组。真实生产进程在越界后不能继续创建标记；子进程持有管道也受限。
- #79 配置读异常存在双重 close；明确 FD 所有权转交，真实复用编号的另一个文件描述符保持打开。该最终复核修复 `7aab9c9` 随 #90 的完整组合交付。
- #87 正常主体后的 cleanup 错误原来被吞掉；现在上抛，主体已失败时保留原异常。
- #82 两个同名专项测试文件分别保留为 `test_github_pr_security.py`（#80）和 `test_github_pr_reader_security.py`（#82）。过时的 subprocess.run 后验 mock 改为真实进程输出测试，原有断言和兼容回归保留。

## Spec

独立 Spec 审查对最终源码复核，未发现剩余可复现阻断。相关安全专项及 context/fingerprint 独立复验 150 passed；该数量不与完整集合相加。

| PR / SDD 任务 | 审查修复与有区分能力的证据 |
| --- | --- |
| #79 / SF-PROV-02 | 两个真实 loopback HTTP 服务复现跨源 redirect 泄露 fixture 凭据；现在禁止跨源重定向，同源链共用绝对截止时间。持久化前限制实际序列化字节，超大更新保留原配置；超大整数 timeout 与 JSON parser RecursionError 受控。递归异常用故障注入证明错误映射，不声称普通深 JSON 一定被原解析器拒绝。 |
| #80 / SF-INT-01 | 实际命令 stdout/stderr 超限前终止；复用共享捕获，不再等子进程全部输出后才检查。保留检出根路径、SHA 和 hunk 防御。 |
| #81 / SF-PRICE-01 | USD 消费者拒绝非 USD rate card 建立美元成本/预算证据；显式同币种合计仍可用，非 USD 格式不带美元符号。校验未验证对象 currency，环境上限按 UTF-8 字节，巨大整数格式回退。#84 同时补齐历史 usage 的严格整数反序列化，避免 True/字符串/浮点经 Pydantic 转成合法用量。 |
| #82 / SF-GH-01 | 修正过度限制，合法 `.github`、下划线与尾部标点仓库名保留；只拒绝危险路径/选项。真实只读 `github/.github` API 验证兼容性。路径保留首尾空格，拒绝实际 `..` 分段和尾部控制字符；元数据不再把字符串 false/非字符串字段静默强转。 |
| #83 / SF-CORE-01 | 真实 Git 回归保留 ` space.py `、`..valid.py`、`:literal.py`、`[literal].py` 文件；全局 literal pathspec 防止合法文件名变成查询语法。hunk 增量行号也检查上限，保留更严格的 5000 hunks / 50000 lines。 |
| #84 / SF-REV-01 | response/choices 先检查结构再访问，服务记录受控 error；prompt 按实际 UTF-8 字节限额。历史 usage 三字段严格整数，畸形用量保留 unknown 成本。 |
| #85 / SF-CTRL-01 | 在实际遍历前截取最后 1000 消息，工具参数预算同时约束检测器实际消费的字典；拒绝畸形 call ID、防循环与超大值。巨大压力/时间值受控。 |
| #86 / SF-MODEL-01 | 9 个原实现失败用例：非对象响应先于 usage 校验，合法 JSON 中的 backticks 保留，拒绝 `dir/..` 和根路径 `.`，复用统一 URL 端口/空白校验。 |
| #87 / SF-WT-01 | 3 个原实现失败用例：正常主体后的清理失败可见，stdout/stderr 超限即时停产；有界 Git 捕获共用进程工具。 |
| #88 / SF-RISK-01 | 原实现默认 reviewer 接收完整 1001 文件 context，事后截结果无效。现在先复制有界 context：最多 1000 文件、1000 hunks、10000 扫描行；baseline 和启发式共享该输入，不修改调用者。 |
| #89 / SF-FPR-01 | 真实 rename 旧根并在原路径建立新树，原实现仍返回旧树指纹。返回前重查根路径完整身份；目录替换或已观察变化使证据失效。指纹仍是有界观察，不是原子快照。 |
| #90 / SF-CTX-01 | 保留全部普通 system 指令和全部 user 任务；过滤畸形 native function 结构。真实结构正控保留单 assistant 的两条调用及反序到达的全部 replies；缺失/重复回复拒绝整块。估算仍标为启发式。 |

Standards：0 剩余阻断、0 新 smell 建议；Spec：0 剩余阻断。两轴结论独立，不能用测试通过代替需求核对。

## 最终验证

完整组合 **1036 passed，0 skipped**，510.70 秒；1 条既有 Starlette/httpx 弃用警告。[最终组合 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)：1021 单元 + 15 Docker 通过。Ruff 通过；SDD 33 tasks / 131 acceptance items 结构及本地引用检查通过；空白检查通过。

```sh
uv sync --locked --dev --default-index https://pypi.org/simple
uv run --no-sync ruff check .
uv run --no-sync python scripts/check_sdd.py
git diff --check
uv run --no-sync python scripts/prepare_eval_sources.py --profile v3 --offline --root "$SELFFORGE_EVAL_SOURCE_ROOT"
SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q
```

固定源为 v3 的 jmespath、boltons、pocketflow、more-itertools 和 itsdangerous；通过 offline 预检。真实 Docker 镜像：基础 `sha256:8d45ef8ef046ddf7320d86bb0a10d2f09855e8765f41874397604d15bb4b7853`，锁定依赖 `sha256:6b13b3c0348cf54585daf0e9ceff7b072efe91cbf76ad9c1bbd595b019b18e1d`。标准安装镜像曾 403，改用 PyPI 完成 locked 安装，不改锁文件。

模型只使用本地 HTTP 服务、脚本与替身；没有付费模型测量。没有修改 UI。历史分支数量不累加；失败日志、私有路径、会话及配置未提交。后续 SDD/报告对账仅修改文档，运行文档检查，不把旧源测试称作后续文档提交的新执行结果。

## 合并记录

| PR | 准确审查 head | 准确 head CI | Merge SHA |
| --- | --- | --- | --- |
| [#79](https://github.com/liuzhouquan/selfforge-agent/pull/79) | `6cf3bd23829629b64a44c4596b32be5d00646721` | [43786522012](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43786522012)：单元及 Docker 成功 | `036e67863d83a69b4bde3f5c3ffe3a34a171d54c` |
| [#80](https://github.com/liuzhouquan/selfforge-agent/pull/80) | `7db8ce333d501d37be7450b0297edb15081b88a5` | [43787112189](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43787112189)：单元及 Docker 成功 | `97ec2989c1815a068983d8cdb96ef3acbf8ec32f` |
| [#81](https://github.com/liuzhouquan/selfforge-agent/pull/81) | `c77552e877dde7d9f2bc7efa2aed406ee419b184` | [43787452326](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43787452326)：单元及 Docker 成功 | `18b8021ed147ce9778fd483a8c1ae5c4fd499849` |
| [#82](https://github.com/liuzhouquan/selfforge-agent/pull/82) | `dbeb0fa28577331fafa6af88d3ac5c235d65d124` | [43788149709](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43788149709)：单元及 Docker 成功 | `094d074a1e3df08340c61bf7ff9339de67cb21c0` |
| [#83](https://github.com/liuzhouquan/selfforge-agent/pull/83) | `113b34c4097ea721da65e2fedc1025eae16f9b82` | [43788741637](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43788741637)：单元及 Docker 成功 | `827cd214726ab52944a13c55758c200f25398e81` |
| [#84](https://github.com/liuzhouquan/selfforge-agent/pull/84) | `d3f3490c8a0be4053f60968e191b21a528b4cbf2` | [43790334233](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43790334233)：单元及 Docker 成功 | `7f7a951f71dbd1b0092532c395b8343dd575e8c5` |
| [#85](https://github.com/liuzhouquan/selfforge-agent/pull/85) | `466f414261f349673af9c66bca94e9542bca446b` | [43791204722](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43791204722)：单元及 Docker 成功 | `149b127713472b682d5f57fb2724ebe3aa1f38e9` |
| [#86](https://github.com/liuzhouquan/selfforge-agent/pull/86) | `42e07a9f47dcd8c75cb4240ec669fa5c61d7d85f` | [43792070774](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43792070774)：单元及 Docker 成功 | `ce5a22a51243482adf743033c1719afe555a2e64` |
| [#87](https://github.com/liuzhouquan/selfforge-agent/pull/87) | `7629de8032bd15e1a77d14879d5578c6c68c884e` | [43792231279](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43792231279)：单元及 Docker 成功 | `7910ad956ed8b0fae3f244b4feffb3c26dcf6a03` |
| [#88](https://github.com/liuzhouquan/selfforge-agent/pull/88) | `3943c8a0b2506e6d565b4ab1097c163c9a28034c` | [43792480706](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43792480706)：单元及 Docker 成功 | `36280f7ac28e6cfb08339d34bb978a84ae1fc56a` |
| [#89](https://github.com/liuzhouquan/selfforge-agent/pull/89) | `d346c4df5e1b35f6438d0a82b36a700f9e05a97c` | [43792651911](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43792651911)：单元及 Docker 成功 | `fa7b698087d7c7a803de7dbcb839d12d3bd4889a` |
| [#90](https://github.com/liuzhouquan/selfforge-agent/pull/90) | `25dd53cc31317bdf6a3612f7ca3ed3388487e097` | [43793394572](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)：单元及 Docker 成功 | `224a147e6abe6810edafb6bab0bb91f2dd89b663` |

每个 PR 的准确审查 head CI 通过后按 79→90 合并，未强推或重写源提交；累积整合包含先前审查修复。#90 的组合是完整受测源码；最终合并主线的源码 tree 与之相同。各 merge SHA 与当前状态回填 SDD，最终主线 CI 准确结论回填十二个 PR 的交付描述，避免文档状态递归提交。

兼容补修：首次完整测试发现旧会话省略 call.type 的完整工具块被新校验误丢弃；保留该历史格式，显式非法类型仍拒绝。上下文完整专项25 passed后，从新源码重新运行完整集合。首次中断运行不计为验收通过。
